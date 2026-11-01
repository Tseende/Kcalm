"""Intake: фото / голос / текст / скриншоты → конвейер еды или метрик."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.domain import analytics, intents, mealtime, matching, water
from app.domain import dishes as dishdom
from app.domain import products as proddom
from app import rich
from app.storage import photos
from app.routers import agent
from app.routers import products as products_router
from app.services import assistant
from app.routers import craving as craving_router
from app.adapters import off
from app.services import barcode
from app.services import food as foodsvc
from app.services import meals as meals_svc
from app.services import water as watersvc
from app.services import screens
from app.storage import repo
from app.textutil import esc

router = Router(name="intake")
log = logging.getLogger(__name__)


def _meal_kb(meal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Верно", callback_data=f"meal_ok:{meal_id}"),
        InlineKeyboardButton(text="✏️ Изменить", callback_data=f"meal_edit:{meal_id}"),
        InlineKeyboardButton(text="↩️ Отменить", callback_data=f"meal_undo:{meal_id}"),
    ], [
        InlineKeyboardButton(text="💬 Уточнить", callback_data=f"meal_refine:{meal_id}"),
        InlineKeyboardButton(text="🕐 Другое время", callback_data=f"meal_time:{meal_id}"),
    ], [
        InlineKeyboardButton(text="🍽 Посуда", callback_data=f"meal_dishes:{meal_id}"),
        InlineKeyboardButton(text="➕ Фото к приёму", callback_data=f"meal_addphoto:{meal_id}"),
    ]])


# блюда, где на фото не видно масла и соусов: про них бот сам напоминает рассказать, как готовили
_HIDDEN_FAT_WORDS = ("салат", "жарен", "котлет", "плов", "паста", "макарон", "картоф", "картош",
                     "омлет", "яичниц", "суп", "борщ", "запеканк", "сырник", "блин", "оладь",
                     "шашлык", "рагу", "тушен", "пицц", "бургер", "шаурм", "ролл", "bowl",
                     "бутерброд", "заправк", "соус", "fried")


def _may_hide_fat(u) -> bool:
    for it in u.items:
        name = f"{it.name_ru} {it.name}".lower()
        if it.preparation in ("fried", "baked") or any(w in name for w in _HIDDEN_FAT_WORDS):
            return True
    return False


def _screenshot_confirm_kb(pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Записать", callback_data=f"screen_yes:{pending_id}"),
        InlineKeyboardButton(text="✖️ Не записывать", callback_data=f"screen_no:{pending_id}"),
    ]])


def _target_row(db: sqlite3.Connection, uid: int):
    return repo.current_target(db, uid)


def _user_or_ask(msg: Message, db: sqlite3.Connection):
    user = repo.get_user_by_tg(db, msg.from_user.id)
    if not user or not user["onboarded"]:
        return None
    return user


# ------------------------------------------------------------------ текст

# команды, которые нельзя отдавать ни уточнению, ни подписи к фото: «спал 7 часов» —
# это запись сна, а не правка еды
EARLY_INTENTS = ("dish_add", "dishes", "product_add", "products",
                 "sleep_log", "steps_log", "workout_log")

@router.message(F.text & ~F.text.startswith("/"))
async def on_text(msg: Message, db: sqlite3.Connection, user_row=None) -> None:
    user = user_row or _user_or_ask(msg, db)
    if user is None:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    text = (msg.text or "").strip()

    if repo.latest_pending_input(db, uid, "screenshot_confirm", max_age_min=30):
        await msg.answer("Сначала подтверди или отклони предыдущий скриншот кнопкой выше.")
        return

    # упакованный продукт: штрихкод цифрами или ответ с граммами на вопрос «сколько съел(а)»
    pending_bc = repo.latest_pending_input(db, uid, "barcode", max_age_min=15)
    if pending_bc:
        grams = barcode.parse_grams_reply(text)
        if grams is not None:
            await _finish_barcode(msg, db, user, pending_bc, grams)
            return
    scanned = barcode.parse_barcode_message(text)
    if scanned:
        await _handle_barcode(msg, db, user, scanned[0], scanned[1])
        return

    # «добавь посуду: ...» и «моя посуда»: раньше подписи к фото и уточнений, иначе их съедят
    # свои продукты и сон/шаги/тренировки: тоже заранее, по той же причине
    if await products_router.handle_draft_text(msg, db, user, text):
        return
    dish_intent = intents.detect(text)
    if dish_intent is not None and dish_intent.name in EARLY_INTENTS:
        await agent.run_intent(msg, db, dish_intent)
        return

    if await _try_refine(msg, db, user, text):
        return

    # подпись после фото: фото уже записано, текст в окне combo_window_sec правит ту же запись
    session = repo.fresh_photo_session(db, uid)
    if session:
        if await _apply_late_caption(msg, db, user, session, text):
            return

    await _process_meal(msg, db, user, text=text, photo_path=None, source="text")


# ------------------------------------------------------------ штрихкод (Open Food Facts)

async def _handle_barcode(msg: Message, db: sqlite3.Connection, user, code: str,
                          grams: float | None) -> None:
    uid = user["id"]
    if not off.valid_barcode(code):
        await msg.answer("Не похоже на штрихкод: контрольная цифра не сошлась. Проверь цифры "
                         "(обычно их 13 или 8) или просто опиши продукт текстом.")
        return
    status = await msg.answer("🔎 ищу по штрихкоду…")
    found = await barcode.lookup(db, code, user_id=uid)
    if not found:
        await status.edit_text(
            "Не нашёл этот штрихкод в Open Food Facts (база не знает многих местных "
            "продуктов). Опиши продукт текстом, например «йогурт Простоквашино 200 г», "
            "или пришли фото с подписью, посчитаю по оценке.")
        return
    ref, serving = found
    pending_id = repo.save_pending_input(
        db, uid, "barcode", {"food_id": ref.food_id, "serving": serving})
    if grams is not None:
        await _finish_barcode(msg, db, user, repo.pending_input(db, pending_id, uid), grams,
                              status=status)
        return
    text = (f"Нашёл: <b>{esc(ref.name_ru)}</b>, {ref.kcal_100g:g} ккал на 100 г "
            f"(Б {ref.protein_100g:g} / Ж {ref.fat_100g:g} / У {ref.carbs_100g:g}).\n"
            "Сколько грамм ты съел(а)? Напиши число, например 40.")
    rows = []
    if serving:
        text += f"\nПорция по упаковке: {serving:g} г."
        rows.append([InlineKeyboardButton(
            text=f"Порция {serving:g} г", callback_data=f"bc_serv:{pending_id}")])
    if ref.source != "user":
        rows.append([InlineKeyboardButton(text="💾 Сохранить себе",
                                          callback_data=f"bc_save:{ref.food_id}")])
    await status.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
                           if rows else None)


async def _finish_barcode(msg: Message, db: sqlite3.Connection, user, pending, grams: float,
                          status: Message | None = None) -> None:
    uid = user["id"]
    payload = json.loads(pending["payload_json"])
    row = db.execute("SELECT * FROM foods WHERE id=?", (payload["food_id"],)).fetchone()
    repo.delete_pending_input(db, pending["id"], uid)
    if not row:
        await msg.answer("Продукт пропал из базы, отправь штрихкод ещё раз.")
        return
    ref = barcode._ref_from_row(row)
    u = barcode.understanding_for(ref, grams)
    calc = foodsvc.calculate_meal([(u.items[0], ref)])
    status = status or await msg.answer("🔢 считаю…")
    await _save_and_reply(msg, db, user, u, text=None, photo_path=None, source="text",
                          status=status, calc=calc)


@router.callback_query(F.data.startswith("bc_serv:"))
async def cb_barcode_serving(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _callback_user(cb, db)
    pending = repo.pending_input(db, int(cb.data.split(":")[1]), user["id"], "barcode") if user else None
    serving = json.loads(pending["payload_json"]).get("serving") if pending else None
    if not pending or not serving:
        await cb.answer("Это уже неактуально", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await _finish_barcode(cb.message, db, user, pending, float(serving))
    await cb.answer()


# ------------------------------------------------------------------ голос

@router.message(F.voice | F.audio)
async def on_voice(msg: Message, db: sqlite3.Connection, bot=None) -> None:
    from aiogram import Bot
    bot = bot or msg.bot
    user = _user_or_ask(msg, db)
    if user is None:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    status = await msg.answer("🎙 распознаю голос…")
    from app.config import get_config
    from app.llm import client as llm
    try:
        media = msg.voice or msg.audio
        file = await bot.get_file(media.file_id)
        buf = await bot.download(file)
        ogg = buf.read() if hasattr(buf, "read") else b""
        if len(ogg) > get_config().max_media_bytes:
            await status.edit_text("Аудиофайл слишком большой. Лимит — 10 МБ.")
            return
        text = await llm.transcribe_ogg(
            ogg, filename=getattr(media, "file_name", None) or "voice.ogg",
            model=get_config().model_asr, user_id=uid, db=db,
        )
        if await _try_refine(msg, db, user, text, voice=True):
            await status.delete()
            return
        await _process_meal(msg, db, user, text=text, photo_path=None, source="voice",
                            transcript=text, status=status)
    except Exception:                                        # noqa: BLE001
        repo.save_pending_input(db, uid, "voice", {"tg_file_id": media.file_id})
        await status.edit_text(
            "Не смог распознать голосовое. Сохранил его: повтори позже командой /retry "
            "или опиши еду текстом."
        )


# ------------------------------------------------------------------ фото

ALBUM_WAIT_SEC = 1.5          # сколько ждём остальные фото альбома
_ALBUMS: dict[tuple[int, str], "_Album"] = {}
_BG_TASKS: set[asyncio.Task] = set()


@dataclass
class _Image:
    data: bytes
    file_hash: str
    photo_path: str
    taken_at: datetime | None = None     # из EXIF файла-картинки


@dataclass
class _Album:
    msg: Message
    user: object
    db: sqlite3.Connection
    images: list[_Image] = field(default_factory=list)
    caption: str = ""
    pending: int = 0
    duplicates: int = 0
    add_to: dict | None = None           # фото альбома дополняют готовый приём (кнопка «➕ Фото к приёму»)


async def _typing(msg: Message) -> None:
    """Индикатор «печатает» вместо цепочки статусов; сбой индикатора не важен."""
    try:
        await msg.bot.send_chat_action(msg.chat.id, "typing")
    except Exception:                                        # noqa: BLE001
        log.debug("send_chat_action не прошёл", exc_info=True)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


@router.message(F.photo)
async def on_photo(msg: Message, db: sqlite3.Connection) -> None:
    photo = msg.photo[-1]
    await _ingest_image(msg, db, photo.file_id, photo.file_size, photo)


@router.message(F.document & F.document.mime_type.startswith("image/"))
async def on_image_document(msg: Message, db: sqlite3.Connection) -> None:
    """Картинка файлом (без сжатия): в EXIF может быть настоящее время съёмки."""
    doc = msg.document
    await _ingest_image(msg, db, doc.file_id, doc.file_size, None, from_document=True)


async def _ingest_image(msg: Message, db: sqlite3.Connection, file_id: str,
                        file_size: int | None, photo, from_document: bool = False) -> None:
    user = _user_or_ask(msg, db)
    if user is None:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    add_to = _pending_add_photo(db, uid)
    group = (uid, msg.media_group_id) if msg.media_group_id else None
    album = None
    if group:
        # альбом заводим до скачивания: фото группы приходят параллельными апдейтами
        album = _ALBUMS.get(group)
        if album is None:
            album = _ALBUMS[group] = _Album(msg=msg, user=user, db=db, add_to=add_to)
            _spawn(_flush_album(group))
        album.pending += 1
    try:
        image = await _download_image(msg, db, uid, file_id, file_size, from_document, album)
    finally:
        if album:
            album.pending -= 1
    if image is None:
        return

    caption = (msg.caption or "").strip()
    if album is not None:
        album.images.append(image)
        album.caption = album.caption or caption
        return

    if add_to:
        repo.delete_pending_input(db, add_to["id"], uid)
        status = await msg.answer("📷 добавляю к приёму…")
        await _add_photos_to_meal(msg, db, user, [image], add_to, caption or None, status)
        return

    if caption and proddom.caption_says_label(caption):
        status = await msg.answer("🥫 читаю этикетку…")
        await products_router.from_label_photo(msg, db, user, base64.b64encode(image.data).decode(),
                                               status)
        return

    if caption and _caption_says_screen(caption):
        status = await msg.answer("📷 смотрю…")
        if not await _process_screenshot(msg, db, user, image.data, image.file_hash,
                                         image.photo_path, status):
            # подпись похожа на скриншот, а экран не опознан: это может быть еда («после тренировки»)
            await _recognize_images(msg, db, user, [image], caption, status, source="combo")
        return

    status = await msg.answer("📷 смотрю…")
    # Скриншот телефона без подписи: сразу парсер экрана. Не опознан — обычная еда.
    if _looks_like_phone_screenshot(photo):
        if await _process_screenshot(msg, db, user, image.data, image.file_hash,
                                     image.photo_path, status):
            return
    await _recognize_images(msg, db, user, [image], caption or None, status,
                            source="combo" if caption else "photo")


async def _download_image(msg: Message, db: sqlite3.Connection, uid: int, file_id: str,
                          file_size: int | None, from_document: bool,
                          album: _Album | None) -> _Image | None:
    """Скачать, проверить размер и дубликат, сохранить на диск. None — дальше не идём."""
    from app.config import get_config
    cfg = get_config()
    await _typing(msg)
    if file_size and file_size > cfg.max_media_bytes:
        await msg.answer("Фото слишком большое. Лимит — 10 МБ.")
        return None
    buf = await msg.bot.download(await msg.bot.get_file(file_id))
    data = buf.read() if hasattr(buf, "read") else b""
    if len(data) > cfg.max_media_bytes:
        await msg.answer("Фото слишком большое. Лимит — 10 МБ.")
        return None
    file_hash = hashlib.sha256(data).hexdigest()
    if repo.file_seen(db, uid, file_hash, window_min=10):
        if album is not None:
            album.duplicates += 1
        else:
            await msg.answer("Это фото я уже записал пару минут назад 🙂")
        return None
    repo.remember_file(db, uid, file_hash)
    p = photos.write(f"{cfg.photos_dir}/{file_hash[:16]}.jpg", data)
    return _Image(data, file_hash, p, _fresh_exif(data) if from_document else None)


def _fresh_exif(data: bytes) -> datetime | None:
    """Время съёмки из EXIF, если оно не старше 3 дней (иначе это старая картинка)."""
    shot = mealtime.exif_datetime(data)
    if shot is None:
        return None
    now = datetime.now()
    return shot if now - timedelta(days=3) <= shot <= now + timedelta(hours=1) else None


async def _flush_album(group: tuple[int, str]) -> None:
    """Подождать хвост альбома и записать все фото ОДНИМ приёмом пищи."""
    await asyncio.sleep(ALBUM_WAIT_SEC)
    album = _ALBUMS.get(group)
    for _ in range(100):                         # ещё качается — ждём, но не дольше ~30 с
        if album is None or album.pending <= 0:
            break
        await asyncio.sleep(0.3)
    album = _ALBUMS.pop(group, None)
    if album is None:
        return
    msg, db, user = album.msg, album.db, album.user
    if not album.images:
        if album.duplicates:
            await msg.answer("Эти фото я уже записал пару минут назад 🙂")
        return
    try:
        if album.add_to:
            repo.delete_pending_input(db, album.add_to["id"], user["id"])
            status = await msg.answer("📷 добавляю фото к приёму…")
            await _add_photos_to_meal(msg, db, user, album.images, album.add_to,
                                      album.caption or None, status)
            return
        if album.caption and proddom.caption_says_label(album.caption):
            status = await msg.answer("🥫 читаю этикетку…")
            await products_router.from_label_photo(
                msg, db, user, base64.b64encode(album.images[0].data).decode(), status)
            return
        status = await msg.answer("📷 смотрю фото…")
        await _recognize_images(msg, db, user, album.images, album.caption or None, status,
                                source="combo" if album.caption else "photo")
    except Exception:                                        # noqa: BLE001
        log.exception("альбом не обработан")


_SCREEN_CAPTION_RE = re.compile(
    r"(?<![а-яa-z])сон(?![а-яa-z])|(?<![а-яa-z])сна(?![а-яa-z])|(?<![а-яa-z])спал|шаг|"
    r"активност|тренировк|пикук|picooc|mi fitness|весы|пульс|закрыл кольцо|health|garmin|"
    r"samsung|huawei|apple|zepp|amazfit|fitbit|google fit", re.IGNORECASE)


def _caption_says_screen(caption: str) -> bool:
    return bool(_SCREEN_CAPTION_RE.search(caption))


SCREENSHOT_ASPECT = 1.9   # h/w: 9:16 фото = 1.78, полноэкранный скриншот = 2.0–2.3


def _looks_like_phone_screenshot(photo) -> bool:
    """Полноэкранный скриншот телефона узнаём по вытянутой пропорции.

    Порог 1.9 отсекает портретные фото 3:4 (1.33) и 9:16 (1.78), но ловит
    скриншоты 1179×2556 (2.17) и 1284×2778 (2.16). Это дешёвая эвристика:
    окончательное решение всё равно за vision-парсером — не опознал экран,
    значит уходим в распознавание еды.
    """
    if photo is None or not getattr(photo, "width", 0):
        return False
    return photo.height / photo.width >= SCREENSHOT_ASPECT


async def _process_photo_after_window(db: sqlite3.Connection, session_id: int) -> None:
    """Закрыть окно для подписи, когда оно истекло. Распознавание уже сделано сразу."""
    from app.config import get_config
    await asyncio.sleep(get_config().combo_window_sec)
    repo.close_photo_session(db, session_id)


_merge = meals_svc.merge_understandings


async def _parse_images(images: list[_Image], caption: str | None, uid: int,
                        db: sqlite3.Connection, dishes=None):
    """Одно фото — parse_photo, несколько — одним запросом со всеми (запасной путь внутри)."""
    return await meals_svc.parse_images(
        [base64.b64encode(im.data).decode() for im in images], caption, uid, db, dishes)


async def _recognize_photo(msg: Message, db: sqlite3.Connection, user, data: bytes,
                           file_hash: str, photo_path: str, caption: str | None,
                           status: Message, source: str) -> None:
    await _recognize_images(msg, db, user, [_Image(data, file_hash, photo_path)],
                            caption, status, source)


async def _recognize_images(msg: Message, db: sqlite3.Connection, user, images: list[_Image],
                            caption: str | None, status: Message, source: str) -> None:
    """Распознать фото (одно или весь альбом) сразу и записать одним приёмом пищи.

    Подпись, пришедшая позже, правит этот же приём (см. _apply_late_caption).
    """
    uid = user["id"]
    await _typing(msg)
    dishes = meals_svc.resolve_dishes(db, uid, None, len(images))     # посуда по умолчанию
    try:
        u = await _parse_images(images, caption, uid, db, dishes)
    except Exception:                                     # noqa: BLE001
        for im in images:
            repo.save_pending_input(db, uid, "photo", {"path": im.photo_path, "caption": caption})
            repo.forget_file(db, uid, im.file_hash)
        await status.edit_text("Не смог распознать фото (LLM недоступен). "
                               "Сохранил: повтори позже командой /retry или опиши текстом.")
        return
    if not u.items:
        if len(images) == 1:
            await _process_screenshot(msg, db, user, images[0].data, images[0].file_hash,
                                      images[0].photo_path, status)
        else:
            await status.edit_text("Не увидел еды на этих фото. Опиши текстом, что было съедено.")
        return
    taken = next((im.taken_at for im in images if im.taken_at), None)
    meal_id = await _save_and_reply(msg, db, user, u, text=caption,
                                    photo_path=images[0].photo_path, source=source,
                                    status=status, taken_at=taken,
                                    photo_paths=[im.photo_path for im in images],
                                    dish_ids=[d["id"] if d is not None else None for d in dishes])
    if meal_id is None:
        return
    if not caption:
        from app.config import get_config
        window = get_config().combo_window_sec
        session_id = repo.put_photo_session(
            db, uid, {"photo_paths": [im.photo_path for im in images],
                      "file_hashes": [im.file_hash for im in images],
                      "taken_at": taken.isoformat() if taken else None,
                      "chat_id": status.chat.id, "card_message_id": status.message_id},
            expires_min=window / 60, meal_id=meal_id)
        _spawn(_process_photo_after_window(db, session_id))
    await _food_logged_hook(msg, db, user, meal_id)


def _looks_like_caption(text: str) -> bool:
    """Текст сразу после фото — подпись, если это не вопрос, не длинная реплика и не вода."""
    if text.endswith("?") or len(text) > 200:
        return False
    ml, rest = water.extract_water(text)
    return not (ml and water.is_water_only(rest))


async def _apply_late_caption(msg: Message, db: sqlite3.Connection, user, session,
                              text: str) -> bool:
    """Подпись после фото: пересчитать ТОТ ЖЕ приём пищи и отредактировать его карточку.

    True — сообщение использовано как подпись; False — это обычный текст, идём дальше.
    """
    uid = user["id"]
    meal = repo.get_meal(db, uid, session["meal_id"]) if session["meal_id"] else None
    if not meal or meal["deleted_at"] or not _looks_like_caption(text):
        return False
    ctx = json.loads(session["context_json"])
    repo.close_photo_session(db, session["id"])
    await _typing(msg)
    try:
        u = await meals_svc.rerecognize_meal(db, user, meal["id"], comment=text)
    except (meals_svc.NoFood, meals_svc.NoPhotos, LookupError):
        return False
    except Exception:                                     # noqa: BLE001
        await msg.answer("Не смог учесть подпись (LLM недоступен): фото записано как было.")
        return True
    taken = datetime.fromisoformat(ctx["taken_at"]) if ctx.get("taken_at") else None
    when = _resolve_when(user, text, taken)
    if when:
        _set_meal_time(db, user, meal["id"], when, meal["local_time"])
    card = _render_card(db, user, meal["id"], u=u)
    from aiogram.types import Chat
    card_msg = Message(message_id=ctx["card_message_id"], date=datetime.now(),
                       chat=Chat(id=ctx["chat_id"], type="private")).as_(msg.bot)
    try:
        await rich.edit(card_msg, card, reply_markup=_meal_kb(meal["id"]))
    except Exception:                                     # noqa: BLE001
        log.warning("не смог отредактировать карточку, шлю новую", exc_info=True)
        await rich.answer(msg, card, reply_markup=_meal_kb(meal["id"]))
    return True


ADD_PHOTO_WAIT_MIN = 10       # сколько ждём фото после кнопки «➕ Фото к приёму»


def _pending_add_photo(db: sqlite3.Connection, uid: int) -> dict | None:
    """Ожидание «ещё фото к приёму»: {id, meal_id, chat_id, message_id} или None."""
    row = repo.latest_pending_input(db, uid, "meal_add_photo", max_age_min=ADD_PHOTO_WAIT_MIN)
    if not row:
        return None
    return {"id": row["id"], **json.loads(row["payload_json"])}


async def _add_photos_to_meal(msg: Message, db: sqlite3.Connection, user, images: list[_Image],
                              pending: dict, caption: str | None, status: Message) -> None:
    """Новые фото дописываются к приёму, и он пересчитывается по ВСЕМ своим фото сразу."""
    uid = user["id"]
    meal = repo.get_meal(db, uid, pending["meal_id"])
    if not meal or meal["deleted_at"]:
        await status.edit_text("Эта запись уже отменена, фото не добавил.")
        return
    old_kcal = meal["kcal_total"]
    dish_id = next((r["dish_id"] for r in repo.meal_photos(db, meal["id"]) if r["dish_id"]), None)
    comment = None
    if caption:
        old = meal["transcript"]
        comment = f"{old}; {caption}" if old and caption not in old else caption
    await _typing(msg)
    try:
        u = await meals_svc.rerecognize_meal(
            db, user, meal["id"], comment=comment,
            extra_photos=[im.photo_path for im in images], extra_dish_id=dish_id)
    except meals_svc.NoFood:
        await status.edit_text("Не увидел еды на новых фото: приём остался как был.")
        return
    except Exception:                                     # noqa: BLE001
        log.warning("не смог добавить фото к приёму", exc_info=True)
        for im in images:
            repo.forget_file(db, uid, im.file_hash)
        await status.edit_text("Не смог пересчитать (LLM недоступен): приём остался как был. "
                               "Нажми «➕ Фото к приёму» и пришли фото чуть позже.")
        return
    when = _resolve_when(user, caption)
    if when:
        _set_meal_time(db, user, meal["id"], when, meal["local_time"])
    card = _render_card(db, user, meal["id"], u=u)
    new_kcal = repo.get_meal(db, uid, meal["id"])["kcal_total"]
    await status.edit_text(f"➕ Добавил фото: {len(images)}. Пересчитал по всем: "
                           f"{old_kcal} → {new_kcal} ккал")
    if pending.get("chat_id") and pending.get("message_id"):
        try:
            await rich.edit(_card_message(msg, pending["chat_id"], pending["message_id"]), card,
                            reply_markup=_meal_kb(meal["id"]))
            return
        except Exception:                                 # noqa: BLE001
            log.warning("не смог отредактировать карточку, шлю новую", exc_info=True)
    await rich.answer(msg, card, reply_markup=_meal_kb(meal["id"]))


REFINE_WAIT_MIN = 10          # сколько ждём рассказ после кнопки «Уточнить»


def _card_message(msg: Message, chat_id: int, message_id: int) -> Message:
    from aiogram.types import Chat
    return Message(message_id=message_id, date=datetime.now(),
                   chat=Chat(id=chat_id, type="private")).as_(msg.bot)


def _diff_text(old: list[dict], calc, old_kcal: int) -> str:
    """«Было → стало»: итог и что добавилось или поменялось."""
    old_by = {o["name_ru"].strip().lower(): o for o in old}
    lines = []
    for it in calc.items:
        prev = old_by.get(it.name_ru.strip().lower())
        if prev is None:
            lines.append(f"＋ {esc(it.name_ru)} {it.grams:g} г ({it.kcal} ккал)")
        elif abs(prev["grams"] - it.grams) >= 1:
            lines.append(f"✎ {esc(it.name_ru)}: {prev['grams']:g} → {it.grams:g} г")
    new_names = {it.name_ru.strip().lower() for it in calc.items}
    for name, o in old_by.items():
        if name not in new_names:
            lines.append(f"－ {esc(o['name_ru'])}")
    delta = calc.kcal_total - old_kcal
    head = (f"💬 Учёл: {old_kcal} → <b>{calc.kcal_total}</b> ккал ({delta:+d})"
            if lines or delta else "💬 Ничего не поменялось: по описанию состав тот же.")
    return "\n".join([head] + lines)


async def _refine_meal(msg: Message, db: sqlite3.Connection, user, meal_id: int, comment: str,
                       chat_id: int | None = None, card_message_id: int | None = None) -> bool:
    """Уточнить готовую запись рассказом о приготовлении: правим состав, а не считаем заново."""
    uid = user["id"]
    meal = repo.get_meal(db, uid, meal_id)
    if not meal or meal["deleted_at"]:
        await msg.answer("Эта запись уже отменена.")
        return True
    old = [dict(r) for r in repo.get_meal_items(db, meal_id)]
    await _typing(msg)
    try:
        u, _changes = await foodsvc.refine_meal(old, comment, uid, db)
    except Exception:                                     # noqa: BLE001
        log.warning("refine_meal упал", exc_info=True)
        await msg.answer("Не смог учесть уточнение (LLM недоступен): запись осталась как была. "
                         "Нажми «Уточнить» ещё раз чуть позже.")
        return True
    if not u.items:
        await msg.answer("Не понял, что поменять. Расскажи, например: «жарила на подсолнечном "
                         "масле, сверху две ложки майонеза».")
        return True
    calc = await foodsvc.build_calc(u, db, uid)
    repo.replace_meal_items(db, uid, meal_id, calc)
    for estimate, item in zip(u.items, calc.items):
        if item.food_ref_id:
            repo.touch_user_food(db, uid, item.food_ref_id, estimate.explicit_grams)
    card = _render_card(db, user, meal_id)
    if chat_id and card_message_id:
        try:
            await rich.edit(_card_message(msg, chat_id, card_message_id), card,
                            reply_markup=_meal_kb(meal_id))
        except Exception:                                 # noqa: BLE001
            log.warning("не смог отредактировать карточку", exc_info=True)
            await rich.answer(msg, card, reply_markup=_meal_kb(meal_id))
    else:
        await rich.answer(msg, card, reply_markup=_meal_kb(meal_id))
    await msg.answer(_diff_text(old, calc, meal["kcal_total"]))
    return True


async def _try_refine(msg: Message, db: sqlite3.Connection, user, text: str,
                      voice: bool = False) -> bool:
    """Текст или голос как рассказ о приготовлении: после кнопки «Уточнить» или сразу после фото.

    True — сообщение использовано; False — это обычный ввод.
    """
    uid = user["id"]
    pending = repo.latest_pending_input(db, uid, "meal_refine", max_age_min=REFINE_WAIT_MIN)
    if pending:
        payload = json.loads(pending["payload_json"])
        repo.delete_pending_input(db, pending["id"], uid)
        return await _refine_meal(msg, db, user, payload["meal_id"], text,
                                  payload.get("chat_id"), payload.get("message_id"))
    if voice:
        session = repo.fresh_photo_session(db, uid)
        if session and session["meal_id"]:
            return await _apply_late_caption(msg, db, user, session, text)
    return False


async def _food_logged_hook(msg: Message, db: sqlite3.Connection, user, meal_id: int) -> None:
    """Сообщить открытому разговору о тяге, что еда записана (если такая функция есть)."""
    try:
        from app.services import craving as craving_svc
        note = craving_svc.note_food_logged
    except (ImportError, AttributeError):
        return
    try:
        items = repo.get_meal_items(db, meal_id)
        meal = repo.get_meal(db, user["id"], meal_id)
        names = ", ".join(f"{i['name_ru']} ({i['kcal']} ккал)" for i in items)
        reply = note(db, user, f"{names}; всего {meal['kcal_total']} ккал")
        if reply:
            await msg.answer(reply)
    except Exception:                                        # noqa: BLE001
        log.warning("note_food_logged упала", exc_info=True)


# ------------------------------------------------------------------ конвейер

async def _process_meal(msg: Message, db: sqlite3.Connection, user, text: str,
                        photo_path: str | None, source: str,
                        transcript: str | None = None, status: Message | None = None) -> None:
    uid = user["id"]
    if source == "voice" and await craving_router.maybe_handle(msg, db, user, text, status):
        return
    water_ml, rest = water.extract_water(text)
    if water_ml:
        water_id, reply = watersvc.log_water(db, user, water_ml, source, text)
        if water.is_water_only(rest):
            if status:
                await status.edit_text(reply, reply_markup=watersvc.undo_keyboard(water_id))
            else:
                await msg.answer(reply, reply_markup=watersvc.undo_keyboard(water_id))
            return
        await msg.answer(reply, reply_markup=watersvc.undo_keyboard(water_id))
        text = rest                              # остальное в сообщении — еда
    if not water_ml and await agent.maybe_handle(msg, db, user, text, status):
        return
    status = status or await msg.answer("🧠 распознаю…")
    try:
        u = await foodsvc.parse_text(text, uid, db)
    except Exception:                                      # noqa: BLE001
        repo.save_pending_input(db, uid, "text", {"text": text})
        await status.edit_text("Не смог разобрать (LLM недоступен). Сохранил ввод: "
                               "повтори позже командой /retry или напиши ещё раз.")
        return
    if source in ("text", "voice") and await agent.route_llm_intent(msg, db, user, text, u, status):
        return
    if not u.items and source in ("text", "voice"):
        # не еда: это реплика или комментарий, ответит ассистент, а не «не увидел еды»
        await status.edit_text("💭 думаю…")
        reply = await assistant.answer(db, user, text)
        await status.edit_text(reply[:4000])
        return
    await _save_and_reply(msg, db, user, u, text=text, photo_path=photo_path,
                          source=source, status=status, transcript=transcript)


_utc_of = meals_svc.utc_of
_resolve_when = meals_svc.resolve_when
_set_meal_time = meals_svc.set_meal_time


def _when_label(user, local_date: str, local_time: str) -> str:
    today = foodsvc.local_now(user["tz"]).date()
    d = date.fromisoformat(local_date)
    if d == today:
        return local_time
    if d == today - timedelta(days=1):
        return f"вчера {local_time}"
    return f"{d.strftime('%d.%m')} {local_time}"


def _dish_label(db: sqlite3.Connection, user_id: int, meal_id: int | None) -> str:
    """Названия посуды, с которой считали приём («Белая, Кружка»); пусто — без посуды."""
    names: list[str] = []
    for row in repo.meal_photos(db, meal_id) if meal_id else []:
        dish = repo.get_dish(db, user_id, row["dish_id"], include_deleted=True)
        if dish and dish["name"] not in names:
            names.append(dish["name"])
    return ", ".join(names)


def _build_card(db: sqlite3.Connection, user, local_date: str, local_time: str,
                meal_type: str, calc, u=None, meal_id: int | None = None):
    """Карточка приёма: итоги дня берутся за дату самой записи, а не за «сегодня»."""
    uid = user["id"]
    tgt = _target_row(db, uid)
    target_kcal = tgt["kcal_target"] if tgt else 2100
    protein_tgt = int(tgt["protein_g"]) if tgt else 120
    day = repo.day_totals(db, uid, local_date)
    is_late = analytics.is_late(local_time, user["eating_cutoff"])
    card = foodsvc.format_card(
        _when_label(user, local_date, local_time), foodsvc.meal_type_ru(meal_type), calc,
        day["kcal"], target_kcal, int(day["protein"]), protein_tgt,
        late=is_late, late_kcal=calc.kcal_total if is_late else 0,
    )
    dish_label = _dish_label(db, uid, meal_id)
    if dish_label:
        card.text(f"🍽 {esc(dish_label)}")
    drink_ml = sum(water.hydration_ml(getattr(it, "name_ru", ""), getattr(it, "name_en", ""),
                                      it.grams) for it in calc.items)
    if drink_ml:
        total = repo.day_water_ml(db, uid, local_date)
        card.text(f"💧 Чай и кофе засчитал в воду: +{drink_ml} мл · сегодня {total} мл")
    if u is not None and u.assumptions:
        card.text("<i>Допущения: " + esc("; ".join(u.assumptions)) + "</i>")
    if u is not None and u.clarification_question:
        card.text("❓ " + esc(u.clarification_question))
    elif u is not None and _may_hide_fat(u):
        card.text("<i>💬 Масло, соус, майонез на фото не видны. Как готовили? "
                  "Нажми «Уточнить» и расскажи текстом или голосом.</i>")
    return card


def _render_card(db: sqlite3.Connection, user, meal_id: int, u=None):
    """Перерисовать карточку записи из БД (после правки времени или состава)."""
    from types import SimpleNamespace
    meal = repo.get_meal(db, user["id"], meal_id)
    calc = SimpleNamespace(
        items=[SimpleNamespace(**dict(r)) for r in repo.get_meal_items(db, meal_id)],
        kcal_total=meal["kcal_total"], protein_g=meal["protein_g"], fat_g=meal["fat_g"],
        carbs_g=meal["carbs_g"], kcal_low=meal["kcal_low"], kcal_high=meal["kcal_high"],
        method=meal["method"], quality_flags=[])
    return _build_card(db, user, meal["local_date"], meal["local_time"], meal["meal_type"],
                       calc, u, meal_id=meal_id)


async def _save_and_reply(msg: Message, db: sqlite3.Connection, user,
                          u, text: str | None, photo_path: str | None,
                          source: str, status: Message,
                          transcript: str | None = None, calc=None,
                          taken_at: datetime | None = None,
                          photo_paths: list[str] | None = None,
                          dish_ids: list[int | None] | None = None) -> int | None:
    """Записать приём пищи и заменить статус карточкой. Возвращает id записи (None — не записано)."""
    if not u.items:
        await status.edit_text("Не увидел еды в сообщении. Опиши подробнее, что было съедено.")
        return None

    await _typing(msg)
    saved = await meals_svc.save_understanding(
        db, user, u, text=text, source=source, photo_path=photo_path, photo_paths=photo_paths,
        dish_ids=dish_ids, transcript=transcript, calc=calc, taken_at=taken_at)
    meal_id = saved.meal_id
    card = _build_card(db, user, saved.local_date, saved.local_time, saved.meal_type,
                       saved.calc, u, meal_id=meal_id)
    await rich.edit(status, card, reply_markup=_meal_kb(meal_id))
    if source in ("text", "voice"):          # фото сообщает разговору о тяге само, после карточки
        await _food_logged_hook(msg, db, user, meal_id)
    return meal_id


async def _process_screenshot(msg: Message, db: sqlite3.Connection, user,
                              data: bytes, file_hash: str, photo_path: str,
                              status: Message) -> bool:
    """Разобрать скриншот. True — обработано (записано или спросили подтверждение),
    False — это не экран, вызывающий код может попробовать распознать еду."""
    uid = user["id"]
    await _typing(msg)
    try:
        parsed = await screens.parse_screenshot(base64.b64encode(data).decode(), uid, db)
    except Exception:                                       # noqa: BLE001
        repo.save_pending_input(db, uid, "screenshot", {"path": photo_path})
        repo.forget_file(db, uid, file_hash)
        await status.edit_text("Не смог разобрать скриншот (LLM недоступен). "
                               "Сохранил: повтори позже командой /retry.")
        return True
    kind_s = parsed.get("screen", "unknown")
    raw_id = repo.save_raw_observation(db, uid, kind_s, photo_path, file_hash,
                                       parsed, parsed.get("model"))
    if kind_s == "unknown" or not parsed.get("metrics"):
        return False
    verdict = screens.assess_parsed(db, uid, parsed,
                                    date.fromisoformat(screens.local_date_of(user["tz"])))
    local_date = verdict["local_date"]
    if verdict["needs_confirm"]:
        reason = "; ".join(verdict["reasons"])
        preview = " · ".join(str(v) for v in (parsed.get("raw_text") or [])[:6])
        pending_id = repo.save_pending_input(db, uid, "screenshot_confirm",
                                             {"raw_id": raw_id, "parsed": parsed})
        await status.edit_text(
            f"Проверь скриншот: {reason}.\n{esc(preview[:500])}",
            reply_markup=_screenshot_confirm_kb(pending_id),
        )
        return True
    await status.edit_text(_apply_screenshot(db, user, parsed, local_date, raw_id) or (
        "Не смог надёжно прочитать значения на скриншоте (нечитаемо или вне допустимых "
        "границ), ничего не записал. Пришли скриншот ещё раз, лучше целиком и без бликов."))
    return True


FASTING_NOTES = {
    1: " Утренний замер натощак, отлично для тренда.",
    0: " Это не утренний замер натощак: в тренд он войдёт, только если нет утренних. "
       "Лучше взвешиваться утром после туалета и до еды.",
}


def _apply_screenshot(db: sqlite3.Connection, user, parsed: dict, local_date: str,
                      raw_id: int | None) -> str | None:
    """Записать разобранный скриншот и вернуть текст ответа (None — ничего не записано)."""
    applied, fasting = screens.apply_for_user(db, user, parsed, local_date, raw_id)
    labels = {"sleep": "😴 Сон записал", "activity": "🏃 Активность записал",
              "body": "⚖️ Метрики тела записал" + FASTING_NOTES.get(fasting, "")}
    return labels.get(applied)


@router.message(Command("retry"))
async def cmd_retry(msg: Message, db: sqlite3.Connection) -> None:
    """Повторить один неудавшийся ввод без потери исходного файла или текста."""
    user = _user_or_ask(msg, db)
    if user is None:
        await msg.answer("Сначала настрой профиль: /start")
        return
    row = db.execute(
        """SELECT * FROM pending_inputs WHERE user_id=? AND kind != 'screenshot_confirm'
           ORDER BY id LIMIT 1""", (user["id"],)
    ).fetchone()
    if not row:
        await msg.answer("Нет сохранённых неудавшихся вводов для повтора.")
        return
    payload = json.loads(row["payload_json"])
    if row["kind"] not in {"text", "photo", "screenshot", "voice"}:
        await msg.answer("Неизвестный тип сохранённого ввода; данные оставлены без изменений.")
        return
    repo.delete_pending_input(db, row["id"], user["id"])
    if row["kind"] == "text":
        await _process_meal(msg, db, user, payload["text"], None, "text")
        return
    if row["kind"] in {"photo", "screenshot"}:
        try:
            path = payload["path"]
            data = Path(path).read_bytes()
        except (KeyError, OSError):
            await msg.answer("Исходный файл для повтора не найден. Пришли его ещё раз.")
            return
        status = await msg.answer("🔁 повторяю распознавание…")
        file_hash = hashlib.sha256(data).hexdigest()
        if row["kind"] == "photo":
            await _recognize_photo(msg, db, user, data, file_hash, path,
                                   payload.get("caption"), status, "photo")
        else:
            await _process_screenshot(msg, db, user, data, file_hash, path, status)
        return
    if row["kind"] == "voice":
        try:
            file = await msg.bot.get_file(payload["tg_file_id"])
            buf = await msg.bot.download(file)
            audio = buf.read() if hasattr(buf, "read") else b""
            from app.config import get_config
            from app.llm import client as llm
            text = await llm.transcribe_ogg(audio, model=get_config().model_asr)
        except Exception:  # noqa: BLE001
            repo.save_pending_input(db, user["id"], "voice", payload)
            await msg.answer("Повтор пока не удался; голосовое осталось в очереди.")
            return
        await _process_meal(msg, db, user, text, None, "voice", transcript=text)
        return


# ------------------------------------------------------------------ колбэки

def _callback_user(cb: CallbackQuery, db: sqlite3.Connection):
    return repo.get_user_by_tg(db, cb.from_user.id)


def _owned_item(db: sqlite3.Connection, user_id: int, item_id: int):
    return db.execute(
        """SELECT mi.* FROM meal_items mi JOIN meals m ON m.id=mi.meal_id
           WHERE mi.id=? AND m.user_id=? AND m.deleted_at IS NULL""",
        (item_id, user_id),
    ).fetchone()


@router.callback_query(F.data.startswith("screen_yes:"))
async def cb_screen_yes(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _callback_user(cb, db)
    pending_id = int(cb.data.split(":")[1])
    pending = repo.pending_input(db, pending_id, user["id"], "screenshot_confirm") if user else None
    if not pending:
        await cb.answer("Подтверждение уже неактуально", show_alert=True)
        return
    payload = json.loads(pending["payload_json"])
    reply = _apply_screenshot(db, user, payload["parsed"], screens.local_date_of(user["tz"]),
                              payload["raw_id"])
    repo.delete_pending_input(db, pending_id, user["id"])
    await cb.message.edit_text(reply or "Не смог применить данные скриншота.")
    await cb.answer()


@router.callback_query(F.data.startswith("screen_no:"))
async def cb_screen_no(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _callback_user(cb, db)
    pending_id = int(cb.data.split(":")[1])
    if not user or not repo.delete_pending_input(db, pending_id, user["id"]):
        await cb.answer("Подтверждение уже неактуально", show_alert=True)
        return
    await cb.message.edit_text("Не записывал данные скриншота.")
    await cb.answer()

@router.callback_query(F.data.startswith("water_undo:"))
async def cb_water_undo(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _callback_user(cb, db)
    water_id = int(cb.data.split(":")[1])
    if not user or not repo.delete_water(db, user["id"], water_id):
        await cb.answer("Уже отменено", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Воду отменил")


@router.callback_query(F.data.startswith("meal_ok:"))
async def cb_ok(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _callback_user(cb, db)
    meal_id = int(cb.data.split(":")[1])
    if not user or not repo.get_meal(db, user["id"], meal_id):
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    # «Верно» учит бота: ваша формулировка закрепляется за найденным продуктом
    for it in repo.get_meal_items(db, meal_id):
        if it["food_ref_id"] and it["name_ru"]:
            repo.add_alias(db, user["id"], it["food_ref_id"], matching.normalize(it["name_ru"]))
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Записал 👍")


@router.callback_query(F.data.startswith("meal_undo:"))
async def cb_undo(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _callback_user(cb, db)
    if not user:
        await cb.answer("Нет профиля", show_alert=True)
        return
    meal_id = int(cb.data.split(":")[1])
    meal = repo.get_meal(db, user["id"], meal_id)
    if not meal or meal["deleted_at"]:
        await cb.answer("Уже отменено", show_alert=True)
        return
    repo.soft_delete_meal(db, user["id"], meal_id)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Запись отменена")


@router.callback_query(F.data.startswith("meal_refine:"))
async def cb_meal_refine(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    """«💬 Уточнить»: следующий текст или голос — рассказ о том, как готовили."""
    meal_id = int(cb.data.split(":")[1])
    user = _callback_user(cb, db)
    meal = repo.get_meal(db, user["id"], meal_id) if user else None
    if not meal or meal["deleted_at"]:
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    repo.save_pending_input(db, user["id"], "meal_refine", {
        "meal_id": meal_id, "chat_id": cb.message.chat.id, "message_id": cb.message.message_id})
    await cb.message.answer(
        "Расскажи, как готовили: на чём жарили, какой соус или майонез, сколько ложек, "
        "сколько реально весит. Можно текстом или голосовым.")
    await cb.answer()


@router.callback_query(F.data.startswith("meal_time:"))
async def cb_meal_time(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    """«🕐 Другое время»: быстрый выбор слота приёма пищи."""
    meal_id = int(cb.data.split(":")[1])
    user = _callback_user(cb, db)
    meal = repo.get_meal(db, user["id"], meal_id) if user else None
    if not meal or meal["deleted_at"]:
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=label, callback_data=f"meal_at:{meal_id}:{key}")]
            for key, (label, *_rest) in mealtime.SLOTS.items()]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"meal_back:{meal_id}")])
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("meal_back:"))
async def cb_meal_back(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    await cb.message.edit_reply_markup(reply_markup=_meal_kb(int(cb.data.split(":")[1])))
    await cb.answer()


@router.callback_query(F.data.startswith("meal_dishes:"))
async def cb_meal_dishes(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    """«🍽 Посуда»: список посуды для выбора (пересчёт по фото с известным размером)."""
    meal_id = int(cb.data.split(":")[1])
    user = _callback_user(cb, db)
    meal = repo.get_meal(db, user["id"], meal_id) if user else None
    if not meal or meal["deleted_at"]:
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    photos = repo.meal_photos(db, meal_id)
    if not photos:
        await cb.answer("У записи нет фото: посуда нужна для оценки порции по фото",
                        show_alert=True)
        return
    current = {r["dish_id"] for r in photos if r["dish_id"]}
    rows = repo.list_dishes(db, user["id"])
    buttons = []
    for d in rows:
        mark = "✔ " if d["id"] in current else ("⭐ " if d["is_default"] else "")
        size = f" · Ø{d['diameter_cm']:g}" if d["diameter_cm"] else (
            f" · {d['volume_ml']:g} мл" if d["volume_ml"] else "")
        buttons.append([InlineKeyboardButton(text=f"{mark}{d['name'][:22]}{size}",
                                             callback_data=f"meal_dish:{meal_id}:{d['id']}")])
    if rows:
        buttons.append([InlineKeyboardButton(text="🚫 Без посуды",
                                             callback_data=f"meal_dish:{meal_id}:0")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить посуду", callback_data="dish_help")])
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=f"meal_back:{meal_id}")])
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    if not rows:
        await cb.message.answer("🍽 Посуды пока нет. " + esc(dishdom.HELP_TEXT))
    await cb.answer()


@router.callback_query(F.data.startswith("meal_dish:"))
async def cb_meal_dish(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    """Выбрана посуда: фото приёма пересчитываются с её размером как масштабом."""
    _, meal_id, dish_id = cb.data.split(":")
    meal_id, dish_id = int(meal_id), int(dish_id)
    user = _callback_user(cb, db)
    meal = repo.get_meal(db, user["id"], meal_id) if user else None
    if not meal or meal["deleted_at"]:
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    dish = repo.get_dish(db, user["id"], dish_id) if dish_id else None
    if dish_id and dish is None:
        await cb.answer("Такой посуды нет", show_alert=True)
        return
    await cb.answer("Пересчитываю с этой посудой…" if dish else "Пересчитываю без посуды…")
    await _typing(cb.message)
    old_kcal = meal["kcal_total"]
    try:
        u = await meals_svc.rerecognize_meal(db, user, meal_id,
                                             dish_id=dish["id"] if dish else None)
    except meals_svc.NoPhotos:
        await cb.message.answer("Не нашёл файлы фото этого приёма, пересчитать не смогу.")
    except meals_svc.NoFood:
        await cb.message.answer("С этой посудой еды на фото не нашёл: оставил запись как была.")
    except Exception:                                     # noqa: BLE001
        log.warning("пересчёт с посудой не удался", exc_info=True)
        await cb.message.answer("Не смог пересчитать (LLM недоступен): запись осталась как была. "
                                "Выбери посуду ещё раз чуть позже.")
    else:
        card = _render_card(db, user, meal_id, u=u)
        await rich.edit(cb.message, card, reply_markup=_meal_kb(meal_id))
        new_kcal = repo.get_meal(db, user["id"], meal_id)["kcal_total"]
        if new_kcal != old_kcal:
            await cb.message.answer(f"🍽 Пересчитал: {old_kcal} → <b>{new_kcal}</b> ккал "
                                    f"({new_kcal - old_kcal:+d})")
        return
    await cb.message.edit_reply_markup(reply_markup=_meal_kb(meal_id))


@router.callback_query(F.data.startswith("meal_addphoto:"))
async def cb_meal_addphoto(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    """«➕ Фото к приёму»: следующие фото (10 минут) дописываются к этой записи."""
    meal_id = int(cb.data.split(":")[1])
    user = _callback_user(cb, db)
    meal = repo.get_meal(db, user["id"], meal_id) if user else None
    if not meal or meal["deleted_at"]:
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    repo.clear_pending_inputs(db, user["id"], "meal_add_photo")
    repo.save_pending_input(db, user["id"], "meal_add_photo", {
        "meal_id": meal_id, "chat_id": cb.message.chat.id, "message_id": cb.message.message_id})
    await cb.message.answer(f"Пришли ещё фото этого приёма: можно несколько сразу. "
                            f"Жду {ADD_PHOTO_WAIT_MIN} минут.")
    await cb.answer()


@router.callback_query(F.data.startswith("meal_at:"))
async def cb_meal_at(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    _, meal_id, key = cb.data.split(":")
    meal_id = int(meal_id)
    user = _callback_user(cb, db)
    meal = repo.get_meal(db, user["id"], meal_id) if user else None
    if not meal or meal["deleted_at"]:
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    when = mealtime.slot_time(key, foodsvc.local_now(user["tz"]).date())
    if when is None:
        await cb.answer("Не понял время", show_alert=True)
        return
    _set_meal_time(db, user, meal_id, when, meal["local_time"])
    await rich.edit(cb.message, _render_card(db, user, meal_id), reply_markup=_meal_kb(meal_id))
    await cb.answer("Перенёс")


@router.callback_query(F.data.startswith("meal_edit:"))
async def cb_edit(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    meal_id = int(cb.data.split(":")[1])
    user = _callback_user(cb, db)
    if not user or not repo.get_meal(db, user["id"], meal_id):
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    items = repo.get_meal_items(db, meal_id)
    if not items:
        await cb.answer("Пусто", show_alert=True)
        return
    buttons = []
    for it in items[:8]:
        row = [InlineKeyboardButton(
            text=f"{it['name_ru'][:20]} · {it['grams']:g} г",
            callback_data=f"item_edit:{it['id']}",
        )]
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="↩️ Отменить всю запись",
                                         callback_data=f"meal_undo:{meal_id}")])
    await cb.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("item_edit:"))
async def cb_item_edit(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    item_id = int(cb.data.split(":")[1])
    user = _callback_user(cb, db)
    item = _owned_item(db, user["id"], item_id) if user else None
    if not item:
        await cb.answer("Нет позиции", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"−25% ({max(0, item['grams'] * 0.75):g} г)",
                              callback_data=f"item_grams:{item_id}:{item['grams'] * 0.75:g}"),
         InlineKeyboardButton(text=f"+25% ({item['grams'] * 1.25:g} г)",
                              callback_data=f"item_grams:{item_id}:{item['grams'] * 1.25:g}")],
        [InlineKeyboardButton(text="💾 В мои продукты",
                              callback_data=f"item_save:{item_id}")],
        [InlineKeyboardButton(text="🗑 Удалить позицию",
                              callback_data=f"item_del:{item_id}")],
    ])
    await cb.message.edit_reply_markup(reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("item_grams:"))
async def cb_item_grams(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    _, item_id, grams = cb.data.split(":")
    user = _callback_user(cb, db)
    if not user or not _owned_item(db, user["id"], int(item_id)):
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    repo.edit_item_grams(db, int(item_id), float(grams))
    edited = _owned_item(db, user["id"], int(item_id))
    if edited and edited["food_ref_id"]:
        repo.touch_user_food(db, user["id"], edited["food_ref_id"], float(grams))   # учим порцию
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Обновил")


@router.callback_query(F.data.startswith("item_del:"))
async def cb_item_del(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    item_id = int(cb.data.split(":")[1])
    user = _callback_user(cb, db)
    if not user or not _owned_item(db, user["id"], item_id):
        await cb.answer("Эта запись вам недоступна", show_alert=True)
        return
    repo.delete_item(db, item_id)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Удалил позицию")
