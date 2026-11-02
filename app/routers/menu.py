"""Вход в Mini App. Чат с ботом остаётся только для переписки: еда, скрины, разговор.

Отчёты, графики и настройки живут в приложении, поэтому кнопка одна, а старые кнопки меню
(«📋 Меню» под полем ввода и инлайн-меню из прежних сообщений) просто ведут туда же.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
                           ReplyKeyboardRemove, WebAppInfo)

from app.config import get_config

router = Router(name="menu")

MENU_BUTTON = "📋 Меню"            # кнопка из прежней версии: у старых чатов она ещё висит
APP_TEXT = ("📱 Отчёты, графики и настройки теперь в приложении.\n"
            "А здесь просто пиши: еду, фото, скрины, или поговорим, если тянет переесть.")
NO_APP_TEXT = "Приложение пока не подключено."


def main_kb(screen: str | None = None,
            text: str = "📱 Открыть приложение") -> InlineKeyboardMarkup | None:
    """Одна кнопка «Приложение». Без WEBAPP_URL кнопки нет. `screen` открывает сразу нужный
    экран (?screen=analysis): хеш адреса Telegram занимает своими данными запуска."""
    url = get_config().webapp_url
    if not url:
        return None
    if screen:
        base, sep, frag = url.partition("#")
        url = base + ("&" if "?" in base else "?") + "screen=" + screen + sep + frag
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]])


def bottom_kb() -> ReplyKeyboardRemove:
    """Под полем ввода больше нет кнопок: убираем и старую «📋 Меню»."""
    return ReplyKeyboardRemove()


async def send_app(msg: Message, text: str = APP_TEXT) -> None:
    kb = main_kb()
    await msg.answer(text if kb else NO_APP_TEXT, reply_markup=kb)


@router.message(Command("menu", "app"))
async def cmd_menu(msg: Message) -> None:
    await send_app(msg)


@router.message(F.text == MENU_BUTTON)
async def on_menu_button(msg: Message, state: FSMContext) -> None:
    await state.clear()
    # reply-клавиатуру снимаем отдельным сообщением: вместе с inline её не отправить
    await msg.answer("Кнопку убрал, всё в приложении.", reply_markup=bottom_kb())
    await send_app(msg)


@router.callback_query(F.data.startswith("m:"))
async def on_menu_callback(cb: CallbackQuery) -> None:
    """Кнопки старых меню: действий больше нет, показываем, где теперь всё."""
    await cb.answer("Это теперь в приложении")
    if cb.message is not None:
        await send_app(cb.message)
