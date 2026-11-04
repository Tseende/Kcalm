"""Репозитории SQLite. Все функции принимают соединение; тесты — на временной БД."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.domain.nutrition import MealCalculation


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------- users

def get_user_by_tg(conn: sqlite3.Connection, tg_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()


def create_user(conn: sqlite3.Connection, tg_id: int, **fields) -> int:
    cur = conn.execute("INSERT INTO users (tg_id) VALUES (?)", (tg_id,))
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), cur.lastrowid))
    conn.commit()
    return cur.lastrowid


def update_user(conn: sqlite3.Connection, user_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))
    conn.commit()


def current_target(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM targets_history WHERE user_id = ? ORDER BY valid_from DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def insert_target(conn: sqlite3.Connection, user_id: int, valid_from: str,
                  kcal_target: int, protein_g: float, fat_g: float, carbs_g: float,
                  tdee_est: int | None, reason: str) -> None:
    conn.execute(
        """INSERT INTO targets_history (user_id, valid_from, kcal_target, protein_g,
           fat_g, carbs_g, tdee_est, reason) VALUES (?,?,?,?,?,?,?,?)""",
        (user_id, valid_from, kcal_target, protein_g, fat_g, carbs_g, tdee_est, reason),
    )
    conn.commit()


# ------------------------------------------------------------------- foods

def upsert_food(conn: sqlite3.Connection, name_ru: str, name_en: str, kcal: float,
                protein: float, fat: float, carbs: float, source: str,
                external_id: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO foods (name_ru, name_en, kcal_100g, protein_100g, fat_100g,
           carbs_100g, source, external_id) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(source, external_id) DO UPDATE SET
             name_ru=excluded.name_ru, kcal_100g=excluded.kcal_100g
           WHERE external_id IS NOT NULL""",
        (name_ru, name_en, kcal, protein, fat, carbs, source, external_id),
    )
    if external_id is None:
        conn.commit()
        return cur.lastrowid
    row = conn.execute("SELECT id FROM foods WHERE source=? AND external_id=?",
                       (source, external_id)).fetchone()
    conn.commit()
    return row["id"]


def add_alias(conn: sqlite3.Connection, user_id: int, food_id: int, alias: str) -> None:
    conn.execute("""INSERT OR IGNORE INTO food_aliases (user_id, food_id, alias_text)
                    VALUES (?,?,?)""", (user_id, food_id, alias))
    conn.commit()


