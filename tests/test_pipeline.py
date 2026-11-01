"""Конвейер еды на моках LLM: 10 текстовых кейсов из фикстур дают суммы ±10 %."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app.db import connect, migrate
from app.storage import repo

FIXTURES = Path(__file__).parent / "fixtures" / "text_cases.json"


class MockLLM:
    """Подменяет llm.chat_json: возвращает фикстурный ответ по тексту."""

    def __init__(self, cases):
        self.cases = {c["text"]: c for c in cases}
        self.calls = 0

    async def chat_json(self, purpose, messages, **kw):
        self.calls += 1
        user = messages[-1]["content"]
        if isinstance(user, str) and '"text"' in user:
            payload = json.loads(user)
            text = payload.get("text", "")
        else:
            text = ""
        case = self.cases.get(text)
        if case is None:
            raise AssertionError(f"нет фикстуры для текста: {text!r}")
        if purpose == "food_llm_prior":
            raise AssertionError("матчинг не должен уходить в llm_prior для фикстур")
        return json.loads(json.dumps(case["llm"])), "mock/model"


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


@pytest.mark.asyncio
async def test_ten_text_cases(db, monkeypatch):
    pytest.importorskip("pytest_asyncio", reason="нужен pytest-asyncio") if False else None
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]
    mock = MockLLM(cases)
    monkeypatch.setattr("app.services.food.llm.chat_json", mock.chat_json)

    from app.services.food import build_calc, understanding_from_json

    uid = repo.create_user(db, 100)
    passed = 0
    for case in cases:
        u = understanding_from_json(case["llm"])
        calc = await build_calc(u, db, uid, use_llm_fallback=False)
        assert calc.items, case["text"]
        lo, hi = case["expect_kcal_min"], case["expect_kcal_max"]
        mid = (lo + hi) / 2
        assert abs(calc.kcal_total - mid) / mid <= 0.10 + 1e-9, \
            f"{case['text']}: {calc.kcal_total} вне ±10 % от [{lo}, {hi}]"
        assert calc.kcal_low <= calc.kcal_total <= calc.kcal_high
        # карточка и сумма дня сходятся
        meal_id = repo.save_meal(db, uid, "2026-10-28 12:00:00", "2026-10-28",
                                 "12:00", "lunch", "text", calc)
        day = repo.day_totals(db, uid, "2026-10-28")
        assert day["kcal"] == calc.kcal_total
        repo.soft_delete_meal(db, uid, meal_id)
        passed += 1
    assert passed == len(cases)
