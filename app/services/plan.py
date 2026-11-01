"""Правила «если … то …» (implementation intentions) и уроки после эпизодов.

Идея: решение, принятое заранее и привязанное к ситуации, срабатывает легче, чем попытка
собраться с силами в момент тяги. Правила пишете вы сами или берёте готовые.
"""
from __future__ import annotations

import re
import sqlite3

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain import craving as craving_dom
from app.domain.weighin import normalize_time
from app.storage import repo
from app.textutil import esc

MIN_LEN, MAX_LEN = 3, 160

# (категория, если, то, время напоминания или None)
PRESETS = [
    ("hunger", "вечером реально голодно", "поем с белком на тарелку (творог, кефир, яйцо), а не буду терпеть", None),
    ("emotion", "тянет есть от стресса или обиды", "10 медленных вдохов и чай, подожду 15 минут и только потом решу", None),
    ("habit", "иду на кухню без голода", "выпью стакан воды и займусь чем-то другим на 15 минут", None),
    ("tired", "хочется есть, а на самом деле хочу спать", "лягу пораньше или выпью тёплый чай", None),
    (None, "поужинал(а)", "почищу зубы и закрою кухню", "21:00"),
]

LESSONS = {
    "hunger": ["Поем нормальный ужин с белком, чтобы вечером не было голода",
               "Добавлю белковый перекус днём, около 16–17 часов",
               "Не буду пропускать обед"],
    "emotion": ["Сначала 10 дыханий и чай, 15 минут, потом решу",
                "Напишу боту, что случилось, прежде чем идти на кухню",
                "Позвоню или напишу близкому человеку"],
    "habit": ["Выпью воды и вернусь к делу на 15 минут",
              "После ужина выключу свет на кухне и почищу зубы",
              "Не буду есть перед экраном"],
    "tired": ["Лягу спать пораньше",
              "Чай и душ вместо перекуса",
              "Заранее приготовлю лёгкий ужин"],
    "overate": ["Вернусь к плану со следующего приёма пищи, без компенсации",
                "Лягу спать, а завтра обычный завтрак с белком",
                "Не буду держать сладкое на виду"],
    "unknown": ["Сделаю паузу на 15 минут перед едой",
                "Выпью воды и пойму, голод это или нет",
                "Лягу спать пораньше"],
}

IF_BY_CATEGORY = {
    "hunger": "вечером реально голодно", "emotion": "тянет есть от стресса или эмоций",
    "habit": "тянет поесть по привычке или от скуки", "tired": "хочется есть от усталости",
    "overate": "случилось переедание", "unknown": "ночью тянет переесть",
}

_RULE = re.compile(
    r"^(?:в\s+(?P<time>\d{1,2}:\d{2})\s+)?если\s+(?P<if>.+?)[,;:]?\s+то\s+(?P<then>.+)$",
    re.IGNORECASE | re.DOTALL)


def parse_rule(text: str) -> tuple[str, str, str | None] | str:
    """«[в 21:30] если ..., то ...» → (если, то, время) или текст ошибки."""
    m = _RULE.match(text.strip())
    if not m:
        return ("Напиши правило так: если тянет сладкого после ужина, то чай и 15 минут. "
                "Можно со временем напоминания: в 21:30 если ..., то ...")
    if_text, then_text = m.group("if").strip(), m.group("then").strip()
    remind = None
    if m.group("time"):
        remind = normalize_time(m.group("time"))
        if remind is None:
            return "Не понял время напоминания. Пример: в 21:30 если ..., то ..."
    for part in (if_text, then_text):
        if not MIN_LEN <= len(part) <= MAX_LEN:
            return f"Каждая часть правила: от {MIN_LEN} до {MAX_LEN} символов."
    if craving_dom.violates_policy(then_text):
        return ("Такое правило я не сохраню: голодание и «отработка» только раскачивают качели. "
                "Предложи действие, которое заботится о тебе: вода, чай, сон, нормальный приём пищи.")
    return if_text, then_text, remind


def add_user_rule(db: sqlite3.Connection, user_id: int, text: str) -> str:
    parsed = parse_rule(text)
    if isinstance(parsed, str):
        return parsed
    if_text, then_text, remind = parsed
    rule_id = repo.add_rule(db, user_id, if_text, then_text, None, remind)
    if rule_id is None:
        return (f"Не получилось: либо такое правило уже есть, либо их уже {repo.MAX_RULES}. "
                "Удалить лишнее: /plan del 2")
    return f"🧭 Добавил: если {esc(if_text)}, то {esc(then_text)}" + \
        (f" (напомню в {remind})" if remind else "")


