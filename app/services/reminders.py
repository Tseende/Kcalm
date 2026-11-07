"""Свои напоминания: шаблоны, проверка полей, «умный пропуск» и расчёт, когда слать.

Системные напоминания (утро, день, вечер…) живут в таблице reminders и в profile.py, а эти —
в user_reminders: их создаёт сам человек по шаблону или с нуля. В текстах нет калорий и нет
обращения в мужском/женском роде.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app.storage import repo
from app.timeutils import validate_hhmm

MAX_PER_USER = 20
MAX_TITLE = 60
MAX_TEXT = 300
EVERY_MIN_ALLOWED = (30, 60, 90, 120, 180, 240)

# key → шаблон. screen — экран Mini App для кнопки в сообщении (None: просто текст).
# skip — правило «не слать, если уже сделано сегодня». care_hidden — скрыт в осторожном режиме.
TEMPLATES: dict[str, dict] = {
    "breakfast": {"title": "Завтрак", "time": "08:30", "screen": "add_meal", "button": "Записать завтрак",
                  "skip": "breakfast", "text": "Время завтрака. Запиши, что на тарелке: хватит пары слов или фото."},
    "lunch": {"title": "Обед", "time": "13:00", "screen": "add_meal", "button": "Записать обед",
              "skip": "lunch", "text": "Пора пообедать. Запиши обед: хватит пары слов или фото."},
    "dinner": {"title": "Ужин", "time": "19:00", "screen": "add_meal", "button": "Записать ужин",
               "skip": "dinner", "text": "Время ужина. Запиши, что на тарелке: пары слов хватит."},
    "snack": {"title": "Перекус", "time": "16:30", "screen": "add_meal", "button": "Записать перекус",
              "skip": None, "text": "Время перекуса. Если что-то перекушено, запиши: хватит пары слов."},
    "water": {"title": "Вода", "time": "10:00", "every_min": 120, "until_time": "20:00", "screen": "drinks",
              "button": "Записать воду", "skip": "water",
              "text": "Глоток воды сейчас не помешает. Выпей стакан и отметь в приложении."},
    "steps": {"title": "Внести шаги", "time": "21:30", "screen": "add_activity", "button": "Внести шаги",
              "skip": "steps", "text": "Загляни в счётчик шагов и внеси итог за день."},
    "sleep": {"title": "Записать сон", "time": "09:00", "screen": "add_sleep", "button": "Записать сон",
              "skip": "sleep", "text": "Запиши сон за ночь: так видно, как идёт восстановление."},
    "weigh": {"title": "Взвеситься", "time": "07:30", "screen": "add_weight", "button": "Записать вес",
              "skip": "weigh", "care_hidden": True,
              "text": "Утреннее взвешивание: после туалета, до еды и воды. "
                      "Одна цифра ничего не значит, смотрим на тренд."},
    "walk": {"title": "Прогулка", "time": "18:00", "screen": None, "skip": None,
             "text": "Время прогулки. Даже 15 минут на свежем воздухе помогают."},
    "vitamins": {"title": "Витамины / таблетки", "time": "09:00", "screen": None, "skip": None,
                 "text": "Время витаминов и таблеток."},
    "bedtime": {"title": "Пора готовиться ко сну", "time": "22:30", "screen": None, "skip": None,
                "text": "Пора готовиться ко сну: приглуши свет и отложи дела на завтра."},
    "screen_off": {"title": "Отложить телефон", "time": "23:00", "screen": None, "skip": None,
                   "text": "Пора отложить телефон: так проще уснуть."},
}
DEFAULT_BUTTON = "Открыть приложение"


class ReminderError(ValueError):
    """Ошибка проверки поля; текст можно показывать человеку."""


class ReminderLimit(ReminderError):
    pass


def templates_json(user) -> list[dict]:
    care = bool(user["care_mode"])
    out = []
    for key, t in TEMPLATES.items():
        out.append({"key": key, "title": t["title"], "time": t["time"], "text": t["text"],
                    "every_min": t.get("every_min"), "until_time": t.get("until_time"),
                    "available": not (care and t.get("care_hidden"))})
    return out


def _clean_days(value) -> str | None:
    """Дни приходят строкой цифр или списком; все семь дней = «каждый день» (None)."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    elif isinstance(value, str):
        parts = list(value)
    else:
        raise ReminderError("Дни недели: выберите нужные дни.")
    if any(p not in "0123456" or len(p) != 1 for p in parts) or len(set(parts)) != len(parts):
        raise ReminderError("Дни недели: выберите нужные дни.")
    days = "".join(sorted(parts))
    return None if days == "0123456" else days


