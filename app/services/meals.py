"""Приём пищи по фото без aiogram: распознать, записать, пересчитать.

Ядро для чат-роутера (routers/intake.py) и будущего веб-API: ни Message, ни клавиатур.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain import mealtime
from app.services import food as foodsvc
from app.storage import repo

log = logging.getLogger(__name__)

_UNSET = object()


class NoFood(Exception):
    """На фото (и в подписи) не нашлось еды: запись не создана и не изменена."""


class NoPhotos(Exception):
    """У приёма нет читаемых фото."""


@dataclass
class SavedMeal:
    meal_id: int
    calc: object
    local_date: str
    local_time: str
    meal_type: str


# ------------------------------------------------------------------ время

def utc_of(tz_name: str, local_date: str, local_time: str) -> str:
    """Локальные дата и время пользователя → UTC-строка для meals.eaten_at_utc."""
    naive = datetime.strptime(f"{local_date} {local_time}", "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


def resolve_when(user, text: str | None, taken_at: datetime | None = None):
    """Когда была еда: указание в тексте главнее EXIF, EXIF главнее «сейчас» (None)."""
    now = foodsvc.local_now(user["tz"])
    found = mealtime.parse_mealtime(text, now)
    if found:
        return found
    if taken_at:
        return mealtime.MealTime(taken_at.strftime("%Y-%m-%d"), taken_at.strftime("%H:%M"))
    return None


def set_meal_time(db: sqlite3.Connection, user, meal_id: int, when, fallback_time: str) -> None:
    local_time = when.local_time or fallback_time
    meal_type = when.meal_type or foodsvc.guess_meal_type(None, local_time, None)
    repo.update_meal_time(db, user["id"], meal_id, when.local_date, local_time, meal_type,
                          utc_of(user["tz"], when.local_date, local_time))


# ------------------------------------------------------------------ распознавание

def merge_understandings(parts: list):
    """Объединить распознавание нескольких фото в один приём пищи (запасной путь)."""
    if len(parts) == 1:
        return parts[0]
    merged = parts[0].model_copy(deep=True)
    for u in parts[1:]:
        merged.items += u.items[:max(0, foodsvc.MAX_ITEMS - len(merged.items))]
        merged.assumptions += [a for a in u.assumptions if a not in merged.assumptions]
        if not merged.clarification_question and u.clarification_question:
            merged.clarification_question = u.clarification_question
            merged.clarification_options = list(u.clarification_options)
    merged.overall_confidence = min(u.overall_confidence for u in parts)
    return merged


async def _parse_each(images_b64: list[str], caption: str | None, uid: int,
                      db: sqlite3.Connection, dishes: list):
    """По одному запросу на фото и слияние: запасной путь для многофото."""
    def one(b64, dish):
        kw = {"dishes": dish} if dish is not None else {}
        return foodsvc.parse_photo(b64, caption, uid, db, **kw)

    parts = await asyncio.gather(*[one(b, d) for b, d in zip(images_b64, dishes)])
    return merge_understandings([p for p in parts if p.items] or [parts[0]])


async def parse_images(images_b64: list[str], caption: str | None, uid: int,
                       db: sqlite3.Connection, dishes=None, groups: list[int] | None = None):
    """Распознать фото одного приёма. Одно фото — parse_photo; два и больше — все в одном
    запросе (parse_photos); сбой или невалидный ответ — запасной путь «по одному»."""
    dish_list = foodsvc._as_dish_list(dishes, len(images_b64))
    if len(images_b64) == 1:
        kw = {"dishes": dish_list[0]} if dish_list[0] is not None else {}
        return await foodsvc.parse_photo(images_b64[0], caption, uid, db, **kw)
    grp = groups if groups and len(groups) == len(images_b64) else [None] * len(images_b64)
    step = foodsvc.MAX_PHOTOS
    chunks = [(images_b64[i:i + step], dish_list[i:i + step], grp[i:i + step])
              for i in range(0, len(images_b64), step)]
    parts = []
    for imgs, dsh, grs in chunks:
        try:
            parts.append(await foodsvc.parse_photos(
                imgs, caption, uid, db, dishes=dsh if any(d is not None for d in dsh) else None,
                groups=None if None in grs else grs))
        except Exception:                                    # noqa: BLE001
            log.warning("запрос по нескольким фото не удался, разбираю по одному", exc_info=True)
            parts.append(await _parse_each(imgs, caption, uid, db, dsh))
    return merge_understandings(parts)


def read_images_b64(paths: list[str]) -> tuple[list[str], list[int]]:
    """Прочитать файлы фото: (base64 читаемых, их индексы в paths)."""
    out, idx = [], []
    for i, path in enumerate(paths):
        try:
            out.append(base64.b64encode(Path(path).read_bytes()).decode())
            idx.append(i)
        except OSError:
            log.warning("файл фото не читается: %s", path)
    return out, idx


def resolve_dishes(db: sqlite3.Connection, user_id: int, dish_ids, n: int) -> list:
    """Посуда под каждое из n фото. dish_ids=None — посуда по умолчанию на все фото;
    список — явный выбор (None в списке: без посуды). Чужие и удалённые id игнорируются."""
    if dish_ids is None:
        default = repo.default_dish(db, user_id)
        return [default] * n
    ids = (list(dish_ids) + [None] * n)[:n]
    return [repo.get_dish(db, user_id, i) if i else None for i in ids]


# ------------------------------------------------------------------ запись

async def save_understanding(db: sqlite3.Connection, user, u, *, text: str | None,
                             source: str, photo_paths: list[str] | None = None,
                             photo_path: str | None = None,
                             dish_ids: list[int | None] | None = None,
                             transcript: str | None = None, calc=None,
                             taken_at: datetime | None = None) -> SavedMeal:
    """Посчитать и записать приём пищи. u.items должны быть непустыми."""
    uid = user["id"]
    now_local = foodsvc.local_now(user["tz"])
    local_date = now_local.strftime("%Y-%m-%d")
    local_time = now_local.strftime("%H:%M")
    meal_type = None
    # «в обед», «вчера вечером», «в 13:30», «час назад» в тексте или время съёмки из EXIF
    when = resolve_when(user, text or transcript, taken_at)
    if when:
        local_date, local_time, meal_type = when.local_date, when.local_time, when.meal_type
    if calc is None:
        calc = await foodsvc.build_calc(u, db, uid)
    meal_type = meal_type or foodsvc.guess_meal_type(u, local_time, None)
    meal_id = repo.save_meal(
        db, uid, utc_of(user["tz"], local_date, local_time),
        local_date, local_time, meal_type, source, calc,
        photo_path=photo_path, transcript=transcript or text,
        photo_paths=photo_paths, dish_ids=dish_ids,
    )
    touch_foods(db, uid, u, calc)
    return SavedMeal(meal_id, calc, local_date, local_time, meal_type)


def repeat_meal(db: sqlite3.Connection, user, meal_id: int) -> int | None:
    """«Как обычно»: повторить частое блюдо на текущее время. None — образца нет."""
    now_local = foodsvc.local_now(user["tz"])
    local_date, local_time = now_local.strftime("%Y-%m-%d"), now_local.strftime("%H:%M")
    return repo.repeat_meal(
        db, user["id"], meal_id, utc_of(user["tz"], local_date, local_time), local_date,
        local_time, foodsvc.guess_meal_type(None, local_time, None))


def touch_foods(db: sqlite3.Connection, uid: int, u, calc) -> None:
    """Личные пресеты: чаще используемые продукты и названные граммы запоминаются."""
    for estimate, item in zip(u.items, calc.items):
        if item.food_ref_id:
            repo.touch_user_food(db, uid, item.food_ref_id, estimate.explicit_grams)


async def recognize_and_save(db: sqlite3.Connection, user, image_paths: list[str],
                             dish_ids: list[int | None] | None = None,
                             comment: str | None = None,
                             local_dt: datetime | None = None,
                             groups: list[int] | None = None) -> int:
    """Фото (1–6) → один приём пищи. groups[i] — номер блюда, к которому относится фото i. Возвращает meal_id.

    dish_ids: None — посуда по умолчанию, список под фото — явный выбор (None: без посуды).
    local_dt — наивное локальное время приёма (EXIF, поле формы); время из comment главнее.
    Исключения: NoPhotos, NoFood, ошибки LLM пробрасываются как есть."""
    uid = user["id"]
    b64, idx = read_images_b64(image_paths)
    if not b64:
        raise NoPhotos("фото не прочитались")
    paths = [image_paths[i] for i in idx]
    dishes = resolve_dishes(db, uid, dish_ids, len(image_paths))
    dishes = [dishes[i] for i in idx]
    if groups and len(groups) == len(image_paths):
        groups = [groups[i] for i in idx]
    else:
        groups = None
    u = await parse_images(b64, comment, uid, db, dishes, groups)
    if not u.items:
        raise NoFood("не увидел еды на фото")
    saved = await save_understanding(
        db, user, u, text=comment, source="combo" if comment else "photo",
        photo_paths=paths, dish_ids=[d["id"] if d is not None else None for d in dishes],
        taken_at=local_dt)
    return saved.meal_id


async def rerecognize_meal(db: sqlite3.Connection, user, meal_id: int,
                           comment: str | None = None, *, dish_id=_UNSET,
                           extra_photos: list[str] | None = None,
                           extra_dish_id: int | None = None):
    """Пересчитать приём пищи по ВСЕМ его фото заново (подпись, новая посуда, ещё фото).

    comment — новый комментарий (по умолчанию прежний). dish_id — одна посуда на все фото
    (None: снять). extra_photos — новые фото: записываются в meal_photos только после
    успешного пересчёта. Позиции и итоги заменяются в той же записи (id, время сохраняются).
    Возвращает понимание модели (для допущений и вопроса в карточке).
    Исключения: LookupError (нет такого приёма), NoPhotos, NoFood (приём не изменён)."""
    uid = user["id"]
    meal = repo.get_meal(db, uid, meal_id)
    if not meal or meal["deleted_at"]:
        raise LookupError("приём не найден")
    rows = repo.meal_photos(db, meal_id)
    paths = [r["path"] for r in rows]
    ids = [r["dish_id"] for r in rows]
    override = dish_id is not _UNSET
    if override:
        ids = [dish_id] * len(paths)
    new_paths = [p for p in dict.fromkeys(extra_photos or []) if p not in paths]
    new_ids = [dish_id if override else extra_dish_id] * len(new_paths)
    all_paths, all_ids = paths + new_paths, ids + new_ids
    b64, idx = read_images_b64(all_paths)
    if not b64:
        raise NoPhotos("фото не прочитались")
    dishes = [repo.get_dish(db, uid, all_ids[i], include_deleted=True) for i in idx]
    caption = comment if comment is not None else meal["transcript"]
    u = await parse_images(b64, caption, uid, db, dishes)
    if not u.items:
        raise NoFood("не увидел еды на фото")
    calc = await foodsvc.build_calc(u, db, uid)
    repo.replace_meal_items(db, uid, meal_id, calc,
                            source="combo" if comment else None, transcript=comment)
    readable = {all_paths[i] for i in idx}
    added = [(p, d) for p, d in zip(new_paths, new_ids) if p in readable]
    if added:
        repo.add_meal_photos(db, meal_id, [p for p, _ in added], dish_ids=[d for _, d in added])
    if override:
        repo.set_meal_photos_dish(db, meal_id, dish_id)
    touch_foods(db, uid, u, calc)
    return u