def format_rule(rule: sqlite3.Row, number: int | None = None) -> str:
    prefix = f"{number}. " if number else ""
    clock = f" ⏰{rule['remind_time']}" if rule["remind_time"] else ""
    return f"{prefix}Если {esc(rule['if_text'])}, то {esc(rule['then_text'])}{clock}"


def rules_text(db: sqlite3.Connection, user_id: int, category: str | None) -> str | None:
    """Правила для показа в момент тяги."""
    rules = repo.rules_for_category(db, user_id, category)
    if not rules:
        return None
    return "🧭 Твои правила, которые ты придумал(а) заранее:\n" + \
        "\n".join(f"• {format_rule(r)}" for r in rules)


def presets_keyboard(db: sqlite3.Connection, user_id: int) -> InlineKeyboardMarkup | None:
    have = {r["if_text"].lower() for r in repo.list_rules(db, user_id)}
    rows = [[InlineKeyboardButton(text=f"➕ {p[1][:40]}", callback_data=f"pl:add:{i}")]
            for i, p in enumerate(PRESETS) if p[1].lower() not in have]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def add_preset(db: sqlite3.Connection, user_id: int, index: int) -> str:
    if not 0 <= index < len(PRESETS):
        return "Такого готового правила нет."
    category, if_text, then_text, remind = PRESETS[index]
    if repo.add_rule(db, user_id, if_text, then_text, category, remind) is None:
        return "Это правило уже есть или лимит исчерпан (/plan)."
    return f"🧭 Добавил: если {esc(if_text)}, то {esc(then_text)}"


def overview(db: sqlite3.Connection, user_id: int) -> str:
    rules = repo.list_rules(db, user_id)
    lines = ["🧭 Правила «если — то»: решение, принятое заранее, срабатывает легче, чем "
             "попытка собраться с силами в момент тяги."]
    if rules:
        lines += ["", *[format_rule(r, i) for i, r in enumerate(rules, 1)]]
    else:
        lines += ["", "Пока пусто."]
    lines += ["", "Добавить своё: /plan если тянет сладкого после ужина, то чай и 15 минут",
              "С напоминанием: /plan в 21:30 если ..., то ...",
              "Удалить: /plan del 2"]
    return "\n".join(lines)


def delete_by_number(db: sqlite3.Connection, user_id: int, number: int) -> bool:
    rules = repo.list_rules(db, user_id)
    if not 1 <= number <= len(rules):
        return False
    return repo.delete_rule(db, user_id, rules[number - 1]["id"])


# ----------------------------------------------------------------------- уроки

def lesson_keyboard(event_id: int, category: str) -> InlineKeyboardMarkup:
    options = LESSONS.get(category, LESSONS["unknown"])
    rows = [[InlineKeyboardButton(text=text[:60], callback_data=f"cr:less:{event_id}:{i}")]
            for i, text in enumerate(options)]
    rows.append([InlineKeyboardButton(text="✍️ Напишу своё", callback_data=f"cr:less:{event_id}:own")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lesson_by_index(category: str, index: int) -> str | None:
    options = LESSONS.get(category, LESSONS["unknown"])
    return options[index] if 0 <= index < len(options) else None


def lesson_saved_text(lesson: str) -> str:
    return (f"📝 Записал: «{esc(lesson)}».\n"
            "А теперь главное: следующий приём пищи по плану, без компенсации и без голода. "
            "Один эпизод не отменяет всего, что ты уже сделал(а).")


def rule_from_lesson(db: sqlite3.Connection, user_id: int, event: sqlite3.Row) -> str:
    lesson = event["lesson"]
    if not lesson:
        return "Сначала запиши, что поможет в следующий раз."
    category = event["category"] if event["category"] != "unknown" else None
    if_text = IF_BY_CATEGORY.get(event["category"], IF_BY_CATEGORY["unknown"])
    if repo.add_rule(db, user_id, if_text, lesson[:MAX_LEN], category, None) is None:
        return "Такое правило уже есть или лимит исчерпан (/plan)."
    return f"🧭 Добавил правило: если {esc(if_text)}, то {esc(lesson)}. Смотреть: /plan."
