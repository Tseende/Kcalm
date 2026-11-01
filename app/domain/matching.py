"""Матчинг продуктов: не брать первый результат — топ-5 кандидатов, скоринг, порог.

Таблица весов — из A.4 PROMPT.md / fomin_n nutrition_ranking (MIT).
"""
from __future__ import annotations

import re
import sqlite3
from difflib import SequenceMatcher

from app.domain.nutrition import FoodRef

STOP_TOKENS = {"и", "с", "на", "в", "по", "a", "the", "with", "and", "of", "de"}

# способ приготовления в запросе отсутствует, но есть у кандидата → штраф
MISMATCH_PREP = {"baked", "breaded", "dried", "powdered"}

# приоритет источника зависит от типа запроса (блюдо / простой ингредиент / бренд)
SOURCE_BONUS = {
    "dish": {"usda": 0.30, "user": 0.35, "seed": 0.20, "off": 0.10, "llm": 0.0},
    "ingredient": {"usda": 0.35, "user": 0.35, "seed": 0.25, "off": 0.10, "llm": 0.0},
    "brand": {"off": 0.24, "user": 0.35, "usda": 0.05, "seed": 0.05, "llm": 0.0},
}

SYNONYMS_RU = {
    "гречка": "гречка варёная",
    "гречневая каша": "гречка варёная",
    "рис": "рис белый варёный",
    "макароны": "макароны варёные",
    "паста": "макароны варёные",
    "пюре": "картофельное пюре",
    "картошка": "картофель варёный",
    "картошка жареная": "картофель жареный",
    "фри": "картофель фри",
    "омлет": "омлет",
    "яичница": "яйцо жареное",
    "яйца": "яйцо куриное варёное",
    "творог": "творог 5%",
    "кофе": "кофе без сахара",
    "чай": "чай без сахара",
    "хлеб": "хлеб белый",
    "ржаной хлеб": "хлеб черный",
    "хлеб ржаной": "хлеб черный",
    "черный хлеб": "хлеб черный",
    "бородинский хлеб": "хлеб черный",
    "белый хлеб": "хлеб белый",
    "цельнозерновой хлеб": "хлеб цельнозерновой",
    "зерновой хлеб": "хлеб цельнозерновой",
    "борщ": "борщ",
    "котлета": "котлета свино-говяжья",
    "котлеты": "котлета свино-говяжья",
    "оливье": "салат оливье",
    "кола": "кока-кола",
    "пельмени": "пельмени варёные",
}


def _clean(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[ё]", "е", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    cleaned = _clean(text)
    return _clean(SYNONYMS_RU.get(cleaned, cleaned))


def _tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in STOP_TOKENS]


_ENDINGS = ("ого", "его", "ому", "ему", "ыми", "ими", "ами", "ями", "ый", "ий", "ой", "ая",
            "яя", "ое", "ее", "ые", "ие", "ых", "их", "ым", "им", "ом", "ем", "ов", "ев",
            "ей", "ам", "ям", "ах", "ях", "ою", "ую", "юю", "а", "я", "у", "ю", "е", "ы", "и", "о")


def stem(token: str) -> str:
    """Грубое отсечение русского окончания: «соуса»/«соус», «лососевого»/«лососевый» → одна основа."""
    for end in _ENDINGS:
        if token.endswith(end) and len(token) - len(end) >= (3 if len(end) == 1 else 4):
            return token[: -len(end)]
    return token


def tokens_match(a: str, b: str) -> bool:
    """Слова совпадают с учётом падежа и мелкой опечатки («фаршмака» ~ «форшмак»)."""
    if a == b:
        return True
    sa, sb = stem(a), stem(b)
    if sa == sb:
        return True
    if min(len(sa), len(sb)) >= 6:
        return SequenceMatcher(None, sa, sb).ratio() >= 0.84
    return False


def _split_tokens(text: str) -> list[str]:
    """Токены с разбивкой дефисных слов: «бургер-соус» → бургер, соус."""
    out: list[str] = []
    for t in _tokens(text):
        out.extend(part for part in t.split("-") if part)
    return out


def _name_overlap(name_en: str, name_ru: str, query_tokens: list[str]) -> float:
    """Пересечение токенов запроса с англ. и рус. именами (нормализованными)."""
    hay = set(_tokens(name_en)) | set(_tokens(name_ru))
    if not query_tokens:
        return 0.0
    hit = sum(1 for t in query_tokens if t in hay)
    return hit / len(query_tokens)


