"""Замер точности граммов по фото на Food Portion Benchmark (issai/Food_Portion_Benchmark, CC-BY-NC).

Берёт по одному фото на пару «блюдо + размер порции» из размеченной части (val), прогоняет через
боевой разбор фото foodsvc.parse_photo и сравнивает суммарный вес позиций с эталонными граммами.
Эталон: сумма шестых колонок label-файла (вес в граммах).

Запуск: .venv/bin/python tools/eval_portions.py [число_фото] [модель] [--prompt vision|vision_v2|vision_v3] [--set A|B]
Без модели берётся боевая MODEL_VISION из конфига (с эскалацией на MODEL_VISION_ESCALATION).
--prompt: имя любого файла из app/prompts (без .md); по умолчанию vision.
Набор A: первые фото выборки; набор B: следующие, не пересекающиеся с A (проверка на невиденных кадрах).
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import food as foodsvc  # noqa: E402

REPO = "issai/Food_Portion_Benchmark"
BASE = f"https://huggingface.co/datasets/{REPO}/resolve/main/FPB_Dataset/RGB"
CACHE = Path(__file__).resolve().parent.parent / "data" / "eval_fpb"

# блюда, знакомые человеку из Беларуси/России; остальное в наборе среднеазиатское
CLASSES = [
    "bliny", "borsh", "buckwheat", "beef_cutlet", "cottage_cheese", "syrniki", "mashed_potato",
    "olivie_salad", "golubcy", "egg", "rice", "potato", "pizza", "burger", "french_fries",
    "chicken", "salmon", "meatball", "sausage", "nuggets", "fresh_salad", "beet_salad",
    "greek_salad", "caesar_salad", "okroshka", "lentiil_soup", "fish_soup", "rice_porridge",
    "bread", "toast", "kefir", "plov", "cheesecake", "croissant", "bun", "sandwich", "pie",
    "pirojki", "khachapuri", "beefstroganov",
]
SIZES = ("small", "average", "big")


def listing() -> list[str]:
    path = CACHE / "listing.json"
    if path.exists():
        return json.loads(path.read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    data = httpx.get(f"https://huggingface.co/api/datasets/{REPO}", timeout=60).json()
    files = [s["rfilename"] for s in data["siblings"]]
    path.write_text(json.dumps(files))
    return files


def pick(files: list[str], limit: int, seed: int = 7) -> list[tuple[str, str, str]]:
    """(группа, размер, имя изображения): по одному фото на пару «группа + размер».

    Группа — всё, что в имени файла перед размером: одиночное блюдо («pizza») или составной обед
    («set12»). Одиночные блюда берутся из знакомых кухонь, составные добирают выборку."""
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[str]] = {}
    for f in files:
        if not re.search(r"/RGB/(val|train)/images/", f) or not f.endswith(".jpg"):
            continue
        m = re.match(r"^(?P<g>.+?)_(?P<s>small|average|big)_", Path(f).name, re.I)
        if m:
            groups.setdefault((m["g"].lower(), m["s"].lower()), []).append(f)
    singles = [k for k in groups if not k[0].startswith("set") and any(c in k[0] for c in CLASSES)]
    sets = [k for k in groups if k[0].startswith("set")]
    rng.shuffle(singles)
    rng.shuffle(sets)
    keys = singles[:int(limit * 0.6)]
    keys += sets[:limit - len(keys)]
    chosen = [(g, s, rng.choice(groups[(g, s)])) for g, s in keys]
    rng.shuffle(chosen)
    return chosen[:limit]


def truth_grams(label_text: str) -> float:
    total = 0.0
    for line in label_text.strip().splitlines():
        parts = line.split()
        if len(parts) >= 6:
            total += float(parts[5])
    return total


async def fetch(client: httpx.AsyncClient, rel: str, dest: Path) -> Path:
    if not dest.exists():
        resp = await client.get(f"https://huggingface.co/datasets/{REPO}/resolve/main/{rel}")
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


async def one(client, sem, cls: str, size: str, image_rel: str, model: str | None,
              prompt_name: str) -> dict:
    async with sem:
        name = Path(image_rel).name
        label_rel = image_rel.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt"
        img = await fetch(client, image_rel, CACHE / name)
        lab = await fetch(client, label_rel, CACHE / (Path(label_rel).name))
        truth = truth_grams(lab.read_text())
        started = time.time()
        try:
            u = await foodsvc.parse_photo(base64.b64encode(img.read_bytes()).decode(), None, None, None,
                                           model=model, prompt_name=prompt_name)
            est = sum((i.grams_min + i.grams_max) / 2 for i in u.items)
            names = ", ".join(i.name_ru for i in u.items)
        except Exception as e:  # noqa: BLE001
            return {"cls": cls, "size": size, "truth": truth, "est": None, "names": f"ERR {type(e).__name__}",
                    "sec": time.time() - started}
        return {"cls": cls, "size": size, "truth": truth, "est": est, "names": names,
                "sec": time.time() - started}


def choose(limit: int, which: str) -> list[tuple[str, str, str]]:
    files = listing()
    first = pick(files, limit)
    if which == "A":
        return first
    seen = {(g, s) for g, s, _ in first}
    return [c for c in pick(files, limit * 4) if (c[0], c[1]) not in seen][:limit]


async def run(limit: int, model: str | None, prompt_name: str, which: str) -> list[dict]:
    chosen = choose(limit, which)
    sem = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        return await asyncio.gather(*(one(client, sem, c, s, f, model, prompt_name) for c, s, f in chosen))


def report(rows: list[dict]) -> str:
    ok = [r for r in rows if r["est"] is not None and r["truth"] > 0]
    out = [f"Фото: {len(rows)}, разобрано: {len(ok)}"]
    ratios = [r["est"] / r["truth"] for r in ok]
    ape = [abs(r["est"] - r["truth"]) / r["truth"] for r in ok]
    out.append(f"Медиана оценка/эталон: {statistics.median(ratios):.2f} "
               f"(среднее {statistics.mean(ratios):.2f}); MAPE {statistics.mean(ape):.0%}; "
               f"медианная ошибка {statistics.median(ape):.0%}")
    out.append(f"В пределах ±20 %: {sum(a <= 0.2 for a in ape) / len(ape):.0%}; "
               f"±35 %: {sum(a <= 0.35 for a in ape) / len(ape):.0%}; "
               f"занижено: {sum(r < 1 for r in ratios) / len(ratios):.0%} фото")
    mae = statistics.mean(abs(r["est"] - r["truth"]) for r in ok)
    out.append(f"Средняя абсолютная ошибка: {mae:.0f} г")
    out.append("\nПо размеру порции (эталон, оценка, оценка/эталон по медиане):")
    for size in SIZES:
        sub = [r for r in ok if r["size"] == size]
        if sub:
            out.append(f"  {size:8} n={len(sub):3} эталон {statistics.mean(r['truth'] for r in sub):5.0f} г, "
                       f"оценка {statistics.mean(r['est'] for r in sub):5.0f} г, "
                       f"медиана отношения {statistics.median(r['est'] / r['truth'] for r in sub):.2f}")
    # наклон: оценка = a * эталон + b (МНК), как в литературе: ошибка растёт с размером порции
    xs = [r["truth"] for r in ok]
    ys = [r["est"] for r in ok]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    out.append(f"\nРегрессия оценка = {slope:.2f} × эталон + {my - slope * mx:.0f} г")
    out.append("\nПо корзинам эталонного веса:")
    for lo, hi in ((0, 100), (100, 200), (200, 350), (350, 10000)):
        sub = [r for r in ok if lo <= r["truth"] < hi]
        if sub:
            out.append(f"  {lo:4}–{hi:5} г n={len(sub):3}: медиана отношения "
                       f"{statistics.median(r['est'] / r['truth'] for r in sub):.2f}, "
                       f"MAPE {statistics.mean(abs(r['est'] - r['truth']) / r['truth'] for r in sub):.0%}")
    out.append("\nПо блюдам (медиана отношения оценка/эталон), от самых заниженных:")
    by_cls: dict[str, list[float]] = {}
    for r in ok:
        by_cls.setdefault(r["cls"], []).append(r["est"] / r["truth"])
    for cls, vals in sorted(by_cls.items(), key=lambda kv: statistics.median(kv[1])):
        out.append(f"  {cls:16} n={len(vals)} {statistics.median(vals):.2f}")
    out.append("\nСамые большие промахи:")
    for r in sorted(ok, key=lambda r: abs(r["est"] - r["truth"]) / r["truth"], reverse=True)[:8]:
        out.append(f"  {r['cls']}/{r['size']}: эталон {r['truth']:.0f} г, оценка {r['est']:.0f} г ({r['names']})")
    errors = [r for r in rows if r["est"] is None]
    if errors:
        out.append(f"\nОшибок разбора: {len(errors)}: {errors[0]['names']}")
    out.append(f"\nСредняя задержка: {statistics.mean(r['sec'] for r in rows):.1f} с")
    return "\n".join(out)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("limit", type=int, nargs="?", default=120)
    ap.add_argument("model", nargs="?", default=None)
    ap.add_argument("--prompt", default="vision")
    ap.add_argument("--set", dest="which", default="A", choices=("A", "B"))
    args = ap.parse_args()
    tag = f"{(args.model or 'default').replace('/', '_')}__{args.prompt}__{args.which}"
    rows = asyncio.run(run(args.limit, args.model, args.prompt, args.which))
    text = report(rows)
    print(text)
    (CACHE / f"report_{tag}.txt").write_text(text, encoding="utf-8")
    (CACHE / f"rows_{tag}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
