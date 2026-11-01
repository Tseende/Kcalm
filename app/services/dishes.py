"""Посуда: файлы фото пустой посуды (PHOTOS_DIR/dishes/). Без aiogram: пригодится веб-API."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from app.config import get_config
from app.storage import photos


def save_dish_photo(data: bytes) -> str:
    """Записать фото пустой посуды, вернуть путь для dishes.photo_path."""
    return photos.write(Path(get_config().photos_dir) / "dishes" / f"{hashlib.sha256(data).hexdigest()[:16]}.jpg", data)


def photo_b64(dish) -> str | None:
    """Фото посуды в base64 для запроса к модели; нет фото или файла — None."""
    try:
        path = dish["photo_path"]
    except (KeyError, IndexError, TypeError):
        path = getattr(dish, "photo_path", None)
    if not path:
        return None
    try:
        return base64.b64encode(Path(path).read_bytes()).decode()
    except OSError:
        return None