def _hhmm(value, label: str) -> str:
    if not isinstance(value, str):
        raise ReminderError(f"{label}: время нужно в формате ЧЧ:ММ.")
    try:
        return validate_hhmm(value)
    except ValueError:
        raise ReminderError(f"{label}: время нужно в формате ЧЧ:ММ, например 21:00.") from None


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def clean_fields(data: dict, current: dict | None = None) -> dict:
    """Проверенные поля для записи. current — текущие значения (для PATCH): итог проверяется целиком."""
    out = dict(current or {})
    if "title" in data:
        title = data["title"]
        if not isinstance(title, str) or not title.strip():
            raise ReminderError("Напишите название напоминания.")
        if len(title.strip()) > MAX_TITLE:
            raise ReminderError(f"Название не длиннее {MAX_TITLE} символов.")
        out["title"] = title.strip()
    if "text" in data:
        text = data["text"]
        if text is not None and not isinstance(text, str):
            raise ReminderError("Текст сообщения должен быть строкой.")
        text = (text or "").strip()
        if len(text) > MAX_TEXT:
            raise ReminderError(f"Текст не длиннее {MAX_TEXT} символов.")
        out["text"] = text
    if "time" in data:
        out["local_time"] = _hhmm(data["time"], "Время")
    if "days" in data:
        out["days"] = _clean_days(data["days"])
    if "every_min" in data:
        em = data["every_min"]
        if em in (None, 0, ""):
            out["every_min"] = None
        elif isinstance(em, bool) or not isinstance(em, int) or em not in EVERY_MIN_ALLOWED:
            raise ReminderError("Повтор: каждые 30 минут, 1, 1,5, 2, 3 или 4 часа.")
        else:
            out["every_min"] = em
    if "until_time" in data:
        ut = data["until_time"]
        out["until_time"] = None if ut in (None, "") else _hhmm(ut, "Повтор до")
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise ReminderError("enabled: true или false.")
        out["enabled"] = int(data["enabled"])
    if not out.get("every_min"):
        out["every_min"] = None
        out["until_time"] = None
    elif not out.get("until_time"):
        raise ReminderError("Укажите, до какого времени повторять.")
    elif _minutes(out["until_time"]) <= _minutes(out["local_time"]):
        raise ReminderError("Повтор «до» должен быть позже времени начала.")
    return out


def _row_json(r) -> dict:
    return {"id": r["id"], "template": r["template"], "title": r["title"], "text": r["text"] or "",
            "time": r["local_time"], "days": r["days"] or "", "every_min": r["every_min"],
            "until_time": r["until_time"], "enabled": bool(r["enabled"])}


def list_json(db: sqlite3.Connection, user) -> list[dict]:
    rows = db.execute("SELECT * FROM user_reminders WHERE user_id=? ORDER BY local_time, id",
                      (user["id"],)).fetchall()
    return [_row_json(r) for r in rows]


def get_row(db: sqlite3.Connection, user, rid: int):
    return db.execute("SELECT * FROM user_reminders WHERE id=? AND user_id=?", (rid, user["id"])).fetchone()


def create(db: sqlite3.Connection, user, data: dict) -> dict:
    """Из шаблона (data['template']) с правками или с нуля ('custom')."""
    key = data.get("template") or "custom"
    if not isinstance(key, str):
        raise ReminderError("Такого шаблона нет.")
    base: dict = {"template": "custom", "enabled": 1, "days": None, "every_min": None, "until_time": None,
                  "text": ""}
    if key != "custom":
        t = TEMPLATES.get(key)
        if t is None:
            raise ReminderError("Такого шаблона нет.")
        if user["care_mode"] and t.get("care_hidden"):
            raise ReminderError("Этот шаблон сейчас недоступен.")
        base.update(template=key, title=t["title"], text=t["text"], local_time=t["time"],
                    every_min=t.get("every_min"), until_time=t.get("until_time"))
    else:
        if not str(data.get("title") or "").strip() or not str(data.get("text") or "").strip():
            raise ReminderError("Напишите название и текст сообщения.")
        if "time" not in data:
            raise ReminderError("Выберите время.")
    clean = clean_fields({k: v for k, v in data.items() if k != "template"}, base)
    if not clean.get("text"):
        raise ReminderError("Напишите текст сообщения.")
    count = db.execute("SELECT COUNT(*) FROM user_reminders WHERE user_id=?", (user["id"],)).fetchone()[0]
    if count >= MAX_PER_USER:
        raise ReminderLimit(f"Можно создать не больше {MAX_PER_USER} напоминаний.")
    cur = db.execute(
        """INSERT INTO user_reminders (user_id, template, title, text, local_time, days, every_min,
           until_time, enabled) VALUES (?,?,?,?,?,?,?,?,?)""",
        (user["id"], clean["template"], clean["title"], clean["text"], clean["local_time"], clean["days"],
         clean["every_min"], clean["until_time"], clean["enabled"]))
    db.commit()
    return _row_json(get_row(db, user, cur.lastrowid))


