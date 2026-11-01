"""Фразы своими словами → команды бота. Детерминированно, без LLM и без сети.

Идея: каждая команда должна срабатывать и без слэша («что я сегодня ел?», «отмени последнее»,
«напомни взвеситься в 7:00»). Всё, что не опознано, остаётся едой или идёт ассистенту, если
это вопрос.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain import tracking as trk
from app.domain.dishes import DISH_ADD_RE, DISH_LIST_RE, DISH_SHORT_RE
from app.domain.products import PRODUCT_ADD_RE, PRODUCT_LIST_RE
from app.domain.weighin import normalize_time, parse_days


@dataclass
class Intent:
    name: str
    arg: str = ""


def _norm(text: str) -> str:
    t = text.lower().replace("ё", "е").strip()
    return re.sub(r"\s+", " ", t)


def _any(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


_QUESTION_START = re.compile(
    r"^(что|как|сколько|почему|зачем|когда|где|какой|какая|какие|какое|покажи|расскажи|объясни|"
    r"посоветуй|подскажи|сравни|помоги|можно ли|стоит ли|нужно ли|дай совет|скажи|"
    r"а если|а что|а как|а сколько|чем)\b")
_FOOD_LOG = re.compile(r"\b(?:съел|съела|поел|поела|скушал|скушала|выпил|выпила)\b")


def is_question(text: str) -> bool:
    """Вопрос или просьба, а не запись еды."""
    t = _norm(text)
    if _QUESTION_START.match(t):
        return True
    return t.endswith("?") and not _FOOD_LOG.search(t)


def detect(text: str) -> Intent | None:
    """Распознать явную команду. None — не команда."""
    t = _norm(text)
    if not t or len(t) > 300:
        return None

    # --- справка и вежливость
    if _any(r"^(что ты умеешь|что умеешь|помощь|помоги разобраться|help|команды|как (тобой )?пользоваться)\W*$", t):
        return Intent("help")
    if _any(r"^(привет|здравствуй(те)?|хай|добрый (день|вечер|утро)|доброе утро)\W*$", t):
        return Intent("greeting")
    if _any(r"^(спасибо|благодарю|спс)\W*$", t):
        return Intent("thanks")

    # --- посуда (до еды: «посуда кружка 350 мл» не должна стать записью приёма пищи)
    raw = text.strip()
    m = DISH_ADD_RE.match(raw) or DISH_SHORT_RE.match(raw)
    if m:
        return Intent("dish_add", m.group(1).strip())
    if DISH_LIST_RE.match(raw):
        return Intent("dishes")

    # --- свои продукты: до еды, иначе «добавь продукт: ... 121 ккал» стал бы записью приёма
    m = PRODUCT_ADD_RE.match(raw)
    if m and m.group(1).strip():
        return Intent("product_add", raw)
    if PRODUCT_LIST_RE.match(raw):
        return Intent("products")

    # --- сон, шаги, тренировки: «спал 7 часов», «10 тысяч шагов», «зал 1 час»
    if trk.parse_sleep_text(raw):
        return Intent("sleep_log", raw)
    if trk.parse_steps_text(raw):
        return Intent("steps_log", raw)
    if trk.parse_workout_text(raw):
        return Intent("workout_log", raw)

    # --- отмена
    if _any(r"^(отмени(ть)?|удали(ть)?) (пожалуйста )?(последн\w*|запись|это|приём пищи|прием пищи)\b|"
            r"^не то записал\w*|^ошибся записью", t):
        return Intent("undo")

    # --- осторожный режим
    if _any(r"(включи|включить|хочу|давай)\b.*осторожный режим", t):
        return Intent("care_on")
    if _any(r"(выключи|выключить|отключи|отключить)\b.*осторожный режим", t):
        return Intent("care_off")

    # --- правила «если-то»
    m = re.search(r"(?:добавь|создай|запомни|запиши) (?:мне )?правило:? ?(.+)$", t)
    if m and _any(r"\bесли\b.+\bто\b", m.group(1)):
        return Intent("plan_add", m.group(1))
    if _any(r"^(мои |покажи (мои )?|список )правила\W*$|^что у меня в плане\W*$", t):
        return Intent("plan_list")

    # --- напоминание о взвешивании
    if _any(r"напом\w*.*(взвес|взвеш|вес)|(взвес|взвеш|вес).*напом", t):
        if _any(r"\b(выключи|отключи|убери|не надо|не напоминай|отмени)\b", t):
            return Intent("weighin", "off")
        if _any(r"\b(включи|верни)\b", t) and not re.search(r"\d{1,2}:\d{2}", t):
            return Intent("weighin", "on")
        parts: list[str] = []
        time_match = re.search(r"\b(\d{1,2}[:.]\d{2})\b", t)
        if time_match:
            hhmm = normalize_time(time_match.group(1).replace(".", ":"))
            if hhmm:
                parts.append(hhmm)
        else:
            hour = re.search(r"\bв (\d{1,2})(?: ?(?:час\w*|утра))?\b", t)
            if hour and 0 <= int(hour.group(1)) <= 23:
                parts.append(f"{int(hour.group(1)):02d}:00")
        days = parse_days(t)
        if days:
            parts.append("ежедневно" if days == "0123456" else " ".join(
                "пн вт ср чт пт сб вс".split()[int(d)] for d in days))
        elif _any(r"каждый день|ежедневно", t):
            parts.append("ежедневно")
        return Intent("weighin", " ".join(parts))

    # --- вода: сколько выпито (запись воды разбирается раньше, в water.extract_water)
    if _any(r"сколько (воды )?(я )?(сегодня )?(выпил|выпила|воды)|как (у меня )?(с )?вод[ао]|сколько жидкости", t):
        return Intent("water_status")

    # --- состав тела с весов
    if _any(r"(показател\w*|результат\w*|данные|состав\w*|замер\w*|отчет\w*)"
            r".*(весов|взвешиван\w*|тела|picooc|пикук)|"
            r"(процент|уровень)\w* жира|висцеральн\w*|состав тела|мои показатели|жир и мышцы|"
            r"как (мой )?(процент )?жир", t):
        return Intent("body")
    # --- «статистика» без уточнения: по умолчанию итоги дня
    if _any(r"\b(статистик\w*|сводк\w*)", t):
        if _any(r"\bсн[аеу]?\b|\bсон\w*", t):
            return Intent("sleep")
        if _any(r"\bвес\w*", t):
            return Intent("weight")
        if _any(r"недел", t):
            return Intent("week")
        if _any(r"\bвод[аыуе]\b", t):
            return Intent("water_status")
        return Intent("today")
    # --- отчёты
    if _any(r"(сон|спал|спала|выспал\w*)", t) and _any(r"(как|покажи|сколько|за)", t):
        return Intent("sleep")
    if _any(r"(недел\w*)", t) and _any(r"(как|итог|покажи|отчет|прошла|результат|сколько)", t):
        return Intent("week")
    if _any(r"\b(вес|вешу|взвешивал\w*|тренд веса)\b", t) and _any(
            r"(какой|как|покажи|сколько|тренд|динамик\w*)", t):
        return Intent("weight")
    if _any(r"(сколько|какой|какая) .*(надо|нужно|можно|лимит|норм\w*)", t) and _any(
            r"(калор|ккал|есть|съесть|белк|день|лимит|норм)", t) and not _any(
            r"осталось|ещ[её]|уже|сегодня", t):
        return Intent("target")
    if _any(r"(сегодня|день|итог дня|дня)\b", t) and _any(
            r"(что|сколько|как|покажи|итог|осталось)", t) and _any(
            r"(ел|ела|съел|съела|калор|ккал|осталось|итог|день|поел)", t):
        return Intent("today")
    m = re.match(r"^сколько (?:калорий|ккал|белка|белков)\s+(?:в|во|у)\s+(.+?)\??$", t)
    if m:
        return Intent("food_info", m.group(1).strip())
    if _any(r"(какая|какой) (у меня |моя |мой )?(цель|норма)", t) or \
            _any(r"сколько (мне )?(можно |нужно )?(калорий|ккал)(?!\s+(?:в|во|на|у|осталось|ещ[её])\b)", t) or \
            _any(r"^(моя цель|цель на день|дневная норма)\W*$", t):
        return Intent("target")
    if _any(r"(мои|покажи мои) (частые |любимые )?продукты|что я чаще всего ем", t):
        return Intent("foods")
    return None


# Команды, которые может назвать LLM при разборе текста. food — запись еды, question и chat
# идут к ассистенту, craving — в поддержку при тяге, остальное — готовые команды бота.
COMMAND_INTENTS = {
    "today", "week", "weight", "sleep", "target", "body", "undo", "foods", "water_status",
    "weighin", "plan_list", "plan_add", "care_on", "care_off", "food_info", "help",
}
LLM_INTENTS = COMMAND_INTENTS | {"food", "question", "chat", "craving"}

_WEIGHIN_ARG = re.compile(r"^(?:on|off|вкл|выкл|нет|ежедневно|(?:\d{1,2}[:.]\d{2})|пн|вт|ср|чт|пт|сб|вс|\s)*$")


def from_llm(name: str, arg: str = "") -> Intent | None:
    """Команда по ответу LLM. None — ответ не годится для действия: сообщение уйдёт
    ассистенту, а не выполнит лишнего."""
    if name not in COMMAND_INTENTS:
        return None
    arg = _norm(arg)
    if name == "plan_add":
        return Intent(name, arg) if _any(r"\bесли\b.+\bто\b", arg) else None
    if name == "food_info":
        return Intent(name, arg) if arg else None
    if name == "weighin":
        if not _WEIGHIN_ARG.match(arg.replace(",", " ")):
            return None
        return Intent(name, arg.replace(",", " ").replace(".", ":"))
    return Intent(name)
