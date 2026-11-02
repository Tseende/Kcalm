"""Ночная поддержка: /craving, автоопределение фраз о тяге, кнопки разбора и итога."""
from __future__ import annotations

import json
import logging
import sqlite3

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from app.domain import craving as dom
from app.domain import intents
from app.domain import water
from app.services import craving as svc
from app.services import plan as plansvc
from app.services import food as foodsvc
from app.storage import repo

router = Router(name="craving")
log = logging.getLogger(__name__)


def _user(db: sqlite3.Connection, tg_id: int):
    user = repo.get_user_by_tg(db, tg_id)
    return user if user and user["onboarded"] else None


async def _send(msg: Message, text: str, kb: InlineKeyboardMarkup | None,
                status: Message | None = None) -> None:
    if status is not None:
        await status.edit_text(text[:4000], reply_markup=kb)
    else:
        await msg.answer(text[:4000], reply_markup=kb)


async def _typing(msg: Message) -> None:
    try:
        await msg.bot.send_chat_action(msg.chat.id, "typing")
    except Exception:  # noqa: BLE001 — индикатор набора не критичен
        pass


def _after_reply_kb(db: sqlite3.Connection, user, event_id: int) -> None:
    """Разговор без кнопок: бот сам понимает по словам, чем всё закончилось."""
    return None


async def maybe_handle(msg: Message, db: sqlite3.Connection, user, text: str,
                       status: Message | None = None) -> bool:
    """Если сообщение относится к тяге к еде, обработать и вернуть True.
    Вызывается для текста и для расшифрованных голосовых."""
    uid = user["id"]
    if await _handle_lesson_text(msg, db, user, text):
        return True
    now_local = foodsvc.local_now(user["tz"])
    local_time = now_local.strftime("%H:%M")

    event = repo.open_craving(db, uid, dom.EPISODE_WINDOW_MIN)
    if event:
        # явная запись еды и воды внутри эпизода идёт обычным путём (разговор о ней узнает),
        # а еду, упомянутую между делом, модель вытащит из реплики сама
        if dom.looks_like_food_log(text) or water.extract_water(text)[0]:
            return False
        if intents.detect(text) is not None:
            return False                     # «что по статистике», «отмени последнее» и т. п.
        await _typing(msg)
        reply, _ = await svc.dialog_reply(db, user, event, text)
        await _send(msg, reply, _after_reply_kb(db, user, event["id"]), status)
        return True

    if dom.detect_craving(text, local_time, user["eating_cutoff"]):
        await _typing(msg)
        _, reply, kb = await svc.open_episode(db, user, text)
        await _send(msg, reply, kb, status)
        return True
    return False


