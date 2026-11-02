"""aiohttp-сервер Mini App: статика /app/ и JSON API /api/* (контракт: docs/V2_API.md).

Работает в процессе бота поверх тех же repo и services. Авторизация: заголовок
`Authorization: tma <initData>`; в логи ни initData, ни токены не попадают.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import inspect
import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from aiohttp import web

from app.config import Config, get_config
from app.domain import dishes as dishdom
from app.domain import drinks as drinksdom
from app.domain import water as waterdom
from app.domain import weekgoal as weekgoaldom
from app.domain import weighin
from app.llm import client as llm
from app.services import dishes as dishsvc
from app.services import food as foodsvc
from app.services import meals as meals_svc
from app.services import screens as screens_svc
from app.services import water as watersvc
from app.services import weekgoal as weekgoalsvc
from app.storage import photos, repo
from app.timeutils import validate_hhmm
from app.web import analysis
from app.web import queries as q
from app.web.auth import InitDataError, validate_init_data
from app.web.serializers import dish_json, meal_json, product_json, targets_json

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_PHOTOS = 6
MAX_FIELD_BYTES = 64 * 1024
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
LOCAL_HOSTS = ("127.0.0.1", "::1")
PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP", "X-Forwarded-Host")
NO_CACHE = {"Cache-Control": "no-cache"}

DB = web.AppKey("db", sqlite3.Connection)
CONFIG = web.AppKey("config", Config)
BOT = web.AppKey("bot", object)
MAX_BODY = web.AppKey("max_body", int)

routes = web.RouteTableDef()


# ------------------------------------------------------------------ ошибки и ответы

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def ok(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps,
                             headers={"Cache-Control": "no-store"})


def _error_response(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status, dumps=_dumps,
                             headers={"Cache-Control": "no-store"})


_HTTP_MESSAGES = {
    400: ("bad_request", "Некорректный запрос."),
    404: ("not_found", "Не найдено."),
    405: ("method_not_allowed", "Метод не поддерживается."),
    413: ("too_large", "Слишком большой запрос. Попробуйте фото поменьше."),
}


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as e:
        return _error_response(e.status, e.code, e.message)
    except web.HTTPException as e:
        if not request.path.startswith("/api/") or e.status < 400:
            raise
        code, message = _HTTP_MESSAGES.get(e.status, ("http_error", "Ошибка запроса."))
        return _error_response(e.status, code, message)
    except asyncio.CancelledError:
        raise
    except Exception:                                        # noqa: BLE001
        log.exception("web: необработанная ошибка в %s %s", request.method, request.path)
        if not request.path.startswith("/api/"):
            raise
        return _error_response(500, "internal", "Что-то пошло не так. Попробуйте ещё раз.")


# ------------------------------------------------------------------ авторизация

def dev_allowed(remote: str | None, headers, config: Config) -> bool:
    """Dev-режим: только с локального адреса и только без заголовков обратного прокси
    (за туннелем или nginx на этой же машине все запросы выглядят локальными)."""
    if not config.web_dev_tg_id or remote not in LOCAL_HOSTS:
        return False
    return not any(h in headers for h in PROXY_HEADERS)


def whitelisted(config: Config, tg_id: int) -> bool:
    return config.allow_public_bot or tg_id in config.allowed_ids


def _unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "Откройте приложение из Telegram, чтобы войти.")


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    config, db = request.app[CONFIG], request.app[DB]
    header = request.headers.get("Authorization", "")
    first_name = ""
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "tma" or not value.strip():
            raise _unauthorized()
        try:
            data = validate_init_data(value.strip(), config.telegram_bot_token)
        except InitDataError as e:
            log.info("web: initData отклонена (%s)", e.code)
            raise _unauthorized() from None
        tg_id = data["user"]["id"]
        first_name = str(data["user"].get("first_name") or "")
        if not whitelisted(config, tg_id):
            log.info("web: whitelist: %s отклонён", tg_id)
            raise _unauthorized()
    elif dev_allowed(request.remote, request.headers, config):
        tg_id, first_name = config.web_dev_tg_id, "Dev"
    else:
        raise _unauthorized()
    user = repo.get_user_by_tg(db, tg_id)
    if user is None or not user["onboarded"]:
        raise ApiError(409, "not_onboarded", "Сначала пройдите настройку в чате с ботом.")
    request["user"] = user
    request["first_name"] = first_name
    return await handler(request)


# ------------------------------------------------------------------ разбор запроса

def ctx(request: web.Request):
    return request.app[DB], request["user"], request.app[CONFIG]


def bad(message: str, code: str = "bad_request") -> ApiError:
    return ApiError(400, code, message)


def path_id(request: web.Request, name: str = "id") -> int:
    raw = request.match_info[name]
    if not raw.isdigit() or len(raw) > 12:
        raise ApiError(404, "not_found", "Не найдено.")
    return int(raw)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise bad("Дата нужна в формате ГГГГ-ММ-ДД.", "bad_date") from None


def now_local(user) -> datetime:
    return foodsvc.local_now(user["tz"])


async def read_json(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise bad("Ожидался JSON.", "bad_json") from None
    if not isinstance(data, dict):
        raise bad("Ожидался JSON-объект.", "bad_json")
    return data


class Form:
    """Поля и файлы multipart-запроса."""

    def __init__(self) -> None:
        self.fields: dict[str, list[str]] = {}
        self.files: dict[str, list[bytes]] = {}

    def value(self, name: str) -> str | None:
        values = self.fields.get(name)
        return values[-1].strip() if values and values[-1].strip() else None


async def read_form(request: web.Request, max_file: int) -> Form:
    """Разобрать multipart потоком: размер каждого файла и всего запроса ограничен."""
    limit = request.app[MAX_BODY]
    if request.content_length and request.content_length > limit:
        raise ApiError(413, "too_large", _HTTP_MESSAGES[413][1])
    if not request.content_type.startswith("multipart/"):
        raise bad("Ожидалась форма multipart/form-data.")
    form, total = Form(), 0
    try:
        reader = await request.multipart()
        async for part in reader:
            is_file = part.filename is not None
            cap = max_file if is_file else MAX_FIELD_BYTES
            buf = bytearray()
            while True:
                chunk = await part.read_chunk(64 * 1024)
                if not chunk:
                    break
                buf += chunk
                total += len(chunk)
                if len(buf) > cap or total > limit:
                    raise ApiError(413, "too_large", _HTTP_MESSAGES[413][1])
            name = part.name or ""
            if is_file:
                if buf:
                    form.files.setdefault(name, []).append(bytes(buf))
            else:
                form.fields.setdefault(name, []).append(bytes(buf).decode("utf-8", "replace"))
    except ValueError:
        raise bad("Не удалось разобрать форму.") from None
    return form


def image_kind(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def require_images(files: list[bytes]) -> None:
    for data in files:
        if image_kind(data) is None:
            raise ApiError(415, "unsupported_media",
                           "Нужно фото в формате JPEG, PNG или WebP.")


def save_upload(config: Config, data: bytes) -> str:
    """Как routers/intake._download_image: PHOTOS_DIR/{sha256[:16]}.jpg."""
    return photos.write(Path(config.photos_dir) / f"{hashlib.sha256(data).hexdigest()[:16]}.jpg", data)


def serve_photo(config: Config, stored_path: str | None) -> web.StreamResponse:
    """Отдать файл только из PHOTOS_DIR (без выхода через .. и симлинки)."""
    root = Path(config.photos_dir).resolve()
    try:
        path = Path(stored_path or "").resolve()
    except (OSError, RuntimeError):
        path = None
    if not path or root not in path.parents or not path.is_file():
        raise ApiError(404, "not_found", "Не найдено.")
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400",
                                           "X-Content-Type-Options": "nosniff"})


def parse_when(user, date_s: str | None, time_s: str | None) -> datetime | None:
    """Поля формы date/time → наивное локальное время приёма (None — «сейчас»)."""
    if not date_s and not time_s:
        return None
    now = now_local(user)
    day = parse_date(date_s) if date_s else now.date()
    if day > now.date():
        raise bad("Приём не может быть в будущем.", "bad_date")
    if time_s:
        try:
            hhmm = validate_hhmm(time_s)
        except ValueError:
            raise bad("Время нужно в формате ЧЧ:ММ.", "bad_time") from None
    else:
        hhmm = now.strftime("%H:%M") if day == now.date() else "12:00"
    return datetime.strptime(f"{day.isoformat()} {hhmm}", "%Y-%m-%d %H:%M")


def number(data: dict, key: str, low: float, high: float, *, required: bool = False):
    """Число из JSON в границах. Нет поля: None (или ошибка, если required)."""
    if key not in data or data[key] is None:
        if required:
            raise bad(f"Не указано поле {key}.", "bad_field")
        return None
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise bad(f"Поле {key} должно быть числом.", "bad_field")
    try:
        value = float(str(value).replace(",", "."))
    except ValueError:
        raise bad(f"Поле {key} должно быть числом.", "bad_field") from None
    if not low <= value <= high or value != value:
        raise bad(f"Поле {key}: допустимо от {low:g} до {high:g}.", "bad_field")
    return value


def service(module: str, attr: str):
    """Функция из сервиса фаз 3–4. Нет модуля или функции: 501 not_ready."""
    try:
        return getattr(importlib.import_module(f"app.services.{module}"), attr)
    except (ImportError, AttributeError):
        raise ApiError(501, "not_ready", "Эта функция ещё не готова.") from None


async def call(fn, *args):
    result = fn(*args)
    return await result if inspect.isawaitable(result) else result


# ------------------------------------------------------------------ профиль и день

@routes.get("/healthz")
async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@routes.get("/api/me")
async def api_me(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    today = now_local(user).date().isoformat()
    return ok({
        "user": {"tg_id": user["tg_id"], "first_name": request["first_name"],
                 "sex": user["sex"], "height_cm": user["height_cm"],
                 "goal_weight_kg": user["goal_weight_kg"], "tz": user["tz"],
                 "eating_cutoff": user["eating_cutoff"]},
        "targets": targets_json(db, user),
        "weight": q.weight_summary(db, user, today),
    })


@routes.get("/api/day")
async def api_day(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    raw = request.query.get("date")
    day = parse_date(raw) if raw else now_local(user).date()
    key, uid = day.isoformat(), user["id"]
    targets = targets_json(db, user)
    totals = q.day_totals(db, uid, key)
    return ok({
        "date": key, "targets": targets, "totals": totals,
        "remaining_kcal": targets["kcal"] - totals["kcal"],
        "water_ml": repo.day_water_ml(db, uid, key),
        "meals": [meal_json(db, m) for m in repo.day_meals(db, uid, key)],
        "sleep": q.day_sleep(db, uid, key),
        "activity": q.day_activity(db, uid, key),
        "weight": q.day_weight(db, uid, key),
    })


@routes.get("/api/stats")
async def api_stats(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    range_name = request.query.get("range", "week")
    if range_name not in q.RANGE_DAYS:
        raise bad("range: week, month или 90d.", "bad_range")
    return ok(q.stats(db, user, range_name, now_local(user).date()))


# ------------------------------------------------------------------ приёмы пищи

def _meal_or_404(db, user, meal_id: int):
    meal = q.live_meal(db, user["id"], meal_id)
    if meal is None:
        raise ApiError(404, "not_found", "Приём пищи не найден.")
    return meal


def _meal_response(db, user, meal_id: int, status: int = 200) -> web.Response:
    return ok(meal_json(db, repo.get_meal(db, user["id"], meal_id)), status)


@routes.get("/api/meals/{id}")
async def api_meal_get(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    return ok(meal_json(db, _meal_or_404(db, user, path_id(request))))


@routes.post("/api/meals")
async def api_meal_create(request: web.Request) -> web.Response:
    db, user, config = ctx(request)
    form = await read_form(request, config.max_media_bytes)
    photos = form.files.get("photo", [])
    comment = form.value("comment")
    text = form.value("text")
    if len(photos) > MAX_PHOTOS:
        raise bad(f"Не больше {MAX_PHOTOS} фото в одном приёме.", "too_many_photos")
    if not photos and not text:
        raise bad("Нужно фото или текстовое описание.", "empty_meal")
    if text and len(text) > MAX_TEXT:
        raise bad("Слишком длинное описание.", "text_too_long")
    require_images(photos)
    local_dt = parse_when(user, form.value("date"), form.value("time"))
    try:
        if photos:
            dish_ids = None
            if "dish_id" in form.fields:
                dish_ids = []
                for raw in form.fields["dish_id"]:
                    raw = raw.strip()
                    if raw and not raw.isdigit():
                        raise bad("dish_id должен быть числом или пустым.", "bad_field")
                    dish_ids.append(int(raw) if raw else None)
            paths = [save_upload(config, data) for data in photos]
            meal_id = await meals_svc.recognize_and_save(
                db, user, paths, dish_ids, comment or text, local_dt)
        else:
            u = await foodsvc.parse_text(text, user["id"], db)
            if not u.items:
                raise meals_svc.NoFood()
            meal_id = (await meals_svc.save_understanding(
                db, user, u, text=text, source="text", taken_at=local_dt)).meal_id
    except meals_svc.NoFood:
        raise ApiError(422, "no_food", "Не увидел еды. Опишите приём словами или пришлите другое фото.") from None
    except meals_svc.NoPhotos:
        raise ApiError(422, "no_photos", "Фото не прочитались. Попробуйте ещё раз.") from None
    except llm.LLMError:
        log.warning("web: распознавание не удалось", exc_info=True)
        raise ApiError(502, "llm_unavailable",
                       "Нет связи с моделью. Попробуйте ещё раз чуть позже.") from None
    return _meal_response(db, user, meal_id, 201)


@routes.patch("/api/meals/{id}")
async def api_meal_patch(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    meal = _meal_or_404(db, user, path_id(request))
    data = await read_json(request)
    local_date, local_time, meal_type = meal["local_date"], meal["local_time"], meal["meal_type"]
    if data.get("time") is not None:
        try:
            local_time = validate_hhmm(str(data["time"]))
        except ValueError:
            raise bad("Время нужно в формате ЧЧ:ММ.", "bad_time") from None
    if data.get("date") is not None:
        local_date = parse_date(str(data["date"])).isoformat()
        if local_date > now_local(user).date().isoformat():
            raise bad("Приём не может быть в будущем.", "bad_date")
    if data.get("type") is not None:
        if data["type"] not in MEAL_TYPES:
            raise bad("type: breakfast, lunch, dinner или snack.", "bad_field")
        meal_type = data["type"]
    repo.update_meal_time(db, user["id"], meal["id"], local_date, local_time, meal_type,
                          meals_svc.utc_of(user["tz"], local_date, local_time))
    return _meal_response(db, user, meal["id"])


@routes.delete("/api/meals/{id}")
async def api_meal_delete(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    if not repo.soft_delete_meal(db, user["id"], path_id(request)):
        raise ApiError(404, "not_found", "Приём пищи не найден.")
    return web.Response(status=204)


def _short_title(names: list[str]) -> str:
    """Подпись для кнопки частого блюда: первое название и сколько ещё в составе."""
    first = names[0][:1].upper() + names[0][1:]
    if len(first) > 28:
        first = first[:27].rstrip() + "…"
    return first if len(names) == 1 else f"{first} +{len(names) - 1}"


@routes.get("/api/frequent")
async def api_frequent(request: web.Request) -> web.Response:
    """Частые блюда за 60 дней для записи в одно нажатие. Разовые блюда не предлагаем."""
    db, user, _ = ctx(request)
    today = now_local(user).strftime("%Y-%m-%d")
    items = [{"meal_id": f["meal_id"], "title": _short_title(f["names"]),
              "names": f["names"], "kcal": int(f["kcal"]), "count": f["count"]}
             for f in repo.frequent_meals(db, user["id"], limit=8, today=today)
             if f["count"] >= 2]
    return ok({"items": items})


@routes.post("/api/meals/{id}/repeat")
async def api_meal_repeat(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    new_id = meals_svc.repeat_meal(db, user, path_id(request))
    if new_id is None:
        raise ApiError(404, "not_found", "Приём пищи не найден.")
    return ok({"id": new_id, "kcal": int(repo.get_meal(db, user["id"], new_id)["kcal_total"])}, 201)


@routes.post("/api/meals/{id}/refine")
async def api_meal_refine(request: web.Request) -> web.Response:
    """Уточнение рассказом («жарил на масле»): правим состав, как кнопка «Уточнить» в боте."""
    db, user, _ = ctx(request)
    meal = _meal_or_404(db, user, path_id(request))
    comment = str((await read_json(request)).get("comment") or "").strip()
    if not comment:
        raise bad("Напишите, что уточнить.", "empty_comment")
    if len(comment) > MAX_TEXT:
        raise bad("Слишком длинное уточнение.", "text_too_long")
    old = [dict(r) for r in repo.get_meal_items(db, meal["id"])]
    try:
        u, _changes = await foodsvc.refine_meal(old, comment, user["id"], db)
    except llm.LLMError:
        log.warning("web: refine не удался", exc_info=True)
        raise ApiError(502, "llm_unavailable",
                       "Нет связи с моделью: запись осталась как была.") from None
    if not u.items:
        raise ApiError(422, "unclear", "Не понял, что поменять. Расскажите подробнее.")
    calc = await foodsvc.build_calc(u, db, user["id"])
    repo.replace_meal_items(db, user["id"], meal["id"], calc)
    meals_svc.touch_foods(db, user["id"], u, calc)
    return _meal_response(db, user, meal["id"])


# ------------------------------------------------------------------ позиции

def _item_or_404(db, user, item_id: int):
    item = q.owned_item(db, user["id"], item_id)
    if item is None:
        raise ApiError(404, "not_found", "Позиция не найдена.")
    return item


@routes.patch("/api/items/{id}")
async def api_item_patch(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    item = _item_or_404(db, user, path_id(request))
    grams = number(await read_json(request), "grams", 1, 5000, required=True)
    repo.edit_item_grams(db, item["id"], grams)
    if item["food_ref_id"]:
        repo.touch_user_food(db, user["id"], item["food_ref_id"], grams)    # учим порцию
    return _meal_response(db, user, item["meal_id"])


@routes.delete("/api/items/{id}")
async def api_item_delete(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    item = _item_or_404(db, user, path_id(request))
    repo.delete_item(db, item["id"])
    return _meal_response(db, user, item["meal_id"])


@routes.post("/api/items/{id}/save_product")
async def api_item_save_product(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    item = _item_or_404(db, user, path_id(request))
    fn = service("products", "product_from_item")
    body = await read_json(request) if request.can_read_body else {}
    name = str(body.get("name") or "").strip()[:60] or None
    try:
        product_id, _warnings = await call(fn, db, user, item["id"], name)
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    return ok(product_json(repo.get_product(db, user["id"], product_id)), 201)


# ------------------------------------------------------------------ продукты (фаза 3)

_PRODUCT_NUMBERS = {"kcal": (0, 900), "protein": (0, 100), "fat": (0, 100), "carbs": (0, 100),
                    "default_grams": (1, 5000), "unit_grams": (1, 5000)}
_PRODUCT_TEXTS = {"name": 60, "brand": 60, "unit_name": 30, "barcode": 32}


def clean_product(data: dict, *, partial: bool) -> dict:
    """Поля продукта из JSON: только переданные и проверенные (на 100 г)."""
    out: dict = {}
    for key, (low, high) in _PRODUCT_NUMBERS.items():
        required = not partial and key in ("kcal", "protein", "fat", "carbs")
        value = number(data, key, low, high, required=required)
        if key in data and (value is not None or partial):
            out[key] = value
    for key, limit in _PRODUCT_TEXTS.items():
        if key in data:
            text = str(data[key] or "").strip()[:limit]
            if key == "name" and not text:
                raise bad("У продукта должно быть название.", "bad_field")
            out[key] = text or None
    if not partial and not out.get("name"):
        raise bad("У продукта должно быть название.", "bad_field")
    return out


@routes.get("/api/products")
async def api_products(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    rows = repo.list_products(db, user["id"], request.query.get("q", "").strip() or None,
                              limit=200)
    return ok({"products": [product_json(p) for p in rows]})


@routes.post("/api/products")
async def api_product_create(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    data = clean_product(await read_json(request), partial=False)
    name = data.pop("name")
    try:
        product_id = repo.create_product(db, user["id"], name, data.pop("kcal"),
                                         data.pop("protein"), data.pop("fat"),
                                         data.pop("carbs"), **data)
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    return ok(product_json(repo.get_product(db, user["id"], product_id)), 201)


@routes.patch("/api/products/{id}")
async def api_product_patch(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    product_id = path_id(request)
    data = clean_product(await read_json(request), partial=True)
    try:
        product = repo.update_product(db, user["id"], product_id, **data)
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    if product is None:
        raise ApiError(404, "not_found", "Продукт не найден.")
    return ok(product_json(repo.get_product(db, user["id"], product_id)))


@routes.delete("/api/products/{id}")
async def api_product_delete(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    if not repo.delete_product(db, user["id"], path_id(request)):
        raise ApiError(404, "not_found", "Продукт не найден.")
    return web.Response(status=204)


@routes.post("/api/products/label")
async def api_product_label(request: web.Request) -> web.Response:
    db, user, config = ctx(request)
    fn = service("products", "parse_label")
    form = await read_form(request, config.max_media_bytes)
    photos = form.files.get("photo", [])
    if len(photos) != 1:
        raise bad("Нужно одно фото этикетки.", "bad_photo")
    require_images(photos)
    try:
        draft, confidence, notes = await call(fn, db, user, base64.b64encode(photos[0]).decode())
    except llm.LLMError:
        raise ApiError(502, "llm_unavailable",
                       "Нет связи с моделью. Попробуйте ещё раз чуть позже.") from None
    return ok({"draft": {**product_json(draft), "id": None}, "confidence": confidence,
               "notes": notes})


# ------------------------------------------------------------------ посуда

def _dish_or_404(db, user, dish_id: int):
    dish = repo.get_dish(db, user["id"], dish_id)
    if dish is None:
        raise ApiError(404, "not_found", "Посуда не найдена.")
    return dish


def _dish_fields(data: dict, *, partial: bool) -> dict:
    """Поля посуды (JSON или значения формы) → аргументы repo.create_dish/update_dish."""
    out: dict = {}
    if "name" in data or not partial:
        name = str(data.get("name") or "").strip()
        if not name:
            raise bad("У посуды должно быть название.", "bad_field")
        out["name"] = name
    if "kind" in data or not partial:
        kind = data.get("kind") or "other"
        if kind not in repo.DISH_KINDS:
            raise bad("Неизвестный тип посуды.", "bad_field")
        out["kind"] = kind
    for key, (low, high) in dishdom.LIMITS.items():
        if key in data:
            out[key] = number(data, key, low, high) if str(data[key] or "").strip() else None
    if "is_default" in data:
        out["is_default"] = str(data["is_default"]).lower() in ("1", "true", "yes", "on")
    return out


async def _dish_input(request: web.Request, config: Config, *, partial: bool):
    """Тело запроса посуды: multipart (поля + photo) или JSON. Возвращает (поля, фото|None)."""
    if request.content_type.startswith("multipart/"):
        form = await read_form(request, config.max_media_bytes)
        data = {k: form.value(k) or "" for k in form.fields}
        photos = form.files.get("photo", [])
        if len(photos) > 1:
            raise bad("Достаточно одного фото посуды.", "bad_photo")
        require_images(photos)
        return _dish_fields(data, partial=partial), (photos[0] if photos else None)
    return _dish_fields(await read_json(request), partial=partial), None


@routes.get("/api/dishes")
async def api_dishes(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    return ok({"dishes": [dish_json(d) for d in repo.list_dishes(db, user["id"])]})


@routes.post("/api/dishes")
async def api_dish_create(request: web.Request) -> web.Response:
    db, user, config = ctx(request)
    fields, photo = await _dish_input(request, config, partial=False)
    if photo:
        fields["photo_path"] = dishsvc.save_dish_photo(photo)
    try:
        dish_id = repo.create_dish(db, user["id"], **fields)
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    return ok(dish_json(repo.get_dish(db, user["id"], dish_id)), 201)


@routes.patch("/api/dishes/{id}")
async def api_dish_patch(request: web.Request) -> web.Response:
    db, user, config = ctx(request)
    dish = _dish_or_404(db, user, path_id(request))
    fields, photo = await _dish_input(request, config, partial=True)
    if photo:
        fields["photo_path"] = dishsvc.save_dish_photo(photo)
    try:
        repo.update_dish(db, user["id"], dish["id"], **fields)
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    return ok(dish_json(repo.get_dish(db, user["id"], dish["id"])))


@routes.delete("/api/dishes/{id}")
async def api_dish_delete(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    if not repo.delete_dish(db, user["id"], path_id(request)):
        raise ApiError(404, "not_found", "Посуда не найдена.")
    return web.Response(status=204)


@routes.get("/api/dishes/{id}/photo")
async def api_dish_photo(request: web.Request) -> web.StreamResponse:
    db, user, config = ctx(request)
    return serve_photo(config, _dish_or_404(db, user, path_id(request))["photo_path"])


@routes.get("/api/photos/{id}")
async def api_photo(request: web.Request) -> web.StreamResponse:
    db, user, config = ctx(request)
    photo = q.owned_photo(db, user["id"], path_id(request))
    if photo is None:
        raise ApiError(404, "not_found", "Фото не найдено.")
    return serve_photo(config, photo["path"])


# ------------------------------------------------------------------ вода и вес

def _water_total(db, user) -> int:
    return repo.day_water_ml(db, user["id"], now_local(user).date().isoformat())


@routes.post("/api/water")
async def api_water_add(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    ml = number(await read_json(request), "ml", waterdom.MIN_ML, waterdom.MAX_ML, required=True)
    water_id, _ = watersvc.log_water(db, user, int(round(ml)), "command")
    return ok({"water_ml": _water_total(db, user), "goal_ml": watersvc.goal_for(db, user),
               "id": water_id}, 201)


@routes.delete("/api/water/{id}")
async def api_water_delete(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    if not repo.delete_water(db, user["id"], path_id(request)):
        raise ApiError(404, "not_found", "Запись воды не найдена.")
    return ok({"water_ml": _water_total(db, user), "goal_ml": watersvc.goal_for(db, user)})


@routes.get("/api/drinks")
async def api_drinks(request: web.Request) -> web.Response:
    ctx(request)
    return ok({"drinks": drinksdom.catalog_json()})


@routes.post("/api/drinks")
async def api_drink_add(request: web.Request) -> web.Response:
    """Напиток записывается приёмом пищи (snack, source=manual): так калории попадают в день,
    чай и кофе сами идут в воду, а отмена и правка работают как у любого приёма."""
    db, user, _ = ctx(request)
    data = await read_json(request)
    drink = drinksdom.get(str(data.get("drink") or ""))
    if drink is None:
        raise bad("Неизвестный напиток.", "bad_field")
    ml = number(data, "ml", drinksdom.MIN_ML, drinksdom.MAX_ML, required=True)
    sugar = number(data, "sugar_tsp", 0, drinksdom.MAX_SUGAR_TSP) or 0
    calc = drinksdom.calculate(drink, round(ml), int(round(sugar)))
    now = now_local(user)
    local_date, local_time = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    meal_id = repo.save_meal(
        db, user["id"], meals_svc.utc_of(user["tz"], local_date, local_time),
        local_date, local_time, "snack", "manual", calc)
    return ok({"id": meal_id, "kcal": calc.kcal_total,
               "water_ml": _water_total(db, user), "goal_ml": watersvc.goal_for(db, user)}, 201)


@routes.post("/api/weight")
async def api_weight(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    low, high = screens_svc.METRIC_RANGES["weight_kg"]
    weight = round(number(await read_json(request), "weight_kg", low, high, required=True), 1)
    now = now_local(user)
    local_date = now.strftime("%Y-%m-%d")
    fasting = weighin.classify_fasting(now.strftime("%H:%M"),
                                       repo.first_meal_time(db, user["id"], local_date))
    repo.save_body_measurement(db, user["id"], local_date,
                               {"weight_kg": weight, "fasting": fasting}, "manual", 1.0)
    return ok({"weight": q.day_weight(db, user["id"], local_date),
               "trend": q.weight_trend(db, user["id"], local_date)})


# ------------------------------------------------------------------ скриншоты, сон, активность (фаза 4)

@routes.post("/api/screens")
async def api_screen_upload(request: web.Request) -> web.Response:
    db, user, config = ctx(request)
    fn = service("tracking", "process_screenshot")
    form = await read_form(request, config.max_media_bytes)
    photos = form.files.get("photo", [])
    if len(photos) != 1:
        raise bad("Нужен один скриншот.", "bad_photo")
    require_images(photos)
    try:
        result = await call(fn, db, user, save_upload(config, photos[0]))
    except llm.LLMError:
        raise ApiError(502, "llm_unavailable",
                       "Нет связи с моделью. Попробуйте ещё раз чуть позже.") from None
    result = {**result, "applied": bool(result.get("applied"))}
    return ok(result, 201)


@routes.post("/api/screens/{id}/confirm")
async def api_screen_confirm(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    fn = service("tracking", "confirm_screenshot")
    screen_id = path_id(request)
    apply = (await read_json(request)).get("apply", True)
    kind = await call(fn, db, user, screen_id, bool(apply))
    if apply and kind is None:
        raise ApiError(404, "not_found", "Скриншот не найден или данные не прошли проверку.")
    return ok({"applied": kind is not None})


@routes.post("/api/sleep")
async def api_sleep(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    fn = service("tracking", "log_sleep")
    data = await read_json(request)
    duration = number(data, "duration_min", 0, 1440)
    score = number(data, "score", 0, 100)
    if data.get("date"):
        parse_date(str(data["date"]))
    for key in ("start", "end"):
        if data.get(key):
            try:
                validate_hhmm(str(data[key]))
            except ValueError:
                raise bad("Время нужно в формате ЧЧ:ММ.", "bad_time") from None
    try:
        await call(fn, db, user, data.get("date") or None, duration,
                   data.get("start") or None, data.get("end") or None, score)
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    return ok({"ok": True})


@routes.post("/api/activity")
async def api_activity(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    fn = service("tracking", "log_activity")
    data = await read_json(request)
    steps = number(data, "steps", 0, 200000)
    active_kcal = number(data, "active_kcal", 0, 10000)
    active_minutes = number(data, "active_minutes", 0, 1440)
    if data.get("date"):
        parse_date(str(data["date"]))
    if data.get("workout") is not None and len(str(data["workout"])) > 200:
        raise bad("Слишком длинное описание тренировки.", "bad_field")
    try:
        await call(fn, db, user, data.get("date") or None, steps, active_kcal, active_minutes,
                   data.get("workout") or None)
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    return ok({"ok": True})


# ------------------------------------------------------------------ анализ дня и выгрузка

@routes.get("/api/analysis")
async def api_analysis(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    now = now_local(user)
    raw = request.query.get("date")
    return ok(analysis.build(db, user, parse_date(raw) if raw else now.date(), now))


@routes.get("/api/week-goal")
async def api_week_goal(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    today = now_local(user).date()
    data = weekgoalsvc.build(db, user, today)
    data["recap"] = weekgoalsvc.recap(db, user, today)
    return ok(data)


@routes.put("/api/week-goal")
async def api_week_goal_set(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    data = await read_json(request)
    target = number(data, "target", 1, 7, required=True)
    today = now_local(user).date()
    try:
        weekgoalsvc.choose(db, user, today, str(data.get("kind") or ""), int(round(target)))
    except ValueError as e:
        raise bad(str(e), "bad_field") from None
    return ok(weekgoalsvc.build(db, user, today))


@routes.delete("/api/week-goal")
async def api_week_goal_clear(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    today = now_local(user).date()
    repo.clear_weekly_goal(db, user["id"], weekgoaldom.week_start(today).isoformat())
    return ok(weekgoalsvc.build(db, user, today))


@routes.get("/api/export.csv")
async def api_export(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    kind = request.query.get("kind", "meals")
    if kind not in q.CSV_KINDS:
        raise bad("kind: meals, body, water, sleep или activity.", "bad_kind")
    body = "﻿" + q.export_csv(db, user["id"], kind)           # BOM: Excel читает кириллицу
    name = f"export_{kind}_{now_local(user).date().isoformat()}.csv"
    return web.Response(text=body, content_type="text/csv", charset="utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "no-store"})


# ------------------------------------------------------------------ статика

@routes.get("/")
async def root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/app/")


@routes.get("/app")
@routes.get("/app/")
async def app_index(request: web.Request) -> web.StreamResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(index, headers=NO_CACHE)


@routes.get("/app/{path:.+}")
async def app_file(request: web.Request) -> web.StreamResponse:
    root_dir = STATIC_DIR.resolve()
    path = (root_dir / request.match_info["path"]).resolve()
    if root_dir not in path.parents or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={**NO_CACHE, "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------ сборка и запуск

MAX_TEXT = 2000                                    # длина текста и комментария к приёму
WEBHOOK_PATH = "/tg/webhook"


def build_app(db, bot, config: Config | None = None, dp=None) -> web.Application:
    config = config or get_config()
    max_body = MAX_PHOTOS * config.max_media_bytes + 1024 * 1024
    app = web.Application(middlewares=[error_middleware, auth_middleware],
                          client_max_size=max_body)
    app[DB], app[CONFIG], app[BOT], app[MAX_BODY] = db, config, bot, max_body
    app.add_routes(routes)
    if dp is not None and bot is not None and config.webhook_base_url:
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler
        SimpleRequestHandler(dispatcher=dp, bot=bot,
                             secret_token=config.webhook_secret_value).register(app, path=WEBHOOK_PATH)
    return app


async def set_menu_button(bot, config: Config) -> None:
    """Кнопка меню чата открывает Mini App (WEBAPP_URL)."""
    from aiogram.types import MenuButtonWebApp, WebAppInfo
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text="Приложение", web_app=WebAppInfo(url=config.webapp_url)))
    except Exception:                                        # noqa: BLE001 — не критично для бота
        log.warning("не удалось поставить кнопку меню", exc_info=True)


async def start_web(db, bot, config: Config, dp=None) -> web.AppRunner | None:
    """Запустить сервер рядом с polling (или как приёмник webhook). None — выключен или порт не открылся."""
    if not (config.web_enabled or config.webhook_base_url):
        return None
    if config.web_dev_tg_id:
        log.warning("WEB_DEV_TG_ID задан: запросы с 127.0.0.1 без initData проходят как "
                    "пользователь %s. Для боевого запуска очисти его.", config.web_dev_tg_id)
    runner = web.AppRunner(build_app(db, bot, config, dp), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, config.web_host, config.web_port).start()
    except OSError:
        log.exception("веб-сервер не запустился на %s:%s", config.web_host, config.web_port)
        await runner.cleanup()
        return None
    log.info("веб-сервер Mini App: http://%s:%s/app/", config.web_host, config.web_port)
    if bot is not None and config.webapp_url:
        await set_menu_button(bot, config)
    return runner
