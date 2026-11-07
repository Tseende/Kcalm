"""Графики картинками, которые встраиваются в сообщение вместе с таблицами.

Рисует matplotlib без экрана, PNG в памяти. Рисование блокирующее, поэтому снаружи вызывается
через asyncio.to_thread (см. report.render). Эмодзи в шрифте графиков нет, подписи текстом.
Фон белая «карточка»: одинаково читается и в светлой, и в тёмной теме Telegram.
"""
from __future__ import annotations

import functools
import io
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from datetime import date, timedelta

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from app.domain.targets import ewma_dated  # noqa: E402
from app.services import insights  # noqa: E402
from app.storage import repo  # noqa: E402

INK, MUTED, GRID, TRACK = "#1F2D3D", "#8A97A6", "#E8EDF3", "#EDF1F6"
GOOD, WARN, OVER, ACCENT = "#2FB5A0", "#F2B84B", "#EF6F5E", "#4C7BF4"
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": GRID, "text.color": INK,
                     "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})


ASSETS = Path(__file__).resolve().parents[2] / "assets"


# pyplot хранит глобальное состояние фигур и не потокобезопасен, а рисуем мы из asyncio.to_thread:
# одновременно строится только один график.
_PLOT_LOCK = threading.RLock()


def _plot_locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _PLOT_LOCK:
            return fn(*args, **kwargs)
    return wrapper


@lru_cache(maxsize=8)
def _art(name: str):
    """Иллюстрация из assets/ (массив пикселей); нет файла — None, график рисуется без неё."""
    try:
        return plt.imread(ASSETS / name)
    except (OSError, ValueError):
        return None


def _cover(img, ratio: float):
    """Обрезать картинку по центру до нужных пропорций (как background-size: cover)."""
    h, w = img.shape[:2]
    if w / h > ratio:
        new_w = int(h * ratio)
        left = (w - new_w) // 2
        return img[:, left:left + new_w]
    new_h = int(w / ratio)
    top = (h - new_h) // 2
    return img[top:top + new_h]


def _backdrop(ax, name: str, w: float, h: float, art_h: float | None = None,
              fade=(0.0, 0.8)) -> None:
    """Иллюстрация сверху на всю ширину, поверх белая вуаль: у верха прозрачная, книзу плотнее,
    так что картинка плавно растворяется в белом и цифры читаются. art_h: высота зоны картинки
    (по умолчанию весь график). Нет файла: остаётся чистый белый фон."""
    img = _art(name)
    if img is None:
        return
    art_h = art_h or h
    limits = (ax.get_xlim(), ax.get_ylim())
    box = (0, w, h - art_h, h)
    ax.imshow(_cover(img, w / art_h), extent=box, aspect="auto", zorder=0,
              interpolation="bilinear")
    veil = np.ones((64, 1, 4))
    veil[:, 0, 3] = np.linspace(fade[0], fade[1], 64)  # строка 0 = верх картинки
    ax.imshow(veil, extent=box, aspect="auto", zorder=1)
    ax.set_xlim(*limits[0])
    ax.set_ylim(*limits[1])


