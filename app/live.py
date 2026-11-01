"""--live проверка на реальных вызовах OpenRouter (экономно!).

Запуск:
  python -m app.live --photo path/to/food.jpg [--caption "описание"]
  python -m app.live --screenshot path/to/screen.png
  python -m app.live --voice path/to/voice.ogg
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import tempfile
from pathlib import Path

from app.config import get_config
from app.db import connect, migrate
from app.llm import client as llm
from app.services import food as foodsvc
from app.services import screens


def _cost(model: str, tin: int, tout: int) -> float:
    """Грубая оценка стоимости по промт-цендам из PROMPT.md ($/1M)."""
    cheap = ("gemma", "flash", "mini", "haiku")
    in_price = 0.15 if any(c in model.lower() for c in cheap) else 0.5
    return tin / 1e6 * in_price + tout / 1e6 * (in_price * 3)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo")
    ap.add_argument("--caption", default=None)
    ap.add_argument("--screenshot")
    ap.add_argument("--voice")
    args = ap.parse_args()
    cfg = get_config()
    if not cfg.openrouter_api_key:
        print("Нет OPENROUTER_API_KEY в .env")
        return 1
    db = connect(":memory:")
    migrate(db)
    total = 0.0

    if args.photo:
        b64 = base64.b64encode(Path(args.photo).read_bytes()).decode()
        u = await foodsvc.parse_photo(b64, args.caption, None, db)
        print("FOOD:", json.dumps([i.model_dump() for i in u.items], ensure_ascii=False, indent=1))

    if args.screenshot:
        b64 = base64.b64encode(Path(args.screenshot).read_bytes()).decode()
        parsed = await screens.parse_screenshot(b64, None, db)
        print("SCREEN:", json.dumps(parsed, ensure_ascii=False, indent=1)[:2000])

    if args.voice:
        text = await llm.transcribe_ogg(Path(args.voice).read_bytes())
        print("VOICE:", text)

    rows = db.execute("SELECT purpose, model, tokens_in, tokens_out, ok, latency_ms "
                      "FROM llm_calls").fetchall()
    print("\nВызовы:")
    for r in rows:
        cost = _cost(r["model"], r["tokens_in"] or 0, r["tokens_out"] or 0)
        total += cost
        print(f"  {r['purpose']:<18} {r['model']:<36} tokens {r['tokens_in']}/{r['tokens_out']} "
              f"latency {r['latency_ms']}ms ok={r['ok']} ≈${cost:.4f}")
    print(f"Итого ≈ ${total:.4f}")
    return 0


if __name__ == "__main__":
    raise asyncio.run(main())
