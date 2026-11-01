"""Сборка отчётов как rich-документов: график, таблицы, выводы и советы, кнопка «спросить»."""
from __future__ import annotations

import sqlite3
import asyncio
import logging
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.rich import Doc
from app.services import charts, insights
from app.services.food import meal_type_ru
from app.storage import repo
from app.textutil import esc

log = logging.getLogger(__name__)

CHART_KINDS = {"today", "week", "weight", "body"}
KIND_TITLES = {"today": "день", "week": "неделю", "weight": "вес", "body": "состав тела",
               "sleep": "сон"}


async def render(fn, *args) -> bytes | None:
    """Нарисовать график в потоке; сбой рисования не должен ломать сам отчёт."""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception:  # noqa: BLE001
        log.warning("график не нарисован", exc_info=True)
        return None


async def day_chart_bytes(db, user, day: str) -> bytes | None:
    return await render(charts.day_chart, insights.day_facts(db, user, day))


async def week_chart_bytes(db, user, today: date) -> bytes | None:
    wf = insights.week_facts(db, user, today)
    return await render(charts.week_chart, wf["days"], wf["care"])


async def weight_chart_bytes(db, user, today: date) -> bytes | None:
    data = charts.weight_data(db, user, today)       # БД читаем здесь, рисуем в потоке
    return await render(charts.weight_chart, data) if data else None


async def body_chart_bytes(db, user, today: date) -> bytes | None:
    data = charts.body_data(db, user, today)
    return await render(charts.body_chart, data) if data else None


def actions_kb(kind: str) -> InlineKeyboardMarkup:
    """Под отчётом: вопрос по нему. Сам график уже встроен в сообщение."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Спросить об этом", callback_data=f"rp:ask:{kind}")]])


def day_doc(db: sqlite3.Connection, user, day: str, coach: dict | None = None,
            closed: bool = False, title: str = "Сегодня", chart: bytes | None = None) -> Doc:
    """День: график-карточка, показатели таблицей, приёмы пищи, что получилось и советы.

    coach: ответ вечернего коуча (LLM): его «одно действие» выносится цитатой в конец.
    closed: день закончен (вечерний итог), тогда недобор по белку и воде тоже разбираем.
    """
    f = insights.day_facts(db, user, day)
    doc = Doc().text(f"📊 <b>{title}</b> · {day[5:]}").image(chart)
    doc.table(["Показатель", "Факт", "Цель", ""], insights.scorecard_rows(f), align="lrrc")
    if f["meals"]:
        meals = repo.day_meals(db, user["id"], day)
        doc.heading("🍽 Что съедено")
        doc.table(["Время", "Приём", "ккал"],
                  [[m["local_time"], meal_type_ru(m["meal_type"]), str(m["kcal_total"])]
                   for m in meals], align="llr", total=["", "Всего", str(f["kcal"])])
        doc.text(f"Б {f['protein']} · Ж {f['fat']} · У {f['carbs']} г")
    good, tips = insights.day_tips(f, closed=closed)
    if good:
        doc.heading("✅ Получилось")
        doc.bullets([esc(g[:1].upper() + g[1:]) for g in good[:4]])
    if tips:
        doc.heading("💡 Что подправить")
        doc.bullets([t.html for t in tips[:3]])
    if coach:
        if coach.get("one_action"):
            doc.quote(f"<b>🎯 На завтра:</b> {esc(coach['one_action'])}")
        if coach.get("ask_user"):
            doc.text(f"<i>❓ {esc(coach['ask_user'])}</i>")
    return doc


def week_doc(db: sqlite3.Connection, user, today: date, detail_lines: list[str],
             chart: bytes | None = None) -> Doc:
    """Неделя: график, таблицы, выводы и советы; технические подробности свёрнуты."""
    wf = insights.week_facts(db, user, today)
    days = wf["days"]
    doc = Doc().text(f"📅 <b>Неделя</b> · {days[0]['date'][5:]} — {days[-1]['date'][5:]}")
    doc.image(chart)
    doc.table(["День", "ккал", "Белок", "Вода", "Шаги", "Сон"],
              insights.week_table_rows(days), align="lrrrrr")
    doc.heading("📈 В среднем за неделю")
    doc.table(["Показатель", "Факт", "Цель", ""],
              insights.week_scorecard(days, wf["care"]), align="lrrc")
    good, tips = insights.week_tips(wf)
    if good:
        doc.heading("✅ Что хорошо")
        doc.bullets([esc(g[:1].upper() + g[1:]) for g in good[:4]])
    if tips:
        doc.heading("💡 Наблюдения и советы")
        doc.bullets([t.html for t in tips[:4]])
    # детали прежнего отчёта (вес, безопасность, пересчёт цели, срывы) свёрнуты в один блок
    extra = [ln for ln in detail_lines[1:] if not ln.startswith(("Еда:", "Шаги:", "Вода:", "Сон:"))]
    doc.details("🔎 Подробности", extra)
    return doc