def _card(ax, x: float, y: float, w: float, h: float, alpha: float = 0.88,
          radius: float = 0.22) -> None:
    """Полупрозрачная белая плашка под текстом: сквозь неё виден фон-иллюстрация."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                linewidth=0.8, facecolor=(1, 1, 1, alpha), edgecolor=GRID,
                                zorder=2))


def _png(fig, bleed: bool = False) -> bytes:
    """bleed: картинка во весь кадр, поэтому без обрезки и белых полей вокруг."""
    buf = io.BytesIO()
    if bleed:
        fig.savefig(buf, format="png", dpi=160, facecolor="white")
    else:
        fig.savefig(buf, format="png", dpi=160, bbox_inches="tight", pad_inches=0.25,
                    facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _num(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _tone(value: float, goal: float, band: tuple[float, float] | None = None) -> str:
    """Цвет по выполнению цели. band: коридор вокруг цели (калории), иначе «чем больше, тем лучше»."""
    ratio = value / goal if goal else 0
    if band:
        low, high = band
        return GOOD if low <= ratio <= high else WARN if 0.7 <= ratio <= 1.2 else OVER
    return GOOD if ratio >= 0.9 else WARN if ratio >= 0.5 else OVER


def _clean(ax) -> None:
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0, labelsize=9)
    ax.set_facecolor("none")


def _pill(ax, x, y, w, h, color, z=2) -> None:
    ax.add_patch(FancyBboxPatch((x, y), max(w, h), h, boxstyle=f"round,pad=0,rounding_size={h / 2}",
                                linewidth=0, facecolor=color, zorder=z))


# ------------------------------------------------------------------------ день

@_plot_locked
def day_chart(f: dict) -> bytes:
    """Карточка дня на фоне иллюстрации: крупная цифра калорий и полоски прогресса."""
    rows = [("Белок", f["protein"], f["protein_target"], "г", None),
            ("Вода", f["water"], f["water_goal"], "мл", None),
            ("Шаги", f["steps"] or 0, insights.STEPS_GOAL, "", None)]
    if not f["care"]:
        rows.insert(0, ("Калории", f["kcal"], f["kcal_target"], "ккал", (0.85, 1.1)))
    width, row_h, head = 8.0, 0.62, 1.9
    rows_h = row_h * len(rows) + 0.3
    height = head + rows_h + 0.45
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")                                  # единицы данных = дюймы: скругления круглые
    _backdrop(ax, "menu.jpg", width, height, fade=(0.0, 0.6))
    top = height - 0.3
    _card(ax, 0.3, top - 1.35, 3.9, 1.35, alpha=0.82)
    ax.text(0.55, top - 0.36, f"День {f['date'][8:]}.{f['date'][5:7]}", fontsize=11,
            color=MUTED, va="center", zorder=5)
    if not f["care"]:
        ax.text(0.55, top - 0.85, _num(f["kcal"]), fontsize=26, fontweight="bold", va="center",
                zorder=5)
        ax.text(0.55 + 0.2 * len(_num(f["kcal"])) + 0.45, top - 0.9,
                f"из {_num(f['kcal_target'])} ккал", fontsize=12, color=MUTED, va="center",
                zorder=5)
    _card(ax, 0.3, 0.3, width - 0.6, rows_h)
    track_x, track_w = 1.85, 3.1
    for i, (name, value, goal, unit, band) in enumerate(rows):
        y = 0.3 + 0.15 + (len(rows) - 1 - i) * row_h
        ratio = value / goal if goal else 0
        ax.text(0.6, y + 0.14, name, fontsize=11, va="center", zorder=5)
        _pill(ax, track_x, y + 0.04, track_w, 0.2, TRACK, z=3)
        if ratio > 0:
            _pill(ax, track_x, y + 0.04, track_w * min(ratio, 1.0), 0.2,
                  _tone(value, goal, band), z=4)
        label = f"{_num(value)} / {_num(goal)} {unit}".strip()
        ax.text(width - 0.6, y + 0.14, label, fontsize=10, ha="right", va="center", zorder=5)
        ax.text(track_x + track_w + 0.12, y + 0.14, f"{ratio * 100:.0f}%", fontsize=10,
                color=MUTED, va="center", zorder=5)
    return _png(fig, bleed=True)


# ---------------------------------------------------------------------- неделя

@_plot_locked
def week_chart(days: list[dict], care: bool) -> bytes:
    """Панели по дням на плашках поверх иллюстрации: калории (с коридором цели), вода, шаги."""
    labels = [WEEKDAYS[date.fromisoformat(d["date"]).weekday()] for d in days]
    panels = []
    if not care:
        logged = [d["kcal"] for d in days if d["meals"]]
        panels.append(("Калории, ккал", [d["kcal"] if d["meals"] else 0 for d in days],
                       days[-1]["kcal_target"], (0.85, 1.1),
                       sum(logged) / len(logged) if logged else None))
    water = [d["water"] for d in days if d["water"]]
    steps = [d["steps"] for d in days if d["steps"] is not None]
    panels += [("Вода, мл", [d["water"] for d in days], days[-1]["water_goal"], None,
                sum(water) / len(water) if water else None),
               ("Шаги", [d["steps"] or 0 for d in days], insights.STEPS_GOAL, None,
                sum(steps) / len(steps) if steps else None)]
    width, card_h, gap, head = 7.4, 2.45, 0.2, 2.1
    height = head + len(panels) * (card_h + gap) + 0.1
    fig = plt.figure(figsize=(width, height))
    bg = fig.add_axes([0, 0, 1, 1])
    bg.set_xlim(0, width)
    bg.set_ylim(0, height)
    bg.axis("off")
    _backdrop(bg, "charts.jpg", width, height, art_h=4.0, fade=(0.0, 1.0))
    axes = []
    for i in range(len(panels)):
        y0 = height - head - (i + 1) * card_h - i * gap
        _card(bg, 0.2, y0, width - 0.4, card_h)
        axes.append(fig.add_axes([0.45 / width, (y0 + 0.4) / height,
                                  (width - 0.9) / width, (card_h - 1.05) / height]))
    for ax, (title, values, goal, band, avg) in zip(axes, panels):
        top = max(max(values), goal) * 1.3
        if band:                                  # зелёный коридор вокруг цели
            ax.axhspan(goal * band[0], goal * band[1], color=GOOD, alpha=0.10, lw=0)
        ax.axhline(goal, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=1)
        colors = [_tone(v, goal, band) if v else TRACK for v in values]
        bars = ax.bar(range(len(values)), values, width=0.56, color=colors, zorder=2)
        bars[-1].set_edgecolor(INK)
        bars[-1].set_linewidth(1.4)
        for x, v in enumerate(values):
            if v:
                ax.text(x, v + top * 0.02, _num(v), ha="center", va="bottom", fontsize=8,
                        color=INK)
        ax.set_xticks(range(len(values)), labels)
        ax.set_ylim(0, top)
        ax.set_yticks([])
        ax.text(0, 1.12, title, transform=ax.transAxes, fontsize=11, fontweight="bold")
        sub = f"цель {_num(goal)}" + (f"  ·  среднее {_num(avg)}" if avg is not None else "")
        ax.text(1, 1.12, sub, transform=ax.transAxes, fontsize=9, color=MUTED, ha="right")
        _clean(ax)
        ax.set_zorder(5)
    return _png(fig, bleed=True)


# ------------------------------------------------------------------------- вес

def weight_data(db: sqlite3.Connection, user, today: date, days: int = 60) -> dict | None:
    """Данные для графика веса. Читает БД, поэтому вызывается в основном потоке."""
    rows, _ = repo.trend_weight_series(db, user["id"], (today - timedelta(days=days)).isoformat(),
                                       today.isoformat())
    if len(rows) < 2:
        return None
    return {"rows": [(r["local_date"], r["weight_kg"]) for r in rows],
            "goal": user["goal_weight_kg"]}


def _framed(art: str, title: str, sub: str, width: float = 7.4, height: float = 4.7,
            head: float = 1.45):
    """Каркас графика-линии: иллюстрация-фон сверху, заголовок на ней, под ним белая плашка с осью."""
    fig = plt.figure(figsize=(width, height))
    bg = fig.add_axes([0, 0, 1, 1])
    bg.set_xlim(0, width)
    bg.set_ylim(0, height)
    bg.axis("off")
    _backdrop(bg, art, width, height, art_h=head + 2.2, fade=(0.0, 1.0))
    bg.text(0.35, height - 0.55, title, fontsize=15, fontweight="bold", va="center", zorder=3)
    bg.text(0.35, height - 1.0, sub, fontsize=10, color=INK, alpha=0.8, va="center", zorder=3)
    _card(bg, 0.2, 0.2, width - 0.4, height - head - 0.2)
    ax = fig.add_axes([0.55 / width, 0.65 / height, (width - 1.0) / width,
                       (height - head - 1.0) / height])
    return fig, ax


@_plot_locked
def weight_chart(data: dict) -> bytes:
    rows = data["rows"]
    pts = [(date.fromisoformat(d), w) for d, w in rows]
    smooth = ewma_dated([(d.toordinal(), w) for d, w in pts])
    xs = [date.fromordinal(o) for o, _ in smooth]
    ys = [v for _, v in smooth]
    delta = ys[-1] - ys[0]
    fig, ax = _framed("weight.jpg", "Вес", f"тренд {ys[-1]:.1f} кг  ·  {delta:+.1f} кг за период")
    ax.scatter([d for d, _ in pts], [w for _, w in pts], s=26, color=MUTED, alpha=0.55,
               zorder=3, label="замеры")
    ax.plot(xs, ys, color=ACCENT, lw=3, solid_capstyle="round", zorder=4, label="тренд")
    low = min(min(ys), min(w for _, w in pts))
    high = max(max(ys), max(w for _, w in pts))
    ax.fill_between(xs, ys, low - 0.6, color=ACCENT, alpha=0.10, zorder=1)
    ax.annotate(f"{ys[-1]:.1f} кг", (xs[-1], ys[-1]), xytext=(0, 14), textcoords="offset points",
                ha="center", fontsize=11, fontweight="bold", color="white",
                bbox={"boxstyle": "round,pad=0.35", "fc": ACCENT, "ec": "none"}, zorder=6)
    goal = data["goal"]
    if goal and abs(goal - ys[-1]) < 12:               # цель далеко внизу сплющила бы график
        ax.axhline(goal, color=GOOD, lw=1.4, ls=(0, (4, 3)), zorder=2)
        ax.text(xs[0], goal, f"  цель {goal:g} кг", va="bottom", fontsize=9, color=GOOD)
        low, high = min(low, goal), max(high, goal)
    ax.set_ylim(low - 0.8, high + 1.4)
    ax.grid(axis="y", color=GRID, zorder=0)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    _clean(ax)
    return _png(fig, bleed=True)


def body_data(db: sqlite3.Connection, user, today: date, days: int = 120) -> list | None:
    """Замеры состава тела для графика. Читает БД, поэтому вызывается в основном потоке."""
    rows = db.execute(
        """SELECT local_date, body_fat_pct, muscle_mass_pct FROM body_measurements
           WHERE user_id=? AND local_date>=? AND (body_fat_pct IS NOT NULL
           OR muscle_mass_pct IS NOT NULL) ORDER BY local_date""",
        (user["id"], (today - timedelta(days=days)).isoformat())).fetchall()
    if len(rows) < 2:
        return None
    return [(r["local_date"], r["body_fat_pct"], r["muscle_mass_pct"]) for r in rows]


@_plot_locked
def body_chart(rows: list) -> bytes | None:
    fig, ax = _framed("body.jpg", "Состав тела", "жир и мышцы, % от массы")
    drawn = 0
    for idx, label, color in ((1, "Жир", OVER), (2, "Мышцы", GOOD)):
        pts = [(date.fromisoformat(r[0]), r[idx]) for r in rows if r[idx] is not None]
        if len(pts) < 2:
            continue
        drawn += 1
        ax.plot([d for d, _ in pts], [v for _, v in pts], color=color, lw=2.8, marker="o", ms=5,
                zorder=3)
        ax.annotate(f"{label} {pts[-1][1]:.1f}%", pts[-1], xytext=(0, 12),
                    textcoords="offset points", ha="center", fontsize=10, fontweight="bold",
                    color="white", bbox={"boxstyle": "round,pad=0.3", "fc": color, "ec": "none"})
        ax.annotate(f"{pts[0][1]:.1f}", pts[0], xytext=(0, -16), textcoords="offset points",
                    ha="center", fontsize=9, color=color)
    if not drawn:
        plt.close(fig)
        return None
    ax.margins(x=0.08, y=0.25)
    ax.grid(axis="y", color=GRID, zorder=0)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    _clean(ax)
    return _png(fig, bleed=True)
