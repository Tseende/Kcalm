"""Меню с инлайн-кнопками: /menu и постоянная кнопка «📋 Меню» внизу экрана.

Кнопки не дублируют логику: они вызывают те же обработчики, что и команды
(/today, /week, ...), подставляя сообщение с автором-нажавшим. Ответы идут новыми
сообщениями, а само меню остаётся на месте и годится для следующего нажатия.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, KeyboardButton, Message, ReplyKeyboardMarkup,
                           WebAppInfo)

from app.config import get_config
from app.routers import commands, craving, dishes, products, support
from app.services import water as watersvc
from app.storage import repo

router = Router(name="menu")

MENU_BUTTON = "📋 Меню"
WATER_STEPS = (200, 300, 500)
ASSETS = Path(__file__).resolve().parents[2] / "assets"
BANNERS = {"main": "menu.jpg", "water": "water.jpg", "more": "settings.jpg"}
_banner_ids: dict[str, str] = {}       # file_id после первой отправки: дальше без загрузки файла
MAIN_TEXT = ("📋 <b>Меню</b>\n"
             "Еду, воду, скрин с весов и голосовые просто присылай в чат, "
             "кнопки нужны для отчётов и настроек.")
WATER_TEXT = "💧 <b>Вода</b>\nСколько добавить?"
SETTINGS_TEXT = "⚙️ <b>Настройки</b>"


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"m:{action}")


def main_kb() -> InlineKeyboardMarkup:
    rows = [
        [_btn("📊 Сегодня", "today"), _btn("📅 Неделя", "week")],
        [_btn("⚖️ Вес", "wtrend"), _btn("🧬 Состав тела", "body")],
        [_btn("💧 Вода", "water"), _btn("🆘 Тянет переесть", "craving")],
        [_btn("💬 Поговорить", "talk"), _btn("⚙️ Настройки", "more")],
    ]
    url = get_config().webapp_url
    if url:                                   # Mini App включена: кнопка в самом верху
        rows.insert(0, [InlineKeyboardButton(text="📱 Приложение", web_app=WebAppInfo(url=url))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def water_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"+{ml} мл", f"w:{ml}") for ml in WATER_STEPS],
        [_btn("‹ Назад", "main")],
    ])


def more_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🎯 Цель на день", "target"), _btn("⏰ Взвешивание", "weighin")],
        [_btn("🤍 Осторожный режим", "care"), _btn("📤 Выгрузить данные", "export")],
        [_btn("🍽 Посуда", "dishes"), _btn("🥫 Мои продукты", "products")],
        [_btn("🔧 Время и напоминания", "settings")],
        [_btn("‹ Назад", "main")],
    ])


def bottom_kb() -> ReplyKeyboardMarkup:
    """Одна постоянная кнопка под полем ввода: меню всегда под рукой."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MENU_BUTTON)]],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Что съел? Или нажми «Меню»")


SCREENS = {
    "main": (MAIN_TEXT, main_kb),
    "water": (WATER_TEXT, water_kb),
    "more": (SETTINGS_TEXT, more_kb),
}


def _as_command(name: str, args: str | None = None) -> CommandObject:
    return CommandObject(prefix="/", command=name, args=args)


# действие → (обработчик, нужен ли CommandObject, аргумент)
COMMAND_ACTIONS = {
    "today": (commands.cmd_today, None, None),
    "week": (commands.cmd_week, None, None),
    "wtrend": (commands.cmd_weight, None, None),
    "body": (commands.cmd_body, None, None),
    "target": (commands.cmd_target, None, None),
    "settings": (commands.cmd_settings, None, None),
    "export": (commands.cmd_export, None, None),
    "weighin": (commands.cmd_weighin, "weighin", None),
    "care": (support.cmd_care, "care", None),
    "craving": (craving.cmd_craving, "craving", None),
    "talk": (craving.cmd_talk, "talk", None),
    "dishes": (dishes.cmd_dishes, None, None),
    "products": (products.cmd_products, None, None),
}


def _banner(screen: str):
    """Картинка экрана: file_id из кеша или файл из assets/. Нет файла экрана: баннер главного меню."""
    if screen in _banner_ids:
        return _banner_ids[screen]
    path = ASSETS / BANNERS[screen]
    if not path.exists():
        path = ASSETS / BANNERS["main"]
    if not path.exists():
        return None
    return BufferedInputFile(path.read_bytes(), filename=path.name)


def _remember(screen: str, sent: Message) -> None:
    if sent.photo:
        _banner_ids[screen] = sent.photo[-1].file_id


async def send_menu(msg: Message) -> None:
    """Главное меню: баннер с подписью и кнопками; без файла баннера просто текст."""
    photo = _banner("main")
    if photo is not None:
        try:
            _remember("main", await msg.answer_photo(photo, caption=MAIN_TEXT,
                                                     reply_markup=main_kb()))
            return
        except Exception:  # noqa: BLE001
            _banner_ids.pop("main", None)
    await msg.answer(MAIN_TEXT, reply_markup=main_kb())


@router.message(Command("menu"))
async def cmd_menu(msg: Message) -> None:
    # reply-клавиатура и inline-меню — разные типы разметки, одним сообщением их не отправить
    await msg.answer("Кнопка «📋 Меню» теперь всегда под полем ввода.",
                     reply_markup=bottom_kb())
    await send_menu(msg)


@router.message(F.text == MENU_BUTTON)
async def on_menu_button(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await send_menu(msg)


def _proxy(cb: CallbackQuery) -> Message:
    """Сообщение меню, но «от имени» нажавшего: обработчики команд берут автора из from_user."""
    return cb.message.model_copy(update={"from_user": cb.from_user})


@router.callback_query(F.data.startswith("m:"))
async def on_menu_callback(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    if cb.message is None:
        await cb.answer()
        return
    action = (cb.data or "")[2:]

    if action in SCREENS:                       # переход по меню правит то же сообщение
        text, kb = SCREENS[action]
        photo = _banner(action)
        if cb.message.photo and photo is not None:   # у экрана с баннером меняется картинка и подпись
            sent = await cb.message.edit_media(
                InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"), reply_markup=kb())
            if isinstance(sent, Message):
                _remember(action, sent)
        elif cb.message.photo:
            await cb.message.edit_caption(caption=text, reply_markup=kb())
        else:
            await cb.message.edit_text(text, reply_markup=kb())
        await cb.answer()
        return

    if action.startswith("w:"):                 # быстрая запись воды
        user = repo.get_user_by_tg(db, cb.from_user.id)
        if not user or not user["onboarded"]:
            await cb.answer("Сначала настрой профиль: /start", show_alert=True)
            return
        try:
            ml = int(action[2:])
        except ValueError:
            await cb.answer()
            return
        if ml not in WATER_STEPS:
            await cb.answer()
            return
        water_id, text = watersvc.log_water(db, user, ml, "command", f"menu:{ml}")
        await cb.message.answer(text, reply_markup=watersvc.undo_keyboard(water_id))
        await cb.answer(f"+{ml} мл")
        return

    entry = COMMAND_ACTIONS.get(action)
    if entry is None:
        await cb.answer()
        return
    handler, cmd_name, arg = entry
    await cb.answer()
    proxy = _proxy(cb)
    if cmd_name:
        await handler(proxy, _as_command(cmd_name, arg), db)
    else:
        await handler(proxy, db)
