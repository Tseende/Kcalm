"""Виджет «Пауза» при тяге к еде: по одному на эпизод, после первого ответа бота."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram.exceptions import TelegramAPIError

from app.services import craving as cravingsvc
from app.storage import repo
from app.timeutils import local_now
from app.widgets import chatdata, pages, runtime
from app.widgets.api import EditMessageWidgetData, SendWidget

log = logging.getLogger(__name__)

SEND_DELAY_S = 2.0           # обычный ответ бота должен уйти раньше виджета
_tasks: set[asyncio.Task] = set()

TALK_TEXT = "Я здесь 🤍 Расскажи, что сейчас происходит: с едой, с днём, с чем угодно."
STILL_TEXT = "Пока не отпускает."
CLOSED_ANSWER = "Этот разговор уже закончен. Если снова потянет, напишите мне"


def spawn(coro) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def on_episode_opened(user, event_id: int) -> None:
    """Эпизод тяги открыт и первый ответ получен: отправить виджет (в фоне, один раз на эпизод)."""
    rt = runtime.active()
    if rt is None:
        return
    try:
        spawn(_send_later(rt, user["id"], event_id))
    except RuntimeError:                           # нет цикла событий
        return


async def _send_later(rt, user_id: int, event_id: int) -> None:
    try:
        await asyncio.sleep(SEND_DELAY_S)
        await send(rt, user_id, event_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("widgets: пауза не отправилась", exc_info=True)


async def send(rt, user_id: int, event_id: int) -> str:
    db = rt.db
    user = repo.get_user_by_id(db, user_id)
    if not user or not rt.cfg.is_allowed(user["tg_id"]):
        return "skipped"
    today = local_now(user["tz"]).strftime("%Y-%m-%d")
    if repo.widget_for_ref(db, "pause", event_id) or (user_id, today) in rt.blocked:
        return "skipped"
    event = repo.get_craving(db, user_id, event_id)
    if not event or event["outcome"] != "open":
        return "skipped"                           # человек уже ответил сам
    try:
        msg = await rt.bot(SendWidget(
            chat_id=user["tg_id"], html=pages.load("pause"),
            chat_data=chatdata.pause_data("run", minutes=cravingsvc.FOLLOWUP_MIN),
            caption="Пауза", allow_queries=True, disable_notification=True))
    except TelegramAPIError as e:
        log.info("widgets: sendWidget (пауза) отклонён (%s), сегодня не повторяю", e)
        rt.blocked.add((user_id, today))
        return "skipped"
    repo.add_widget_message(db, user_id, "pause", today, msg.chat.id, msg.message_id, event_id)
    return "sent"


def _since(row) -> int:
    try:
        dt = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return int(datetime.now(timezone.utc).timestamp())


async def _set_state(rt, row, state: str) -> None:
    try:
        await rt.bot(EditMessageWidgetData(
            chat_id=row["chat_id"], message_id=row["message_id"],
            chat_data=chatdata.pause_data(state, _since(row), cravingsvc.FOLLOWUP_MIN)))
    except TelegramAPIError:
        log.info("widgets: не удалось обновить паузу", exc_info=True)


async def _say_still(rt, user, event) -> None:
    """Мягкий отклик в чате на «всё ещё хочется»: ответ модели или запасной текст."""
    try:
        reply, _ = await cravingsvc.dialog_reply(rt.db, user, event, STILL_TEXT)
        await rt.bot.send_message(user["tg_id"], reply[:4000])
    except Exception:  # noqa: BLE001
        log.warning("widgets: отклик на «всё ещё хочется» не отправился", exc_info=True)


async def _say_talk(rt, user, event) -> None:
    try:
        repo.add_craving_message(rt.db, event["id"], "assistant", TALK_TEXT)
        await rt.bot.send_message(user["tg_id"], TALK_TEXT)
    except Exception:  # noqa: BLE001
        log.warning("widgets: приглашение к разговору не отправилось", exc_info=True)


async def handle(rt, user, row, code: str) -> tuple[str, "object | None"]:
    """Запрос из виджета паузы → (текст ответа, корутина для фона или None).

    Сначала меняется база, ответ виджету уходит из вызывающего кода, обновление данных и
    сообщения в чат делает вызывающий код уже после ответа."""
    event = repo.get_craving(rt.db, user["id"], row["ref_id"]) if row["ref_id"] else None
    if event is None or event["outcome"] != "open":
        return CLOSED_ANSWER, None
    if code == "cr:ok":
        repo.close_craving(rt.db, event["id"], "resisted")
        return "Здорово, что дали себе паузу", _after(rt, row, "ok", None)
    if code == "cr:still":
        return "Понимаю. Я рядом, сейчас напишу", _after(rt, row, "still", _say_still(rt, user, event))
    if code == "cr:talk":
        return "Напишите мне — я здесь", _after(rt, row, None, _say_talk(rt, user, event))
    return "Не получилось, попробуйте ещё раз", None


async def _after(rt, row, state: str | None, follow) -> None:
    if state:
        await _set_state(rt, row, state)
    if follow is not None:
        await follow
