"""Кнопки под отчётами: график картинкой и вопрос по этому отчёту.

«Спросить об этом» включает режим на ОДНО следующее сообщение (и не дольше 10 минут): так
обычная еда, которую пользователь пришлёт потом, не примется за вопрос.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from app import rich
from app.services import assistant, charts, insights, report
from app.storage import repo
from app.timeutils import local_today

router = Router(name="reports")

ASK_TTL_S = 600
QUESTION_HINTS = {
    "today": "«почему мало белка?», «что съесть на ужин?», «как добрать воду?»",
    "week": "«с чего начать на этой неделе?», «почему в выходные больше ем?»",
    "weight": "«почему вес стоит?», «когда будет цель?»",
    "body": "«что значит висцеральный жир?», «как снизить жир и сохранить мышцы?»",
    "sleep": "«как сон влияет на аппетит?», «как лучше засыпать?»",
}


class Ask(StatesGroup):
    question = State()


def _user(db: sqlite3.Connection, tg_id: int):
    user = repo.get_user_by_tg(db, tg_id)
    return user if user and user["onboarded"] else None


def after_answer_kb(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Ещё вопрос", callback_data=f"rp:ask:{kind}")]])


@router.callback_query(F.data.startswith("rp:ask:"))
async def cb_ask(cb: CallbackQuery, state: FSMContext, db: sqlite3.Connection) -> None:
    kind = cb.data.split(":")[2]
    if kind not in report.KIND_TITLES or cb.message is None:
        await cb.answer()
        return
    if not _user(db, cb.from_user.id):
        await cb.answer("Сначала настрой профиль: /start", show_alert=True)
        return
    await state.set_state(Ask.question)
    await state.update_data(kind=kind, until=time.time() + ASK_TTL_S)
    await cb.answer()
    await cb.message.answer(f"💬 Спроси про {report.KIND_TITLES[kind]}: например "
                            f"{QUESTION_HINTS.get(kind, '')}.\nНапиши вопрос одним сообщением.")


@router.message(Ask.question, F.text & ~F.text.startswith("/"))
async def on_question(msg: Message, state: FSMContext, db: sqlite3.Connection) -> None:
    data = await state.get_data()
    await state.clear()
    if data.get("until", 0) < time.time():
        # режим вопроса истёк: текст не теряем, пусть дальше его разберёт обычный путь
        from aiogram.dispatcher.event.bases import SkipHandler
        raise SkipHandler
    user = _user(db, msg.from_user.id)
    if not user:
        return
    kind = data.get("kind", "today")
    status = await msg.answer("🤔 Думаю…")
    reply = await assistant.answer(db, user, msg.text.strip(), focus=kind)
    await rich.edit(status, reply, reply_markup=after_answer_kb(kind))


@router.callback_query(F.data.startswith("rp:chart:"))
async def cb_chart(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    kind = cb.data.split(":")[2]
    user = _user(db, cb.from_user.id)
    if not user or cb.message is None or kind not in report.CHART_KINDS:
        await cb.answer()
        return
    await cb.answer("Рисую график…")
    today = local_today(user["tz"])
    if kind == "today":
        facts = insights.day_facts(db, user, today.isoformat())
        image = await asyncio.to_thread(charts.day_chart, facts)
    elif kind == "week":
        wf = insights.week_facts(db, user, today)
        image = await asyncio.to_thread(charts.week_chart, wf["days"], wf["care"])
    elif kind == "weight":
        image = await report.weight_chart_bytes(db, user, today)
    else:
        image = await report.body_chart_bytes(db, user, today)
    if image is None:
        await cb.message.answer("Для графика пока мало замеров: нужно хотя бы два.")
        return
    await cb.message.answer_photo(BufferedInputFile(image, filename=f"{kind}.png"))
