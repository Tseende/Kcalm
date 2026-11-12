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
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from aiohttp import web

from app.adapters import off
from app.config import Config, get_config
from app.domain import dishes as dishdom
from app.domain import drinks as drinksdom
from app.domain import manual as manualdom
from app.domain import water as waterdom
from app.domain import weekgoal as weekgoaldom
from app.domain import weighin
from app.llm import client as llm
from app.services import dishes as dishsvc
from app.services import food as foodsvc
from app.services import meals as meals_svc
from app.services import onboarding as onboardingsvc
from app.services import profile as profilesvc
from app.services import reminders as remsvc
from app.services import screens as screens_svc
from app.services import water as watersvc
from app.services import weekgoal as weekgoalsvc
from app.storage import photos, repo
from app.timeutils import validate_hhmm, validate_timezone
from app.web import analysis
from app.web import period as period_analysis
from app.web import queries as q
from app.web.auth import InitDataError, validate_init_data
from app.web.serializers import dish_json, food_json, meal_json, product_json, targets_json

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
async def activity_middleware(request: web.Request, handler):
    """Любой запрос, кроме health-check Render и самопинга, продлевает окно keep-alive."""
    if request.path != "/healthz":
        from app.scheduler import touch_activity
        touch_activity()
    return await handler(request)


def _rollback(request: web.Request) -> None:
    """Недописанная транзакция не должна доехать до чужого commit() на общем соединении."""
    try:
        db = request.app[DB]
        db.rollback()
    except Exception:                                        # noqa: BLE001
        log.debug("web: rollback не удался", exc_info=True)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as e:
        _rollback(request)
        return _error_response(e.status, e.code, e.message)
    except web.HTTPException as e:
        if not request.path.startswith("/api/") or e.status < 400:
            raise
        code, message = _HTTP_MESSAGES.get(e.status, ("http_error", "Ошибка запроса."))
        return _error_response(e.status, code, message)
    except asyncio.CancelledError:
        _rollback(request)
        raise
    except Exception:                                        # noqa: BLE001
        _rollback(request)
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
    return config.is_allowed(tg_id)


def _unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "Откройте приложение из Telegram, чтобы войти.")


# Только эти запросы доступны до онбординга: статус профиля и сама настройка.
OPEN_BEFORE_ONBOARDING = {("GET", "/api/me"), ("POST", "/api/onboarding")}


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
            # 401, а не 403: экран блокировки в приложении привязан к 401, а текст берётся из ответа
            raise ApiError(401, "not_allowed", "Доступ к тесту пока закрыт.")
    elif dev_allowed(request.remote, request.headers, config):
        tg_id, first_name = config.web_dev_tg_id, "Dev"
    else:
        raise _unauthorized()
    user = repo.get_user_by_tg(db, tg_id)
    if (user is None or not user["onboarded"]) and (request.method, request.path) not in OPEN_BEFORE_ONBOARDING:
        raise ApiError(409, "not_onboarded", "Сначала пройдите настройку в чате с ботом.")
    request["user"] = user
    request["tg_id"] = tg_id
    request["first_name"] = first_name
    return await handler(request)


# ------------------------------------------------------------------ разбор запроса

def llm_gate(db, user) -> None:
    """До дорогой работы: дневной лимит обращений к модели выбран → 429 llm_limit."""
    try:
        llm.check_limit(db, user["id"])
    except llm.LLMLimitError:
        raise ApiError(429, "llm_limit", llm.LIMIT_TEXT) from None


def llm_failure(exc: Exception, message: str) -> ApiError:
    """Сбой модели в ответ API: лимит остаётся лимитом (429), остальное 502."""
    if isinstance(exc, llm.LLMLimitError):
        return ApiError(429, "llm_limit", llm.LIMIT_TEXT)
    return ApiError(502, "llm_unavailable", message)


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


def checked_date(user, raw) -> str | None:
    """Дата записи из тела запроса: не в будущем и не старше 30 дней (по местной дате пользователя).

    Пусто -> None (сервис возьмёт сегодня). Иначе 400 bad_date."""
    if not raw:
        return None
    day = parse_date(str(raw))
    today = now_local(user).date()
    if day > today or (today - day).days > 30:
        raise bad("Дату можно выбрать за последние 30 дней, не в будущем.", "bad_date")
    return day.isoformat()


