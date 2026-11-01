"""Проверка боевого промпта разбора (intent + еда в одном вызове) на тех же фразах, что и eval_extra.

Идёт через настоящий foodsvc.parse_text: так видно и намерение, и то, что еда по-прежнему
разбирается (items не пустые для food). Считает точность самого LLM и всей цепочки
«регулярки → LLM», как в боте.
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import embedding_eval as ev                      # noqa: E402
import eval_dataset as ds                        # noqa: E402
from app.services import food as foodsvc         # noqa: E402


async def run(tests: list[tuple[str, str]]):
    sem = asyncio.Semaphore(8)
    out: list[tuple[str, int, float]] = [("", 0, 0.0)] * len(tests)

    async def one(i: int, text: str) -> None:
        async with sem:
            t = time.time()
            try:
                u = await foodsvc.parse_text(text, None, None)
                out[i] = (u.intent, len(u.items), time.time() - t)
            except Exception as e:  # noqa: BLE001
                out[i] = (f"ERR:{type(e).__name__}", 0, time.time() - t)

    await asyncio.gather(*(one(i, t) for i, (_, t) in enumerate(tests)))
    return out


def main() -> None:
    tests = [(label, text) for label, items in ds.TESTS.items() for text in items
             if label != "water_log"]
    res = asyncio.run(run(tests))
    preds = [r[0] for r in res]
    lat = [r[2] for r in res]
    s = ev.summarize(tests, preds)
    print(f"LLM в составе разбора: точность {s['acc']:.0%} | мимо {s['missed']:.0%} | "
          f"не та команда {s['wrong_cmd']:.0%} | угон {s['hijack']:.0%} "
          f"(еда {s['food_hijack']:.0%}) | задержка медиана {statistics.median(lat):.1f} с, "
          f"макс {max(lat):.1f} с")
    hyb = [ev.route_regex(t) if ev.decisive(ev.route_regex(t)) else p
           for (_, t), p in zip(tests, preds)]
    s = ev.summarize(tests, hyb)
    print(f"Цепочка регулярки → LLM: точность {s['acc']:.0%} | мимо {s['missed']:.0%} | "
          f"не та {s['wrong_cmd']:.0%} | угон {s['hijack']:.0%} (еда {s['food_hijack']:.0%})")
    pc = ev.per_class(tests, preds)
    print("по классам:", ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in pc.items()))

    food_empty = [(t, r) for (l, t), r in zip(tests, res) if l == "food" and r[1] == 0]
    cmd_items = [(l, t, r[0], r[1]) for (l, t), r in zip(tests, res)
                 if l != "food" and r[0] == "food" and r[1] > 0]
    print(f"\nЕда без позиций (потеряна запись): {len(food_empty)}")
    for t, r in food_empty:
        print(f"  «{t}»: intent={r[0]}")
    print(f"Не еда, но разобрана как еда с позициями: {len(cmd_items)}")
    for l, t, intent, n in cmd_items:
        print(f"  «{t}»: ожидали {l}, intent={intent}, позиций {n}")
    print("\nОшибки намерения:")
    for (exp, text), got in zip(tests, preds):
        if exp != got:
            print(f"  «{text}»: {exp} → {got}")


if __name__ == "__main__":
    main()
