"""Живой прогон парсера скриншота на реальном файле.

    .venv/bin/python tools/try_screenshot.py path/to/shot.jpg [model]

Ключ берётся из OPENROUTER_API_KEY или из конфига Hermes (~/.hermes/config.yaml).
Тратит копейки: один vision-вызов. Нужен только для ручной проверки промпта.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    cfg = pathlib.Path.home() / ".hermes" / "config.yaml"
    if cfg.exists():
        import re
        text = cfg.read_text(encoding="utf-8")
        # без PyYAML: первый ключ OpenRouter в конфиге
        match = re.search(r"api_key:\s*[\"']?(sk-or-[A-Za-z0-9._-]+)", text)
        if match:
            key = match.group(1)
    if not key:
        raise SystemExit("нет ключа: задай OPENROUTER_API_KEY")
    return key


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    image_path = pathlib.Path(sys.argv[1]).expanduser()
    model = sys.argv[2] if len(sys.argv) > 2 else None

    os.environ["OPENROUTER_API_KEY"] = load_key()
    if model:
        os.environ["MODEL_VISION"] = model

    from app.config import get_config
    from app.db import connect, migrate
    from app.services import screens
    from app.storage import repo

    cfg = get_config()
    db_path = tempfile.mktemp(suffix=".db")
    db = connect(db_path)
    migrate(db)
    uid = repo.create_user(db, 1, tz=cfg.tz)

    data = base64.b64encode(image_path.read_bytes()).decode()
    print(f"файл: {image_path.name}  модель: {cfg.model_vision}")
    parsed = await screens.parse_screenshot(data, uid, db)
    print("\n--- ответ модели ---")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))

    kind = screens.apply_parsed(db, uid, parsed, parsed.get("date"))
    print(f"\n--- в БД (тип: {kind}) ---")
    row = db.execute("SELECT * FROM body_measurements WHERE user_id=?", (uid,)).fetchone()
    if row is None:
        print("ничего не записано")
    else:
        print({k: row[k] for k in row.keys() if row[k] is not None})
    db.close()
    os.unlink(db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
