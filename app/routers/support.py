"""Команды психологической поддержки: /plan (правила «если-то») и /care (осторожный режим)."""
from __future__ import annotations

import sqlite3

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.services import plan as plansvc
from app.services import safety as safetysvc
from app.storage import repo

router = Router(name="support")


def _user(db: sqlite3.Connection, tg_id: int):
    user = repo.get_user_by_tg(db, tg_id)
    return user if user and user["onboarded"] else None


@router.message(Command("plan"))
async def cmd_plan(msg: Message, command: CommandObject, db: sqlite3.Connection) -> None:
    user = _user(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    args = (command.args or "").strip()
    if args.lower().startswith(("del ", "удалить ")):
        number = args.split(maxsplit=1)[1].strip()
        if number.isdigit() and plansvc.delete_by_number(db, user["id"], int(number)):
            await msg.answer("Удалил.")
        else:
            await msg.answer("Не нашёл такой номер. Список: /plan")
        return
    if args:
        await msg.answer(plansvc.add_user_rule(db, user["id"], args))
        return
    await msg.answer(plansvc.overview(db, user["id"]) + "\n\nИли возьми готовое:",
                     reply_markup=plansvc.presets_keyboard(db, user["id"]))


@router.callback_query(F.data.startswith("pl:add:"))
async def cb_preset(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _user(db, cb.from_user.id)
    if not user:
        await cb.answer("Нет профиля", show_alert=True)
        return
    index = int(cb.data.split(":")[2])
    await cb.message.answer(plansvc.add_preset(db, user["id"], index))
    await cb.answer()


@router.message(Command("care"))
async def cmd_care(msg: Message, command: CommandObject, db: sqlite3.Connection) -> None:
    user = _user(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    arg = (command.args or "").strip().lower()
    if arg in ("on", "вкл"):
        await msg.answer(safetysvc.set_care(db, user, True))
    elif arg in ("off", "выкл"):
        await msg.answer(safetysvc.set_care(db, user, False))
    else:
        state = "включён" if user["care_mode"] else "выключен"
        await msg.answer(f"Сейчас осторожный режим {state}.\n\n" + safetysvc.CARE_EXPLAIN)
