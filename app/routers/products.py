"""Свои продукты: /products, «добавь продукт ...», фото этикетки, сохранение из записи и штрихкода."""
from __future__ import annotations

import logging
import re
import sqlite3

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.domain import products as dom
from app.services import products as prodsvc
from app.storage import repo
from app.textutil import esc

router = Router(name="products")
log = logging.getLogger(__name__)

LIST_LIMIT = 20
DRAFT_MAX_AGE_MIN = 30
_PORTION = re.compile(r"порци\w*\s*[:=\-—]?\s*(\d{1,4}(?:[.,]\d+)?)\s*(?:г|гр|грамм\w*|g)?",
                      re.IGNORECASE)
_NAME_FIX = re.compile(r"^\s*(?:назван\w*|имя)\s*[:\-—]?\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)
_BRAND_FIX = re.compile(r"(?:бренд|марка|производитель)\s*[:\-—]?\s*([^,;]+)", re.IGNORECASE)
_FOOD_VERBS = re.compile(r"\b(?:съел|съела|поел|поела|выпил|выпила|скушал|скушала)\b", re.IGNORECASE)
_CANCEL = re.compile(r"^\s*(?:отмена|отмени(?:ть)?|не надо|не нужно|стоп|cancel)\W*$", re.IGNORECASE)

FIX_HINT = ("Напиши, что поправить: «белки 17, жиры 5, углеводы 1.8», «ккал 120», "
            "«название: Творог Савушкин», «порция 200 г». Или «отмена».")


def _short(name: str, n: int = 16) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


# ------------------------------------------------------------------ карточка черновика

def _problems(draft: dict) -> tuple[list[str], str | None, list[str]]:
    """(чего не хватает, ошибка границ, предупреждения) для черновика."""
    filled, _ = dom.complete_draft(draft)
    missing = dom.draft_missing(filled)
    if missing:
        return missing, None, []
    try:
        return [], None, dom.check_values(filled["kcal"], filled["protein"], filled["fat"],
                                          filled["carbs"])
    except ValueError as exc:
        return [], str(exc), []


def draft_text(draft: dict, notes: list[str] | None = None) -> str:
    filled, derived = dom.complete_draft(draft)
    title = esc(filled.get("name") or "без названия")
    if filled.get("brand"):
        title += f" · {esc(filled['brand'])}"
    lines = [f"🥫 <b>{title}</b>"]
    per100 = dom.describe_per100(filled)
    lines.append(f"{per100} на 100 г" if per100 else "числа пока не заполнены")
    if filled.get("default_grams"):
        unit = f" ({esc(filled['unit_name'])})" if filled.get("unit_name") else ""
        lines.append(f"Порция: {dom.fmt_num(filled['default_grams'])} г{unit}")
    elif filled.get("unit_grams") and filled.get("unit_name"):
        lines.append(f"{esc(filled['unit_name']).capitalize()}: {dom.fmt_num(filled['unit_grams'])} г")
    missing, error, warnings = _problems(draft)
    extra = list(notes or [])
    if derived:
        extra.append("калории посчитал по БЖУ")
    for note in extra:
        lines.append(f"ℹ️ {esc(note)}")
    if error:
        lines.append(f"⛔ {esc(error)}")
    for warning in warnings:
        lines.append(f"⚠️ {esc(warning)}")
    if missing:
        lines.append(f"Не хватает: {', '.join(missing)}.")
    return "\n".join(lines)


def draft_kb(pending_id: int, draft: dict) -> InlineKeyboardMarkup:
    missing, error, _ = _problems(draft)
    row = []
    if not missing and not error:
        row.append(InlineKeyboardButton(text="💾 Сохранить", callback_data=f"prod_save:{pending_id}"))
    row.append(InlineKeyboardButton(text="✏️ Исправить", callback_data=f"prod_edit:{pending_id}"))
    row.append(InlineKeyboardButton(text="✖️ Отмена", callback_data=f"prod_cancel:{pending_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def show_draft(msg: Message, db: sqlite3.Connection, user, draft: dict,
                     notes: list[str] | None = None, status: Message | None = None,
                     barcode: str | None = None, editing: bool = False) -> int:
    """Новая карточка черновика (старые черновики пользователя заменяются). → id ожидания."""
    repo.clear_pending_inputs(db, user["id"], "product_draft")
    pid = repo.save_pending_input(db, user["id"], "product_draft",
                                  {"draft": draft, "editing": editing, "notes": notes or [],
                                   "barcode": barcode})
    text = draft_text(draft, notes)
    kb = draft_kb(pid, draft)
    if status is not None:
        await status.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)
    return pid


# ------------------------------------------------------------------ список

def list_text(rows) -> str:
    if not rows:
        return "🥫 Своих продуктов пока нет.\n\n" + esc(dom.HELP_TEXT)
    lines = [f"🥫 <b>Твои продукты</b> ({len(rows)}). Числа на 100 г:"]
    for r in rows[:LIST_LIMIT]:
        name = esc(r["name_ru"]) + (f" · {esc(r['brand'])}" if r["brand"] else "")
        lines.append(f"• {name}: {esc(dom.describe_per100(r))}")
    if len(rows) > LIST_LIMIT:
        lines.append(f"…и ещё {len(rows) - LIST_LIMIT}")
    lines += ["", "Добавить: «добавь продукт: творог 5% Савушкин, 121 ккал, Б 17, Ж 5, У 1.8» "
                  "или фото этикетки с подписью «этикетка»."]
    return "\n".join(lines)


def list_kb(rows) -> InlineKeyboardMarkup | None:
    buttons = [[InlineKeyboardButton(text=f"🗑 {_short(r['name_ru'])}",
                                     callback_data=f"prod_del:{r['id']}")]
               for r in rows[:LIST_LIMIT]]
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


async def cmd_products(msg: Message, db: sqlite3.Connection) -> None:
    user = repo.get_user_by_tg(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    rows = repo.list_products(db, user["id"])
    await msg.answer(list_text(rows), reply_markup=list_kb(rows))


@router.message(Command("products"))
async def on_products_command(msg: Message, db: sqlite3.Connection) -> None:
    await cmd_products(msg, db)


# ------------------------------------------------------------------ добавление текстом

async def add_from_text(msg: Message, db: sqlite3.Connection, text: str) -> None:
    """«добавь продукт: ...»: полные данные сохраняются сразу, неполные уходят в карточку."""
    user = repo.get_user_by_tg(db, msg.from_user.id)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    draft = await prodsvc.parse_text(db, user, text)
    if draft is None:
        await msg.answer(esc("Не нашёл чисел. " + dom.HELP_TEXT))
        return
    missing, error, _ = _problems(draft)
    if missing or error:
        await show_draft(msg, db, user, draft, editing=True)
        await msg.answer(FIX_HINT)
        return
    await _save_and_reply(msg, db, user, draft)


async def _save_and_reply(msg: Message, db: sqlite3.Connection, user, draft: dict,
                          barcode: str | None = None) -> None:
    try:
        pid, warnings = prodsvc.save_product(db, user, draft, barcode=barcode)
    except ValueError as exc:
        await msg.answer(f"⛔ {esc(str(exc))}")
        return
    await msg.answer(_saved_text(repo.get_product(db, user["id"], pid), warnings))


def _saved_text(row, warnings: list[str]) -> str:
    name = esc(row["name_ru"]) + (f" · {esc(row['brand'])}" if row["brand"] else "")
    lines = [f"💾 Сохранил: <b>{name}</b>, {esc(dom.describe_per100(row))} на 100 г.",
             "Теперь буду считать по этим числам. Список и удаление: /products."]
    lines += [f"⚠️ {esc(w)}" for w in warnings]
    return "\n".join(lines)


# ------------------------------------------------------------------ исправление черновика

def apply_fix(draft: dict, text: str) -> tuple[dict, bool]:
    """Правка черновика текстом пользователя. → (новый черновик, что-то изменилось)."""
    new: dict = {}
    m = _NAME_FIX.match(text)
    if m:
        new["name"] = m.group(1)[:80]
    else:
        parsed = dom.parse_product_text(text)
        if parsed:
            for key in ("kcal", "protein", "fat", "carbs"):
                if parsed.get(key) is not None:
                    new[key] = parsed[key]
        bm = _BRAND_FIX.search(text)
        if bm:
            new["brand"] = bm.group(1).strip()[:40]
        pm = _PORTION.search(text)
        if pm:
            new["default_grams"] = float(pm.group(1).replace(",", "."))
            new.setdefault("unit_name", "порция")
            new["unit_grams"] = new["default_grams"]
        if not new and not re.search(r"\d", text) and len(text) <= 60 \
                and not _FOOD_VERBS.search(text) and not text.rstrip().endswith("?"):
            new["name"] = text.strip(" .")[:80]            # простой текст = новое название
    merged = dom.merge_draft(draft, new)
    return merged, merged != draft


async def handle_draft_text(msg: Message, db: sqlite3.Connection, user, text: str) -> bool:
    """Текст пока висит карточка в режиме «исправить»: это правка, а не еда. True — обработано."""
    row = repo.latest_pending_input(db, user["id"], "product_draft",
                                    max_age_min=DRAFT_MAX_AGE_MIN)
    if row is None:
        return False
    import json
    payload = json.loads(row["payload_json"])
    if not payload.get("editing"):
        return False
    if _CANCEL.match(text):
        repo.delete_pending_input(db, row["id"], user["id"])
        await msg.answer("Не сохранял.")
        return True
    draft, changed = apply_fix(payload["draft"], text)
    if not changed:
        await msg.answer("Не понял правку. " + esc(FIX_HINT))
        return True
    payload["draft"] = draft
    repo.update_pending_input(db, row["id"], user["id"], payload)
    await msg.answer(draft_text(draft, payload.get("notes")),
                     reply_markup=draft_kb(row["id"], draft))
    return True


# ------------------------------------------------------------------ этикетка

async def from_label_photo(msg: Message, db: sqlite3.Connection, user, image_b64: str,
                           status: Message, parsed=None) -> None:
    """Фото этикетки → карточка с распознанными числами (ничего не сохраняется само).

    parsed — уже запущенная задача parse_label (чтение шло параллельно с распознаванием еды)."""
    try:
        draft, conf, notes = await (parsed if parsed is not None
                                    else prodsvc.parse_label(db, user, image_b64))
    except Exception:                                        # noqa: BLE001
        log.warning("этикетка не прочитана", exc_info=True)
        await status.edit_text("Не смог прочитать этикетку (LLM недоступен). Попробуй позже "
                               "или добавь текстом: «добавь продукт: ...».")
        return
    if all(draft.get(k) is None for k in ("kcal", "protein", "fat", "carbs")):
        await status.edit_text("Не вижу таблицу пищевой ценности. Сфотографируй этикетку "
                               "ближе и без бликов или добавь текстом: «добавь продукт: ...».")
        return
    if conf < 0.6:
        notes = [*notes, f"уверенность {conf:.0%}, проверь числа"]
    await show_draft(msg, db, user, draft, notes=notes, status=status)


# ------------------------------------------------------------------ колбэки

def _user(cb: CallbackQuery, db: sqlite3.Connection):
    return repo.get_user_by_tg(db, cb.from_user.id)


def _draft_pending(db: sqlite3.Connection, user, pending_id: int):
    return repo.pending_input(db, pending_id, user["id"], "product_draft") if user else None


@router.callback_query(F.data.startswith("prod_save:"))
async def cb_save(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    import json
    user = _user(cb, db)
    pending = _draft_pending(db, user, int(cb.data.split(":")[1]))
    if not pending:
        await cb.answer("Это уже неактуально", show_alert=True)
        return
    payload = json.loads(pending["payload_json"])
    try:
        pid, warnings = prodsvc.save_product(db, user, payload["draft"],
                                             barcode=payload.get("barcode"))
    except ValueError as exc:
        await cb.answer(str(exc)[:190], show_alert=True)
        return
    repo.delete_pending_input(db, pending["id"], user["id"])
    await cb.message.edit_text(_saved_text(repo.get_product(db, user["id"], pid), warnings))
    await cb.answer("Сохранил")


@router.callback_query(F.data.startswith("prod_edit:"))
async def cb_edit(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    import json
    user = _user(cb, db)
    pending = _draft_pending(db, user, int(cb.data.split(":")[1]))
    if not pending:
        await cb.answer("Это уже неактуально", show_alert=True)
        return
    payload = json.loads(pending["payload_json"])
    payload["editing"] = True
    repo.update_pending_input(db, pending["id"], user["id"], payload)
    await cb.message.answer(esc(FIX_HINT))
    await cb.answer()


@router.callback_query(F.data.startswith("prod_cancel:"))
async def cb_cancel(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _user(cb, db)
    if not user or not repo.delete_pending_input(db, int(cb.data.split(":")[1]), user["id"]):
        await cb.answer("Это уже неактуально", show_alert=True)
        return
    await cb.message.edit_text("Не сохранял.")
    await cb.answer()


@router.callback_query(F.data.startswith("prod_del:"))
async def cb_delete(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    user = _user(cb, db)
    if not user or not repo.delete_product(db, user["id"], int(cb.data.split(":")[1])):
        await cb.answer("Уже удалено", show_alert=True)
        return
    rows = repo.list_products(db, user["id"])
    try:
        await cb.message.edit_text(list_text(rows), reply_markup=list_kb(rows))
    except Exception:                                        # noqa: BLE001
        log.debug("список продуктов не перерисован", exc_info=True)
    await cb.answer("Удалил")


@router.callback_query(F.data.startswith("item_save:"))
async def cb_item_save(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    """«💾 В мои продукты» под позицией записи еды."""
    user = _user(cb, db)
    if not user:
        await cb.answer("Сначала /start", show_alert=True)
        return
    try:
        pid, warnings = prodsvc.product_from_item(db, user, int(cb.data.split(":")[1]))
    except ValueError as exc:
        await cb.answer(str(exc)[:190], show_alert=True)
        return
    await cb.message.answer(_saved_text(repo.get_product(db, user["id"], pid), warnings))
    await cb.answer("Сохранил")


@router.callback_query(F.data.startswith("bc_save:"))
async def cb_barcode_save(cb: CallbackQuery, db: sqlite3.Connection) -> None:
    """«💾 Сохранить себе» под продуктом, найденным по штрихкоду."""
    user = _user(cb, db)
    if not user:
        await cb.answer("Сначала /start", show_alert=True)
        return
    try:
        pid, warnings = prodsvc.product_from_food(db, user, int(cb.data.split(":")[1]))
    except ValueError as exc:
        await cb.answer(str(exc)[:190], show_alert=True)
        return
    await cb.message.answer(_saved_text(repo.get_product(db, user["id"], pid), warnings))
    await cb.answer("Сохранил")
