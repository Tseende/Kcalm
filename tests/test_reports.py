"""Советы, графики, вопросы по отчёту и единое rich-оформление."""
from __future__ import annotations

import pytest
from aiogram.methods import EditMessageText, SendMessage, SendRichMessage

from app import rich
from app.domain import body
from app.services import assistant, charts, insights
from tests.test_routers_smoke import _press, build, db, message, onboard, send  # noqa: F401




def _facts(**kw):
    base = {"date": "2026-10-30", "kcal": 1900, "meals": 3, "protein": 80, "fat": 60, "carbs": 200,
            "kcal_target": 2100, "protein_target": 120, "water": 900, "water_goal": 2200,
            "steps": 12768, "sleep_min": 400, "late_kcal": 0, "cutoff": "21:00", "care": False}
    return {**base, **kw}


def test_active_day_with_low_protein_and_water_gets_both_tips():
    good, tips = insights.day_tips(_facts())
    texts = " ".join(t.text for t in tips)
    assert "белок" in texts.lower() and "Воды мало" in texts
    assert any("движения" in g for g in good)


def test_care_mode_hides_calorie_advice_and_row():
    f = _facts(care=True, kcal=800)
    assert all(r[0] != "Калории" for r in insights.scorecard_rows(f))
    _, tips = insights.day_tips(f)
    assert not any("ккал" in t.text for t in tips)


def test_open_day_does_not_scold_unfinished_protein():
    _, tips = insights.day_tips(_facts(protein=20, water=0), closed=False)
    assert tips == []


def test_week_tips_stay_quiet_without_data():
    days = [_facts(date=f"2026-10-{24 + i}", meals=0, kcal=0, protein=0, water=0, steps=None,
                   sleep_min=None) for i in range(7)]
    good, tips = insights.week_tips({"days": days, "prev": days, "care": False})
    assert good == [] and [t.icon for t in tips] == ["📝"]


def test_body_advice_reads_fat_vs_lean_loss():
    prev = {"weight_kg": 92.0, "body_fat_pct": 28.0}
    lean_loss = {"weight_kg": 90.5, "body_fat_pct": 28.6}
    assert "мышцы" in " ".join(body.advice(lean_loss, prev, "male", 33))
    fat_loss = {"weight_kg": 90.5, "body_fat_pct": 26.5}
    assert "в основном жир" in " ".join(body.advice(fat_loss, prev, "male", 33))
    assert "точка отсчёта" in " ".join(body.advice(fat_loss, None, "male", 33))


def test_charts_render_png():
    assert charts.day_chart(_facts())[:4] == b"\x89PNG"
    days = [_facts(date=f"2026-10-{24 + i}") for i in range(7)]
    assert charts.week_chart(days, False)[:4] == b"\x89PNG"


def test_rich_middleware_converts_plain_messages_only():
    sent = rich._to_rich(SendMessage(chat_id=1, text="<b>Привет</b>\n\nмир"))
    assert isinstance(sent, SendRichMessage) and "<p>" in sent.rich_message.html
    assert isinstance(rich._to_rich(EditMessageText(chat_id=1, message_id=2, text="x")),
                      EditMessageText)
    from aiogram.types import ReplyKeyboardMarkup
    assert rich._to_rich(SendMessage(chat_id=1, text="x", reply_markup=ReplyKeyboardMarkup(
        keyboard=[[]]))) is None


@pytest.mark.asyncio
async def test_report_buttons_chart_and_question(db, monkeypatch):  # noqa: F811
    bot, dp, session = build(db)
    await onboard(dp, bot)
    await _press(dp, bot, "rp:chart:today")
    assert "SendPhoto" in session.names()

    seen = {}

    async def fake_chat(purpose, messages, **kw):
        seen["payload"] = messages[-1]["content"]
        return {"reply": "Белок помогает сытости."}, "m"
    monkeypatch.setattr(assistant.llm, "chat_json", fake_chat)
    await _press(dp, bot, "rp:ask:week")
    await send(dp, bot, "почему мало белка?")
    assert '"focus": "week"' in seen["payload"] and "почему мало белка?" in seen["payload"]

    # режим вопроса одноразовый: следующая фраза идёт обычным путём, без привязки к отчёту
    seen.clear()
    await send(dp, bot, "гречка 150 г")
    assert '"focus"' not in seen.get("payload", "")


@pytest.mark.asyncio
async def test_today_embeds_chart_in_rich_message(db):  # noqa: F811
    bot, dp, session = build(db)
    await onboard(dp, bot)
    session.rich.clear()
    await send(dp, bot, "/today")
    assert "tg://photo?id=img1" in session.rich[-1]
    assert "Показатель" in session.rich[-1]                 # график и таблица в одном сообщении


def test_doc_blocks_render_and_degrade_to_plain():
    doc = (rich.Doc().text("<b>Неделя</b>").image(b"png").heading("💡 Советы")
           .bullets(["<b>Вода.</b> пей больше"]).quote("<b>🎯</b> прогулка")
           .details("🔎 Подробности", ["вес 112 кг"]).divider())
    html = doc.rich()
    assert "<h3>💡 Советы</h3>" in html and "<ul><li><b>Вода.</b>" in html
    assert "<blockquote>" in html and "<details><summary>" in html and "<hr/>" in html
    plain = doc.plain()
    assert "• <b>Вода.</b>" in plain and "Подробности" in plain and "png" not in plain
    assert len(doc.images()) == 1


def test_week_details_are_collapsed_and_plurals_ok(db):  # noqa: F811
    from app.services.insights import days_word
    assert [days_word(n) for n in (1, 2, 5, 11, 21)] == ["день", "дня", "дней", "дней", "день"]