def update(db: sqlite3.Connection, user, rid: int, data: dict) -> dict | None:
    row = get_row(db, user, rid)
    if row is None:
        return None
    current = {"title": row["title"], "text": row["text"] or "", "local_time": row["local_time"],
               "days": row["days"] or None, "every_min": row["every_min"], "until_time": row["until_time"],
               "enabled": row["enabled"]}
    clean = clean_fields(data, current)
    if not clean.get("text") and row["template"] == "custom":
        raise ReminderError("Напишите текст сообщения.")
    # сменилось время или повтор — разрешаем снова отправить в ту же минуту
    reset = (clean["local_time"], clean["every_min"], clean["until_time"]) != (
        row["local_time"], row["every_min"], row["until_time"])
    db.execute(
        """UPDATE user_reminders SET title=?, text=?, local_time=?, days=?, every_min=?, until_time=?,
           enabled=?""" + (", last_sent_at=NULL" if reset else "") + " WHERE id=? AND user_id=?",
        (clean["title"], clean["text"], clean["local_time"], clean["days"], clean["every_min"],
         clean["until_time"], clean["enabled"], rid, user["id"]))
    db.commit()
    return _row_json(get_row(db, user, rid))


def delete(db: sqlite3.Connection, user, rid: int) -> bool:
    cur = db.execute("DELETE FROM user_reminders WHERE id=? AND user_id=?", (rid, user["id"]))
    db.commit()
    return cur.rowcount > 0


# ------------------------------------------------------------------ когда слать

def is_due(local_time: str, days: str | None, every_min: int | None, until_time: str | None,
           now: datetime) -> bool:
    if days and str(now.weekday()) not in days:
        return False
    cur = now.hour * 60 + now.minute
    try:
        start = _minutes(local_time)
        if not every_min:
            return cur == start
        end = _minutes(until_time) if until_time else 23 * 60 + 59
    except (ValueError, TypeError):
        return False
    return start <= cur <= end and (cur - start) % every_min == 0


def row_is_due(r, now: datetime) -> bool:
    return is_due(r["local_time"], r["days"], r["every_min"], r["until_time"], now) \
        and r["last_sent_at"] != now.strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ умный пропуск

def should_skip(db: sqlite3.Connection, user, rule: str | None, now: datetime) -> bool:
    """True, если за сегодня уже всё записано и напоминание только раздражало бы."""
    if not rule:
        return False
    uid, today, hhmm = user["id"], now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    if rule in ("breakfast", "lunch", "dinner"):
        start = {"breakfast": "05:00", "lunch": "11:00", "dinner": "17:00"}[rule]
        row = db.execute(
            """SELECT 1 FROM meals WHERE user_id=? AND local_date=? AND deleted_at IS NULL
               AND (meal_type=? OR (local_time >= ? AND local_time <= ?)) LIMIT 1""",
            (uid, today, rule, start, hhmm)).fetchone()
        return row is not None
    if rule == "water":
        from app.services import water as watersvc
        return repo.day_water_ml(db, uid, today) >= watersvc.goal_for(db, user)
    if rule == "steps":
        return db.execute("SELECT 1 FROM activity_daily WHERE user_id=? AND local_date=? AND steps IS NOT NULL "
                          "LIMIT 1", (uid, today)).fetchone() is not None
    if rule == "sleep":
        return db.execute("SELECT 1 FROM sleep_sessions WHERE user_id=? AND wake_date=? LIMIT 1",
                          (uid, today)).fetchone() is not None
    if rule == "weigh":
        return bool(repo.weight_series(db, uid, today, today))
    return False
