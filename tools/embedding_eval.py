"""Замер: помогают ли эмбеддинги узнавать фразы и подбирать продукты лучше, чем текущая логика.

Запуск (нужен OPENROUTER_API_KEY в .env, стоит доли цента):
    .venv/bin/python tools/embedding_eval.py qwen/qwen3-embedding-8b perplexity/pplx-embed-v1-0.6b

Сравнивает: текущий роутер (регулярки) / эмбеддинги (ближайший прототип) / гибрид, а для
продуктов: текущий подбор / ближайшее название по эмбеддингу / гибрид. Векторы кэшируются
в data/eval_cache.json, повторный запуск ничего не стоит.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_config                      # noqa: E402
from app.db import connect, migrate                    # noqa: E402
from app.domain import intents, water                  # noqa: E402
from app.domain.craving import detect_craving          # noqa: E402
from app.domain.matching import find_food              # noqa: E402
import eval_dataset as ds                              # noqa: E402

CACHE = ROOT / "data" / "eval_cache.json"
COMMANDS = {"today", "week", "weight", "sleep", "target", "undo", "water_status", "body", "foods",
            "plan_list", "care_on", "care_off", "weighin", "help", "food_info", "craving"}
NON_COMMANDS = {"food", "question", "chat"}
THRESHOLDS = [0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85]


# ------------------------------------------------------------------ эмбеддинги

class Embedder:
    def __init__(self, model: str) -> None:
        self.model = model
        self.cfg = get_config()
        self.cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        self.tokens = 0
        self.seconds = 0.0

    def _key(self, text: str) -> str:
        return f"{self.model}||{text}"

    def vectors(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in self.cache]
        for i in range(0, len(missing), 48):
            batch = missing[i:i + 48]
            started = time.time()
            resp = httpx.post(
                f"{self.cfg.openrouter_base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.cfg.openrouter_api_key}"},
                json={"model": self.model, "input": batch}, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            self.seconds += time.time() - started
            self.tokens += (data.get("usage") or {}).get("prompt_tokens", 0)
            for item in sorted(data["data"], key=lambda d: d["index"]):
                self.cache[self._key(batch[item["index"]])] = _unit([round(x, 6) for x in item["embedding"]])
        if missing:
            CACHE.parent.mkdir(exist_ok=True)
            CACHE.write_text(json.dumps(self.cache))
        return [self.cache[self._key(t)] for t in texts]


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ------------------------------------------------------------------ фразы

def route_regex(text: str) -> str:
    """Текущий порядок бота: поддержка при тяге → вода → команда → вопрос → еда."""
    if detect_craving(text, "23:30", "21:00"):
        return "craving"
    if water.extract_water(text)[0]:
        return "water_log"
    intent = intents.detect(text)
    if intent:
        if intent.name in ("greeting", "thanks"):
            return "chat"
        return intent.name
    return "question" if intents.is_question(text) else "food"


def decisive(label: str) -> bool:
    return label in COMMANDS or label == "water_log"


def evaluate_phrases(emb: Embedder, include_water: bool = False) -> dict:
    protos = [(label, text) for label, texts in ds.PROTOTYPES.items() for text in texts]
    tests = [(label, text) for label, texts in ds.TESTS.items() for text in texts
             if label != "water_log"]
    proto_vecs = emb.vectors([t for _, t in protos])
    test_vecs = emb.vectors([t for _, t in tests])

    nearest = []
    for vec in test_vecs:
        best = max(range(len(protos)), key=lambda i: _dot(vec, proto_vecs[i]))
        nearest.append((protos[best][0], _dot(vec, proto_vecs[best]), protos[best][1]))

    def fallback(text: str) -> str:
        return "question" if intents.is_question(text) else "food"

    def knn(thr: float):
        return [lab if sim >= thr else fallback(text)
                for (lab, sim, _), (_, text) in zip(nearest, tests)]

    def hybrid(thr: float):
        out = []
        for (lab, sim, _), (_, text) in zip(nearest, tests):
            rx = route_regex(text)
            if decisive(rx):
                out.append(rx)
            else:
                out.append(lab if sim >= thr else rx)
        return out

    return {"tests": tests, "nearest": nearest, "knn": knn, "hybrid": hybrid, "protos": protos}


def summarize(tests, preds) -> dict:
    n = len(tests)
    correct = sum(1 for (t, _), p in zip(tests, preds) if t == p)
    hijack = sum(1 for (t, _), p in zip(tests, preds) if t in NON_COMMANDS and p in COMMANDS)
    food_hijack = sum(1 for (t, _), p in zip(tests, preds) if t == "food" and p in COMMANDS)
    missed = sum(1 for (t, _), p in zip(tests, preds) if t in COMMANDS and p in NON_COMMANDS)
    wrong_cmd = sum(1 for (t, _), p in zip(tests, preds)
                    if t in COMMANDS and p in COMMANDS and t != p)
    n_non = sum(1 for t, _ in tests if t in NON_COMMANDS)
    n_food = sum(1 for t, _ in tests if t == "food")
    n_cmd = sum(1 for t, _ in tests if t in COMMANDS)
    return {"acc": correct / n, "hijack": hijack / n_non, "food_hijack": food_hijack / n_food,
            "missed": missed / n_cmd, "wrong_cmd": wrong_cmd / n_cmd,
            "correct": correct, "n": n}


def per_class(tests, preds) -> dict[str, tuple[int, int]]:
    stat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (t, _), p in zip(tests, preds):
        stat[t][1] += 1
        stat[t][0] += int(t == p)
    return {k: (v[0], v[1]) for k, v in stat.items()}


# ------------------------------------------------------------------ продукты

def seed_foods() -> list[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    conn = connect(path)
    migrate(conn)
    rows = [r["name_ru"] for r in conn.execute("SELECT name_ru FROM foods WHERE source='seed'")]
    conn.close()
    return rows


def evaluate_foods(emb: Embedder) -> dict:
    fd, path = tempfile.mkstemp(suffix=".db")
    conn = connect(path)
    migrate(conn)
    names = [r["name_ru"] for r in conn.execute("SELECT name_ru FROM foods WHERE source='seed'")]
    queries = list(ds.FOODS)
    name_vecs = emb.vectors(names)
    query_vecs = emb.vectors(queries)

    current = {}
    for q in queries:
        ref = find_food(conn, "", q)
        current[q] = ref.name_ru if ref else None
    nearest = {}
    for q, vec in zip(queries, query_vecs):
        best = max(range(len(names)), key=lambda i: _dot(vec, name_vecs[i]))
        nearest[q] = (names[best], _dot(vec, name_vecs[best]))
    conn.close()

    def score(preds: dict[str, str | None]) -> dict:
        pos = [q for q in queries if ds.FOODS[q]]
        neg = [q for q in queries if not ds.FOODS[q]]
        right = sum(1 for q in pos if preds[q] in ds.FOODS[q])
        wrong = sum(1 for q in pos if preds[q] is not None and preds[q] not in ds.FOODS[q])
        none_pos = sum(1 for q in pos if preds[q] is None)
        neg_ok = sum(1 for q in neg if preds[q] is None)
        return {"right": right / len(pos), "wrong": wrong / len(pos), "none_pos": none_pos / len(pos),
                "neg_ok": neg_ok / len(neg), "n_pos": len(pos), "n_neg": len(neg)}

    return {"queries": queries, "current": current, "nearest": nearest, "score": score,
            "hybrid": lambda thr: {q: current[q] if current[q] else
                                   (nearest[q][0] if nearest[q][1] >= thr else None)
                                   for q in queries}}


# ------------------------------------------------------------------ отчёт

def main(models: list[str]) -> None:
    if not get_config().openrouter_api_key:
        sys.exit("Нет OPENROUTER_API_KEY в .env")

    # --- текущая логика
    base_tests = [(l, t) for l, ts in ds.TESTS.items() for t in ts]
    rx_preds = [route_regex(t) for _, t in base_tests]
    rx_tests = [(l, t) for l, t in base_tests if l != "water_log"]
    rx_preds_nowater = [route_regex(t) for _, t in rx_tests]
    rx = summarize(rx_tests, rx_preds_nowater)
    water_ok = sum(1 for (l, _), p in zip(base_tests, rx_preds) if l == "water_log" and p == "water_log")
    print("=" * 78)
    print(f"ФРАЗЫ: тестовых {len(rx_tests)} (+{sum(1 for l,_ in base_tests if l=='water_log')} про воду), "
          f"классов {len(ds.PROTOTYPES)}")
    print(f"Текущий роутер (регулярки): точность {rx['acc']:.0%} | команды мимо {rx['missed']:.0%} | "
          f"не та команда {rx['wrong_cmd']:.0%} | еда/вопрос/болтовня ушли в команду {rx['hijack']:.0%} "
          f"(из них запись еды {rx['food_hijack']:.0%}) | вода {water_ok}/5")
    rx_class = per_class(rx_tests, rx_preds_nowater)

    results = {}
    for model in models:
        emb = Embedder(model)
        print("=" * 78)
        print("МОДЕЛЬ:", model)
        started = time.time()
        ph = evaluate_phrases(emb)
        fo = evaluate_foods(emb)
        dim = len(next(iter(emb.cache.values())))
        print(f"размерность {dim}, новых токенов {emb.tokens}, время API {emb.seconds:.1f} с "
              f"(всего {time.time() - started:.1f} с)")

        print("\nФразы: порог → точность | команды мимо | не та команда | ушло в команду (еда) — "
              "режим «только эмбеддинги» / «гибрид»")
        print(f"{'порог':>6} {'emb acc':>8} {'мимо':>6} {'не та':>6} {'угон':>6} {'угон еды':>9}"
              f" || {'hyb acc':>8} {'мимо':>6} {'не та':>6} {'угон':>6} {'угон еды':>9}")
        best = None
        for thr in THRESHOLDS:
            k = summarize(ph["tests"], ph["knn"](thr))
            h = summarize(ph["tests"], ph["hybrid"](thr))
            print(f"{thr:>6.2f} {k['acc']:>8.0%} {k['missed']:>6.0%} {k['wrong_cmd']:>6.0%} "
                  f"{k['hijack']:>6.0%} {k['food_hijack']:>9.0%} || {h['acc']:>8.0%} {h['missed']:>6.0%} "
                  f"{h['wrong_cmd']:>6.0%} {h['hijack']:>6.0%} {h['food_hijack']:>9.0%}")
            if h["food_hijack"] == 0 and (best is None or h["acc"] > best[1]["acc"]):
                best = (thr, h)
        if best:
            thr = best[0]
            print(f"\nЛучший гибрид без угона записи еды: порог {thr:.2f}, точность {best[1]['acc']:.0%}")
            hp = ph["hybrid"](thr)
            pc = per_class(ph["tests"], hp)
            print("по классам (регулярки → гибрид):")
            for label in ds.PROTOTYPES:
                a = rx_class.get(label, (0, 0))
                b = pc.get(label, (0, 0))
                print(f"  {label:<13} {a[0]:>2}/{a[1]:<2} → {b[0]:>2}/{b[1]:<2}")
            wrong = [(t[1], t[0], p) for t, p in zip(ph["tests"], hp) if t[0] != p]
            print("ошибки гибрида (фраза: ожидалось → получено):")
            for text, exp, got in wrong[:25]:
                print(f"  «{text}»: {exp} → {got}")
        results[model] = {"phrases": best, "foods": fo}

        print("\nПродукты: подбор названия из базы (сид 206 продуктов)")
        cur = fo["score"](fo["current"])
        print(f"текущий подбор: верно {cur['right']:.0%} | уверенно не тот {cur['wrong']:.0%} | "
              f"не нашёл (а продукт есть) {cur['none_pos']:.0%} | верно «нет в базе» {cur['neg_ok']:.0%}")
        print(f"{'порог':>6} {'emb верно':>10} {'не тот':>7} {'нет':>5} {'нет-ок':>7} || "
              f"{'гибрид верно':>13} {'не тот':>7} {'нет':>5} {'нет-ок':>7}")
        for thr in THRESHOLDS:
            e = fo["score"]({q: (n if s >= thr else None) for q, (n, s) in fo["nearest"].items()})
            h = fo["score"](fo["hybrid"](thr))
            print(f"{thr:>6.2f} {e['right']:>10.0%} {e['wrong']:>7.0%} {e['none_pos']:>5.0%} "
                  f"{e['neg_ok']:>7.0%} || {h['right']:>13.0%} {h['wrong']:>7.0%} "
                  f"{h['none_pos']:>5.0%} {h['neg_ok']:>7.0%}")
        bad = [(q, fo["current"][q], fo["nearest"][q]) for q in fo["queries"]
               if ds.FOODS[q] and fo["current"][q] not in ds.FOODS[q]]
        print("\nГде текущий подбор не справился (запрос: он → эмбеддинг):")
        for q, cur_name, (emb_name, sim) in bad[:30]:
            ok = "✓" if emb_name in ds.FOODS[q] else "✗"
            print(f"  «{q}»: {cur_name} → {emb_name} ({sim:.2f}) {ok}")
        neg_bad = [(q, fo["current"][q], fo["nearest"][q]) for q in fo["queries"]
                   if not ds.FOODS[q] and fo["current"][q]]
        if neg_bad:
            print("Текущий подбор нашёл то, чего нет в базе:",
                  ", ".join(f"«{q}»→{c}" for q, c, _ in neg_bad))
        print("Сходство эмбеддинга для «нет в базе»: " + ", ".join(
            f"{q} {fo['nearest'][q][1]:.2f}" for q in fo["queries"] if not ds.FOODS[q]))
        pos_sims = sorted(fo["nearest"][q][1] for q in fo["queries"]
                          if ds.FOODS[q] and fo["nearest"][q][0] in ds.FOODS[q])
        if pos_sims:
            print(f"Сходство верных совпадений: мин {pos_sims[0]:.2f}, медиана "
                  f"{pos_sims[len(pos_sims) // 2]:.2f}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["qwen/qwen3-embedding-8b", "perplexity/pplx-embed-v1-0.6b"])