def touch_user_food(conn: sqlite3.Connection, user_id: int, food_id: int,
                    default_grams: float | None = None) -> None:
    """Персональный пресет порции: правка граммов обучает следующий расчёт."""
    row = conn.execute("SELECT * FROM user_foods WHERE user_id=? AND food_id=?",
                       (user_id, food_id)).fetchone()
    if row:
        grams = default_grams if default_grams is not None else row["default_grams"]
        conn.execute(
            """UPDATE user_foods SET times_used = times_used + 1, last_used_at = ?,
               default_grams = ? WHERE id = ?""",
            (utcnow_iso(), grams, row["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO user_foods (user_id, food_id, default_grams, times_used,
               last_used_at) VALUES (?,?,?,1,?)""",
            (user_id, food_id, default_grams, utcnow_iso()),
        )
    conn.commit()


def user_default_grams(conn: sqlite3.Connection, user_id: int, food_id: int) -> float | None:
    row = conn.execute("SELECT default_grams FROM user_foods WHERE user_id=? AND food_id=?",
                       (user_id, food_id)).fetchone()
    if row and row["default_grams"]:
        return row["default_grams"]
    own = conn.execute("SELECT default_grams FROM foods WHERE id=? AND owner_user_id=?",
                       (food_id, user_id)).fetchone()          # порция, заданная в своём продукте
    return own["default_grams"] if own and own["default_grams"] else None


# ------------------------------------------------------------------- свои продукты

PRODUCT_FIELDS = ("name", "brand", "kcal", "protein", "fat", "carbs", "default_grams",
                  "unit_name", "unit_grams", "barcode", "icon")
_PRODUCT_COLS = {"name": "name_ru", "brand": "brand", "kcal": "kcal_100g",
                 "protein": "protein_100g", "fat": "fat_100g", "carbs": "carbs_100g",
                 "default_grams": "default_grams", "unit_name": "unit_name",
                 "unit_grams": "unit_grams", "barcode": "barcode", "icon": "icon"}


def _product_aliases(name: str, brand: str | None) -> list[str]:
    from app.domain.matching import normalize
    out = [normalize(name)]
    if brand:
        out.append(normalize(f"{name} {brand}"))
    return [a for a in dict.fromkeys(out) if a]


def _set_product_aliases(conn: sqlite3.Connection, user_id: int, food_id: int,
                         name: str, brand: str | None) -> None:
    conn.execute("DELETE FROM food_aliases WHERE user_id=? AND food_id=?", (user_id, food_id))
    for alias in _product_aliases(name, brand):
        conn.execute(
            """INSERT INTO food_aliases (user_id, food_id, alias_text) VALUES (?,?,?)
               ON CONFLICT(user_id, alias_text) DO UPDATE SET food_id=excluded.food_id""",
            (user_id, food_id, alias))


def _check_product(fields: dict) -> None:
    from app.domain import products as prod
    prod.check_values(fields["kcal"], fields["protein"], fields["fat"], fields["carbs"])
    for key in ("default_grams", "unit_grams"):
        value = fields.get(key)
        if value is not None and not 0 < value <= 5000:
            raise ValueError("Граммы должны быть от 1 до 5000.")


def create_product(conn: sqlite3.Connection, user_id: int, name: str, kcal: float,
                   protein: float, fat: float, carbs: float, brand: str | None = None,
                   default_grams: float | None = None, unit_name: str | None = None,
                   unit_grams: float | None = None, barcode: str | None = None,
                   icon: str | None = None) -> int:
    """Свой продукт (КБЖУ на 100 г). ValueError — значения вне допустимых границ."""
    import uuid
    name = (name or "").strip()
    if not name:
        raise ValueError("Нужно название продукта.")
    fields = {"kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs,
              "default_grams": default_grams, "unit_grams": unit_grams}
    _check_product(fields)
    brand = (brand or "").strip() or None
    full_name = f"{name} {brand}" if brand else name
    cur = conn.execute(
        """INSERT INTO foods (name_ru, name_en, kcal_100g, protein_100g, fat_100g, carbs_100g,
           source, barcode, external_id, owner_user_id, default_grams, unit_name, unit_grams,
           brand, created_at, icon) VALUES (?,?,?,?,?,?, 'user', ?,?,?,?,?,?,?,?,?)""",
        (name, full_name, kcal, protein, fat, carbs, barcode,
         f"u{user_id}-{uuid.uuid4().hex}", user_id, default_grams, unit_name, unit_grams,
         brand, utcnow_iso(), icon))
    food_id = cur.lastrowid
    _set_product_aliases(conn, user_id, food_id, name, brand)
    conn.commit()
    return food_id


def get_product(conn: sqlite3.Connection, user_id: int, product_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM foods WHERE id=? AND owner_user_id=? AND deleted_at IS NULL""",
        (product_id, user_id)).fetchone()


def update_product(conn: sqlite3.Connection, user_id: int, product_id: int,
                   **fields) -> sqlite3.Row | None:
    """Меняет переданные поля (ключи как у create_product). None — продукта нет или он чужой."""
    row = get_product(conn, user_id, product_id)
    if row is None:
        return None
    unknown = set(fields) - set(PRODUCT_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные поля: {', '.join(sorted(unknown))}")
    merged = {
        "name": row["name_ru"], "brand": row["brand"], "kcal": row["kcal_100g"],
        "protein": row["protein_100g"], "fat": row["fat_100g"], "carbs": row["carbs_100g"],
        "default_grams": row["default_grams"], "unit_name": row["unit_name"],
        "unit_grams": row["unit_grams"], "barcode": row["barcode"], "icon": row["icon"],
    }
    merged.update(fields)
    merged["name"] = (merged["name"] or "").strip()
    if not merged["name"]:
        raise ValueError("Нужно название продукта.")
    merged["brand"] = (merged["brand"] or "").strip() or None
    _check_product(merged)
    full_name = f"{merged['name']} {merged['brand']}" if merged["brand"] else merged["name"]
    conn.execute(
        """UPDATE foods SET name_ru=?, name_en=?, brand=?, kcal_100g=?, protein_100g=?,
           fat_100g=?, carbs_100g=?, default_grams=?, unit_name=?, unit_grams=?, barcode=?, icon=?
           WHERE id=? AND owner_user_id=?""",
        (merged["name"], full_name, merged["brand"], merged["kcal"], merged["protein"],
         merged["fat"], merged["carbs"], merged["default_grams"], merged["unit_name"],
         merged["unit_grams"], merged["barcode"], merged["icon"], product_id, user_id))
    _set_product_aliases(conn, user_id, product_id, merged["name"], merged["brand"])
    conn.commit()
    return get_product(conn, user_id, product_id)


def delete_product(conn: sqlite3.Connection, user_id: int, product_id: int) -> bool:
    """Мягкое удаление: старые приёмы остаются, в поиск продукт больше не попадает."""
    cur = conn.execute(
        """UPDATE foods SET deleted_at=? WHERE id=? AND owner_user_id=? AND deleted_at IS NULL""",
        (utcnow_iso(), product_id, user_id))
    if cur.rowcount:
        conn.execute("DELETE FROM food_aliases WHERE user_id=? AND food_id=?",
                     (user_id, product_id))
    conn.commit()
    return cur.rowcount > 0


def list_products(conn: sqlite3.Connection, user_id: int, q: str | None = None,
                  limit: int = 50) -> list[sqlite3.Row]:
    """Свои продукты: чаще используемые выше, потом по алфавиту. ``q`` — подстрока в названии
    или бренде (без учёта регистра, в том числе кириллицы)."""
    rows = conn.execute(
        """SELECT f.*, COALESCE(uf.times_used, 0) AS times_used FROM foods f
           LEFT JOIN user_foods uf ON uf.food_id = f.id AND uf.user_id = f.owner_user_id
           WHERE f.owner_user_id=? AND f.deleted_at IS NULL""", (user_id,)).fetchall()
    needle = (q or "").strip().lower().replace("ё", "е")
    if needle:
        rows = [r for r in rows
                if needle in f"{r['name_ru']} {r['brand'] or ''}".lower().replace("ё", "е")]
    rows.sort(key=lambda r: (-r["times_used"], (r["name_ru"] or "").lower()))
    return rows[:limit]


def owned_meal_item(conn: sqlite3.Connection, user_id: int, item_id: int) -> sqlite3.Row | None:
    """Позиция приёма, который принадлежит пользователю."""
    return conn.execute(
        """SELECT i.* FROM meal_items i JOIN meals m ON m.id = i.meal_id
           WHERE i.id=? AND m.user_id=?""", (item_id, user_id)).fetchone()


# ------------------------------------------------------------------- meals

def _insert_items(conn: sqlite3.Connection, meal_id: int, calc: MealCalculation) -> None:
    for it in calc.items:
        snapshot = json.dumps({
            "kcal": it.kcal, "protein": it.protein_g, "fat": it.fat_g,
            "carbs": it.carbs_g, "basis": "kcal_100g",
            "kcal_100g": it.kcal_100g, "source": it.source,
        }, ensure_ascii=False)
        conn.execute(
            """INSERT INTO meal_items (meal_id, name_ru, name_en, grams, kcal, protein_g,
               fat_g, carbs_g, food_ref_id, source, confidence, snapshot_json,
               kcal_100g, protein_100g, fat_100g, carbs_100g)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (meal_id, it.name_ru, it.name_en, it.grams, it.kcal, it.protein_g,
             it.fat_g, it.carbs_g, it.food_ref_id, it.source, it.confidence, snapshot,
             it.kcal_100g, it.protein_100g, it.fat_100g, it.carbs_100g),
        )


def save_meal(conn: sqlite3.Connection, user_id: int, eaten_utc: str, local_date: str,
              local_time: str, meal_type: str, source: str, calc: MealCalculation,
              photo_path: str | None = None, transcript: str | None = None,
              photo_paths: list[str] | None = None,
              dish_ids: list[int | None] | None = None) -> int:
    """photo_paths — все фото приёма по порядку (альбом); photo_path остаётся первым.
    dish_ids — посуда каждого фото (того же порядка), None — без посуды."""
    paths = [p for p in (photo_paths or ([photo_path] if photo_path else [])) if p]
    photo_path = photo_path or (paths[0] if paths else None)
    cur = conn.execute(
        """INSERT INTO meals (user_id, eaten_at_utc, local_date, local_time, meal_type,
           source, photo_path, transcript, kcal_total, protein_g, fat_g, carbs_g,
           kcal_low, kcal_high, confidence, method)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, eaten_utc, local_date, local_time, meal_type, source, photo_path,
         transcript, calc.kcal_total, calc.protein_g, calc.fat_g, calc.carbs_g,
         calc.kcal_low, calc.kcal_high, calc.confidence, calc.method),
    )
    meal_id = cur.lastrowid
    _insert_items(conn, meal_id, calc)
    add_meal_photos(conn, meal_id, paths, dish_ids=dish_ids, commit=False)
    sync_meal_water(conn, meal_id)
    conn.commit()
    return meal_id


def frequent_meals(conn: sqlite3.Connection, user_id: int, limit: int = 6,
                   days: int = 60, today: str | None = None) -> list[dict]:
    """Блюда, которые человек ест чаще всего: записи с одним составом считаются одним блюдом.

    Возвращает от частого к редкому (при равенстве — свежее первым):
    {meal_id (последняя запись), count, kcal, names}. Напитки без калорий не в счёт."""
    rows = conn.execute(
        """SELECT id, kcal_total FROM meals WHERE user_id=? AND deleted_at IS NULL
           AND kcal_total>=50 AND local_date >= date(COALESCE(?, 'now'), ?)
           ORDER BY id DESC LIMIT 150""",
        (user_id, today, f"-{int(days)} days")).fetchall()
    # позиции всех приёмов одним запросом (вместо запроса на каждый приём), порядок как в get_meal_items
    items_by_meal: dict[int, list] = {}
    if rows:
        marks = ",".join("?" * len(rows))
        for i in conn.execute(
                f"SELECT meal_id, name_ru FROM meal_items WHERE meal_id IN ({marks}) ORDER BY id",
                [r["id"] for r in rows]).fetchall():
            items_by_meal.setdefault(i["meal_id"], []).append(i)
    groups: dict[str, dict] = {}
    for r in rows:
        names = [i["name_ru"].strip() for i in items_by_meal.get(r["id"], [])]
        if not names:
            continue
        sig = "|".join(sorted(n.lower() for n in names))
        g = groups.get(sig)
        if g is None:
            groups[sig] = {"meal_id": r["id"], "count": 1, "kcal": r["kcal_total"],
                           "names": names, "order": len(groups)}
        else:
            g["count"] += 1
    ranked = sorted(groups.values(), key=lambda g: (-g["count"], g["order"]))
    return [{k: v for k, v in g.items() if k != "order"} for g in ranked[:limit]]


def repeat_meal(conn: sqlite3.Connection, user_id: int, meal_id: int, eaten_utc: str,
                local_date: str, local_time: str, meal_type: str,
                allow_deleted: bool = False) -> int | None:
    """Записать ещё раз такой же приём пищи (состав и граммы как в образце) на новое время.

    allow_deleted: образец избранного живёт, даже если исходную запись убрали из дневника."""
    src = get_meal(conn, user_id, meal_id)
    if not src or (src["deleted_at"] and not allow_deleted):
        return None
    cur = conn.execute(
        """INSERT INTO meals (user_id, eaten_at_utc, local_date, local_time, meal_type,
           source, transcript, kcal_total, protein_g, fat_g, carbs_g,
           kcal_low, kcal_high, confidence, method)
           VALUES (?,?,?,?,?,'manual','повтор частого блюда',?,?,?,?,?,?,?,?)""",
        (user_id, eaten_utc, local_date, local_time, meal_type, src["kcal_total"],
         src["protein_g"], src["fat_g"], src["carbs_g"], src["kcal_low"], src["kcal_high"],
         src["confidence"], src["method"]))
    new_id = cur.lastrowid
    cols = [r[1] for r in conn.execute("PRAGMA table_info(meal_items)")
            if r[1] not in ("id", "meal_id")]
    names = ", ".join(cols)
    conn.execute(f"INSERT INTO meal_items (meal_id, {names}) SELECT ?, {names} "
                 "FROM meal_items WHERE meal_id=?", (new_id, meal_id))
    sync_meal_water(conn, new_id)
    conn.commit()
    return new_id


# ------------------------------------------------------------------ избранные блюда

def meal_signature(names: list[str]) -> str:
    """Подпись состава: одинаковый набор названий (в любом порядке и регистре) — одно блюдо."""
    return "|".join(sorted(n.strip().lower() for n in names))


def _meal_names(conn: sqlite3.Connection, meal_id: int) -> list[str]:
    return [r["name_ru"].strip() for r in conn.execute(
        "SELECT name_ru FROM meal_items WHERE meal_id=? ORDER BY id", (meal_id,))]


def favorite_for_meal(conn: sqlite3.Connection, user_id: int, meal_id: int) -> sqlite3.Row | None:
    """Избранное с тем же составом, что у приёма (или None)."""
    names = _meal_names(conn, meal_id)
    if not names:
        return None
    return conn.execute("SELECT * FROM favorite_meals WHERE user_id=? AND sig=?",
                        (user_id, meal_signature(names))).fetchone()


def add_favorite(conn: sqlite3.Connection, user_id: int, meal_id: int) -> int | None:
    """Приём → избранное. Повтор состава не двоит запись. None — приёма нет или в нём нет позиций."""
    meal = get_meal(conn, user_id, meal_id)
    if not meal or meal["deleted_at"]:
        return None
    names = _meal_names(conn, meal_id)
    if not names:
        return None
    sig = meal_signature(names)
    cur = conn.execute("INSERT OR IGNORE INTO favorite_meals (user_id, meal_id, sig) VALUES (?,?,?)",
                       (user_id, meal_id, sig))
    conn.commit()
    if cur.rowcount:
        return cur.lastrowid
    return conn.execute("SELECT id FROM favorite_meals WHERE user_id=? AND sig=?",
                        (user_id, sig)).fetchone()["id"]


def list_favorites(conn: sqlite3.Connection, user_id: int, limit: int = 30) -> list[dict]:
    """Избранное, новое сверху: {id, meal_id, kcal, names}."""
    rows = conn.execute(
        """SELECT f.id, f.meal_id, m.kcal_total FROM favorite_meals f
           JOIN meals m ON m.id = f.meal_id WHERE f.user_id=? ORDER BY f.id DESC LIMIT ?""",
        (user_id, limit)).fetchall()
    out = []
    for r in rows:
        names = _meal_names(conn, r["meal_id"])
        if names:
            out.append({"id": r["id"], "meal_id": r["meal_id"], "kcal": r["kcal_total"],
                        "names": names})
    return out


def favorite_meal_id(conn: sqlite3.Connection, user_id: int, favorite_id: int) -> int | None:
    row = conn.execute("SELECT meal_id FROM favorite_meals WHERE id=? AND user_id=?",
                       (favorite_id, user_id)).fetchone()
    return row["meal_id"] if row else None


def remove_favorite(conn: sqlite3.Connection, user_id: int, favorite_id: int) -> bool:
    cur = conn.execute("DELETE FROM favorite_meals WHERE id=? AND user_id=?",
                       (favorite_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def add_meal_photos(conn: sqlite3.Connection, meal_id: int, paths: list[str],
                    dish_ids: list[int | None] | None = None, commit: bool = True) -> int:
    """Дописать фото приёма (порядок сохраняется). Уже записанные пути пропускаются,
    поэтому повторный вызов (подпись после фото) дублей не даёт. Возвращает число новых."""
    have = {r["path"] for r in conn.execute(
        "SELECT path FROM meal_photos WHERE meal_id=?", (meal_id,))}
    pos = conn.execute("SELECT COALESCE(MAX(position)+1, 0) FROM meal_photos WHERE meal_id=?",
                       (meal_id,)).fetchone()[0]
    added = 0
    for i, path in enumerate(paths):
        if not path or path in have:
            continue
        dish_id = dish_ids[i] if dish_ids and i < len(dish_ids) else None
        conn.execute("INSERT INTO meal_photos (meal_id, path, dish_id, position) "
                     "VALUES (?,?,?,?)", (meal_id, path, dish_id, pos))
        have.add(path)
        pos += 1
        added += 1
    if commit:
        conn.commit()
    return added


def meal_photos(conn: sqlite3.Connection, meal_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM meal_photos WHERE meal_id=? ORDER BY position, id",
                        (meal_id,)).fetchall()


def set_meal_photos_dish(conn: sqlite3.Connection, meal_id: int, dish_id: int | None) -> None:
    """Одна посуда на все фото приёма (выбор кнопкой «🍽 Посуда»); None — снять."""
    conn.execute("UPDATE meal_photos SET dish_id=? WHERE meal_id=?", (dish_id, meal_id))
    conn.commit()


# ------------------------------------------------------------------ посуда

DISH_KINDS = ("plate", "deep_plate", "bowl", "salad_bowl", "mug", "cup", "glass", "pan", "pot", "container", "jar", "thermos", "other")
_DISH_FIELDS = ("name", "kind", "diameter_cm", "depth_cm", "volume_ml", "photo_path")


def _check_dish(name: str | None, kind: str | None) -> None:
    if name is not None and not str(name).strip():
        raise ValueError("у посуды должно быть название")
    if kind is not None and kind not in DISH_KINDS:
        raise ValueError(f"неизвестный тип посуды: {kind}")


def create_dish(conn: sqlite3.Connection, user_id: int, name: str, kind: str,
                diameter_cm: float | None = None, depth_cm: float | None = None,
                volume_ml: float | None = None, photo_path: str | None = None,
                is_default: bool = False) -> int:
    """Новая посуда. Умолчание у пользователя только одно: новое снимает прежнее."""
    _check_dish(name, kind)
    if is_default:
        conn.execute("UPDATE dishes SET is_default=0 WHERE user_id=?", (user_id,))
    cur = conn.execute(
        """INSERT INTO dishes (user_id, name, kind, diameter_cm, depth_cm, volume_ml,
           photo_path, is_default) VALUES (?,?,?,?,?,?,?,?)""",
        (user_id, str(name).strip()[:60], kind, diameter_cm, depth_cm, volume_ml,
         photo_path, int(bool(is_default))))
    conn.commit()
    return cur.lastrowid


def update_dish(conn: sqlite3.Connection, user_id: int, dish_id: int, **fields) -> bool:
    """Частичное обновление: name, kind, diameter_cm, depth_cm, volume_ml, photo_path,
    is_default. Чужая или удалённая посуда не меняется (False)."""
    if get_dish(conn, user_id, dish_id) is None:
        return False
    unknown = set(fields) - set(_DISH_FIELDS) - {"is_default"}
    if unknown:
        raise ValueError(f"неизвестные поля посуды: {sorted(unknown)}")
    _check_dish(fields.get("name"), fields.get("kind"))
    if "name" in fields:
        fields["name"] = str(fields["name"]).strip()[:60]
    default = fields.pop("is_default", None)
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE dishes SET {sets} WHERE id=? AND user_id=?",
                     (*fields.values(), dish_id, user_id))
    if default is not None:
        if default:
            conn.execute("UPDATE dishes SET is_default=0 WHERE user_id=?", (user_id,))
        conn.execute("UPDATE dishes SET is_default=? WHERE id=? AND user_id=?",
                     (int(bool(default)), dish_id, user_id))
    conn.commit()
    return True


def delete_dish(conn: sqlite3.Connection, user_id: int, dish_id: int) -> bool:
    """Мягкое удаление; удалённая посуда перестаёт быть умолчанием."""
    cur = conn.execute(
        """UPDATE dishes SET deleted_at=CURRENT_TIMESTAMP, is_default=0
           WHERE id=? AND user_id=? AND deleted_at IS NULL""", (dish_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def list_dishes(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM dishes WHERE user_id=? AND deleted_at IS NULL "
        "ORDER BY is_default DESC, id", (user_id,)).fetchall()


def get_dish(conn: sqlite3.Connection, user_id: int, dish_id: int | None,
             include_deleted: bool = False) -> sqlite3.Row | None:
    if not dish_id:
        return None
    sql = "SELECT * FROM dishes WHERE id=? AND user_id=?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    return conn.execute(sql, (dish_id, user_id)).fetchone()


def default_dish(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM dishes WHERE user_id=? AND is_default=1 AND deleted_at IS NULL "
        "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()


def sync_meal_water(conn: sqlite3.Connection, meal_id: int) -> int:
    """Чай и кофе из приёма пищи идут в воду одной строкой water_log, привязанной к записи.

    Вызывается при любом изменении записи: строка пересчитывается с нуля, у удалённой
    записи исчезает. Возвращает мл, засчитанные в воду."""
    from app.domain.water import hydration_ml
    conn.execute("DELETE FROM water_log WHERE meal_id=?", (meal_id,))
    meal = conn.execute("SELECT * FROM meals WHERE id=? AND deleted_at IS NULL",
                        (meal_id,)).fetchone()
    if not meal:
        return 0
    ml = sum(hydration_ml(i["name_ru"], i["name_en"], i["grams"])
             for i in conn.execute("SELECT * FROM meal_items WHERE meal_id=?", (meal_id,)))
    ml = min(ml, 3000)
    if ml:
        conn.execute(
            """INSERT INTO water_log (user_id, logged_at_utc, local_date, local_time, ml,
               source, raw_text, meal_id) VALUES (?,?,?,?,?,'text','чай/кофе из еды',?)""",
            (meal["user_id"], utcnow_iso(), meal["local_date"], meal["local_time"], ml, meal_id),
        )
    return ml


def get_last_meal(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM meals WHERE user_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def soft_delete_meal(conn: sqlite3.Connection, user_id: int, meal_id: int) -> bool:
    cur = conn.execute(
        "UPDATE meals SET deleted_at = ? WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (utcnow_iso(), meal_id, user_id),
    )
    sync_meal_water(conn, meal_id)
    conn.commit()
    return cur.rowcount > 0


def get_meal_items(conn: sqlite3.Connection, meal_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM meal_items WHERE meal_id=? ORDER BY id",
                        (meal_id,)).fetchall()


def get_meal(conn: sqlite3.Connection, user_id: int, meal_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM meals WHERE id=? AND user_id=?",
                        (meal_id, user_id)).fetchone()


def edit_item_grams(conn: sqlite3.Connection, item_id: int, new_grams: float) -> None:
    """Правка граммов: пересчёт от базы на 100 г (у старых строк — пропорционально), meal_edits + audit."""
    item = conn.execute("SELECT * FROM meal_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return
    old = item["grams"]
    if item["kcal_100g"] is not None:
        # база на 100 г известна: считаем от неё, как в calculate_item
        scale = new_grams / 100.0
        kcal = max(0, round(item["kcal_100g"] * scale / 5) * 5)
        protein = round((item["protein_100g"] or 0) * scale, 1)
        fat = round((item["fat_100g"] or 0) * scale, 1)
        carbs = round((item["carbs_100g"] or 0) * scale, 1)
    else:
        ratio = new_grams / old if old else 0
        kcal = max(0, round(item["kcal"] * ratio / 5) * 5)
        protein = round(item["protein_g"] * ratio, 1)
        fat = round(item["fat_g"] * ratio, 1)
        carbs = round(item["carbs_g"] * ratio, 1)
    conn.execute(
        """UPDATE meal_items SET grams=?, kcal=?, protein_g=?, fat_g=?, carbs_g=?,
           edited_by_user=1 WHERE id=?""",
        (new_grams, kcal, protein, fat, carbs, item_id),
    )
    _resum_meal(conn, item["meal_id"])
    conn.execute("INSERT INTO meal_edits (meal_item_id, field, old_value, new_value) "
                 "VALUES (?,?,?,?)", (item_id, "grams", str(old), str(new_grams)))
    conn.execute(
        """INSERT INTO audit_events (user_id, entity_type, entity_id, event_type,
           before_json, after_json) VALUES (?, 'meal_item', ?, 'edit_grams', ?, ?)""",
        (None, item_id, json.dumps({"grams": old}), json.dumps({"grams": new_grams})),
    )
    conn.commit()


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    item = conn.execute("SELECT meal_id FROM meal_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return
    conn.execute("DELETE FROM meal_items WHERE id=?", (item_id,))
    _resum_meal(conn, item["meal_id"])
    conn.commit()


def _resum_meal(conn: sqlite3.Connection, meal_id: int) -> None:
    """Дневной итог — пересчёт с нуля, не инкрементом."""
    conn.execute(
        """UPDATE meals SET
             kcal_total = COALESCE((SELECT SUM(kcal) FROM meal_items WHERE meal_id=?), 0),
             protein_g  = COALESCE((SELECT ROUND(SUM(protein_g),1) FROM meal_items WHERE meal_id=?), 0),
             fat_g      = COALESCE((SELECT ROUND(SUM(fat_g),1) FROM meal_items WHERE meal_id=?), 0),
             carbs_g    = COALESCE((SELECT ROUND(SUM(carbs_g),1) FROM meal_items WHERE meal_id=?), 0)
           WHERE id = ?""",
        (meal_id, meal_id, meal_id, meal_id, meal_id),
    )
    sync_meal_water(conn, meal_id)


def day_totals(conn: sqlite3.Connection, user_id: int, local_date: str) -> sqlite3.Row:
    return conn.execute(
        """SELECT COALESCE(SUM(kcal_total),0) AS kcal,
                  COALESCE(SUM(protein_g),0) AS protein,
                  COALESCE(SUM(fat_g),0) AS fat,
                  COALESCE(SUM(carbs_g),0) AS carbs,
                  COUNT(*) AS meals
           FROM meals WHERE user_id=? AND local_date=? AND deleted_at IS NULL""",
        (user_id, local_date),
    ).fetchone()


def day_meals(conn: sqlite3.Connection, user_id: int, local_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM meals WHERE user_id=? AND local_date=? AND deleted_at IS NULL
           ORDER BY local_time""",
        (user_id, local_date),
    ).fetchall()


def range_meals(conn: sqlite3.Connection, user_id: int, from_date: str,
                to_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM meals WHERE user_id=? AND local_date BETWEEN ? AND ?
           AND deleted_at IS NULL ORDER BY local_date, local_time""",
        (user_id, from_date, to_date),
    ).fetchall()


# ------------------------------------------------------------ метрики тела/сна

def save_body_measurement(conn: sqlite3.Connection, user_id: int, local_date: str,
                          metrics: dict, source: str, confidence: float,
                          measured_utc: str | None = None,
                          raw_id: int | None = None) -> int | None:
    """Дедуп по (user, date): новый замер обновляет существующую строку."""
    cols = ["weight_kg", "bmi", "body_fat_pct", "fat_mass_kg", "muscle_mass_kg",
            "skeletal_muscle_pct", "skeletal_muscle_kg", "body_water_pct", "bone_mass_kg",
            "protein_pct", "subcutaneous_fat_pct", "impedance_ohm",
            "muscle_mass_pct", "body_tone", "body_score", "resting_hr",
            "visceral_fat", "bmr_kcal", "metabolic_age", "fasting"]
    values = {c: metrics.get(c) for c in cols if metrics.get(c) is not None}
    if not values:
        return None
    existing = conn.execute(
        "SELECT id, fasting FROM body_measurements WHERE user_id=? AND local_date=?",
        (user_id, local_date)).fetchone()
    if existing and existing["fasting"] == 1 and metrics.get("fasting") == 0:
        # утренний замер натощак главнее: вечерний не перезаписывает вес дня
        return existing["id"]
    if existing:
        sets = ", ".join(f"{c}=?" for c in values)
        conn.execute(f"""UPDATE body_measurements SET {sets},
                         source=?, confidence=?, measured_at_utc=COALESCE(?, measured_at_utc)
                         WHERE id=?""",
                     (*values.values(), source, confidence, measured_utc, existing["id"]))
        mid = existing["id"]
    else:
        names = ", ".join(["user_id", "local_date", *cols, "source", "confidence",
                           "measured_at_utc", "raw_observation_id"])
        full_vals = [user_id, local_date, *[values.get(c) for c in cols],
                     source, confidence, measured_utc, raw_id]
        qmarks = ", ".join(["?"] * len(full_vals))
        cur = conn.execute(f"INSERT INTO body_measurements ({names}) VALUES ({qmarks})",
                           full_vals)
        mid = cur.lastrowid
    conn.commit()
    return mid


def save_activity(conn: sqlite3.Connection, user_id: int, local_date: str,
                  metrics: dict, source: str, confidence: float,
                  raw_id: int | None = None) -> None:
    cols = ["steps", "distance_km", "active_kcal", "resting_hr", "avg_hr",
            "active_minutes", "workouts_json"]
    values = {c: metrics.get(c) for c in cols if metrics.get(c) is not None}
    if not values:
        return
    names = ", ".join(["user_id", "local_date", *cols, "source", "confidence", "raw_observation_id"])
    full_vals = [user_id, local_date, *[values.get(c) for c in cols],
                 source, confidence, raw_id]
    # пустое поле нового скриншота не стирает уже сохранённое
    sets = ", ".join([*(f"{c}=COALESCE(excluded.{c}, {c})" for c in cols),
                      "source=excluded.source", "confidence=excluded.confidence"])
    conn.execute(
        f"""INSERT INTO activity_daily ({names}) VALUES ({", ".join(["?"] * len(full_vals))})
            ON CONFLICT(user_id, local_date, source) DO UPDATE SET {sets}""",
        full_vals,
    )
    conn.commit()


def save_sleep(conn: sqlite3.Connection, user_id: int, wake_date: str,
               metrics: dict, source: str, confidence: float,
               raw_id: int | None = None) -> None:
    cols = ["sleep_start_utc", "sleep_end_utc", "duration_min", "deep_min", "light_min",
            "rem_min", "awake_min", "score"]
    values = {c: metrics.get(c) for c in cols if metrics.get(c) is not None}
    if not values:
        return
    names = ", ".join(["user_id", "wake_date", *cols, "source", "confidence", "raw_observation_id"])
    full_vals = [user_id, wake_date, *[values.get(c) for c in cols],
                 source, confidence, raw_id]
    # пустое поле нового скриншота не стирает уже сохранённое
    sets = ", ".join([*(f"{c}=COALESCE(excluded.{c}, {c})" for c in cols),
                      "source=excluded.source", "confidence=excluded.confidence"])
    conn.execute(
        f"""INSERT INTO sleep_sessions ({names}) VALUES ({", ".join(["?"] * len(full_vals))})
            ON CONFLICT(user_id, wake_date, source) DO UPDATE SET {sets}""",
        full_vals,
    )
    conn.commit()


def weight_series(conn: sqlite3.Connection, user_id: int, from_date: str,
                  to_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT local_date, weight_kg FROM body_measurements
           WHERE user_id=? AND local_date BETWEEN ? AND ? AND weight_kg IS NOT NULL
           ORDER BY local_date""",
        (user_id, from_date, to_date),
    ).fetchall()


def trend_weight_series(conn: sqlite3.Connection, user_id: int, from_date: str,
                        to_date: str) -> tuple[list[sqlite3.Row], bool]:
    """Замеры веса для тренда: без «не натощак», если утренних достаточно.
    Второе значение True, когда пришлось брать и такие (тренд менее точный)."""
    from app.domain.weighin import pick_trend_rows
    rows = conn.execute(
        """SELECT local_date, weight_kg, fasting FROM body_measurements
           WHERE user_id=? AND local_date BETWEEN ? AND ? AND weight_kg IS NOT NULL
           ORDER BY local_date""",
        (user_id, from_date, to_date),
    ).fetchall()
    return pick_trend_rows(rows)


def first_meal_time(conn: sqlite3.Connection, user_id: int, local_date: str) -> str | None:
    row = conn.execute(
        """SELECT MIN(local_time) AS t FROM meals
           WHERE user_id=? AND local_date=? AND deleted_at IS NULL""",
        (user_id, local_date),
    ).fetchone()
    return row["t"] if row else None


_SOURCE_RANK = {"screenshot": 0, "csv": 1, "manual": 2}


def _dedupe_by_date(rows: list[sqlite3.Row], date_key: str) -> list[dict]:
    """Одна запись на дату: источники сливаются (скриншот важнее csv, csv важнее ручного),
    пустые поля берутся из менее приоритетного. Иначе сон/шаги за день считались бы дважды."""
    by_date: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_date.setdefault(r[date_key], []).append(r)
    out: list[dict] = []
    for day in sorted(by_date):
        group = sorted(by_date[day], key=lambda r: _SOURCE_RANK.get(r["source"], 1))
        merged = {k: group[0][k] for k in group[0].keys() if k != "source"}
        for other in group[1:]:
            for k in merged:
                if merged[k] is None and other[k] is not None:
                    merged[k] = other[k]
        out.append(merged)
    return out


def sleep_series(conn: sqlite3.Connection, user_id: int, from_date: str,
                 to_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT wake_date, duration_min, deep_min, light_min, rem_min, score, source
           FROM sleep_sessions WHERE user_id=? AND wake_date BETWEEN ? AND ?
           ORDER BY wake_date""",
        (user_id, from_date, to_date),
    ).fetchall()
    return _dedupe_by_date(rows, "wake_date")


def activity_series(conn: sqlite3.Connection, user_id: int, from_date: str,
                    to_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT local_date, steps, active_kcal, resting_hr, avg_hr, active_minutes, source
           FROM activity_daily WHERE user_id=? AND local_date BETWEEN ? AND ?
           ORDER BY local_date""",
        (user_id, from_date, to_date),
    ).fetchall()
    return _dedupe_by_date(rows, "local_date")


# ------------------------------------------------------------ raw / llm / misc

def save_raw_observation(conn: sqlite3.Connection, user_id: int, kind: str,
                         file_path: str | None, file_hash: str | None,
                         parsed_json: dict | None, model: str | None) -> int:
    cur = conn.execute(
        """INSERT INTO raw_observations (user_id, kind, file_path, file_hash,
           parsed_json, model) VALUES (?,?,?,?,?,?)""",
        (user_id, kind, file_path, file_hash,
         json.dumps(parsed_json, ensure_ascii=False) if parsed_json else None, model),
    )
    conn.commit()
    return cur.lastrowid


def file_seen(conn: sqlite3.Connection, user_id: int, file_hash: str,
              window_min: int = 10) -> bool:
    row = conn.execute(
        """SELECT created_at FROM recent_files WHERE user_id=? AND file_hash=?
           AND created_at >= datetime('now', ?) ORDER BY created_at DESC LIMIT 1""",
        (user_id, file_hash, f"-{window_min} minutes"),
    ).fetchone()
    return row is not None


def forget_file(conn: sqlite3.Connection, user_id: int, file_hash: str) -> None:
    """Снять отметку «уже видел»: после сбоя распознавания повтор того же файла должен работать."""
    conn.execute("DELETE FROM recent_files WHERE user_id=? AND file_hash=?", (user_id, file_hash))
    conn.commit()


def remember_file(conn: sqlite3.Connection, user_id: int, file_hash: str) -> None:
    conn.execute("INSERT INTO recent_files (user_id, file_hash) VALUES (?,?)",
                 (user_id, file_hash))
    conn.execute("DELETE FROM recent_files WHERE created_at < datetime('now', '-1 day')")
    conn.commit()


def log_llm_call(conn: sqlite3.Connection, user_id: int | None, purpose: str,
                 model: str, tokens_in: int | None, tokens_out: int | None,
                 latency_ms: int, ok: bool, error: str | None,
                 tokens_cached: int | None = None, cost_usd: float | None = None) -> None:
    conn.execute(
        """INSERT INTO llm_calls (user_id, purpose, model, tokens_in, tokens_out,
           latency_ms, ok, error, tokens_cached, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user_id, purpose, model, tokens_in, tokens_out, latency_ms,
         int(ok), error, tokens_cached, cost_usd),
    )
    if ok:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO model_usage_daily (day, user_id, model, requests, input_tokens,
               output_tokens) VALUES (?,?,?,1,?,?)
               ON CONFLICT(day, user_id, model) DO UPDATE SET
                 requests = requests + 1,
                 input_tokens = input_tokens + excluded.input_tokens,
                 output_tokens = output_tokens + excluded.output_tokens""",
            (day, user_id or 0, model, tokens_in or 0, tokens_out or 0),
        )
    conn.commit()


def save_pending_input(conn: sqlite3.Connection, user_id: int, kind: str,
                       payload: dict) -> int:
    cur = conn.execute(
        "INSERT INTO pending_inputs (user_id, kind, payload_json) VALUES (?,?,?)",
        (user_id, kind, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def latest_pending_input(conn: sqlite3.Connection, user_id: int, kind: str,
                         max_age_min: int | None = None) -> sqlite3.Row | None:
    """Последний ожидающий ввод; с max_age_min старые («протухшие») не считаются."""
    sql = "SELECT * FROM pending_inputs WHERE user_id=? AND kind=?"
    params: list = [user_id, kind]
    if max_age_min is not None:
        sql += " AND created_at >= datetime('now', ?)"
        params.append(f"-{int(max_age_min)} minutes")
    return conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()


def clear_pending_inputs(conn: sqlite3.Connection, user_id: int, kind: str) -> None:
    conn.execute("DELETE FROM pending_inputs WHERE user_id=? AND kind=?", (user_id, kind))
    conn.commit()


def pending_input(conn: sqlite3.Connection, pending_id: int, user_id: int,
                  kind: str | None = None) -> sqlite3.Row | None:
    sql = "SELECT * FROM pending_inputs WHERE id=? AND user_id=?"
    params: list = [pending_id, user_id]
    if kind is not None:
        sql += " AND kind=?"
        params.append(kind)
    return conn.execute(sql, params).fetchone()


def update_pending_input(conn: sqlite3.Connection, pending_id: int, user_id: int,
                         payload: dict) -> bool:
    cur = conn.execute("UPDATE pending_inputs SET payload_json=? WHERE id=? AND user_id=?",
                       (json.dumps(payload, ensure_ascii=False), pending_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def delete_pending_input(conn: sqlite3.Connection, pending_id: int, user_id: int) -> bool:
    cur = conn.execute("DELETE FROM pending_inputs WHERE id=? AND user_id=?", (pending_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def put_photo_session(conn: sqlite3.Connection, user_id: int, context: dict,
                      expires_min: int = 2, meal_id: int | None = None) -> int:
    """Окно для подписи. ``meal_id`` — запись, уже созданная из фото: подпись правит её."""
    conn.execute("DELETE FROM photo_sessions WHERE user_id=? AND expires_at < ?",
                 (user_id, utcnow_iso()))
    cur = conn.execute(
        """INSERT INTO photo_sessions (user_id, status, context_json, expires_at, meal_id)
           VALUES (?, 'awaiting_caption', ?, datetime('now', ?), ?)""",
        (user_id, json.dumps(context, ensure_ascii=False), f"+{expires_min * 60} seconds",
         meal_id),
    )
    conn.commit()
    return cur.lastrowid


def fresh_photo_session(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    row = conn.execute(
        """SELECT * FROM photo_sessions WHERE user_id=? AND status='awaiting_caption'
           AND expires_at >= ? ORDER BY id DESC LIMIT 1""",
        (user_id, utcnow_iso()),
    ).fetchone()
    return row


def close_photo_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute("UPDATE photo_sessions SET status='ready' WHERE id=?", (session_id,))
    conn.commit()


def replace_meal_items(conn: sqlite3.Connection, user_id: int, meal_id: int,
                       calc: MealCalculation, source: str | None = None,
                       transcript: str | None = None) -> bool:
    """Заменить позиции и итоги приёма пищи (подпись к фото пересчитала состав).

    Запись остаётся той же: id, время и фото сохраняются, второй записи не появляется.
    """
    meal = conn.execute("SELECT * FROM meals WHERE id=? AND user_id=? AND deleted_at IS NULL",
                        (meal_id, user_id)).fetchone()
    if not meal:
        return False
    old = [dict(r) for r in get_meal_items(conn, meal_id)]
    conn.execute("DELETE FROM meal_items WHERE meal_id=?", (meal_id,))
    _insert_items(conn, meal_id, calc)
    conn.execute(
        """UPDATE meals SET kcal_total=?, protein_g=?, fat_g=?, carbs_g=?, kcal_low=?,
           kcal_high=?, confidence=?, method=?, source=COALESCE(?, source),
           transcript=COALESCE(?, transcript) WHERE id=?""",
        (calc.kcal_total, calc.protein_g, calc.fat_g, calc.carbs_g, calc.kcal_low,
         calc.kcal_high, calc.confidence, calc.method, source, transcript, meal_id),
    )
    sync_meal_water(conn, meal_id)
    conn.execute(
        """INSERT INTO audit_events (user_id, entity_type, entity_id, event_type,
           before_json, after_json) VALUES (?, 'meal', ?, 'replace_items', ?, ?)""",
        (user_id, meal_id, json.dumps({"items": [i["name_ru"] for i in old]}, ensure_ascii=False),
         json.dumps({"items": [i.name_ru for i in calc.items]}, ensure_ascii=False)),
    )
    conn.commit()
    return True


def update_meal_time(conn: sqlite3.Connection, user_id: int, meal_id: int, local_date: str,
                     local_time: str, meal_type: str, eaten_utc: str | None = None) -> bool:
    """Перенести приём пищи на другую дату/время (итоги дня считаются по local_date)."""
    cur = conn.execute(
        """UPDATE meals SET local_date=?, local_time=?, meal_type=?,
           eaten_at_utc=COALESCE(?, eaten_at_utc)
           WHERE id=? AND user_id=? AND deleted_at IS NULL""",
        (local_date, local_time, meal_type, eaten_utc, meal_id, user_id),
    )
    sync_meal_water(conn, meal_id)
    conn.commit()
    return cur.rowcount > 0


def set_reminder(conn: sqlite3.Connection, user_id: int, kind: str, local_time: str,
                 enabled: bool = True, days: str | None = None) -> None:
    conn.execute(
        """INSERT INTO reminders (user_id, kind, local_time, enabled, days) VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, kind) DO UPDATE SET local_time=excluded.local_time,
           enabled=excluded.enabled, days=COALESCE(excluded.days, reminders.days)""",
        (user_id, kind, local_time, int(enabled), days),
    )
    conn.commit()


def due_reminders(conn: sqlite3.Connection, user_id: int, kind: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reminders WHERE user_id=? AND kind=?",
                        (user_id, kind)).fetchone()


# ------------------------------------------------------------------- вода

def add_water(conn: sqlite3.Connection, user_id: int, local_date: str, local_time: str,
              ml: int, source: str, raw_text: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO water_log (user_id, logged_at_utc, local_date, local_time, ml, source,
           raw_text) VALUES (?,?,?,?,?,?,?)""",
        (user_id, utcnow_iso(), local_date, local_time, ml, source, raw_text),
    )
    conn.commit()
    return cur.lastrowid


def day_water_ml(conn: sqlite3.Connection, user_id: int, local_date: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(ml), 0) AS ml FROM water_log WHERE user_id=? AND local_date=?",
        (user_id, local_date),
    ).fetchone()
    return int(row["ml"])


def water_by_day(conn: sqlite3.Connection, user_id: int, from_date: str,
                 to_date: str) -> dict[str, int]:
    rows = conn.execute(
        """SELECT local_date, SUM(ml) AS ml FROM water_log
           WHERE user_id=? AND local_date BETWEEN ? AND ? GROUP BY local_date""",
        (user_id, from_date, to_date),
    ).fetchall()
    return {r["local_date"]: int(r["ml"]) for r in rows}


def last_water(conn: sqlite3.Connection, user_id: int, local_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM water_log WHERE user_id=? AND local_date=? ORDER BY id DESC LIMIT 1",
        (user_id, local_date),
    ).fetchone()


def delete_water(conn: sqlite3.Connection, user_id: int, water_id: int) -> bool:
    cur = conn.execute("DELETE FROM water_log WHERE id=? AND user_id=?", (water_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def latest_weight_kg(conn: sqlite3.Connection, user_id: int) -> float | None:
    row = conn.execute(
        """SELECT weight_kg FROM body_measurements
           WHERE user_id=? AND weight_kg IS NOT NULL ORDER BY local_date DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    if row:
        return float(row["weight_kg"])
    user = conn.execute("SELECT start_weight_kg FROM users WHERE id=?", (user_id,)).fetchone()
    return float(user["start_weight_kg"]) if user and user["start_weight_kg"] else None


def last_adaptive_review_ordinal(conn: sqlite3.Connection, user_id: int) -> int | None:
    """День последнего адаптивного пересмотра цели (toordinal) или None."""
    row = conn.execute(
        """SELECT valid_from FROM targets_history
           WHERE user_id=? AND reason LIKE 'adaptive_tdee%' ORDER BY valid_from DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    return datetime.strptime(row["valid_from"], "%Y-%m-%d").toordinal() if row else None


# ------------------------------------------------------------ тяга к еде

def create_craving(conn: sqlite3.Connection, user_id: int, local_date: str, local_time: str,
                   trigger_text: str | None, kcal_so_far: int | None,
                   kcal_target: int | None, risk_flag: int = 0, mode: str = "craving") -> int:
    cur = conn.execute(
        """INSERT INTO craving_events (user_id, started_utc, local_date, local_time,
           trigger_text, kcal_so_far, kcal_target, risk_flag, mode) VALUES (?,?,?,?,?,?,?,?,?)""",
        (user_id, utcnow_iso(), local_date, local_time, trigger_text, kcal_so_far,
         kcal_target, risk_flag, mode),
    )
    conn.commit()
    return cur.lastrowid


def get_craving(conn: sqlite3.Connection, user_id: int, event_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM craving_events WHERE id=? AND user_id=?",
                        (event_id, user_id)).fetchone()


_LAST_ACTIVITY = """COALESCE((SELECT MAX(m.created_utc) FROM craving_messages m
                   WHERE m.event_id = c.id), c.started_utc)"""


def open_craving(conn: sqlite3.Connection, user_id: int,
                 window_min: int = 90) -> sqlite3.Row | None:
    """Незакрытый эпизод, в котором последнее сообщение было не раньше window_min минут назад:
    живой разговор не обрывается только потому, что начался давно."""
    return conn.execute(
        f"""SELECT c.* FROM craving_events c WHERE c.user_id=? AND c.outcome='open'
           AND {_LAST_ACTIVITY} >= datetime('now', ?) ORDER BY c.id DESC LIMIT 1""",
        (user_id, f"-{int(window_min)} minutes"),
    ).fetchone()


def update_craving(conn: sqlite3.Connection, event_id: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE craving_events SET {sets} WHERE id=?", (*fields.values(), event_id))
    conn.commit()


def close_craving(conn: sqlite3.Connection, event_id: int, outcome: str) -> None:
    update_craving(conn, event_id, outcome=outcome, closed_utc=utcnow_iso())


def add_craving_message(conn: sqlite3.Connection, event_id: int, role: str, text: str) -> None:
    conn.execute(
        "INSERT INTO craving_messages (event_id, role, text, created_utc) VALUES (?,?,?,?)",
        (event_id, role, text[:2000], utcnow_iso()),
    )
    conn.commit()


def craving_messages(conn: sqlite3.Connection, event_id: int, limit: int = 20) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT role, text FROM craving_messages WHERE event_id=? ORDER BY id DESC LIMIT ?",
        (event_id, limit),
    ).fetchall()
    return list(reversed(rows))


def cravings_range(conn: sqlite3.Connection, user_id: int, from_date: str,
                   to_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM craving_events WHERE user_id=? AND local_date BETWEEN ? AND ?
           ORDER BY id""",
        (user_id, from_date, to_date),
    ).fetchall()


def due_craving_followups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT c.*, u.tg_id, u.sex FROM craving_events c JOIN users u ON u.id = c.user_id
           WHERE c.outcome='open' AND c.followup_sent=0 AND c.followup_at_utc IS NOT NULL
           AND c.followup_at_utc <= datetime('now')"""
    ).fetchall()


def dismiss_stale_cravings(conn: sqlite3.Connection, hours: int = 3) -> None:
    # Сначала чтение: зовётся каждую минуту, и UPDATE «впустую» в Turso открывал транзакцию
    # без коммита, после чего поток умирал по простою и ломал следующую запись.
    ids = [r[0] for r in conn.execute(
        f"""SELECT c.id FROM craving_events c WHERE c.outcome='open'
            AND {_LAST_ACTIVITY} < datetime('now', ?)""",
        (f"-{int(hours)} hours",),
    ).fetchall()]
    if not ids:
        return
    conn.execute(
        f"""UPDATE craving_events SET outcome='dismissed', closed_utc=datetime('now')
           WHERE id IN ({",".join("?" * len(ids))})""",
        ids,
    )
    conn.commit()


def craving_memory(conn: sqlite3.Connection, user_id: int, limit: int = 3) -> list[sqlite3.Row]:
    """Прошлые разговоры, о которых есть что вспомнить (итог модели или решение из разбора)."""
    return conn.execute(
        """SELECT local_date, local_time, category, outcome, summary, lesson FROM craving_events
           WHERE user_id=? AND outcome != 'open' AND (summary IS NOT NULL OR lesson IS NOT NULL)
           ORDER BY id DESC LIMIT ?""", (user_id, limit)).fetchall()


def last_meal_time(conn: sqlite3.Connection, user_id: int, local_date: str) -> str | None:
    row = conn.execute(
        """SELECT local_time FROM meals WHERE user_id=? AND local_date=? AND deleted_at IS NULL
           ORDER BY local_time DESC LIMIT 1""",
        (user_id, local_date),
    ).fetchone()
    return row["local_time"] if row else None


# ----------------------------------------------------------- Open Food Facts

def save_off_food(conn: sqlite3.Connection, code: str, name_ru: str, name_en: str,
                  kcal: float, protein: float, fat: float, carbs: float) -> int:
    """Продукт OFF в локальную базу (кэш по штрихкоду); повторный вызов обновляет значения."""
    conn.execute(
        """INSERT INTO foods (name_ru, name_en, kcal_100g, protein_100g, fat_100g, carbs_100g,
           source, barcode, external_id) VALUES (?,?,?,?,?,?, 'off', ?, ?)
           ON CONFLICT(source, external_id) DO UPDATE SET
             name_ru=excluded.name_ru, name_en=excluded.name_en, kcal_100g=excluded.kcal_100g,
             protein_100g=excluded.protein_100g, fat_100g=excluded.fat_100g,
             carbs_100g=excluded.carbs_100g, barcode=excluded.barcode""",
        (name_ru, name_en, kcal, protein, fat, carbs, code, f"off:{code}"),
    )
    conn.commit()
    return conn.execute("SELECT id FROM foods WHERE source='off' AND external_id=?",
                        (f"off:{code}",)).fetchone()["id"]


def food_by_barcode(conn: sqlite3.Connection, code: str,
                    user_id: int | None = None) -> sqlite3.Row | None:
    """Общий продукт по штрихкоду; свой продукт этого пользователя важнее общего."""
    return conn.execute(
        """SELECT * FROM foods WHERE barcode=? AND deleted_at IS NULL
           AND (owner_user_id IS NULL OR owner_user_id = ?)
           ORDER BY (owner_user_id IS NULL), id LIMIT 1""",
        (code, user_id if user_id is not None else -1)).fetchone()


# --------------------------------------------------- правила «если-то» и уроки

MAX_RULES = 7


def add_rule(conn: sqlite3.Connection, user_id: int, if_text: str, then_text: str,
             category: str | None = None, remind_time: str | None = None) -> int | None:
    """Новое правило; None, если лимит исчерпан или такое уже есть."""
    count = conn.execute("SELECT COUNT(*) AS n FROM if_then_rules WHERE user_id=?",
                         (user_id,)).fetchone()["n"]
    if count >= MAX_RULES:
        return None
    dup = conn.execute(
        "SELECT id FROM if_then_rules WHERE user_id=? AND lower(if_text)=lower(?) "
        "AND lower(then_text)=lower(?)", (user_id, if_text, then_text)).fetchone()
    if dup:
        return None
    cur = conn.execute(
        """INSERT INTO if_then_rules (user_id, if_text, then_text, category, remind_time)
           VALUES (?,?,?,?,?)""", (user_id, if_text, then_text, category, remind_time))
    conn.commit()
    return cur.lastrowid


def list_rules(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM if_then_rules WHERE user_id=? ORDER BY id",
                        (user_id,)).fetchall()


def delete_rule(conn: sqlite3.Connection, user_id: int, rule_id: int) -> bool:
    cur = conn.execute("DELETE FROM if_then_rules WHERE id=? AND user_id=?", (rule_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def rules_for_category(conn: sqlite3.Connection, user_id: int,
                       category: str | None, limit: int = 2) -> list[sqlite3.Row]:
    """Правила категории и «общие» (без категории); подходящие по категории идут первыми."""
    return conn.execute(
        """SELECT * FROM if_then_rules WHERE user_id=? AND (category IS NULL OR category=?)
           ORDER BY (category = ?) DESC, id LIMIT ?""",
        (user_id, category, category, limit)).fetchall()


def due_rules(conn: sqlite3.Connection, user_id: int, hhmm: str, local_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM if_then_rules WHERE user_id=? AND remind_time=?
           AND COALESCE(last_sent_date,'') != ?""", (user_id, hhmm, local_date)).fetchall()


def mark_rule_sent(conn: sqlite3.Connection, rule_id: int, local_date: str) -> None:
    conn.execute("UPDATE if_then_rules SET last_sent_date=? WHERE id=?", (local_date, rule_id))
    conn.commit()


def set_craving_lesson(conn: sqlite3.Connection, user_id: int, event_id: int, lesson: str) -> bool:
    cur = conn.execute("UPDATE craving_events SET lesson=? WHERE id=? AND user_id=?",
                       (lesson[:300], event_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def latest_body_rows(conn: sqlite3.Connection, user_id: int, limit: int = 2) -> list[sqlite3.Row]:
    """Последние замеры с составом тела (новые первыми); учитываются только те, где есть вес."""
    return conn.execute(
        """SELECT * FROM body_measurements WHERE user_id=? AND weight_kg IS NOT NULL
           ORDER BY local_date DESC, id DESC LIMIT ?""", (user_id, limit)).fetchall()


# ----------------------------------------------------------------- мини-цель недели

def weekly_goal(conn: sqlite3.Connection, user_id: int, week_start: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM weekly_goals WHERE user_id=? AND week_start=?",
                        (user_id, week_start)).fetchone()


def set_weekly_goal(conn: sqlite3.Connection, user_id: int, week_start: str,
                    kind: str, target_days: int) -> None:
    """Цель недели можно сменить: новая заменяет прежнюю."""
    conn.execute(
        "INSERT INTO weekly_goals (user_id, week_start, kind, target_days) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id, week_start) DO UPDATE SET kind=excluded.kind, "
        "target_days=excluded.target_days", (user_id, week_start, kind, target_days))
    conn.commit()


def clear_weekly_goal(conn: sqlite3.Connection, user_id: int, week_start: str) -> None:
    conn.execute("DELETE FROM weekly_goals WHERE user_id=? AND week_start=?",
                 (user_id, week_start))
    conn.commit()