@router.message(Command("craving"))
async def cmd_craving(msg: Message, command: CommandObject, db: sqlite3.Connection) -> None:
    user = _user(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    open_event = repo.open_craving(db, user["id"], dom.EPISODE_WINDOW_MIN)
    text = (command.args or "").strip()
    await _typing(msg)
    if open_event and text:
        reply, _ = await svc.dialog_reply(db, user, open_event, text)
        await msg.answer(reply, reply_markup=_after_reply_kb(db, user, open_event["id"]))
        return
    if open_event:
        await msg.answer("Я здесь. Напиши, что происходит.")
        return
    _, reply, kb = await svc.open_episode(db, user, text or None)
    await msg.answer(reply[:4000], reply_markup=kb)


@router.message(Command("talk"))
async def cmd_talk(msg: Message, command: CommandObject, db: sqlite3.Connection) -> None:
    """Просто поговорить — о еде, срывах, настроении — в любое время, не только ночью."""
    user = _user(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    text = (command.args or "").strip()
    open_event = repo.open_craving(db, user["id"], dom.EPISODE_WINDOW_MIN)
    await _typing(msg)
    if open_event:
        if text:
            reply, _ = await svc.dialog_reply(db, user, open_event, text)
            await msg.answer(reply, reply_markup=_after_reply_kb(db, user, open_event["id"]))
        else:
            await msg.answer("Я здесь, продолжаем 🙂 Пиши.")
        return
    _, reply, _ = await svc.open_episode(db, user, text or None, mode="talk")
    await msg.answer(reply[:4000])


@router.message(F.text & ~F.text.startswith("/"))
async def on_craving_text(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(db, msg.from_user.id)
    if user is None:
        raise SkipHandler
    if not await maybe_handle(msg, db, user, (msg.text or "").strip()):
        raise SkipHandler


def _event_for(cb: CallbackQuery, db: sqlite3.Connection):
    user = _user(db, cb.from_user.id)
    if not user:
        return None, None
    event_id = int(cb.data.split(":")[2])
    return user, repo.get_craving(db, user["id"], event_id)


@router.callback_query(F.data.startswith("cr:cat:"))
async def cb_category(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user, event = _event_for(cb, db)
    category = cb.data.split(":")[3]
    if not event or event["outcome"] != "open" or category not in dom.CATEGORIES:
        await cb.answer("Этот эпизод уже закрыт", show_alert=True)
        return
    await cb.answer()
    await _typing(cb.message)
    text = await svc.category_reply(db, user, event, category)
    await cb.message.answer(text[:4000], reply_markup=svc.outcome_keyboard(user, event["id"]))


@router.callback_query(F.data.startswith("cr:wait:"))
async def cb_wait(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user, event = _event_for(cb, db)
    if not event or event["outcome"] != "open":
        await cb.answer("Этот эпизод уже закрыт", show_alert=True)
        return
    svc.schedule_followup(db, event["id"])
    await cb.message.answer(
        f"⏳ Договорились: {svc.FOLLOWUP_MIN} минут. Пока: стакан воды, выйди из кухни, "
        "займи руки. Я напишу через это время, а если захочешь поговорить, пиши сразу.")
    await cb.answer()


@router.callback_query(F.data.startswith("cr:out:"))
async def cb_outcome(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user, event = _event_for(cb, db)
    outcome = cb.data.split(":")[3]
    if not event or event["outcome"] != "open" or outcome not in (
            "resisted", "ate_planned", "overate"):
        await cb.answer("Этот эпизод уже закрыт", show_alert=True)
        return
    text = svc.finish_episode(db, user, event, outcome)
    await cb.message.answer(text[:4000], reply_markup=svc.debrief_keyboard(event["id"]))
    await cb.answer()


def _any_event(cb: CallbackQuery, db: sqlite3.Connection):
    """Событие своего пользователя независимо от того, закрыт ли эпизод (разбор идёт после)."""
    user = _user(db, cb.from_user.id)
    if not user:
        return None, None
    return user, repo.get_craving(db, user["id"], int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("cr:deb:"))
async def cb_debrief(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user, event = _any_event(cb, db)
    if not event:
        await cb.answer("Не нашёл эпизод", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    if event["category"] == "unknown":
        await cb.message.answer("Давай коротко. Что это было скорее всего?",
                                reply_markup=svc.category_keyboard(event["id"]))
    else:
        await cb.message.answer(
            "Давай коротко, без вины. Что поможет тебе в следующий раз в такой ситуации?",
            reply_markup=plansvc.lesson_keyboard(event["id"], event["category"]))
    await cb.answer()


@router.callback_query(F.data.startswith("cr:dcat:"))
async def cb_debrief_category(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user, event = _any_event(cb, db)
    category = cb.data.split(":")[3]
    if not event or category not in dom.CATEGORIES:
        await cb.answer("Не нашёл эпизод", show_alert=True)
        return
    repo.update_craving(db, event["id"], category=category)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Что поможет тебе в следующий раз в такой ситуации?",
                            reply_markup=plansvc.lesson_keyboard(event["id"], category))
    await cb.answer()


@router.callback_query(F.data.startswith("cr:less:"))
async def cb_lesson(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user, event = _any_event(cb, db)
    choice = cb.data.split(":")[3]
    if not event:
        await cb.answer("Не нашёл эпизод", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    if choice == "own":
        repo.save_pending_input(db, user["id"], "craving_lesson", {"event_id": event["id"]})
        await cb.message.answer("Напиши одной фразой, что сделаешь иначе. Например: "
                                "«выпью воды и подожду 15 минут».")
        await cb.answer()
        return
    lesson = plansvc.lesson_by_index(event["category"], int(choice)) if choice.isdigit() else None
    if not lesson:
        await cb.answer("Не нашёл вариант", show_alert=True)
        return
    await _save_lesson(cb.message, db, user, event, lesson)
    await cb.answer()


async def _save_lesson(message: Message, db: sqlite3.Connection, user, event, lesson: str) -> None:
    repo.set_craving_lesson(db, user["id"], event["id"], lesson)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🧭 Сделать правилом «если-то»", callback_data=f"cr:rule:{event['id']}")]])
    await message.answer(plansvc.lesson_saved_text(lesson), reply_markup=kb)


@router.callback_query(F.data.startswith("cr:rule:"))
async def cb_rule_from_lesson(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user, event = _any_event(cb, db)
    if not event:
        await cb.answer("Не нашёл эпизод", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(plansvc.rule_from_lesson(db, user["id"], repo.get_craving(
        db, user["id"], event["id"])))
    await cb.answer()


async def _handle_lesson_text(msg: Message, db: sqlite3.Connection, user, text: str) -> bool:
    """Свободный текст в ответ на «напиши своё» после разбора."""
    pending = repo.latest_pending_input(db, user["id"], "craving_lesson", max_age_min=30)
    if not pending:
        return False
    repo.delete_pending_input(db, pending["id"], user["id"])
    level = dom.risk_level(text)
    if level != "none":
        await msg.answer(dom.SELF_HARM_TEXT if level == "self_harm" else dom.PURGING_TEXT)
        return True
    if dom.violates_policy(text) or not 3 <= len(text) <= 160:
        await msg.answer("Давай выберем такой шаг, который о тебе заботится: вода, чай, сон, "
                         "нормальный приём пищи. Напиши ещё раз, коротко (до 160 символов).")
        repo.save_pending_input(db, user["id"], "craving_lesson",
                                json.loads(pending["payload_json"]))
        return True
    event = repo.get_craving(db, user["id"], json.loads(pending["payload_json"])["event_id"])
    if not event:
        return False
    await _save_lesson(msg, db, user, event, text)
    return True
