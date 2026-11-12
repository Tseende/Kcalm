"""Карточка дня: одна на человека на локальную дату.

Бот первым не пишет: карточка уходит после первой за день записи еды или воды, дальше только
обновляются её данные (editMessageWidgetData). Все записи в дневник проходят через
repo.day_changed, оттуда сюда приходит touch(); обновления склеиваются с задержкой в пару секунд.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNotFound

from app.storage import repo
from app.timeutils import local_now
from app.widgets import chatdata, pages, runtime
from app.widgets.api import EditMessageWidgetData, SendWidget

log = logging.getLogger(__name__)

DEBOUNCE_S = 2.0

_timers: dict[int, asyncio.TimerHandle] = {}
_running: dict[int, asyncio.Task] = {}
_again: set[int] = set()
_create: set[int] = set()          # кому из ждущих можно отправить карточку, а не только обновить


def install() -> None:
    repo.set_day_listener(touch)


def uninstall() -> None:
    repo.set_day_listener(None)
    for handle in _timers.values():
        handle.cancel()
    _timers.clear()
    _again.clear()
    _create.clear()
    for task in _running.values():
        task.cancel()
    _running.clear()


def touch(user_id: int, create: bool = True) -> None:
    """День пользователя изменился. Дёшево и безопасно из любого места, в том числе из потока.

    create=False: карточку только обновить, если она уже есть (изменение не от самого человека)."""
    rt = runtime.active()
    if rt is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is rt.loop:
        _arm(user_id, create)
    else:
        try:
            rt.loop.call_soon_threadsafe(_arm, user_id, create)
        except RuntimeError:                       # цикл закрыт
            pass


def cancel_pending(user_id: int) -> None:
    """Отложенное обновление не нужно: данные уже обновлены напрямую (ответ на запрос виджета)."""
    handle = _timers.pop(user_id, None)
    if handle:
        handle.cancel()
    _again.discard(user_id)
    _create.discard(user_id)


def _arm(user_id: int, create: bool) -> None:
    rt = runtime.active()
    if rt is None:
        return
    if create:
        _create.add(user_id)
    if user_id in _running:
        _again.add(user_id)                        # идёт отправка: повторим сразу после неё
        return
    handle = _timers.pop(user_id, None)
    if handle:
        handle.cancel()
    _timers[user_id] = rt.loop.call_later(DEBOUNCE_S, _start, user_id)


def _start(user_id: int) -> None:
    _timers.pop(user_id, None)
    rt = runtime.active()
    if rt is None:
        return
    _running[user_id] = rt.loop.create_task(_run(user_id))


async def _run(user_id: int) -> None:
    try:
        create = user_id in _create
        _create.discard(user_id)
        await flush(user_id, create=create)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — карточка не должна ронять бота
        log.warning("widgets: карточка дня не обновилась", exc_info=True)
    finally:
        _running.pop(user_id, None)
        if user_id in _again:
            _again.discard(user_id)
            _arm(user_id, user_id in _create)


async def wait_idle() -> None:
    """Дождаться всех отложенных и идущих обновлений (для тестов)."""
    for _ in range(50):
        if not _timers and not _running:
            return
        await asyncio.sleep(DEBOUNCE_S / 10 if DEBOUNCE_S else 0.01)


def _has_records(db, user_id: int, local_date: str) -> bool:
    return bool(repo.day_meals(db, user_id, local_date)) or repo.day_water_ml(db, user_id, local_date) > 0


async def flush(user_id: int, create: bool = True) -> str:
    """Привести карточку сегодняшнего дня в актуальное состояние. Возвращает, что сделано:
    'edited', 'sent' или 'skipped'."""
    rt = runtime.active()
    if rt is None:
        return "skipped"
    db = rt.db
    user = repo.get_user_by_id(db, user_id)
    if not user or not user["onboarded"] or not rt.cfg.is_allowed(user["tg_id"]):
        return "skipped"
    now = local_now(user["tz"])
    today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    row = repo.widget_for_day(db, user_id, "day", today)
    if row:
        return await _edit(rt, user, row, today, hhmm)
    if (not create or (user_id, today) in rt.blocked or (user_id, today) in rt.deleted
            or not _has_records(db, user_id, today)):
        return "skipped"
    return await _send(rt, user, today, hhmm)


async def refresh(user, local_date: str | None = None) -> bool:
    """Сразу обновить данные карточки (после действия из самого виджета). True, если обновлена."""
    rt = runtime.active()
    if rt is None:
        return False
    now = local_now(user["tz"])
    today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    row = repo.widget_for_day(rt.db, user["id"], "day", local_date or today)
    if not row:
        return False
    cancel_pending(user["id"])
    return await _edit(rt, user, row, local_date or today, hhmm) == "edited"


async def _edit(rt, user, row, today: str, hhmm: str) -> str:
    data = chatdata.day_data(rt.db, user, row["local_date"], hhmm)
    try:
        await rt.bot(EditMessageWidgetData(chat_id=row["chat_id"], message_id=row["message_id"],
                                           chat_data=data))
        return "edited"
    except (TelegramBadRequest, TelegramNotFound) as e:
        text = str(e).lower()
        if "not modified" in text:
            return "edited"
        log.info("widgets: карточка дня недоступна (%s), больше не трогаю её сегодня", e)
        repo.delete_widget_message(rt.db, row["id"])
        rt.deleted.add((user["id"], row["local_date"]))      # удалили карточку: не навязываем новую
        return "skipped"
    except TelegramAPIError:
        log.info("widgets: editMessageWidgetData отклонён", exc_info=True)
        return "skipped"


async def _send(rt, user, today: str, hhmm: str) -> str:
    username = await runtime.bot_username(rt)
    link = pages.app_link(username, rt.cfg.webapp_url)
    if not link:
        return "skipped"                           # без username ссылка вышла бы чужой: попробуем позже
    try:
        html = pages.load("day", link)
        data = chatdata.day_data(rt.db, user, today, hhmm)
        msg = await rt.bot(SendWidget(
            chat_id=user["tg_id"], html=html, chat_data=data,
            allow_queries=True, disable_notification=True))
    except TelegramAPIError as e:
        log.info("widgets: sendWidget отклонён (%s), сегодня карточку не повторяю", e)
        rt.blocked.add((user["id"], today))
        return "skipped"
    except ValueError:
        log.warning("widgets: html карточки не проходит лимиты", exc_info=True)
        rt.blocked.add((user["id"], today))
        return "skipped"
    repo.add_widget_message(rt.db, user["id"], "day", today, msg.chat.id, msg.message_id)
    return "sent"
