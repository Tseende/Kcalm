"""Строки БД → JSON-объекты контракта docs/V2_API.md (Item, Meal, Dish, Product, Targets)."""
from __future__ import annotations

import sqlite3

from app.services import water as watersvc
from app.storage import repo


def _num(value, ndigits: int = 1):
    return None if value is None else round(float(value), ndigits)


def _int(value):
    return None if value is None else int(round(float(value)))


def _get(row, *keys):
    """Первое найденное поле из sqlite3.Row или dict (имена в сервисах ещё могут меняться)."""
    for key in keys:
        try:
            value = row[key]
        except (KeyError, IndexError, TypeError):
            continue
        return value
    return None


def item_json(row: sqlite3.Row) -> dict:
    per100 = None
    if row["source"] != "unresolved" and row["kcal_100g"] is not None:
        per100 = {"kcal": _num(row["kcal_100g"]), "protein": _num(row["protein_100g"] or 0),
                  "fat": _num(row["fat_100g"] or 0), "carbs": _num(row["carbs_100g"] or 0)}
    return {"id": row["id"], "name": row["name_ru"], "grams": _num(row["grams"]),
            "kcal": int(row["kcal"]), "protein": _num(row["protein_g"]),
            "fat": _num(row["fat_g"]), "carbs": _num(row["carbs_g"]),
            "per100": per100, "confidence": _num(row["confidence"], 2),
            "edited": bool(row["edited_by_user"]), "source": row["source"]}


def meal_json(db: sqlite3.Connection, meal: sqlite3.Row, *, item_rows=None, photo_rows=None) -> dict:
    if photo_rows is None:
        photo_rows = repo.meal_photos(db, meal["id"])
    if item_rows is None:
        item_rows = repo.get_meal_items(db, meal["id"])
    photos = [{"id": p["id"], "url": f"/api/photos/{p['id']}", "dish_id": p["dish_id"]}
              for p in photo_rows]
    kcal = int(meal["kcal_total"])
    # правка граммов пересчитывает итог, но не диапазон: не отдаём диапазон, не содержащий итог
    low, high = min(int(meal["kcal_low"]), kcal), max(int(meal["kcal_high"]), kcal)
    items = [item_json(i) for i in item_rows]
    unresolved = sum(i["source"] == "unresolved" for i in items)
    return {"id": meal["id"], "date": meal["local_date"], "time": meal["local_time"],
            "type": meal["meal_type"], "source": meal["source"],
            "kcal": kcal, "kcal_low": low, "kcal_high": high, "protein": _num(meal["protein_g"]),
            "fat": _num(meal["fat_g"]), "carbs": _num(meal["carbs_g"]),
            "confidence": _num(meal["confidence"], 2), "photos": photos,
            "items": items, **({"unresolved_items": unresolved} if unresolved else {}),
            "comment": meal["transcript"] or None}


DAY_ITEM_SQL = """SELECT i.* FROM meal_items i JOIN meals m ON m.id=i.meal_id
WHERE m.user_id=? AND m.local_date=? AND m.deleted_at IS NULL ORDER BY i.id"""
DAY_PHOTO_SQL = """SELECT p.* FROM meal_photos p JOIN meals m ON m.id=p.meal_id
WHERE m.user_id=? AND m.local_date=? AND m.deleted_at IS NULL ORDER BY p.position, p.id"""


def day_meals_json(db: sqlite3.Connection, meals, item_rows, photo_rows) -> list[dict]:
    items, photos = {}, {}
    for rows, groups in ((item_rows, items), (photo_rows, photos)):
        for row in rows:
            groups.setdefault(row["meal_id"], []).append(row)
    return [meal_json(db, m, item_rows=items.get(m["id"], ()), photo_rows=photos.get(m["id"], ()))
            for m in meals]


def dish_json(dish: sqlite3.Row) -> dict:
    def size(key):
        value = dish[key]
        if value is None:
            return None
        return int(value) if float(value).is_integer() and key == "volume_ml" else round(float(value), 1)
    return {"id": dish["id"], "name": dish["name"], "kind": dish["kind"],
            "diameter_cm": size("diameter_cm"), "width_cm": size("width_cm"), "depth_cm": size("depth_cm"),
            "volume_ml": size("volume_ml"),
            "photo_url": f"/api/dishes/{dish['id']}/photo" if dish["photo_path"] else None,
            "is_default": bool(dish["is_default"])}


def product_json(product) -> dict:
    """Продукт из app.services.products (Row или dict; имена полей допускаются двух видов)."""
    return {"id": _get(product, "id"),
            "name": _get(product, "name", "name_ru"),
            "brand": _get(product, "brand"),
            "kcal": _int(_get(product, "kcal", "kcal_100g")),
            "protein": _num(_get(product, "protein", "protein_100g")),
            "fat": _num(_get(product, "fat", "fat_100g")),
            "carbs": _num(_get(product, "carbs", "carbs_100g")),
            "default_grams": _int(_get(product, "default_grams")),
            "unit_name": _get(product, "unit_name"),
            "unit_grams": _int(_get(product, "unit_grams")),
            "barcode": _get(product, "barcode"),
            "icon": _get(product, "icon"),
            "times_used": int(_get(product, "times_used") or 0)}


def target_values(user, target, water_goal: int) -> dict:
    if user["onboarding_skipped"]:
        return {"kcal": None, "protein": None, "fat": None, "carbs": None, "water_ml": water_goal}
    return {"kcal": int(target["kcal_target"]) if target else 0,
            "protein": _int(target["protein_g"] or 0) if target else 0,
            "fat": _int(target["fat_g"] or 0) if target else 0,
            "carbs": _int(target["carbs_g"] or 0) if target else 0,
            "water_ml": water_goal}


def targets_json(db: sqlite3.Connection, user: sqlite3.Row) -> dict:
    target = None if user["onboarding_skipped"] else repo.current_target(db, user["id"])
    return target_values(user, target, watersvc.goal_for(db, user))


def food_json(food: dict) -> dict:
    """Результат поиска /api/foods/search: свой продукт или общий справочник, КБЖУ на 100 г."""
    return {"id": food["id"], "name": food["name_ru"], "brand": food["brand"],
            "kcal": _num(food["kcal_100g"]), "protein": _num(food["protein_100g"]),
            "fat": _num(food["fat_100g"]), "carbs": _num(food["carbs_100g"]),
            "default_grams": _int(food["default_grams"]), "unit_name": food["unit_name"],
            "unit_grams": _int(food["unit_grams"]), "own": bool(food["own"]),
            "source": food["source"]}