def bounded_int(value, name: str, low: int = 1, high: int = 10**12) -> int:
    """Целое из JSON или формы в границах: мусор, bool, дробные и гигантские числа дают 400 bad_field."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise bad(f"Поле {name} должно быть целым числом.", "bad_field")
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,15}", text) or not low <= int(text) <= high:
        raise bad(f"Поле {name} должно быть целым числом.", "bad_field")
    return int(text)


async def read_json(request: web.Request) -> dict:
    if request.content_length and request.content_length > MAX_FIELD_BYTES:
        raise ApiError(413, "too_large", "Слишком большой запрос.")
    try:
        raw = await request.read()
        if len(raw) > MAX_FIELD_BYTES:                       # тело без Content-Length (chunked)
            raise ApiError(413, "too_large", "Слишком большой запрос.")
        data = json.loads(raw.decode("utf-8"))
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
        if foodsvc.image_too_large(data):
            raise bad(foodsvc.IMAGE_TOO_LARGE_TEXT, "image_too_large")


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
    user = request["user"]
    if user is None or not user["onboarded"]:
        return ok({"onboarded": False, "user": {"first_name": request["first_name"]}})
    db, user, _ = ctx(request)
    today = now_local(user).date().isoformat()
    return ok({
        "onboarded": True,
        "user": {"tg_id": user["tg_id"], "first_name": request["first_name"],
                 "sex": user["sex"], "height_cm": user["height_cm"],
                 "goal_weight_kg": user["goal_weight_kg"], "tz": user["tz"],
                 "eating_cutoff": user["eating_cutoff"]},
        "targets": targets_json(db, user),
        "weight": q.weight_summary(db, user, today),
    })


def _profile_response(db, user, extra: dict | None = None) -> web.Response:
    fresh = repo.get_user_by_tg(db, user["tg_id"])
    today = now_local(fresh).date()
    return ok({"profile": profilesvc.profile_json(db, fresh, today),
               "targets": targets_json(db, fresh), **(extra or {})})


@routes.get("/api/profile")
async def api_profile_get(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    return _profile_response(db, user, {"first_name": request["first_name"]})


@routes.patch("/api/profile")
async def api_profile_patch(request: web.Request) -> web.Response:
    """Профиль целиком или частично. Рост, возраст, пол, цель, активность и темп пересчитывают норму."""
    db, user, _ = ctx(request)
    data = await read_json(request)
    clean: dict = {}
    if data.get("sex") is not None:
        if data["sex"] not in ("male", "female"):
            raise bad("Пол: male или female.", "bad_field")
        clean["sex"] = data["sex"]
    year_now = now_local(user).year
    by = number(data, "birth_year", year_now - 100, year_now - 10)
    if by is not None:
        clean["birth_year"] = int(by)
    for key, lo, hi in (("height_cm", 120, 230), ("goal_weight_kg", 30, 250), ("rate_kg_week", 0, 0.7)):
        v = number(data, key, lo, hi)
        if v is not None:
            clean[key] = v
    if user["care_mode"] and clean.get("rate_kg_week"):
        clean["rate_kg_week"] = 0.0       # режим заботы: дефицит через API не включить
    act = number(data, "activity", 1, 4)
    if act is not None:
        clean["activity"] = int(act)
    if data.get("eating_cutoff") is not None:
        try:
            clean["eating_cutoff"] = validate_hhmm(str(data["eating_cutoff"]))
        except ValueError as exc:
            raise bad(str(exc), "bad_time") from None
    if data.get("tz") is not None:
        try:
            clean["tz"] = validate_timezone(str(data["tz"]))
        except ValueError as exc:
            raise bad(str(exc), "bad_tz") from None
    if data.get("recalc") is True:
        clean["recalc"] = True
    try:
        result = profilesvc.update_profile(db, user, clean, now_local(user).date())
    except profilesvc.ProfileError as exc:
        raise bad(str(exc), "bad_profile") from None
    return _profile_response(db, user, {"result": result})


@routes.put("/api/targets")
async def api_targets_put(request: web.Request) -> web.Response:
    """Норма вручную: калории и БЖУ. Ниже безопасного минимума калории поднимаются."""
    db, user, _ = ctx(request)
    data = await read_json(request)
    kcal = number(data, "kcal", 800, 6000, required=True)
    protein = number(data, "protein", 0, 500, required=True)
    fat = number(data, "fat", 0, 500, required=True)
    carbs = number(data, "carbs", 0, 900, required=True)
    result = profilesvc.set_manual_target(db, user, now_local(user).date(), round(kcal), protein, fat, carbs)
    return _profile_response(db, user, {"result": result})


@routes.get("/api/reminders")
async def api_reminders_get(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    return ok(_reminders_payload(db, user))


def _reminders_payload(db, user) -> dict:
    return {"reminders": profilesvc.reminders_json(db, user), "custom": remsvc.list_json(db, user),
            "templates": remsvc.templates_json(user)}


@routes.put("/api/reminders/{kind}")
async def api_reminder_put(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    data = await read_json(request)
    time = None
    if data.get("time") is not None:
        try:
            time = validate_hhmm(str(data["time"]))
        except ValueError as exc:
            raise bad(str(exc), "bad_time") from None
    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise bad("enabled: true или false.", "bad_field")
    try:
        profilesvc.set_reminder(db, user, request.match_info["kind"], time, enabled)
    except profilesvc.ProfileError as exc:
        raise ApiError(404, "not_found", str(exc)) from None
    return ok(_reminders_payload(db, user))


def _reminder_error(exc: remsvc.ReminderError) -> ApiError:
    if isinstance(exc, remsvc.ReminderLimit):
        return ApiError(409, "limit", str(exc))
    return bad(str(exc), "bad_field")


@routes.post("/api/user-reminders")
async def api_user_reminder_create(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    data = await read_json(request)
    try:
        item = remsvc.create(db, user, data)
    except remsvc.ReminderError as exc:
        raise _reminder_error(exc) from None
    return ok({"reminder": item, **_reminders_payload(db, user)}, 201)


@routes.patch("/api/user-reminders/{id}")
async def api_user_reminder_patch(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    rid = path_id(request)
    data = await read_json(request)
    try:
        item = remsvc.update(db, user, rid, data)
    except remsvc.ReminderError as exc:
        raise _reminder_error(exc) from None
    if item is None:
        raise ApiError(404, "not_found", "Напоминание не найдено.")
    return ok({"reminder": item, **_reminders_payload(db, user)})


@routes.delete("/api/user-reminders/{id}")
async def api_user_reminder_delete(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    if not remsvc.delete(db, user, path_id(request)):
        raise ApiError(404, "not_found", "Напоминание не найдено.")
    return ok(_reminders_payload(db, user))


ONBOARDING_REMINDERS = ("morning", "midday", "evening", "weekly", "water", "weigh")


@routes.post("/api/onboarding")
async def api_onboarding(request: web.Request) -> web.Response:
    """Первичная настройка из Mini App: профиль, норма, напоминания. Логика общая с чатом."""
    db, config = request.app[DB], request.app[CONFIG]
    user = request["user"]
    if user is not None and user["onboarded"]:
        raise ApiError(409, "already_onboarded", "Профиль уже настроен.")
    data = await read_json(request)
    if data.get("sex") not in ("male", "female"):
        raise bad("Пол: male или female.", "bad_field")
    tz = onboardingsvc.DEFAULT_TZ
    if data.get("tz") is not None:
        try:
            tz = validate_timezone(str(data["tz"]))
        except ValueError as exc:
            raise bad(str(exc), "bad_tz") from None
    year_now = foodsvc.local_now(tz).year
    clean: dict = {"sex": data["sex"], "tz": tz,
                   "birth_year": int(number(data, "birth_year", year_now - 100, year_now, required=True)),
                   "height_cm": number(data, "height_cm", 120, 230, required=True),
                   "weight_kg": number(data, "weight_kg", 35, 300, required=True),
                   "goal_weight_kg": number(data, "goal_weight_kg", 30, 250),
                   "rate_kg_week": number(data, "rate_kg_week", 0, 0.7)}
    act = number(data, "activity", 1, 4)
    clean["activity"] = int(act) if act is not None else 2
    clean["eating_cutoff"] = config.eating_cutoff_default
    if data.get("eating_cutoff") is not None:
        try:
            clean["eating_cutoff"] = validate_hhmm(str(data["eating_cutoff"]))
        except ValueError as exc:
            raise bad(str(exc), "bad_time") from None
    care = data.get("care_mode", False)
    if not isinstance(care, bool):
        raise bad("care_mode: true или false.", "bad_field")
    clean["care_mode"] = care
    reminders = data.get("reminders")
    if reminders is not None:
        if not isinstance(reminders, dict) or any(
                k not in ONBOARDING_REMINDERS or not isinstance(v, bool) for k, v in reminders.items()):
            raise bad("reminders: объект вида {\"morning\": true, ...}.", "bad_field")
        clean["reminders"] = reminders
    try:
        result = onboardingsvc.complete(db, request["tg_id"], clean)
    except onboardingsvc.OnboardingError as exc:
        raise bad(str(exc), exc.code) from None
    await _announce_profile_ready(request, result, care)
    return ok({k: result[k] for k in ("profile", "targets", "notes", "goal_date")})


async def _announce_profile_ready(request: web.Request, result: dict, care: bool) -> None:
    """Короткое сообщение в чат после настройки в приложении. Сбой отправки онбординг не ломает."""
    bot, config = request.app[BOT], request.app[CONFIG]
    if bot is None:
        return
    head = "Профиль готов. Норму посчитал, цифры буду показывать бережно." if care \
        else f"Профиль готов, норма ≈{result['kcal']} ккал."
    tail = ("Кидайте еду сюда текстом, фото или голосом." if config.chat_logs_food
            else "Еду добавляйте в приложении кнопкой «+», а здесь можно поговорить, если вечером потянет к холодильнику.")
    try:
        from app.routers import menu
        await bot.send_message(request["tg_id"], f"{head} {tail}", reply_markup=menu.main_kb())
    except Exception:                                        # noqa: BLE001
        log.warning("не удалось отправить сообщение о готовности профиля", exc_info=True)


@routes.delete("/api/me")
async def api_me_delete(request: web.Request) -> web.Response:
    """Удалить аккаунт и все данные. Нужно подтверждение: {"confirm": "УДАЛИТЬ"}."""
    db, user, _ = ctx(request)
    data = await read_json(request)
    if str(data.get("confirm") or "").strip().upper() != "УДАЛИТЬ":
        raise bad("Для удаления нужно подтверждение.", "confirm_required")
    profilesvc.delete_all_data(db, user)
    return ok({"deleted": True})


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
        "weight_last": q.last_weight(db, uid, key),
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
    data = meal_json(db, repo.get_meal(db, user["id"], meal_id))
    fav = repo.favorite_for_meal(db, user["id"], meal_id)      # звезда на экране приёма
    data["favorite_id"] = fav["id"] if fav else None
    return ok(data, status)


@routes.post("/api/meals/product")
async def api_meal_product(request: web.Request) -> web.Response:
    """Продукт целиком одним приёмом (скан штрихкода): значения на 100 г и порция, без LLM."""
    db, user, _ = ctx(request)
    data = await read_json(request)
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 120:
        raise bad("Нет названия продукта.", "bad_field")
    brand = str(data.get("brand") or "").strip()
    grams = number(data, "grams", 1, 3000, required=True)
    calc = manualdom.portion_calculation(
        f"{name} ({brand})" if brand and brand.lower() not in name.lower() else name, grams,
        number(data, "kcal", 0, 1000, required=True), number(data, "protein", 0, 100) or 0.0,
        number(data, "fat", 0, 100) or 0.0, number(data, "carbs", 0, 100) or 0.0)
    now = now_local(user)
    local_date, local_time = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    meal_id = repo.save_meal(
        db, user["id"], meals_svc.utc_of(user["tz"], local_date, local_time), local_date, local_time,
        foodsvc.guess_meal_type(None, local_time, None), "manual", calc)
    return ok({"id": meal_id, "kcal": calc.kcal_total}, 201)


# ------------------------------------------------------------------ ручной ввод

MAX_MANUAL_ITEMS = 30
_MACRO_KEYS = ("kcal", "protein", "fat", "carbs")
_LIMITS = {"100g": {"kcal": 900, "protein": 100, "fat": 100, "carbs": 100},
           "portion": {"kcal": 10000, "protein": 1000, "fat": 1000, "carbs": 1000}}


def _per_of(data: dict) -> str:
    per = data.get("per", "100g")
    if per not in _LIMITS:
        raise bad("per: 100g или portion.", "bad_field")
    return per


def _clean_name(data: dict, *, required: bool) -> str | None:
    if "name" not in data and not required:
        return None
    name = str(data.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise bad("Название: от 1 до 120 символов.", "bad_field")
    return name


def _macros_of(data: dict, per: str, grams: float | None, *, required: bool):
    """(ккал, Б, Ж, У) как ввёл человек или None. Нужны все четыре; на 100 г или на порцию."""
    given = [k for k in _MACRO_KEYS if data.get(k) is not None]
    if not given and not required:
        return None
    if len(given) != len(_MACRO_KEYS):
        raise bad("Нужны все четыре значения: ккал, белки, жиры и углеводы.", "bad_field")
    lim = _LIMITS[per]
    values = tuple(number(data, k, 0, lim[k]) for k in _MACRO_KEYS)
    if per == "portion" and grams:
        # пересчёт на 100 г не должен выходить за физический предел (чистый жир ≈ 900 ккал)
        for k, v in zip(_MACRO_KEYS, values):
            if v * 100.0 / max(grams, 1.0) > _LIMITS["100g"][k] + 0.5:
                raise bad(f"Поле {k}: на 100 г получается больше возможного. Проверьте граммы.",
                          "bad_field")
    return values


def _food_or_404(db, user, raw):
    food = repo.get_food_for_user(db, user["id"], bounded_int(raw, "food_id"))
    if food is None:
        raise ApiError(404, "not_found", "Продукт не найден.")
    return food


def _food_label(food) -> str:
    name, brand = food["name_ru"], (food["brand"] or "").strip()
    return f"{name} ({brand})" if brand and brand.lower() not in name.lower() else name


def _manual_entry(db, user, raw):
    """Позиция из JSON ручного ввода → (CalculatedItem, food | None, save: bool)."""
    if not isinstance(raw, dict):
        raise bad("Позиция должна быть объектом.", "bad_field")
    grams = number(raw, "grams", 1, 5000, required=True)
    if raw.get("food_id") is not None:
        food = _food_or_404(db, user, raw["food_id"])
        owned = food["owner_user_id"] is not None
        item = manualdom.make_item(
            _food_label(food), grams, food["kcal_100g"], food["protein_100g"], food["fat_100g"],
            food["carbs_100g"], "100g", source="user" if owned else food["source"],
            food_ref_id=food["id"], name_en=food["name_en"])
        return item, food, False
    name = _clean_name(raw, required=True)
    per = _per_of(raw)
    macros = _macros_of(raw, per, grams, required=True)
    save = raw.get("save", False)
    if not isinstance(save, bool):
        raise bad("save: true или false.", "bad_field")
    item = manualdom.make_item(name, grams, *macros, per, source="manual", edited=True)
    return item, None, save


def _remember(db, user, item, food, save: bool) -> None:
    """Сохранение в «мои продукты» (с дедупом) и обучение порции для продукта из базы."""
    if food is not None:
        repo.touch_user_food(db, user["id"], food["id"], item.grams)
    elif save:
        draft = {"name": item.name_ru[:60], "kcal": round(item.kcal_100g),
                 "protein": item.protein_100g, "fat": item.fat_100g, "carbs": item.carbs_100g,
                 "default_grams": round(item.grams)}
        try:
            item.food_ref_id, _warnings = service("products", "save_product")(db, user, draft)
        except ValueError as e:
            raise bad(str(e), "bad_field") from None


@routes.get("/api/foods/search")
async def api_foods_search(request: web.Request) -> web.Response:
    """Поиск продукта без LLM и сети: сначала свои, затем общий справочник. До 2 символов: свои частые."""
    db, user, _ = ctx(request)
    try:
        limit = max(1, min(50, int(request.query.get("limit", 30))))
    except ValueError:
        limit = 30
    found = repo.search_foods(db, user["id"], request.query.get("q", "").strip(), limit)
    return ok({"foods": [food_json(f) for f in found]})


@routes.post("/api/meals/manual")
async def api_meal_manual(request: web.Request) -> web.Response:
    """Приём из готовых продуктов и ручного ввода, без LLM: вилка равна итогу."""
    db, user, _ = ctx(request)
    data = await read_json(request)
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_MANUAL_ITEMS:
        raise bad(f"items: от 1 до {MAX_MANUAL_ITEMS} позиций.", "bad_field")
    meal_type = data.get("type")
    if meal_type is not None and meal_type not in MEAL_TYPES:
        raise bad("type: breakfast, lunch, dinner или snack.", "bad_field")
    when = parse_when(user, str(data["date"]) if data.get("date") else None,
                      str(data["time"]) if data.get("time") else None)
    entries = [_manual_entry(db, user, r) for r in raw_items]
    for item, food, save in entries:
        _remember(db, user, item, food, save)
    now = when or now_local(user).replace(tzinfo=None)
    local_date, local_time = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    meal_id = repo.save_meal(
        db, user["id"], meals_svc.utc_of(user["tz"], local_date, local_time), local_date, local_time,
        meal_type or foodsvc.guess_meal_type(None, local_time, None), "manual",
        manualdom.combine([e[0] for e in entries]))
    return _meal_response(db, user, meal_id, 201)


@routes.post("/api/meals/{id}/items")
async def api_meal_item_add(request: web.Request) -> web.Response:
    """Добавить продукт или ручную позицию в сохранённый приём (тело как одна позиция /api/meals/manual)."""
    db, user, _ = ctx(request)
    meal = _meal_or_404(db, user, path_id(request))
    data = await read_json(request)
    if len(repo.get_meal_items(db, meal["id"])) >= MAX_MANUAL_ITEMS:
        raise bad(f"В приёме не больше {MAX_MANUAL_ITEMS} позиций.", "too_many_items")
    item, food, save = _manual_entry(db, user, data)
    _remember(db, user, item, food, save)
    repo.add_meal_item(db, meal["id"], item)
    return _meal_response(db, user, meal["id"], 201)


@routes.get("/api/meals/{id}")
async def api_meal_get(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    meal = _meal_or_404(db, user, path_id(request))
    return _meal_response(db, user, meal["id"])


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
    if comment and len(comment) > MAX_TEXT:
        raise bad("Слишком длинный комментарий.", "text_too_long")
    require_images(photos)
    local_dt = parse_when(user, form.value("date"), form.value("time"))
    llm_gate(db, user)
    try:
        if photos:
            dish_ids = None
            if "dish_id" in form.fields:
                dish_ids = []
                for raw in form.fields["dish_id"]:
                    raw = raw.strip()
                    try:
                        dish_ids.append(bounded_int(raw, "dish_id") if raw else None)
                    except ApiError:
                        raise bad("dish_id должен быть числом или пустым.", "bad_field") from None
            groups = None
            raw_groups = form.value("groups")
            if raw_groups:
                try:
                    groups = [int(x) for x in raw_groups.split(",")]
                except ValueError:
                    raise bad("groups: список номеров блюд через запятую.", "bad_field") from None
                if len(groups) != len(photos):
                    raise bad("groups должен быть по одному номеру на каждое фото.", "bad_field")
            paths = [save_upload(config, data) for data in photos]
            meal_id = await meals_svc.recognize_and_save(
                db, user, paths, dish_ids, comment or text, local_dt, groups)
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
    except llm.LLMError as e:
        log.warning("web: распознавание не удалось", exc_info=True)
        raise llm_failure(e, "Нет связи с моделью. Попробуйте ещё раз чуть позже.") from None
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
    """Избранные блюда, затем частые за 60 дней: запись в одно нажатие. Разовые блюда не предлагаем."""
    db, user, _ = ctx(request)
    try:
        limit = max(1, min(30, int(request.query.get("limit", 8))))
    except ValueError:
        limit = 8
    today = now_local(user).strftime("%Y-%m-%d")
    favorites = repo.list_favorites(db, user["id"], limit=limit)
    items = [{"meal_id": f["meal_id"], "favorite_id": f["id"], "title": f["title"] or _short_title(f["names"]),
              "names": f["names"], "kcal": int(f["kcal"]), "count": 0} for f in favorites]
    have = {repo.meal_signature(f["names"]) for f in favorites}
    for f in repo.frequent_meals(db, user["id"], limit=limit + len(have), today=today):
        if len(items) >= limit:
            break
        if f["count"] < 2 or repo.meal_signature(f["names"]) in have:
            continue
        items.append({"meal_id": f["meal_id"], "favorite_id": None, "title": _short_title(f["names"]),
                      "names": f["names"], "kcal": int(f["kcal"]), "count": f["count"]})
    return ok({"items": items})


def _favorite_json(f: dict) -> dict:
    return {"id": f["id"], "title": f["title"] or _short_title(f["names"]), "note": f["note"],
            "names": f["names"], "kcal": int(f["kcal"]), "protein": round(float(f["protein"] or 0), 1),
            "fat": round(float(f["fat"] or 0), 1), "carbs": round(float(f["carbs"] or 0), 1),
            "meal_type": f.get("meal_type"), "use_count": f.get("use_count", 0),
            "photo_url": f"/api/favorites/{f['id']}/photo" if f.get("has_photo") else None}


def _favorite_fields(data: dict, *, partial: bool) -> tuple[str | None, str | None, "object | None"]:
    """Название, заметка и (если переданы все четыре числа) новый состав порции."""
    title = None
    if "title" in data or not partial:
        title = str(data.get("title") or "").strip()
        if not title:
            raise bad("Назовите блюдо.", "bad_field")
        if len(title) > 80:
            raise bad("Название не длиннее 80 символов.", "bad_field")
    note = None
    if "note" in data:
        note = str(data.get("note") or "").strip()
        if len(note) > 500:
            raise bad("Заметка не длиннее 500 символов.", "bad_field")
    calc = None
    if any(k in data for k in ("kcal", "protein", "fat", "carbs")) or not partial:
        kcal = number(data, "kcal", 0, 5000, required=True)
        protein = number(data, "protein", 0, 500) or 0.0
        fat = number(data, "fat", 0, 500) or 0.0
        carbs = number(data, "carbs", 0, 1000) or 0.0
        calc = manualdom.totals_calculation(title or "Блюдо", kcal, protein, fat, carbs)
    return title, note, calc


@routes.get("/api/favorites")
async def api_favorites(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    return ok({"items": [_favorite_json(f) for f in repo.list_favorites(db, user["id"], limit=100)]})


@routes.get("/api/favorites/{id}")
async def api_favorite_get(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    fav = repo.get_favorite(db, user["id"], path_id(request))
    if fav is None:
        raise ApiError(404, "not_found", "Избранное не найдено.")
    return ok(_favorite_json(fav))


@routes.get("/api/favorites/{id}/photo")
async def api_favorite_photo(request: web.Request) -> web.StreamResponse:
    db, user, config = ctx(request)
    path = repo.favorite_photo_path(db, user["id"], path_id(request))
    if path is None:
        raise ApiError(404, "not_found", "Фото нет.")
    return serve_photo(config, path)


@routes.post("/api/favorites/custom")
async def api_favorite_custom(request: web.Request) -> web.Response:
    """Избранное с нуля: название, ккал и БЖУ порции, заметка. В дневник не попадает."""
    db, user, _ = ctx(request)
    title, note, calc = _favorite_fields(await read_json(request), partial=False)
    fav_id = repo.create_favorite_custom(db, user["id"], title, note, calc)
    if fav_id is None:
        raise ApiError(409, "exists", "Блюдо с таким названием уже есть в избранном.")
    return ok({"id": fav_id}, 201)


@routes.patch("/api/favorites/{id}")
async def api_favorite_patch(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    fav_id = path_id(request)
    old = repo.get_favorite(db, user["id"], fav_id)
    if old is None:
        raise ApiError(404, "not_found", "Избранное не найдено.")
    data = await read_json(request)
    if any(k in data for k in ("kcal", "protein", "fat", "carbs")):      # недостающие числа берём из текущих
        data = {**{k: old[k] for k in ("kcal", "protein", "fat", "carbs")}, **data}
    title, note, calc = _favorite_fields(data, partial=True)
    if title is None:
        title = old["title"]
    if note is None:
        note = old["note"]
    if calc is not None and "title" not in data and not old["title"]:
        calc.items[0].name_ru = old["names"][0] if old["names"] else "Блюдо"
    repo.update_favorite(db, user["id"], fav_id, title=title, note=note, calc=calc)
    return ok(_favorite_json(repo.get_favorite(db, user["id"], fav_id)))


@routes.post("/api/favorites")
async def api_favorite_add(request: web.Request) -> web.Response:
    """Приём пищи → избранное (повтор того же состава не двоится)."""
    db, user, _ = ctx(request)
    data = await read_json(request)
    try:
        meal_id = bounded_int(data.get("meal_id"), "meal_id")
    except ApiError:
        raise bad("Не указан приём пищи.", "bad_field") from None
    fav_id = repo.add_favorite(db, user["id"], meal_id)
    if fav_id is None:
        raise ApiError(404, "not_found", "Приём пищи не найден или в нём нет позиций.")
    return ok({"id": fav_id}, 201)


@routes.delete("/api/favorites/{id}")
async def api_favorite_delete(request: web.Request) -> web.Response:
    db, user, _ = ctx(request)
    if not repo.remove_favorite(db, user["id"], path_id(request)):
        raise ApiError(404, "not_found", "Избранное не найдено.")
    return web.Response(status=204)


@routes.post("/api/favorites/{id}/repeat")
async def api_favorite_repeat(request: web.Request) -> web.Response:
    """Записать избранное в дневник. Тело необязательно: factor (0,25–4), meal_type, date (последние 30 дней)."""
    db, user, _ = ctx(request)
    fav_id = path_id(request)
    data = await read_json(request) if request.can_read_body else {}
    factor = number(data, "factor", 0.25, 4)
    meal_type = data.get("meal_type")
    if meal_type is not None and meal_type not in MEAL_TYPES:
        raise bad("Неизвестный тип приёма пищи.", "bad_field")
    day = checked_date(user, data.get("date"))
    meal_id = repo.favorite_meal_id(db, user["id"], fav_id)
    new_id = meals_svc.repeat_meal(db, user, meal_id, allow_deleted=True, factor=factor or 1.0,
                                   meal_type=meal_type, day=day) if meal_id else None
    if new_id is None:
        raise ApiError(404, "not_found", "Избранное не найдено.")
    repo.touch_favorite(db, user["id"], fav_id)
    return ok({"id": new_id, "kcal": int(repo.get_meal(db, user["id"], new_id)["kcal_total"])}, 201)


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
    rows = repo.get_meal_items(db, meal["id"])
    locked = [r for r in rows if repo.is_locked_item(r)]       # ручные правки модель не трогает
    old = [dict(r) for r in rows if not repo.is_locked_item(r)]
    llm_gate(db, user)
    try:
        if locked:
            u, _changes = await foodsvc.refine_meal(
                old, comment, user["id"], db,
                locked=[{"name_ru": r["name_ru"], "grams": r["grams"]} for r in locked])
            keep = {" ".join(str(r["name_ru"]).lower().split()) for r in locked}
            u.items = [i for i in u.items                         # дубль закреплённой позиции
                       if " ".join(i.name_ru.lower().split()) not in keep]
        else:
            u, _changes = await foodsvc.refine_meal(old, comment, user["id"], db)
    except llm.LLMError as e:
        log.warning("web: refine не удался", exc_info=True)
        raise llm_failure(e, "Нет связи с моделью: запись осталась как была.") from None
    if not u.items:
        raise ApiError(422, "unclear", "Не понял, что поменять. Расскажите подробнее.")
    calc = await foodsvc.build_calc(u, db, user["id"])
    meals_svc.touch_foods(db, user["id"], u, calc)
    if locked:
        calc = _with_locked(calc, locked)
    repo.replace_meal_items(db, user["id"], meal["id"], calc)
    return _meal_response(db, user, meal["id"])


def _with_locked(calc, locked):
    """К пересчитанному составу вернуть закреплённые позиции ровно как были (первыми)."""
    from app.domain.nutrition import CalculatedItem
    kept = [CalculatedItem(
        name_ru=r["name_ru"], name_en=r["name_en"], grams=r["grams"], grams_min=r["grams"],
        grams_max=r["grams"], kcal=r["kcal"], protein_g=r["protein_g"], fat_g=r["fat_g"],
        carbs_g=r["carbs_g"], kcal_low=r["kcal"], kcal_high=r["kcal"], food_ref_id=r["food_ref_id"],
        source=r["source"], confidence=r["confidence"], kcal_100g=r["kcal_100g"],
        protein_100g=r["protein_100g"], fat_100g=r["fat_100g"], carbs_100g=r["carbs_100g"],
        edited_by_user=True) for r in locked]
    items = kept + list(calc.items)
    kcal = sum(i.kcal for i in items)
    fresh = len(calc.items)
    return calc.model_copy(update={
        "items": items, "kcal_total": kcal,
        "kcal_low": min(kcal, sum(i.kcal for i in kept) + (calc.kcal_low if fresh else 0)),
        "kcal_high": max(kcal, sum(i.kcal for i in kept) + (calc.kcal_high if fresh else 0)),
        "protein_g": round(sum(i.protein_g for i in items), 1),
        "fat_g": round(sum(i.fat_g for i in items), 1),
        "carbs_g": round(sum(i.carbs_g for i in items), 1),
        "confidence": round(min(i.confidence for i in items), 2)})


# ------------------------------------------------------------------ позиции

def _item_or_404(db, user, item_id: int):
    item = q.owned_item(db, user["id"], item_id)
    if item is None:
        raise ApiError(404, "not_found", "Позиция не найдена.")
    return item


@routes.patch("/api/items/{id}")
async def api_item_patch(request: web.Request) -> web.Response:
    """Правка позиции. Только grams: пересчёт от базы на 100 г (степпер). Иначе ручная правка:
    name, grams, kcal/protein/fat/carbs (все четыре) на 100 г или на порцию (per), либо food_id
    (заменить продуктом из базы, граммы остаются)."""
    db, user, _ = ctx(request)
    item = _item_or_404(db, user, path_id(request))
    data = await read_json(request)
    if not any(k in data for k in ("grams", "name", "per", "food_id", *_MACRO_KEYS)):
        raise bad("Нечего менять.", "empty_patch")
    grams = number(data, "grams", 1, 5000, required=True) if "grams" in data else None
    if set(data) <= {"grams"}:
        repo.edit_item_grams(db, item["id"], grams)
        if item["food_ref_id"]:
            repo.touch_user_food(db, user["id"], item["food_ref_id"], grams)    # учим порцию
        return _meal_response(db, user, item["meal_id"])
    name = _clean_name(data, required=False)
    per = _per_of(data)
    food = None
    if data.get("food_id") is not None:
        if any(data.get(k) is not None for k in _MACRO_KEYS):
            raise bad("food_id и свои значения вместе нельзя.", "bad_field")
        food = _food_or_404(db, user, data["food_id"])
        macros = None
        name = name or _food_label(food)
    else:
        macros = _macros_of(data, per, grams or item["grams"], required=False)
    if name is None and macros is None and food is None and grams is None:
        raise bad("Нечего менять.", "empty_patch")
    repo.edit_item_manual(db, item["id"], name=name, grams=grams, macros=macros, per=per, food=food)
    if food is not None:
        repo.touch_user_food(db, user["id"], food["id"], grams or item["grams"])
    return _meal_response(db, user, item["meal_id"])


@routes.post("/api/items/{id}/barcode")
async def api_item_barcode(request: web.Request) -> web.Response:
    """Уточнить позицию по упаковке: штрихкод → Open Food Facts, граммы остаются прежними."""
    db, user, _ = ctx(request)
    item = _item_or_404(db, user, path_id(request))
    code = str((await read_json(request)).get("code") or "").strip()
    if not code.isdigit() or not off.valid_barcode(code):
        raise bad("Штрихкод не подходит: проверьте цифры (8, 12 или 13).", "bad_barcode")
    result = await call(service("products", "barcode_draft"), db, user, code)
    if result is None:
        raise ApiError(404, "not_found", "В Open Food Facts такого штрихкода нет. "
                                         "Оставил позицию как была.")
    draft, _existing = result
    name = " ".join(filter(None, [draft.get("name"), draft.get("brand")]))[:60] or item["name_ru"]
    repo.apply_product_to_item(db, item["id"], name, float(draft["kcal"]),
                               float(draft["protein"] or 0), float(draft["fat"] or 0),
                               float(draft["carbs"] or 0))
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
_PRODUCT_TEXTS = {"name": 60, "brand": 60, "unit_name": 30, "barcode": 32, "icon": 24}


def clean_product(data: dict, *, partial: bool) -> dict:
    """Поля продукта из JSON: только переданные и проверенные (на 100 г)."""
    out: dict = {}
    for key, (low, high) in _PRODUCT_NUMBERS.items():
        required = not partial and key in ("kcal", "protein", "fat", "carbs")
        value = number(data, key, low, high, required=required)
        if partial and value is None and key in ("kcal", "protein", "fat", "carbs") and key in data:
            raise bad(f"Поле {key} нельзя очистить.", "bad_field")     # null в PATCH ломал проверку значений
        if key in data and (value is not None or partial):
            out[key] = value
    for key, limit in _PRODUCT_TEXTS.items():
        if key in data:
            text = str(data[key] or "").strip()[:limit]
            if key == "name" and not text:
                raise bad("У продукта должно быть название.", "bad_field")
            if key == "icon" and text and not re.fullmatch(r"[a-z_]{1,24}", text):
                raise bad("Неизвестная иконка.", "bad_field")
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


MAX_LABEL_PHOTOS = 4


@routes.post("/api/products/label")
async def api_product_label(request: web.Request) -> web.Response:
    """1–4 фото одного товара (лицевая сторона, таблица КБЖУ, штрихкод) → черновик продукта."""
    db, user, config = ctx(request)
    fn = service("products", "parse_label")
    form = await read_form(request, config.max_media_bytes)
    photos = form.files.get("photo", [])
    if not 1 <= len(photos) <= MAX_LABEL_PHOTOS:
        raise bad(f"Нужно от 1 до {MAX_LABEL_PHOTOS} фото одного товара.", "bad_photo")
    require_images(photos)
    llm_gate(db, user)
    try:
        draft, confidence, notes = await call(fn, db, user, [base64.b64encode(p).decode() for p in photos])
    except llm.LLMError as e:
        raise llm_failure(e, "Нет связи с моделью. Попробуйте ещё раз чуть позже.") from None
    draft, notes = await call(service("products", "enrich_with_off"), draft, notes)
    return ok({"draft": {**product_json(draft), "id": None}, "confidence": confidence,
               "notes": notes})


@routes.get("/api/products/barcode/{code}")
async def api_product_barcode(request: web.Request) -> web.Response:
    """Штрихкод → черновик из Open Food Facts (поле existing_id: такой продукт у вас уже есть)."""
    db, user, _ = ctx(request)
    code = request.match_info["code"].strip()
    if not code.isdigit() or not off.valid_barcode(code):
        raise bad("Штрихкод не подходит: проверьте цифры (8, 12 или 13).", "bad_barcode")
    result = await call(service("products", "barcode_draft"), db, user, code)
    if result is None:
        raise ApiError(404, "not_found", "В Open Food Facts такого штрихкода нет. "
                                         "Сфотографируйте этикетку или введите вручную.")
    draft, existing_id = result
    return ok({"draft": {**product_json(draft), "id": None}, "existing_id": existing_id,
               "source": "off"})


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
    except (ValueError, sqlite3.IntegrityError) as e:        # IntegrityError: CHECK/NOT NULL базы
        raise bad(str(e) if isinstance(e, ValueError) else "Не удалось сохранить посуду.",
                  "bad_field") from None
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
    except (ValueError, sqlite3.IntegrityError) as e:
        raise bad(str(e) if isinstance(e, ValueError) else "Не удалось сохранить посуду.",
                  "bad_field") from None
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
    data = await read_json(request)
    weight = round(number(data, "weight_kg", low, high, required=True), 1)
    now = now_local(user)
    today = now.strftime("%Y-%m-%d")
    local_date = checked_date(user, data.get("date")) or today
    # За прошлый день время взвешивания неизвестно: «натощак» не определяем.
    fasting = (weighin.classify_fasting(now.strftime("%H:%M"),
                                        repo.first_meal_time(db, user["id"], local_date))
               if local_date == today else None)
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
    llm_gate(db, user)
    try:
        result = await call(fn, db, user, save_upload(config, photos[0]))
    except llm.LLMError as e:
        raise llm_failure(e, "Нет связи с моделью. Попробуйте ещё раз чуть позже.") from None
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
    date_s = checked_date(user, data.get("date"))
    for key in ("start", "end"):
        if data.get(key):
            try:
                validate_hhmm(str(data[key]))
            except ValueError:
                raise bad("Время нужно в формате ЧЧ:ММ.", "bad_time") from None
    try:
        await call(fn, db, user, date_s, duration,
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
    date_s = checked_date(user, data.get("date"))
    if data.get("workout") is not None and len(str(data["workout"])) > 200:
        raise bad("Слишком длинное описание тренировки.", "bad_field")
    try:
        await call(fn, db, user, date_s, steps, active_kcal, active_minutes,
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
    period = request.query.get("period", "day")
    if period != "day":
        if period not in period_analysis.PERIODS:
            raise bad("period: day, week или month.", "bad_period")
        return ok(period_analysis.build(db, user, now.date(), period))
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
    app = web.Application(middlewares=[activity_middleware, error_middleware, auth_middleware],
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
