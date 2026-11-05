"""Агентный слой: фразы своими словами → команды и ответы на вопросы.

Вызывается до распознавания еды. Порядок: явные команды (без LLM) → вопросы ассистенту.
Всё остальное остаётся едой, как и раньше.
"""
from __future__ import annotations

import logging
import sqlite3

from aiogram.filters.command import CommandObject
from aiogram.types import Message

from app.domain import intents
from app.routers import commands, dishes, menu, products, support, tracking
from app.services import assistant
from app.services import food as foodsvc
from app.storage import repo
from app.textutil import esc

log = logging.getLogger(__name__)


APP_INTENTS = {"today", "week", "weight", "sleep", "target", "body", "foods", "dishes", "products",
               "water_status", "plan_list"}


def _args(command: str, arg: str = "") -> CommandObject:
    return CommandObject(prefix="/", command=command, args=arg or None)


async def run_intent(msg: Message, db: sqlite3.Connection, intent: intents.Intent) -> None:
    name, arg = intent.name, intent.arg
    if name in APP_INTENTS:                      # отчёты и списки читаются в Mini App
        await menu.send_app(msg)
    elif name == "undo":
        await commands.cmd_undo(msg, db)
    elif name == "dish_add":
        await dishes.add_from_text(msg, db, arg)
    elif name == "product_add":
        await products.add_from_text(msg, db, arg)
    elif name in ("sleep_log", "steps_log", "workout_log"):
        await tracking.log_from_text(msg, db, arg)
    elif name == "weighin":
        await commands.cmd_weighin(msg, _args("weighin", arg), db)
    elif name == "plan_add":
        await support.cmd_plan(msg, _args("plan", arg), db)
    elif name == "care_on":
        await support.cmd_care(msg, _args("care", "on"), db)
    elif name == "care_off":
        await support.cmd_care(msg, _args("care", "off"), db)
    elif name == "food_info":
        await msg.answer(assistant.food_info(db, arg))
    elif name == "help":
        await msg.answer(esc(assistant.help_text()))
    elif name == "greeting":
        await msg.answer(esc(assistant.greeting(repo.get_user_by_tg(db, msg.from_user.id))))
    elif name == "thanks":
        await msg.answer(assistant.THANKS)


# Намерения, которые пишут в дневник. Из свободного текста и голоса их не выполняем: чат с ботом
# нужен для разговора, а еда, вода, сон и шаги попадают в базу через приложение и скриншоты.
LOGGING_INTENTS = {"sleep_log", "steps_log", "workout_log"}


async def discuss(msg: Message, db: sqlite3.Connection, user, text: str,
                  status: Message | None = None) -> None:
    """Свободная реплика в чате: ничего не записывается, ассистент продолжает разговор.

    Явные команды (отчёты, «отмени последнее», правила, напоминания) работают как раньше;
    «съел тост», «выпил воды», «спал 7 часов» — обычная реплика, а не запись."""
    intent = intents.detect(text)
    if intent is not None and intent.name not in LOGGING_INTENTS:
        if status is not None:
            try:
                await status.delete()
            except Exception:  # noqa: BLE001 — статус не критичен
                pass
        await run_intent(msg, db, intent)
        return
    if status is None:
        status = await msg.answer("💭 думаю…")
    reply = await assistant.answer(db, user, text)
    await status.edit_text(reply[:4000])


async def maybe_handle(msg: Message, db: sqlite3.Connection, user, text: str,
                       status: Message | None = None) -> bool:
    """True, если сообщение не еда: команда словами или вопрос."""
    intent = intents.detect(text)
    if intent is not None:
        if status is not None:
            try:
                await status.delete()
            except Exception:  # noqa: BLE001 — статус «распознаю…» не критичен
                pass
        await run_intent(msg, db, intent)
        return True
    if intents.is_question(text):
        if status is None:
            status = await msg.answer("💭 думаю…")
        reply = await assistant.answer(db, user, text)
        await status.edit_text(reply[:4000])
        return True
    return False


async def route_llm_intent(msg: Message, db: sqlite3.Connection, user, text: str,
                           u, status: Message | None = None) -> bool:
    """Намерение, которое определила модель при разборе. True — сообщение обработано.

    Если в сообщении названа еда, это запись, кроме явной отмены. Сомнительные аргументы
    (кривое правило, непонятное время) не выполняются, а уходят ассистенту."""
    name = u.intent
    if name == "food" or (u.items and name != "undo"):
        return False
    if name == "craving":
        from app.routers import craving as craving_router
        from app.services import craving as craving_svc
        _, reply, kb = await craving_svc.open_episode(db, user, text)
        await craving_router._send(msg, reply, kb, status)
        return True
    intent = intents.from_llm(name, u.intent_arg)
    if intent is None:
        return False                    # question, chat и негодные аргументы: ответит ассистент
    if status is not None:
        try:
            await status.delete()
        except Exception:  # noqa: BLE001 — статус «распознаю…» не критичен
            pass
    await run_intent(msg, db, intent)
    return True
