"""Сводка замеров порций: модели × промпты × наборы фото (читает data/eval_fpb/rows_*.json)."""
from __future__ import annotations

import json
import re
import statistics as st
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "eval_fpb"
NAMES = {"default": "боевая gemma-3-27b+эск.", "deepseek_deepseek-v4.1-flash-1017": "DeepSeek v4.1 flash",
         "openai_gpt-6-luna": "GPT-6 Luna", "google_gemini-3.8-flash": "Gemini 3.8 Flash", "openai_gpt-6.1-sol": "GPT-6.1 Sol"}


def stats(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["est"] is not None and r["truth"] > 0]
    ape = [abs(r["est"] - r["truth"]) / r["truth"] for r in ok]
    ratio = lambda size: (st.median([r["est"] / r["truth"] for r in ok if r["size"] == size])  # noqa: E731
                          if any(r["size"] == size for r in ok) else float("nan"))
    return {"n": len(ok), "mape": st.mean(ape), "med": st.median(ape),
            "w20": sum(a <= 0.2 for a in ape) / len(ape), "w35": sum(a <= 0.35 for a in ape) / len(ape),
            "ratio": st.median([r["est"] / r["truth"] for r in ok]),
            "small": ratio("small"), "avg": ratio("average"), "big": ratio("big"),
            "sec": st.mean(r["sec"] for r in rows), "fail": len(rows) - len(ok)}


def main() -> None:
    data: dict[tuple[str, str, str], list[dict]] = {}
    for f in CACHE.glob("rows_*.json"):
        model, prompt, which = re.match(r"rows_(.+)__(vision(?:_v2)?)__([AB])\.json", f.name).groups()
        data[(model, prompt, which)] = json.loads(f.read_text())
    head = (f"{'модель':26} {'промпт':9} {'MAPE':>5} {'мед.':>5} {'±20%':>5} {'±35%':>5} "
            f"{'отн.':>5} {'мал.':>5} {'сред.':>5} {'бол.':>5} {'сек':>5}")
    for which, title in (("A", "Набор A (те же 10 кадров, что раньше)"), ("B", "Набор B (10 новых кадров)"),
                         ("AB", "A + B вместе (20 кадров)")):
        print(f"\n=== {title}")
        print(head)
        for model, label in NAMES.items():
            for prompt in ("vision", "vision_v2"):
                rows = (data.get((model, prompt, "A"), []) + data.get((model, prompt, "B"), [])
                        if which == "AB" else data.get((model, prompt, which), []))
                if not rows:
                    continue
                s = stats(rows)
                print(f"{label:26} {'старый' if prompt == 'vision' else 'новый':9} {s['mape']:>5.0%} "
                      f"{s['med']:>5.0%} {s['w20']:>5.0%} {s['w35']:>5.0%} {s['ratio']:>5.2f} "
                      f"{s['small']:>5.2f} {s['avg']:>5.2f} {s['big']:>5.2f} {s['sec']:>5.1f}"
                      + (f"  сбоев {s['fail']}" if s["fail"] else ""))


if __name__ == "__main__":
    main()
