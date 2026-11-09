"""Запросы из виджетов (widget_query): проверка, действие, ответ за 15 секунд.

Порядок всегда один: проверка прав → запись в базу → answerWidgetQuery → обновление данных
виджета и сообщения в чат. Любая ошибка превращается в мягкий ответ и строку в журнале.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.services import water as watersvc
from app.storage import repo
from app.timeutils import local_now
from app.widgets import day, pause, runtime
from app.widgets.api import AnswerWidgetQuery, WidgetQuery

log = logging.getLogger(__name__)

FAIL = "Не получилось, попробуйте ещё раз"
UNKNOWN = "Не понял команду"
DISABLED = "Карточки сейчас выключены"
WATER = {"w:+250": 250, "w:+500": 500}
WATER_CODES = set(WATER) | {"w:undo"}
PAUSE_CODES = {"cr:ok", "cr:still", "cr:talk"}


async def _answer(bot: Bot, query_id: str, text: str) -> None:
    try:
        await bot(AnswerWidgetQuery(widget_query_id=query_id, data=text[:300]))
    except TelegramAPIError:
        log.info("widgets: answerWidgetQuery не прошёл", exc_info=True)


async def handle_widget_query(bot: Bot, raw: object) -> None:
    """Точка входа из диспетчера. Никогда не бросает исключений."""
    qid = raw.get("id") if isinstance(raw, dict) else None
    if not isinstance(qid, str) or not qid:
        log.warning("widgets: widget_query без id пропущен")
        return
    answered = False
    try:
        text, follow = await _process(raw)
        await _answer(bot, qid, text)
        answered = True
        if follow is not None:
            await follow
    except Exception:  # noqa: BLE001
        log.exception("widgets: ошибка при обработке запроса виджета")
        if not answered:
            await _answer(bot, qid, FAIL)


async def _process(raw: dict) -> tuple[str, object | None]:
    query = WidgetQuery.parse(raw)
    rt = runtime.active()
    if rt is None:
        return DISABLED, None
    if query is None or query.chat_id is None or query.message_id is None:
        return UNKNOWN, None
    if not rt.cfg.is_allowed(query.user_id):
        log.info("widgets: запрос от %s отклонён whitelist", query.user_id)
        return FAIL, None
    row = repo.widget_by_message(rt.db, query.chat_id, query.message_id)
    user = repo.get_user_by_tg(rt.db, query.user_id)
    if not row or not user or row["user_id"] != user["id"] or query.chat_id != query.user_id:
        log.info("widgets: запрос не из карточки владельца отклонён")
        return FAIL, None
    code = query.data
    if row["kind"] == "day" and code in WATER_CODES:
        return await _water(rt, user, row, code)
    if row["kind"] == "pause" and code in PAUSE_CODES:
        return await pause.handle(rt, user, row, code)
    return UNKNOWN, None


async def _water(rt, user, row, code: str) -> tuple[str, object | None]:
    today = local_now(user["tz"]).strftime("%Y-%m-%d")
    if row["local_date"] != today:
        return "Это карточка за прошлый день", None
    if code == "w:undo":
        last = repo.last_manual_water(rt.db, user["id"], today)
        if not last or not repo.delete_water(rt.db, user["id"], last["id"]):
            text = "Нечего отменять"
        else:
            text = "Отменено"
    else:
        ml = WATER[code]
        watersvc.log_water(rt.db, user, ml, "command", "виджет")
        text = f"+{ml} мл ✓"
    return text, day.refresh(user)
