"""Мини-цель недели: варианты по слабым местам, автоматический прогресс, итог прошлой недели."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain import weekgoal as wg
from tests.test_web_api import H, _fixed_now, cfg, client, db, make_client  # noqa: F401  (фикстуры)


def _day(**kw):
    base = {"meals": 3, "protein": 100, "protein_target": 100, "water": 2000, "water_goal": 2000,
            "late_kcal": 0, "steps": None, "sleep_min": None}
    base.update(kw)
    return base


def test_week_starts_on_monday():
    assert wg.week_start(date(2026, 11, 4)) == date(2026, 11, 2)     # среда
    assert wg.week_start(date(2026, 11, 8)) == date(2026, 11, 2)     # воскресенье


def test_suggest_targets_weak_spot_and_gentle_target():
    days = [_day(protein=40)] * 5 + [_day()] * 2                    # белок в норме 2 дня из 7
    s = wg.suggest(days)
    assert s[0]["kind"] == "protein" and s[0]["hits"] == 2 and s[0]["target"] == 4
    assert all(x["kind"] not in ("steps", "sleep") for x in s)       # данных нет
    assert all(x["hits"] < 6 for x in s)


def test_suggest_starts_with_logging_when_few_days():
    days = [_day(meals=0, protein=0)] * 5 + [_day()] * 2
    assert wg.suggest(days)[0]["kind"] == "logging"


def test_progress_counts_days_and_marks_future():
    start = date(2026, 11, 2)
    facts = {"2026-11-02": _day(protein=100), "2026-11-03": _day(protein=10),
             "2026-11-04": _day(protein=10)}
    p = wg.progress("protein", 3, start, date(2026, 11, 4), lambda d: facts[d])
    assert p["done"] == 1 and p["possible"] is True and p["achieved"] is False
    assert [c["state"] for c in p["cells"]][:4] == ["ok", "miss", "today", "future"]
    p2 = wg.progress("protein", 6, start, date(2026, 11, 7), lambda d: _day(protein=10))
    assert p2["possible"] is False


@pytest.mark.asyncio
async def test_api_choose_progress_and_clear(client):
    body = await (await client.get("/api/week-goal", headers=H)).json()
    assert body["goal"] is None and body["options"]
    pick = body["options"][0]
    resp = await client.put("/api/week-goal", json={"kind": pick["kind"], "target": 4}, headers=H)
    assert resp.status == 200
    goal = (await resp.json())["goal"]
    assert goal["kind"] == pick["kind"] and goal["target"] == 4 and len(goal["cells"]) == 7
    again = await (await client.get("/api/week-goal", headers=H)).json()
    assert again["goal"]["kind"] == pick["kind"] and again["options"] == []
    cleared = await (await client.delete("/api/week-goal", headers=H)).json()
    assert cleared["goal"] is None


@pytest.mark.asyncio
async def test_api_rejects_bad_goal(client):
    for payload in ({"kind": "nope", "target": 4}, {"kind": "water", "target": 1},
                    {"kind": "water", "target": 9}, {"kind": "water"}):
        resp = await client.put("/api/week-goal", json=payload, headers=H)
        assert resp.status == 400, payload


@pytest.mark.asyncio
async def test_recap_hidden_without_last_week_data(client):
    body = await (await client.get("/api/week-goal", headers=H)).json()
    assert body["recap"] is None
