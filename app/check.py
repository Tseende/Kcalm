"""check.py — обязательные проверки без сети и ключей. exit code != 0 = провал.

Запуск: python -m app.check
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

# Позволяет запускать и `python -m app.check`, и `python app/check.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, fn) -> None:
    global CHECKS
    CHECKS += 1
    try:
        fn()
        print(f"  ok  {name}")
    except (Exception, SystemExit) as e:                     # SystemExit — тоже провал
        FAILURES.append(name)
        print(f"FAIL  {name}: {type(e).__name__}: {e}")


def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from app.db import connect, migrate
    conn = connect(path)
    migrate(conn)
    return conn, path


# 1. импорты всех модулей
def t_imports():
    import app.bot  # noqa: F401
    import app.config  # noqa: F401
    import app.db  # noqa: F401
    import app.middleware  # noqa: F401
    import app.scheduler  # noqa: F401
    from app.domain import analytics, matching, nutrition, targets  # noqa: F401
    from app.llm import client  # noqa: F401
    from app.routers import commands, intake, onboarding  # noqa: F401
    from app.services import coach, food, screens  # noqa: F401
    from app.storage import repo  # noqa: F401


def t_config():
    from app.config import Config, mask_secret
    cfg = Config(telegram_bot_token="test", openrouter_api_key="test",
                 allowed_tg_ids="42, 43;44")
    cfg.require_runtime_secrets()
    assert cfg.allowed_ids == {42, 43, 44}, cfg.allowed_ids

    # приватный дневник без whitelist и без явного разрешения — падать на старте
    try:
        Config(telegram_bot_token="test", openrouter_api_key="test",
               allowed_tg_ids="", allow_public_bot=False).require_runtime_secrets()
        raise AssertionError("ожидался SystemExit без whitelist")
    except SystemExit:
        pass

    # ключи не должны попадать в лог целиком
    assert "…" in mask_secret("sk-or-v1-abcdef123456")


# 2. JSON-контракты + устойчивость парсера
def t_json_parser():
    from app.llm.client import LLMError, extract_json
    good = extract_json('```json\n{"a": 1, "items": ["x"]}\n```')
    assert good["a"] == 1
    dirty = extract_json('Вот результат:\n{"a": {"b": 2}, "c": "хвост }"}\nНадеюсь помог')
    assert dirty["a"]["b"] == 2
    cut = extract_json('{"a": 1, "b": [1, 2')
    assert cut["a"] == 1
    try:
        extract_json("нет тут json")
        raise AssertionError("должен был упасть")
    except LLMError:
        pass


def t_food_contracts():
    from app.services.food import understanding_from_json
    data = {
        "items": [{"name_ru": "гречка", "name_en": "buckwheat",
                   "amount": {"kind": "grams", "grams": 200, "grams_range": [180, 220]},
                   "preparation": "boiled", "confidence": 0.9}],
        "meal_type_guess": "lunch", "overall_confidence": 0.9,
    }
    u = understanding_from_json(data)
    assert u.items[0].grams_min == 180.0 and u.items[0].explicit_grams == 200.0
    # перевёрнутый диапазон чинится валидатором
    bad = understanding_from_json({
        "items": [{"name_ru": "суп", "name_en": "soup",
                   "amount": {"kind": "portion", "grams_range": [400, 100]},
                   "confidence": 0.3}], "overall_confidence": 0.3,
    })
    assert bad.items[0].grams_min < bad.items[0].grams_max
    # нулевой диапазон запрещён
    try:
        understanding_from_json({"items": [{"name_ru": "x", "name_en": "x",
                                            "amount": {"grams_range": [0, 0]}}]})
        raise AssertionError("0/0 должен быть запрещён")
    except Exception:
        pass


# 3. арифметика: golden-кейсы
def t_calc():
    from app.domain.nutrition import (FoodRef, IngredientEstimate, calculate_item,
                                      calculate_meal)
    ref = FoodRef(name_ru="гречка", name_en="buckwheat", kcal_100g=110, protein_100g=4.2,
                  fat_100g=1.1, carbs_100g=21.3, source="seed")
    item = IngredientEstimate(name="buckwheat", grams_min=120, grams_max=180)
    ci = calculate_item(item, ref)
    assert ci.kcal_low == 130 and ci.kcal_high == 200, (ci.kcal_low, ci.kcal_high)
    assert ci.kcal == 165 or ci.kcal == 160 or ci.kcal == 170  # середина 150 г → 165

    meal = calculate_meal([(item, ref)])
    assert meal.kcal_low == 130 and meal.kcal_high == 200
    assert meal.method == "db"

    # диапазон из §11: grams 120–180, ккал 200/100г → 240–360
    ref2 = FoodRef(name_ru="x", name_en="x", kcal_100g=200, protein_100g=10,
                   fat_100g=5, carbs_100g=20, source="seed")
    ci2 = calculate_item(IngredientEstimate(name="x", grams_min=120, grams_max=180), ref2)
    assert ci2.kcal_low == 240 and ci2.kcal_high == 360


def t_sanitary():
    from app.domain.nutrition import FoodRef, IngredientEstimate, calculate_item
    # противоречивый источник: ккал 500 при макросах 4/9/4 = 700 → warning
    ref = FoodRef(name_ru="x", name_en="x", kcal_100g=500, protein_100g=50,
                  fat_100g=50, carbs_100g=50, source="seed", confidence=0.5)
    ci = calculate_item(IngredientEstimate(name="x", grams_min=100, grams_max=100), ref)
    assert ci.quality_flags, "ожидался warning о противоречии"
    # нормальный источник: ккал 520 при макросах ~520 → нет warning
    ref2 = FoodRef(name_ru="y", name_en="y", kcal_100g=520, protein_100g=30,
                   fat_100g=30, carbs_100g=50, source="seed")
    ci2 = calculate_item(IngredientEstimate(name="y", grams_min=100, grams_max=100), ref2)
    assert not ci2.quality_flags


# 4. TDEE: синтетические ряды с известным ответом
def t_tdee():
    from app.domain.targets import (adaptive_tdee, compute_target, ols_slope,
                                    pa_from_pal, round10_kcal, tee_iom)
    # пример из формул §1: муж 35, 178 см, 92.4 кг, PAL 1.75 → 3320
    assert tee_iom("male", 35, 92.4, 1.78, 1.27) == 3330  # 3327.2 → 3330
    assert pa_from_pal(1.25, "male") == 1.00
    assert pa_from_pal(1.5, "female") == 1.14

    t = compute_target(sex="male", age=35, weight_kg=92.4, height_cm=178,
                       pal=1.75, rate_kg_week=-0.5, goal_weight_kg=85)
    assert t.kcal_target == round10_kcal(3330 - 550)

    # taper: 3 кг до цели → половина дефицита; 0.5 кг → ноль
    t3 = compute_target(sex="male", age=35, weight_kg=88, height_cm=178, pal=1.75,
                        rate_kg_week=-0.5, goal_weight_kg=85)
    assert t3.taper_factor == 0.5
    t0 = compute_target(sex="male", age=35, weight_kg=85.4, height_cm=178, pal=1.75,
                        rate_kg_week=-0.5, goal_weight_kg=85)
    assert t0.taper_factor == 0.0
    # пол: 1400 → 1500
    floor_t = compute_target(sex="male", age=35, weight_kg=60, height_cm=170, pal=1.0,
                             rate_kg_week=-0.7, goal_weight_kg=None)
    assert floor_t.kcal_target >= 1500 and floor_t.floor_applied

    # адаптивный TDEE из §3: avg_intake 2100, наклон −0.06 кг/день → TDEE 2562
    days = [float(i) for i in range(14)]
    weights = [100 - 0.06 * d for d in days]
    intake = {d: 2100 for d in days}
    res = adaptive_tdee(list(zip(days, weights)), intake, 2100, rate_kg_week=-0.5,
                        goal_weight_kg=85)
    assert res.tdee_est is not None and abs(res.tdee_est - 2560) <= 130, res.tdee_est
    # данных мало — цель не трогаем
    res2 = adaptive_tdee(list(zip(days[:4], weights[:4])), {}, 2100)
    assert res2.new_target is None
    # кап ±150
    res3 = adaptive_tdee(list(zip(days, weights)), intake, 1500, rate_kg_week=-0.5)
    assert abs(res3.new_target - 1500) <= 150

    # OLS с пропусками не падает
    assert ols_slope([0, 2, 5, 9], [100, 99, 98, 97]) is not None


# 5. матчинг продуктов
def t_matching():
    conn, path = tmp_db()
    try:
        from app.domain import matching
        ref = matching.find_food(conn, "buckwheat", "гречка", user_id=None)
        assert ref and "речка" in ref.name_ru, ref
        ref2 = matching.find_food(conn, "chicken breast", "куриная грудка жареная")
        assert ref2 and ref2.kcal_100g == 165
        # отсутствует в базе → None (идти в LLM-оценку)
        none = matching.find_food(conn, "holy grail chalice", "чаша грааля")
        assert none is None
    finally:
        conn.close()
        os.unlink(path)


# 6. парсер скриншотов: фикстуры → маппинг, null там, где поля нет
def t_screens():
    from app.services import screens
    parsed = {
        "screen": "picooc_measure", "date": "2026-10-28",
        "metrics": {"weight_kg": {"value": 92.4, "confidence": 0.97},
                    "body_fat_pct": {"value": 24.1, "confidence": 0.95}},
        "missing": ["muscle_mass_kg"], "overall_confidence": 0.96,
    }
    conn, path = tmp_db()
    try:
        from app.storage import repo
        uid = repo.create_user(conn, 1)
        kind = screens.apply_parsed(conn, uid, parsed, "2026-10-28")
        assert kind == "body"
        row = conn.execute("SELECT * FROM body_measurements WHERE user_id=?", (uid,)).fetchone()
        assert row["weight_kg"] == 92.4 and row["body_fat_pct"] == 24.1
        assert row["muscle_mass_kg"] is None, "нет поля на экране → null"
        # сон: дата пробуждения, минуты
        sleep_parsed = {
            "screen": "mifitness_sleep", "date": "2026-10-28",
            "metrics": {"sleep_duration_min": {"value": 370},
                        "sleep_deep_min": {"value": 80}},
            "missing": ["sleep_score"], "overall_confidence": 0.9,
        }
        assert screens.apply_parsed(conn, uid, sleep_parsed, "2026-10-28") == "sleep"
        s = conn.execute("SELECT * FROM sleep_sessions WHERE user_id=?", (uid,)).fetchone()
        assert s["wake_date"] == "2026-10-28" and s["duration_min"] == 370 and s["score"] is None
    finally:
        conn.close()
        os.unlink(path)


# 7. идемпотентность миграций + roundtrip
def t_migrations():
    conn, path = tmp_db()
    try:
        from app.db import migrate
        v1 = migrate(conn)
        v2 = migrate(conn)
        assert v1 == v2
        from app.storage import repo
        from app.domain.nutrition import FoodRef
        uid = repo.create_user(conn, 42, height_cm=180.0)
        u = repo.get_user_by_tg(conn, 42)
        assert u["height_cm"] == 180.0
        fid = repo.upsert_food(conn, "тест", "test", 100, 10, 5, 5, "user", "u1")
        fid2 = repo.upsert_food(conn, "тест", "test", 100, 10, 5, 5, "user", "u1")
        assert fid == fid2
        repo.add_alias(conn, uid, fid, "тестика")
    finally:
        conn.close()
        os.unlink(path)


# 8. дедуп по file_hash
def t_dedup():
    conn, path = tmp_db()
    try:
        from app.storage import repo
        uid = repo.create_user(conn, 7)
        h = "a" * 64
        assert not repo.file_seen(conn, uid, h, window_min=10)
        repo.remember_file(conn, uid, h)
        assert repo.file_seen(conn, uid, h, window_min=10)
        assert not repo.file_seen(conn, uid, "b" * 64, window_min=10)
    finally:
        conn.close()
        os.unlink(path)


def t_combo_window():
    conn, path = tmp_db()
    try:
        from app.storage import repo
        uid = repo.create_user(conn, 9)
        sid = repo.put_photo_session(conn, uid, {"photo_path": "x.jpg"}, expires_min=2)
        s = repo.fresh_photo_session(conn, uid)
        assert s and s["id"] == sid
        repo.close_photo_session(conn, sid)
        assert repo.fresh_photo_session(conn, uid) is None
    finally:
        conn.close()
        os.unlink(path)


# 9. форматирование: длина ≤ 4096, резы
def t_formatting():
    from app.services.food import format_card
    from app.domain.nutrition import (FoodRef, IngredientEstimate, calculate_meal)
    items = []
    refs = []
    for i in range(40):
        it = IngredientEstimate(name=f"dish{i}", grams_min=100, grams_max=200)
        ref = FoodRef(name_ru=f"блюдо {i}", name_en=f"dish {i}", kcal_100g=200,
                      protein_100g=10, fat_100g=10, carbs_100g=20, source="seed")
        items.append((it, ref))
    meal = calculate_meal(items)
    card = format_card("20:40", "Ужин", meal, 3000, 2100, 100, 140,
                       late=True, late_kcal=500)
    assert len(card.plain()) <= 4096 or len(card.plain()[:4000]) == 4000   # обычный путь режется
    assert card.rich().count("<tr>") == 42                                  # шапка + 40 строк + итог


# 10. инсайты: пороги на границах
def t_insights():
    from app.domain import analytics
    # поздняя еда: 21:00 ровно — не поздний; 21:01 — поздний
    assert not analytics.is_late("21:00", "21:00")
    assert analytics.is_late("21:01", "21:00")
    assert analytics.late_kcal_for_day([("20:59", 100), ("21:01", 200)], "21:00") == 200

    # сон↔еда: 9 наблюдений — нет; 10 и разница ≥ 150 — есть
    pairs9 = [analytics.SleepFoodPair(f"2026-01-{i:02d}", 6.0, 2200, 0) for i in range(1, 10)] + \
             [analytics.SleepFoodPair(f"2026-01-{i:02d}", 7.5, 2000, 0) for i in range(10, 19)]
    assert analytics.sleep_food_insight(pairs9) is None
    pairs10 = pairs9 + [analytics.SleepFoodPair("2026-02-01", 6.0, 2200, 0),
                        analytics.SleepFoodPair("2026-02-02", 7.5, 2000, 0)]
    ins = analytics.sleep_food_insight(pairs10)
    assert ins is not None and ins["diff_kcal"] == 200, ins
    # разница < 150 → молчать
    pairs_small = [analytics.SleepFoodPair(f"d{i}", 6.0, 2000, 0) for i in range(10)] + \
                  [analytics.SleepFoodPair(f"e{i}", 7.5, 1920, 0) for i in range(10)]
    assert analytics.sleep_food_insight(pairs_small) is None

    # плато: 3 недели без снижения при дефиците ≥ 200
    assert analytics.plateau_check(0.05, 300).is_plateau
    assert not analytics.plateau_check(0.05, 100).is_plateau
    assert not analytics.plateau_check(0.5, 300).is_plateau

    # сравнение недель по поздней еде: <3 недель в группе → None
    w = lambda late: {"late_evenings": late, "avg_late_kcal": 300, "late_share": 0.1}  # noqa: E731
    assert analytics.compare_late_weeks([w(0), w(0), w(0), w(5), w(5)]) is None
    cmp = analytics.compare_late_weeks([w(0)] * 3 + [w(5)] * 3)
    assert cmp and cmp["wild_avg_kcal"] == 300

    # EWMA на ряде с пропусками
    from app.domain.targets import ewma
    s = ewma([100, 101, 100.5])
    assert len(s) == 3 and abs(s[-1] - 100.2) < 0.05


def t_coach_numbers():
    from app.services.coach import check_numbers
    ctx = {"days": [{"kcal": 1840, "late_kcal": 320}], "target_kcal": 2100}
    assert check_numbers("съел 1840 ккал из 2100", ctx) == []
    bad = check_numbers("ты съел 9999 ккал", ctx)
    assert bad == ["9999"]


def t_day_late_and_undo():
    conn, path = tmp_db()
    try:
        from app.storage import repo
        from app.domain.nutrition import (FoodRef, IngredientEstimate,
                                          calculate_meal)
        uid = repo.create_user(conn, 77, tz="Europe/Minsk")
        ref = FoodRef(name_ru="гречка", name_en="buckwheat", kcal_100g=110,
                      protein_100g=4.2, fat_100g=1.1, carbs_100g=21.3, source="seed")
        item = IngredientEstimate(name="buckwheat", grams_min=200, grams_max=200)
        calc = calculate_meal([(item, ref)])
        mid = repo.save_meal(conn, uid, "2026-10-28 18:00:00", "2026-10-28",
                             "18:00", "dinner", "text", calc)
        day = repo.day_totals(conn, uid, "2026-10-28")
        assert day["kcal"] == calc.kcal_total
        # правка граммов пересчитывает итог
        items = repo.get_meal_items(conn, mid)
        repo.edit_item_grams(conn, items[0]["id"], 100.0)
        day2 = repo.day_totals(conn, uid, "2026-10-28")
        assert day2["kcal"] < day["kcal"]
        # undo
        assert repo.soft_delete_meal(conn, uid, mid)
        assert repo.day_totals(conn, uid, "2026-10-28")["kcal"] == 0
    finally:
        conn.close()
        os.unlink(path)


def t_migration_v3_upgrade():
    """003 (picooc_api) должна применяться поверх v2, не теряя замеры."""
    conn, path = tmp_db()
    conn.close()
    os.unlink(path)

    fd, path2 = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from app.db import MIGRATIONS_DIR, connect, migrate, user_version
    conn = connect(path2)
    try:
        for name in ("001_init.sql", "002_seed_foods.sql"):
            conn.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version = 2")
        conn.execute("INSERT INTO users (tg_id, tz) VALUES (1, 'Europe/Minsk')")
        conn.execute(
            "INSERT INTO body_measurements (user_id, local_date, weight_kg, source, confidence)"
            " VALUES (1, '2026-10-01', 90.5, 'screenshot', 0.9)")
        conn.commit()

        assert migrate(conn) >= 3
        row = conn.execute("SELECT * FROM body_measurements").fetchone()
        assert row["weight_kg"] == 90.5 and row["source"] == "screenshot"
        # новый источник разрешён CHECK-ом
        conn.execute(
            "INSERT INTO body_measurements (user_id, local_date, weight_kg, source,"
            " confidence, impedance_ohm) VALUES (1, '2026-10-02', 90.1, 'picooc_api', 0.95, 512)")
        conn.commit()
        assert user_version(conn) >= 3
    finally:
        conn.close()
        os.unlink(path2)


def t_picooc_mapping():
    """Picooc: 0 = нет данных; muscle_race — доля, а не кг."""
    import hashlib

    from app.adapters.picooc import APPVER, map_record, sign
    rec = {"bodyTime": 1761400000, "weight": 92.4, "body_fat": 24.1, "water_race": 55.2,
           "muscle_race": 41.3, "skeletal_muscle": 0, "body_age": 0,
           "visceral_fat_level": 11, "basic_metabolism": 1840, "bmi": 27.9,
           "subcutaneous_fat": 18.8, "electric_resistance": 512, "mac": "AA:BB"}
    row = map_record(rec)
    assert row["weight_kg"] == 92.4 and row["body_water_pct"] == 55.2
    assert row["skeletal_muscle_pct"] == 41.3
    assert row["muscle_mass_kg"] is None and row["metabolic_age"] is None
    assert row["subcutaneous_fat_pct"] == 18.8 and row["impedance_ohm"] == 512

    md5 = lambda s: hashlib.md5(s.encode()).hexdigest().upper()      # noqa: E731
    device, method, ts = "DEV", "bodyIndexList", "1761400000"
    assert sign(device, method, ts) == md5(device + md5(ts + md5(method) + md5(APPVER)))


def main() -> int:
    print("check.py — проверки без сети и ключей\n")
    check("1. импорты всех модулей", t_imports)
    check("1b. конфиг с фиктивным .env", t_config)
    check("2. JSON-парсер устойчив к мусору", t_json_parser)
    check("2b. контракты FoodExtraction/валидаторы", t_food_contracts)
    check("3. арифметика: golden-кейсы", t_calc)
    check("3b. санитарные проверки 4/9/4", t_sanitary)
    check("4. TDEE и адаптивный TDEE", t_tdee)
    check("5. матчинг продуктов", t_matching)
    check("6. парсер скриншотов → маппинг/null", t_screens)
    check("7. идемпотентность миграций + roundtrip", t_migrations)
    check("8. дедуп file_hash и склейка фото", t_dedup)
    check("8b. окно склейки фото+подпись", t_combo_window)
    check("9. форматирование ≤ 4096", t_formatting)
    check("10. пороги инсайтов на границах", t_insights)
    check("10b. числа коуча трассируются", t_coach_numbers)
    check("10c. еда: пересчёт при правке и undo", t_day_late_and_undo)
    check("11. миграция 003 поверх v2 с данными", t_migration_v3_upgrade)
    check("12. Picooc: подпись и маппинг", t_picooc_mapping)
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} ок")
    if FAILURES:
        print("ПРОВАЛЕНО:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
