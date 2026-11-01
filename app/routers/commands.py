"""Команды: /today /week /weight /sleep /undo /add /target /export /settings."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from app import rich
from app.domain import analytics, body, water, weighin
from app.domain import safety as safetydom
from app.services import safety as safetysvc
from app.services import water as watersvc
from app.services.craving import week_lines as craving_week_lines
from app.domain.targets import (INTAKE_COVERAGE, MIN_AGE, MIN_BMI, adaptive_tdee, bmi,
                                compute_target, ewma_dated, kcal_floor_check, pal_from_pa,
                                value_at_or_before, weekly_slope)
from app.services import report
from app.services.food import meal_type_ru
from app.storage import repo
from app.textutil import esc
from app.timeutils import local_today, minutes_before, validate_hhmm, validate_timezone

router = Router(name="commands")


def _user(msg: Message, db: sqlite3.Connection):
    return repo.get_user_by_tg(db, msg.from_user.id)


_NOT_FASTING_WARNING = ("⚠️ Замеров натощак мало, поэтому тренд считаю по всем: он менее точный. "
                        "Взвешивайся утром после туалета и до еды, лучше всего в одно время.")


def _weight_points(rows) -> list[tuple[int, float]]:
    """Замеры веса → [(toordinal даты, кг)]: тренд считаем по датам, а не по номеру замера."""
    return [(date.fromisoformat(r["local_date"]).toordinal(), r["weight_kg"]) for r in rows]


def _target(db: sqlite3.Connection, uid: int):
    row = repo.current_target(db, uid)
    if row:
        return row["kcal_target"], int(row["protein_g"] or 120)
    return 2100, 120


@router.message(Command("today"))
async def cmd_today(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    today = local_today(user["tz"]).isoformat()
    chart = await report.day_chart_bytes(db, user, today)
    await rich.answer(msg, report.day_doc(db, user, today, chart=chart),
                      reply_markup=report.actions_kb("today"))


@router.message(Command("weighin"))
async def cmd_weighin(msg: Message, command: CommandObject, db: sqlite3.Connection) -> None:
    """Напоминание о взвешивании натощак: /weighin, /weighin 07:00, /weighin пн ср пт, /weighin off."""
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    reminder = repo.due_reminders(db, uid, "weigh")
    args = (command.args or "").strip().lower()
    if args:
        enabled = reminder["enabled"] if reminder else 1
        local_time = reminder["local_time"] if reminder else "07:30"
        days = None
        if args in ("off", "выкл", "нет"):
            enabled = 0
        else:
            if args.split()[0] in ("on", "вкл"):
                enabled, args = 1, " ".join(args.split()[1:])
            for token in args.split():
                if ":" in token:
                    parsed_time = weighin.normalize_time(token)
                    if parsed_time is None:
                        await msg.answer("Не понял время. Пример: /weighin 07:00")
                        return
                    local_time = parsed_time
            days = weighin.parse_days(args)
            if args and days is None and not any(":" in t for t in args.split()) and args.strip():
                await msg.answer("Не понял. Примеры: /weighin 07:00, /weighin пн ср пт, "
                                 "/weighin ежедневно, /weighin off")
                return
        repo.set_reminder(db, uid, "weigh", local_time, bool(enabled), days)
        reminder = repo.due_reminders(db, uid, "weigh")
    if not reminder:
        await msg.answer("Напоминание не настроено. Включить: /weighin on")
        return
    state = "включено" if reminder["enabled"] else "выключено"
    await msg.answer(
        f"⚖️ Напоминание о взвешивании {state}: {reminder['local_time']}, "
        f"{weighin.reminder_days_label(reminder['days'])}.\n"
        "Изменить: /weighin 07:00, /weighin пн ср пт, /weighin off.\n"
        "Как взвешиваться: утром после туалета, до еды и воды, в одно время и в одной одежде. "
        "Замеры не натощак бот в тренд не берёт, пока есть утренние.")


@router.message(Command("foods"))
async def cmd_foods(msg: Message, db: sqlite3.Connection) -> None:
    """Личные пресеты: что вы едите чаще всего и какую порцию бот помнит."""
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    rows = db.execute(
        """SELECT f.name_ru, uf.default_grams, uf.times_used FROM user_foods uf
           JOIN foods f ON f.id = uf.food_id WHERE uf.user_id=? AND f.deleted_at IS NULL
           ORDER BY uf.times_used DESC, uf.last_used_at DESC LIMIT 12""",
        (user["id"],)).fetchall()
    if not rows:
        await msg.answer("Пока пусто. Бот запоминает продукты, которые ты ешь, и порции: "
                         "назови вес («гречка 150 г») или поправь граммы кнопкой, и в следующий "
                         "раз он предложит твою порцию сам.")
        return
    doc = rich.Doc().text("🍽 Твои частые продукты:")
    doc.table(["Продукт", "Раз", "Порция"],
              [[r["name_ru"], str(r["times_used"]),
                f"{r['default_grams']:g} г" if r["default_grams"] else "—"] for r in rows],
              align="lrr")
    await rich.answer(msg, doc.text("Чтобы сбросить запомненные порции: /foods_reset"))


@router.message(Command("foods_reset"))
async def cmd_foods_reset(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    db.execute("UPDATE user_foods SET default_grams=NULL WHERE user_id=?", (user["id"],))
    db.execute("DELETE FROM food_aliases WHERE user_id=?", (user["id"],))
    db.commit()
    await msg.answer("Сбросил запомненные порции и формулировки. Частоту продуктов оставил.")


@router.message(Command("water"))
async def cmd_water(msg: Message, command: CommandObject, db: sqlite3.Connection) -> None:
    """/water — сколько выпито; /water 300 или /water 2 стакана — добавить; /water undo — отменить последнюю."""
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    arg = (command.args or "").strip()
    today = local_today(user["tz"]).isoformat()
    if not arg:
        total = repo.day_water_ml(db, uid, today)
        await msg.answer(
            f"💧 Сегодня {total} / {watersvc.goal_for(db, user)} мл.\n"
            "Добавить: просто напиши «выпил 2 стакана воды» или запиши голосовое. "
            "Либо /water 300, /water 0.5 л. Отменить последнюю: /water undo."
        )
        return
    if arg.lower() in ("undo", "отмена", "отменить"):
        last = repo.last_water(db, uid, today)
        if not last or not repo.delete_water(db, uid, last["id"]):
            await msg.answer("Сегодня нечего отменять.")
            return
        total = repo.day_water_ml(db, uid, today)
        await msg.answer(f"Отменил {last['ml']} мл. Сегодня {total} / "
                         f"{watersvc.goal_for(db, user)} мл.")
        return
    ml = water.parse_amount(arg)
    if ml is None or not water.MIN_ML <= ml <= water.MAX_ML:
        await msg.answer(f"Не понял объём. Примеры: /water 300, /water 2 стакана, "
                         f"/water 0.5 л (от {water.MIN_ML} до {water.MAX_ML} мл за раз).")
        return
    water_id, text = watersvc.log_water(db, user, ml, "command", arg)
    await msg.answer(text, reply_markup=watersvc.undo_keyboard(water_id))


@router.message(Command("week"))
async def cmd_week(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    today = local_today(user["tz"])
    chart = await report.week_chart_bytes(db, user, today)
    await rich.answer(msg, week_report_doc(db, user, today, chart=chart),
                      reply_markup=report.actions_kb("week"))


@router.message(Command("weight"))
async def cmd_weight(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    today = local_today(user["tz"])
    frm = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    rows, used_all = repo.trend_weight_series(db, uid, frm, today.strftime("%Y-%m-%d"))
    if not rows:
        await msg.answer("Пока нет замеров веса. Кидай скриншот PICOOC — распознаю.")
        return
    points = _weight_points(rows)
    smoothed = ewma_dated(points)
    weekly = weekly_slope(points)
    last = rows[-1]["weight_kg"]
    week_ago = value_at_or_before(smoothed, today.toordinal() - 7)
    week_delta = smoothed[-1][1] - week_ago if week_ago is not None else None
    doc = rich.Doc().text(f"⚖️ <b>Вес</b> · последний замер {rows[-1]['local_date'][5:]}")
    doc.image(await report.weight_chart_bytes(db, user, today))
    table = [["Последний замер", f"{last} кг"], ["Тренд (сглаженный)", f"{smoothed[-1][1]:.1f} кг"]]
    if week_delta is not None:
        table.append(["За неделю", f"{week_delta:+.1f} кг"])
    if weekly is not None and not user["care_mode"]:
        table.append(["Скорость (14 дн)", f"{weekly:+.1f} кг/нед"])
    if user["goal_weight_kg"]:
        table.append(["Цель", f"{user['goal_weight_kg']:g} кг"])
    doc.table(["Показатель", "Значение"], table, align="lr")
    bf = [r["weight_kg"] for r in db.execute(
        """SELECT body_fat_pct AS weight_kg FROM body_measurements
           WHERE user_id=? AND body_fat_pct IS NOT NULL ORDER BY local_date""", (uid,))]
    notes = ["Скачок веса за сутки: обычно вода и соль, на тренд он почти не влияет."]
    if len(bf) >= 2:
        notes.append(f"Жир: {bf[0]} % → {bf[-1]} %")
    if used_all:
        notes.append(_NOT_FASTING_WARNING)
    doc.heading("💡 Как читать")
    doc.bullets(notes + ["Для сравнения взвешивайся утром после туалета и до еды."])
    await rich.answer(msg, doc, reply_markup=report.actions_kb("weight"))


@router.message(Command("body"))
async def cmd_body(msg: Message, db: sqlite3.Connection) -> None:
    """Состав тела по последнему замеру Picooc: показатели, динамика, как их читать."""
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    if user["care_mode"]:
        await msg.answer("В осторожном режиме я не показываю цифры состава тела, чтобы они не "
                         "тревожили. Если хочешь их увидеть: /care off.")
        return
    rows = repo.latest_body_rows(db, user["id"], 2)
    if not rows:
        await msg.answer("Пока нет замеров. Пришли скриншот отчёта PICOOC, и я его разберу.")
        return
    age = None
    if user["birth_date"]:
        age = local_today(user["tz"]).year - int(str(user["birth_date"])[:4])
    latest = {k: rows[0][k] for k in rows[0].keys()}
    previous = {k: rows[1][k] for k in rows[1].keys()} if len(rows) > 1 else None
    when = rows[0]["local_date"]
    note = "" if rows[0]["fasting"] != 0 else (
        "(Замер сделан не натощак: вечером вес и вода в теле выше, сравнивай с такими же.)")
    chart = await report.body_chart_bytes(db, user, local_today(user["tz"]))
    await rich.answer(msg, body.summary_doc(latest, previous, user["sex"], age, when, note,
                                            chart=chart),
                      reply_markup=report.actions_kb("body"))


@router.message(Command("sleep"))
async def cmd_sleep(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    today = local_today(user["tz"])
    frm = (today - timedelta(days=13)).strftime("%Y-%m-%d")
    rows = repo.sleep_series(db, uid, frm, today.strftime("%Y-%m-%d"))
    if not rows:
        await msg.answer("Нет данных о сне. Кидай скриншот Mi Fitness.")
        return
    durations = [r["duration_min"] for r in rows if r["duration_min"]]
    avg = int(sum(durations) / len(durations))
    doc = rich.Doc().text(
        f"😴 Сон за 14 дней: {len(durations)} ночей, среднее {analytics.format_minutes(avg)}")
    doc.table(["Дата", "Сон"], [[r["wake_date"][5:], analytics.format_minutes(r["duration_min"])]
                                for r in rows[-7:]], align="lr")
    lines: list[str] = []
    # связка с едой следующего дня — только при n ≥ 10 и разнице ≥ 150
    pairs = []
    for r in rows:
        if not r["duration_min"]:
            continue
        next_day = (date.fromisoformat(r["wake_date"]) + timedelta(days=1)).strftime("%Y-%m-%d")
        day = repo.day_totals(db, uid, next_day)
        if day["meals"] > 0:
            pairs.append(analytics.SleepFoodPair(
                r["wake_date"], r["duration_min"] / 60, day["kcal"], 0))
    ins = analytics.sleep_food_insight(pairs)
    if ins:
        lines.append(f"🔗 После недосыпа (< 6.5 ч) ешь на {ins['diff_kcal']} ккал больше "
                     f"({ins['short_n']} против {ins['good_n']} дней) — наблюдение, не доказательство.")
    await rich.answer(msg, doc.text(*lines), reply_markup=report.actions_kb("sleep"))


@router.message(Command("undo"))
async def cmd_undo(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    meal = repo.get_last_meal(db, user["id"])
    if not meal:
        await msg.answer("Нечего отменять.")
        return
    created = datetime.fromisoformat(meal["created_at"]).replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > timedelta(minutes=10):
        await msg.answer("Последнюю запись можно отменить в течение 10 минут. "
                         "Для старой записи используй кнопку «Изменить» в её карточке.")
        return
    repo.soft_delete_meal(db, user["id"], meal["id"])
    await msg.answer(f"Отменил последнюю запись: {meal['local_time']} "
                     f"{meal_type_ru(meal['meal_type'])} {meal['kcal_total']} ккал.")


@router.message(Command("add"))
async def cmd_add(msg: Message, db: sqlite3.Connection, command: CommandObject | None = None) -> None:
    text = (command.args if command else None) or ""
    if not text:
        await msg.answer("Формат: /add <что съел> [граммы]. Например: /add творог 200")
        return
    from app.routers.intake import _process_meal
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    await _process_meal(msg, db, user, text=text, photo_path=None, source="manual")


@router.message(Command("target"))
async def cmd_target(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    row = repo.current_target(db, user["id"])
    if not row:
        await msg.answer("Цель ещё не считалась — заверши онбординг /start.")
        return
    await msg.answer(
        f"🎯 Цель: {row['kcal_target']} ккал/день · Б {row['protein_g']:g} / "
        f"Ж {row['fat_g']:g} / У {row['carbs_g']:g}\n"
        f"TDEE-оценка: {row['tdee_est']} · с {row['valid_from']} · причина: {row['reason']}\n"
        "Пересчёт — раз в неделю по факту (адаптивный TDEE), шаг ≤ ±150 ккал."
    )


@router.message(Command("settings"))
async def cmd_settings(msg: Message, db: sqlite3.Connection, command: CommandObject | None = None) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    arg = (command.args if command else "") or ""
    if arg.startswith("cutoff="):
        val = arg.split("=", 1)[1].strip()
        try:
            val = validate_hhmm(val)
        except ValueError as exc:
            await msg.answer(str(exc))
            return
        repo.update_user(db, user["id"], eating_cutoff=val)
        repo.set_reminder(db, user["id"], "kitchen_close", minutes_before(val, 15))
        await msg.answer(f"Кухня закрывается в {val}")
        return
    if arg.startswith("tz="):
        try:
            tz = validate_timezone(arg.split("=", 1)[1])
        except ValueError as exc:
            await msg.answer(str(exc))
            return
        repo.update_user(db, user["id"], tz=tz)
        await msg.answer("Часовой пояс обновил")
        return
    rems = db.execute("SELECT kind, local_time, enabled FROM reminders WHERE user_id=?",
                      (user["id"],)).fetchall()
    lines = [f"cutoff: {user['eating_cutoff']} (менять: /settings cutoff=20:30)",
             f"tz: {user['tz']} (менять: /settings tz=Europe/Minsk)",
             "Напоминания:"]
    for r in rems:
        lines.append(f"• {r['kind']} — {r['local_time']} — {'вкл' if r['enabled'] else 'выкл'}")
    await msg.answer("\n".join(lines)[:4000])


@router.message(Command("export"))
async def cmd_export(msg: Message, db: sqlite3.Connection) -> None:
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    uid = user["id"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["meals"])
    w.writerow(["id", "date", "time", "type", "kcal", "protein", "fat", "carbs", "source", "method"])
    for m in db.execute("SELECT * FROM meals WHERE user_id=? AND deleted_at IS NULL", (uid,)):
        w.writerow([m["id"], m["local_date"], m["local_time"], m["meal_type"], m["kcal_total"],
                    m["protein_g"], m["fat_g"], m["carbs_g"], m["source"], m["method"]])
    w.writerow([])
    w.writerow(["body"])
    w.writerow(["date", "weight_kg", "body_fat_pct", "muscle_mass_kg", "bmr_kcal", "source"])
    for b in db.execute("SELECT * FROM body_measurements WHERE user_id=? ORDER BY local_date", (uid,)):
        w.writerow([b["local_date"], b["weight_kg"], b["body_fat_pct"],
                    b["muscle_mass_kg"], b["bmr_kcal"], b["source"]])
    w.writerow([])
    w.writerow(["activity"])
    w.writerow(["date", "steps", "distance_km", "active_kcal", "resting_hr", "avg_hr"])
    for a in db.execute("SELECT * FROM activity_daily WHERE user_id=? ORDER BY local_date", (uid,)):
        w.writerow([a["local_date"], a["steps"], a["distance_km"], a["active_kcal"],
                    a["resting_hr"], a["avg_hr"]])
    w.writerow([])
    w.writerow(["water"])
    w.writerow(["date", "time", "ml", "source"])
    for r in db.execute("SELECT * FROM water_log WHERE user_id=? ORDER BY id", (uid,)):
        w.writerow([r["local_date"], r["local_time"], r["ml"], r["source"]])
    w.writerow([])
    w.writerow(["cravings"])
    w.writerow(["date", "time", "category", "outcome", "kcal_so_far", "kcal_target", "trigger"])
    for r in db.execute("SELECT * FROM craving_events WHERE user_id=? ORDER BY id", (uid,)):
        w.writerow([r["local_date"], r["local_time"], r["category"], r["outcome"],
                    r["kcal_so_far"], r["kcal_target"], r["trigger_text"]])
    w.writerow([])
    w.writerow(["sleep"])
    w.writerow(["wake_date", "duration_min", "deep_min", "light_min", "rem_min", "score"])
    for s in db.execute("SELECT * FROM sleep_sessions WHERE user_id=? ORDER BY wake_date", (uid,)):
        w.writerow([s["wake_date"], s["duration_min"], s["deep_min"], s["light_min"],
                    s["rem_min"], s["score"]])
    data = buf.getvalue().encode("utf-8")
    await msg.answer_document(
        BufferedInputFile(data, filename=f"export_{local_today(user['tz'])}.csv")
    )


# ------------------------------------------------------------ весы Picooc

@router.message(Command("sync_scale"))
async def cmd_sync_scale(msg: Message, db: sqlite3.Connection) -> None:
    """Ручной синк облака Picooc (неофициальный API). Скриншоты остаются рабочими."""
    user = _user(msg, db)
    if not user:
        await msg.answer("Сначала настрой профиль: /start")
        return
    from app.adapters.picooc import PicoocError
    from app.services.scale import sync_picooc

    status = await msg.answer("⚖️ синхронизирую весы Picooc…")
    try:
        res = await sync_picooc(db, user, days=30)
    except PicoocError as e:
        await status.edit_text(
            f"Не получилось: {e}\n\n"
            "Скриншот с экрана весов работает всегда — пришли его, если API недоступен."
        )
        return
    if not res["saved"]:
        await status.edit_text(
            f"Новых замеров за {res['window_days']} дней не нашёл "
            f"(получено записей: {res['fetched']})."
        )
        return
    await status.edit_text(
        f"⚖️ Записал {res['saved']} замеров за {res['window_days']} дней.\n"
        f"Последний: {res['newest_date']} — {res['newest_weight']} кг.\n"
        "Тренд и среднее: /weight"
    )


# ------------------------------------------------------------ недельный отчёт

def _week_stats(db: sqlite3.Connection, uid: int, end: date, target: int,
                tdee_est: int | None) -> dict | None:
    """Статистика недели, заканчивающейся `end`, для плато и недоучёта.
    None — данных мало: ≥5 дней с покрытием цели ≥80 % и ≥3 взвешиваний."""
    frm = (end - timedelta(days=6)).isoformat()
    by_day: dict[str, int] = {}
    counts: dict[str, int] = {}
    for m in repo.range_meals(db, uid, frm, end.isoformat()):
        by_day[m["local_date"]] = by_day.get(m["local_date"], 0) + m["kcal_total"]
        counts[m["local_date"]] = counts.get(m["local_date"], 0) + 1
    covered = [k for d, k in by_day.items() if k >= INTAKE_COVERAGE * target and counts[d] >= 2]
    points = _weight_points(repo.trend_weight_series(db, uid, frm, end.isoformat())[0])
    if len(covered) < 5 or len(points) < 3 or points[-1][0] - points[0][0] < 4:
        return None
    slope = weekly_slope(points, window_days=7)
    if slope is None or not tdee_est:
        return None
    avg = int(sum(covered) / len(covered))
    return {"slope": slope, "avg_kcal": avg, "deficit": tdee_est - avg}


def _data_quality_notes(db: sqlite3.Connection, uid: int, today: date, target: int) -> list[str]:
    """Плато и возможный недоучёт: осторожные гипотезы, не выводы."""
    tgt = repo.current_target(db, uid)
    tdee_est = tgt["tdee_est"] if tgt else None
    this_week = _week_stats(db, uid, today, target, tdee_est)
    if not this_week:
        return []
    notes: list[str] = []
    prev_week = _week_stats(db, uid, today - timedelta(days=7), target, tdee_est)

    def flagged(week: dict | None) -> bool:
        return bool(week) and week["deficit"] >= 200 and \
            week["slope"] > -(week["deficit"] * 7) / 7700 + 0.25

    weeks = 2 if flagged(this_week) and flagged(prev_week) else 1
    miss = analytics.underreporting(this_week["slope"], this_week["deficit"], weeks)
    if miss:
        notes.append(
            f"🔎 Вес снижается медленнее, чем должен: по записям ждали ≈{miss['expected_kg_week']:+.1f} "
            f"кг/нед, по факту {miss['actual_kg_week']:+.1f} уже {miss['weeks']} недели подряд. "
            "Чаще всего дело в еде, которую забыли записать (напитки, соусы, перекусы, "
            "масло при готовке) или в занижении порций. Попробуй 3 дня подряд взвешивать еду и "
            "записывать всё. Калории резать не нужно.")
    else:
        plateau = analytics.plateau_check(this_week["slope"], this_week["deficit"])
        if plateau.is_plateau:
            notes.append(
                "⏸ Похоже на плато: вес почти не меняется "
                f"({plateau.slope_kg_week:+.1f} кг/нед) при расчётном дефиците ≈{plateau.avg_deficit} ккал. "
                "Недельные колебания воды могут скрывать прогресс. Подожди ещё неделю-две, проверь сон "
                "и записи, а урезать еду ещё сильнее не стоит.")
    return notes


def weekly_report(db: sqlite3.Connection, uid: int, today: date | None = None,
                  as_lines: bool = False) -> str | list[str]:
    """Текст недельного отчёта (используется планировщиком в вс 19:00)."""
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    today = today or local_today(user["tz"])
    frm = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    to = today.strftime("%Y-%m-%d")
    meals = repo.range_meals(db, uid, frm, to)
    by_day: dict[str, list] = {}
    for m in meals:
        by_day.setdefault(m["local_date"], []).append(m)
    target, protein_tgt = _target(db, uid)
    kcal_days = {d: sum(m["kcal_total"] for m in v) for d, v in by_day.items()}
    avg = int(sum(kcal_days.values()) / len(kcal_days)) if kcal_days else 0
    late_evenings = [d for d, v in by_day.items()
                     if any(analytics.is_late(m["local_time"], user["eating_cutoff"]) for m in v)]
    weights, used_all_weights = repo.trend_weight_series(db, uid, frm, to)
    points = _weight_points(weights)
    slope = weekly_slope(points, window_days=7) if len(points) >= 2 else None
    lines = [f"📊 Неделя {frm[5:]}–{to[5:]}",
             f"Еда: {len(kcal_days)}/7 дней, среднее {avg} ккал (цель {target})",
             f"Поздние вечера: {len(late_evenings)}"]
    if points:
        smoothed = ewma_dated(points)
        lines.append(f"Вес (тренд): {smoothed[-1][1]:.1f} кг" +
                     (f", {slope:+.2f} кг/нед" if slope is not None and not user["care_mode"] else ""))
        if used_all_weights:
            lines.append(_NOT_FASTING_WARNING)
    act = repo.activity_series(db, uid, frm, to)
    steps = [r["steps"] for r in act if r["steps"] is not None]
    if steps:
        lines.append(f"Шаги: в среднем {int(sum(steps) / len(steps)):,}".replace(",", " "))
    water_days = repo.water_by_day(db, uid, frm, to)
    if water_days:
        lines.append(f"Вода: в среднем {int(sum(water_days.values()) / len(water_days))} мл "
                     f"({len(water_days)}/7 дней с записью, ориентир {watersvc.goal_for(db, user)})")
    slp = repo.sleep_series(db, uid, frm, to)
    dur = [r["duration_min"] for r in slp if r["duration_min"]]
    if dur:
        lines.append(f"Сон: среднее {analytics.format_minutes(int(sum(dur) / len(dur)))}")
    care = bool(user["care_mode"])
    if not care:
        lines += _data_quality_notes(db, uid, today, target)
    flags = safetysvc.assess(db, user, today)
    lines += safetysvc.weekly_lines(db, user, today, flags)
    lines += craving_week_lines(db, uid, frm, to)
    # Адаптивный TDEE требует до 14 дней, а сам отчёт остаётся недельным.
    adaptive_from = (today - timedelta(days=13)).isoformat()
    adaptive_to = today.isoformat()
    adaptive_meals = repo.range_meals(db, uid, adaptive_from, adaptive_to)
    adaptive_by_day: dict[str, list] = {}
    for meal in adaptive_meals:
        adaptive_by_day.setdefault(meal["local_date"], []).append(meal)
    intake_covered = {
        date.fromisoformat(day).toordinal(): sum(m["kcal_total"] for m in day_meals)
        for day, day_meals in adaptive_by_day.items()
        if sum(m["kcal_total"] for m in day_meals) >= INTAKE_COVERAGE * target
        and len(day_meals) >= 2
    }
    adaptive_weights, _ = repo.trend_weight_series(db, uid, adaptive_from, adaptive_to)
    if adaptive_weights:
        wp = _weight_points(adaptive_weights)
        age = (today.year - int(str(user["birth_date"])[:4])) if user["birth_date"] else 35
        height_cm = user["height_cm"] or 175
        weight_kg = adaptive_weights[-1]["weight_kg"]
        rate = user["target_rate_kg_week"]
        if age < MIN_AGE or bmi(weight_kg, height_cm) < MIN_BMI:
            rate = max(rate, 0.0)                      # без дефицита, как при онбординге
        res = adaptive_tdee(wp, intake_covered, target,
                            rate_kg_week=rate,
                            goal_weight_kg=user["goal_weight_kg"],
                            last_review_day=repo.last_adaptive_review_ordinal(db, uid),
                            today=today.toordinal())
        frozen = safetydom.freeze_target_decrease(flags, care)
        if res.new_target and frozen and res.new_target < target:
            lines.append("🎯 Цель по калориям не снижаю: сейчас важнее самочувствие.")
        elif res.new_target:
            t = compute_target(
                sex=user["sex"], age=age, weight_kg=weight_kg, height_cm=height_cm,
                pal=pal_from_pa(user["activity_pa"]), rate_kg_week=rate,
                goal_weight_kg=user["goal_weight_kg"],
            )
            # адаптация не должна уводить цель ниже безопасного минимума
            new_kcal, raised = kcal_floor_check(res.new_target, user["sex"], age,
                                                weight_kg, height_cm)
            repo.insert_target(db, uid, to, int(new_kcal), t.protein_g, t.fat_g,
                               t.carbs_g, res.tdee_est,
                               res.reason + ("_floor" if raised else ""))
            lines.append(f"🎯 Пересчитал цель по факту: {target} → {int(new_kcal)} ккал/день")
            if raised:
                lines.append("⚠️ По данным цель получалась ниже безопасного минимума — "
                             "оставил минимум. Если вес не идёт вниз, лучше обсудить питание с врачом.")
        elif res.need_more_days or res.need_more_weighs:
            need = res.need_more_days or res.need_more_weighs
            lines.append(f"🎯 Цель не трогаю: нужно ещё ~{need} дней данных")
    if as_lines:
        return lines
    return "\n".join(lines)[:4000]


def week_report_doc(db: sqlite3.Connection, user, today: date, chart: bytes | None = None):
    """Недельный разбор документом: таблицы, выводы и советы + подробности прежнего отчёта."""
    lines = weekly_report(db, user["id"], today=today, as_lines=True)
    return report.week_doc(db, user, today, lines, chart=chart)
