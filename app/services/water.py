"""Запись воды и ответ пользователю — общий код для текста, голоса и /water."""
from __future__ import annotations

import sqlite3

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.water import water_goal_ml
from app.services import food as foodsvc
from app.storage import repo


def goal_for(db: sqlite3.Connection, user) -> int:
    return water_goal_ml(repo.latest_weight_kg(db, user["id"]))


def log_water(db: sqlite3.Connection, user, ml: int, source: str,
              raw_text: str | None = None) -> tuple[int, str]:
    """Записать воду. Возвращает (id записи, текст ответа)."""
    now = foodsvc.local_now(user["tz"])
    local_date = now.strftime("%Y-%m-%d")
    water_id = repo.add_water(db, user["id"], local_date, now.strftime("%H:%M"),
                              ml, source, raw_text)
    return water_id, reply_text(db, user, ml, local_date)


def reply_text(db: sqlite3.Connection, user, added_ml: int, local_date: str) -> str:
    total = repo.day_water_ml(db, user["id"], local_date)
    goal = goal_for(db, user)
    left = max(0, goal - total)
    tail = "цель на день выполнена 🎉" if not left else f"до ориентира {left} мл"
    return f"💧 +{added_ml} мл · сегодня {total} / {goal} мл · {tail}"


def undo_keyboard(water_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Отменить", callback_data=f"water_undo:{water_id}"),
    ]])
