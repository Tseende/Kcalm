"""Посуда: /dishes, добавление фразой, выбор умолчания и удаление кнопками."""
from __future__ import annotations

import logging
import sqlite3

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.domain import dishes as dishdom
from app.storage import repo
from app.textutil import esc

router = Router(name="dishes")
log = logging.getLogger(__name__)

MAX_DISHES = 20


def _short(name: str, n: int = 14) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


def list_text(rows) -> str:
    if not rows:
        return "🍽 Посуды пока нет.\n\n" + esc(dishdom.HELP_TEXT)
    lines = ["🍽 <b>Твоя посуда</b> (⭐ — по умолчанию, её бот берёт для новых фото):"]
    for r in rows:
        lines.append(f"{'⭐ ' if r['is_default'] else '• '}{esc(dishdom.describe_dish(r))}")
    lines.append("")
    lines.append("Добавить: «добавь посуду: глубокая тарелка 20 см 500 мл».")
    return "\n".join(lines)


def list_kb(rows) -> InlineKeyboardMarkup | None:
    buttons = []
    for r in rows:
        star = ("✅ по умолчанию" if r["is_default"] else f"⭐ {_short(r['name'])}")
        buttons.append([InlineKeyboardButton(text=star, callback_data=f"dish_def:{r['id']}"),
                        InlineKeyboardButton(text=f"🗑 {_short(r['name'])}",
                                             callback_data=f"dish_del:{r['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


async def cmd_dishes(msg: Message, db: sqlite3.Connection) -> None:
    user = repo.get_user_by_tg(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    rows = repo.list_dishes(db, user["id"])
    await msg.answer(list_text(rows), reply_markup=list_kb(rows))


@router.message(Command("dishes"))
async def on_dishes_command(msg: Message, db: sqlite3.Connection) -> None:
    await cmd_dishes(msg, db)


async def add_from_text(msg: Message, db: sqlite3.Connection, text: str) -> None:
    """«добавь посуду: ...» — разобрать фразу и сохранить."""
    user = repo.get_user_by_tg(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    spec = dishdom.parse_dish_text(text)
    if spec is None:
        await msg.answer(esc("Не понял, что за посуда. " + dishdom.HELP_TEXT))
        return
    if len(repo.list_dishes(db, user["id"])) >= MAX_DISHES:
        await msg.answer(f"Посуды уже {MAX_DISHES}. Удали ненужную в /dishes.")
        return
    dish_id = repo.create_dish(db, user["id"], **spec)
    dish = repo.get_dish(db, user["id"], dish_id)
    kb = None
    tail = "Теперь её можно выбрать кнопкой «🍽 Посуда» под карточкой приёма."
    if dish["is_default"]:
        tail = "Она по умолчанию: буду учитывать её на новых фото."
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="⭐ Сделать по умолчанию", callback_data=f"dish_def:{dish_id}")]])
    await msg.answer(f"🍽 Добавил: {esc(dishdom.describe_dish(dish))}.\n{tail}", reply_markup=kb)


async def _redraw(cb: CallbackQuery, db: sqlite3.Connection, user_id: int) -> None:
    rows = repo.list_dishes(db, user_id)
    try:
        await cb.message.edit_text(list_text(rows), reply_markup=list_kb(rows))
    except Exception:                                        # noqa: BLE001
        log.debug("список посуды не перерисован", exc_info=True)


@router.callback_query(F.data.startswith("dish_def:"))
async def cb_dish_default(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = repo.get_user_by_tg(db, cb.from_user.id)
    dish = repo.get_dish(db, user["id"], int(cb.data.split(":")[1])) if user else None
    if not dish:
        await cb.answer("Такой посуды нет", show_alert=True)
        return
    now_default = not dish["is_default"]            # повторное нажатие снимает умолчание
    repo.update_dish(db, user["id"], dish["id"], is_default=now_default)
    await _redraw(cb, db, user["id"])
    await cb.answer("По умолчанию" if now_default else "Умолчание снято")


@router.callback_query(F.data.startswith("dish_del:"))
async def cb_dish_delete(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = repo.get_user_by_tg(db, cb.from_user.id)
    if not user or not repo.delete_dish(db, user["id"], int(cb.data.split(":")[1])):
        await cb.answer("Уже удалено", show_alert=True)
        return
    await _redraw(cb, db, user["id"])
    await cb.answer("Удалил")


@router.callback_query(F.data == "dish_help")
async def cb_dish_help(cb: CallbackQuery) -> None:
    await cb.message.answer(esc(dishdom.HELP_TEXT))
    await cb.answer()
