"""Нарезает лист иконок 3x2 на пурпурном фоне в webp 112x112 с прозрачностью.

python tools/slice_sheet.py sheet.png name1 name2 name3 name4 name5 name6 [--out app/web/static/img/food]
Имена — по порядку: слева направо, сверху вниз.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SIZE = 112
PAD = 6


def key_out(im: Image.Image) -> Image.Image:
    """Пурпурный фон (с градиентом) -> прозрачность, мягкий край."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            # «пурпурность»: красный и синий высоки, зелёный низок
            m = min(r, b) - g
            if m > 110 and r > 150 and b > 150:
                a = 0
            elif m > 60 and r > 130 and b > 130:
                a = int(255 * (110 - m) / 50)
                # убираем розовую кайму: тянем цвет к зелёному каналу
                r = min(r, g + 40)
                b = min(b, g + 40)
                px[x, y] = (r, g, b, max(0, min(255, a)))
                continue
            else:
                continue
            px[x, y] = (r, g, b, a)
    return im


def split_items(img: Image.Image, count: int) -> list[Image.Image]:
    """Для листа с прозрачным фоном: предметы = связные области (с небольшой дилатацией)."""
    import numpy as np
    from scipy import ndimage

    arr = np.array(img.convert("RGBA"))
    solid = arr[..., 3] > 24
    total = solid.sum()
    for it in (14, 10, 7, 5, 3, 2, 1):   # самая сильная склейка, при которой предметов ещё хватает
        labels, n = ndimage.label(ndimage.binary_dilation(solid, iterations=it))
        sizes = ndimage.sum(solid, labels, range(1, n + 1))
        if sum(1 for v in sizes if v > 0.015 * total) >= count:
            break
    keep = sorted(range(1, n + 1), key=lambda i: -sizes[i - 1])[:count]
    items = []
    for lab in keep:
        ys, xs = np.where((labels == lab) & solid)
        items.append((ys.mean(), xs.mean(), lab))
    rows = sorted(items, key=lambda t: t[0])
    per_row = count // 2
    ordered = sorted(rows[:per_row], key=lambda t: t[1]) + sorted(rows[per_row:], key=lambda t: t[1])
    out = []
    for _, _, lab in ordered:
        m = (labels == lab)
        sub = arr.copy()
        sub[..., 3] = np.where(m, arr[..., 3], 0)
        out.append(Image.fromarray(sub, "RGBA"))
    return out


def main() -> None:
    args = sys.argv[1:]
    out = Path("app/web/static/img/food")
    if "--out" in args:
        i = args.index("--out")
        out = Path(args[i + 1])
        del args[i:i + 2]
    sheet, names = Path(args[0]), args[1:]
    raw = Image.open(sheet).convert("RGBA")
    transparent = raw.getchannel("A").getpixel((4, 4)) == 0
    out.mkdir(parents=True, exist_ok=True)
    if transparent:
        cells = split_items(raw, len(names))
    else:
        w, h = raw.size
        cw, ch = w / 3, h / 2
        cells = [key_out(raw.crop((int((i % 3) * cw), int((i // 3) * ch), int((i % 3 + 1) * cw), int((i // 3 + 1) * ch))))
                 for i in range(len(names))]
    for idx, name in enumerate(names):
        cell = cells[idx]
        box = cell.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
        if not box:
            print("пусто:", name)
            continue
        cell = cell.crop(box)
        side = max(cell.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.alpha_composite(cell, ((side - cell.width) // 2, (side - cell.height) // 2))
        canvas = canvas.resize((SIZE - 2 * PAD, SIZE - 2 * PAD), Image.LANCZOS)
        final = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        final.alpha_composite(canvas, (PAD, PAD))
        dest = out / f"{name}.webp"
        final.save(dest, "WEBP", quality=88, method=6)
        print(dest, dest.stat().st_size, "байт")


if __name__ == "__main__":
    main()