def score_candidate(query: str, query_type: str, brand: str | None,
                    row: sqlite3.Row) -> float:
    q_tokens = _tokens(query)
    score = 0.0
    name_norm = normalize(row["name_ru"])
    name_en_norm = normalize(row["name_en"])

    if name_norm == normalize(query) or name_en_norm == normalize(query):
        score += 0.34
    elif name_norm in normalize(query) or name_en_norm in normalize(query) \
            or normalize(query) in name_norm:
        score += 0.26
    else:
        overlap = _name_overlap(row["name_en"], row["name_ru"], q_tokens)
        if overlap >= 0.5:
            score += 0.18

    if brand:
        score += 0.18 if brand.lower() in name_norm or brand.lower() in name_en_norm else -0.08

    macros_full = all(row[k] is not None and row[k] >= 0 for k in
                      ("kcal_100g", "protein_100g", "fat_100g", "carbs_100g"))
    score += 0.16 if macros_full else -0.25

    # пропущен главный токен запроса
    missing = [t for t in q_tokens if t not in name_norm and t not in name_en_norm]
    if q_tokens and len(missing) == len(q_tokens):
        score -= 0.36
    elif missing:
        score -= min(0.06 * (len(missing) - 1), 0.30) + 0.0
    # подмена уточнения: в запросе «ржаной», в кандидате «белый» — это другой продукт
    extra = [t for t in _tokens(row["name_ru"]) if t not in q_tokens]
    if missing and extra and len(missing) < len(q_tokens):
        score -= 0.5          # лучше честная LLM-оценка, чем уверенно не тот продукт

    if any(p in name_en_norm for p in MISMATCH_PREP) and \
            not any(p in normalize(query) for p in MISMATCH_PREP):
        score -= 0.12

    score += SOURCE_BONUS.get(query_type, SOURCE_BONUS["dish"]).get(row["source"], 0.0)
    return score


OWN_PRODUCT_BONUS = 0.5


def _own_product_score(query: str, query_type: str, brand: str | None, row: sqlite3.Row,
                       q_tokens: list[str], alias_phrases: list[list[str]] = ()) -> float | None:
    """Свой продукт берём только когда человек назвал его конкретно: в запросе есть ВСЕ слова
    названия (или всего псевдонима) — с учётом падежей и опечаток. Общее слово («сыр», «соус»)
    свой продукт не подхватывает: пусть считается обычным продуктом из базы."""
    flat = [w for t in q_tokens for w in t.split("-") if w]
    if not flat:
        return None
    phrases = [_split_tokens(row["name_ru"])]
    if row["brand"]:
        phrases.append(_split_tokens(f"{row['name_ru']} {row['brand']}"))
    phrases.extend(alias_phrases)
    # «5%», «35%» и т.п. в названии — уточнение, в речи его часто опускают
    phrases = [[w for w in ph if not w.isdigit()] or ph for ph in phrases]
    if not any(ph and all(any(tokens_match(h, t) for t in flat) for h in ph) for ph in phrases):
        return None
    return score_candidate(query, query_type, brand, row) + OWN_PRODUCT_BONUS


def find_food(conn: sqlite3.Connection, name_en: str, name_ru: str = "",
              brand: str | None = None, query_type: str = "dish",
              user_id: int | None = None, top: int = 5,
              threshold: float = 0.15) -> FoodRef | None:
    """Поиск в базе: user_foods → алиасы → foods по токенам. None = идти в LLM-оценку."""
    query = name_ru or name_en
    if not query:
        return None
    norm = normalize(query)

    # 1. точный алиас пользователя
    if user_id is not None:
        row = conn.execute(
            """SELECT f.* FROM food_aliases a JOIN foods f ON f.id = a.food_id
               WHERE a.alias_text = ? AND (a.user_id = ? OR a.user_id IS NULL)
                 AND f.deleted_at IS NULL
                 AND (f.owner_user_id IS NULL OR f.owner_user_id = ?)
               ORDER BY (a.user_id IS NULL), a.id DESC""",
            (norm, user_id, user_id),
        ).fetchone()
        if row:
            return _to_ref(row, confidence=0.95)

    # 2. скоринг по всем кандидатам (база маленькая: seed ~200 + user-записи;
    #    LIKE не годится — SQLite не сравнивает регистр кириллицы)
    #    Чужие и удалённые свои продукты не видны никогда.
    if user_id is None:
        candidates = conn.execute(
            "SELECT * FROM foods WHERE deleted_at IS NULL AND owner_user_id IS NULL").fetchall()
    else:
        candidates = conn.execute(
            """SELECT * FROM foods WHERE deleted_at IS NULL
               AND (owner_user_id IS NULL OR owner_user_id = ?)""", (user_id,)).fetchall()

    q_tokens = _tokens(query)
    alias_phrases: dict[int, list[list[str]]] = {}
    if user_id is not None:
        for a in conn.execute("SELECT food_id, alias_text FROM food_aliases WHERE user_id = ?",
                              (user_id,)):
            alias_phrases.setdefault(a["food_id"], []).append(_split_tokens(a["alias_text"]))
    scored_all = []
    for r in candidates:
        if r["owner_user_id"] is not None:
            own = _own_product_score(query, query_type, brand, r, q_tokens,
                                     alias_phrases.get(r["id"], ()))
            if own is None:
                continue
            scored_all.append((own, r))
        else:
            scored_all.append((score_candidate(query, query_type, brand, r), r))
    scored = sorted(scored_all, key=lambda p: -p[0])[:top]

    if not scored or scored[0][0] < threshold:
        return None
    best_score, best = scored[0]
    conf = min(0.95, 0.5 + best_score / 2)
    return _to_ref(best, confidence=round(conf, 2))


def _to_ref(row: sqlite3.Row, confidence: float) -> FoodRef:
    return FoodRef(
        food_id=row["id"],
        name_ru=row["name_ru"],
        name_en=row["name_en"],
        kcal_100g=row["kcal_100g"],
        protein_100g=row["protein_100g"],
        fat_100g=row["fat_100g"],
        carbs_100g=row["carbs_100g"],
        source=row["source"],
        confidence=confidence,
    )
