"""Сон, шаги и тренировки текстом: «спал 7 часов», «10 тысяч шагов», «зал 1 час»."""
from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta

from aiogram import Router
from aiogram.types import Message

from app.domain import analytics
from app.domain import tracking as trk
from app.services import tracking as tracksvc
from app.storage import repo
from app.textutil import esc
from app.timeutils import local_today

router = Router(name="tracking")        # колбэков нет: текст разбирает intents.detect
log = logging.getLogger(__name__)


def _steps(n: int) -> str:
    return f"{n:,}".replace(",", " ")


async def log_from_text(msg: Message, db: sqlite3.Connection, text: str) -> bool:
    """Записать всё, что нашлось в фразе: сон, шаги, тренировку. True — что-то записано."""
    user = repo.get_user_by_tg(db, msg.from_user.id)
    if not user or not user["onboarded"]:
        await msg.answer("Сначала настрой профиль: /start")
        return True
    today = local_today(user["tz"])
    lines: list[str] = []

    def day_of(spec: dict):
        return today - timedelta(days=1) if spec.get("yesterday") else today

    sleep = trk.parse_sleep_text(text)
    if sleep:
        try:
            res = tracksvc.log_sleep(db, user, date=day_of(sleep),
                                     duration_min=sleep["duration_min"],
                                     start=sleep["start"], end=sleep["end"])
        except ValueError as exc:
            await msg.answer(esc(str(exc)))
            return True
        line = f"😴 Записал сон: {analytics.format_minutes(res['duration_min'])}"
        if res["start"]:
            line += f" ({res['start']}–{res['end']})"
        avg = tracksvc.sleep_avg_7d(db, user, today)
        if avg:
            line += f". Среднее за 7 дней: {analytics.format_minutes(avg[0])} ({avg[1]} ноч.)"
        lines.append(line)

    steps = trk.parse_steps_text(text)
    if steps:
        try:
            tracksvc.log_activity(db, user, date=day_of(steps), steps=steps["steps"])
        except ValueError as exc:
            await msg.answer(esc(str(exc)))
            return True
        line = f"👟 Записал шаги: {_steps(steps['steps'])}"
        avg = tracksvc.steps_avg_7d(db, user, today)
        if avg:
            line += f". Среднее за 7 дней: {_steps(avg[0])} ({avg[1]} дн.)"
        lines.append(line)

    workout = trk.parse_workout_text(text)
    if workout:
        try:
            res = tracksvc.log_activity(db, user, date=day_of(workout), workout=workout["name"],
                                        active_minutes=None)
        except ValueError as exc:
            await msg.answer(esc(str(exc)))
            return True
        line = f"🏃 Записал тренировку: {esc(workout['name'])}"
        if len(res["workouts"]) > 1:
            line += f". Тренировок за день: {len(res['workouts'])}"
        lines.append(line)

    if not lines:
        return False
    await msg.answer("\n".join(lines))
    return True
