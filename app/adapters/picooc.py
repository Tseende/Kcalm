"""Клиент неофициального облака Picooc (весы).

Схема авторизации и полей восстановлена по MIT-проекту AlexxIT/SmartScaleConnect
(https://github.com/AlexxIT/SmartScaleConnect). API неофициальный: подпись зависит
от версии приложения и может измениться в любой момент.

Правила эксплуатации:
- только для собственного аккаунта, креды — в .env;
- API — ускоритель, а не единственный путь: скриншоты приложения работают всегда;
- сырой ответ сохраняется вызывающим кодом при ошибке разбора.

Проверено офлайн (подпись, пагинация, маппинг) и НЕ проверено живыми запросами.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

API_BASE = "https://api2.picooc-int.com/v1/api/"
APPVER = "i4.1.11.0"          # обновлять вместе с версией приложения в сторе
DEFAULT_TIMEOUT = 60.0        # их API медленный


class PicoocError(RuntimeError):
    """Любая неудача протокола: сеть, code != 0, сломанный формат."""


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest().upper()


def sign(device_id: str, method: str, timestamp: str, appver: str = APPVER) -> str:
    """sign = MD5(device_id + MD5(ts + MD5(method) + MD5(appver))), всё в upper."""
    return _md5(device_id + _md5(timestamp + _md5(method) + _md5(appver)))


def base_params(device_id: str, method: str, timestamp: str,
                appver: str = APPVER) -> dict[str, str]:
    return {
        "appver": appver,
        "timestamp": timestamp,
        "lang": "en",
        "method": method,
        "timezone": "",
        "sign": sign(device_id, method, timestamp, appver),
        "push_token": "android::" + device_id,
        "device_id": device_id,
    }


def _num(value, *, zero_is_null: bool = False) -> float | None:
    """0 у Picooc часто значит «нет данных», а не ноль."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if zero_is_null and number == 0.0:
        return None
    return number


def map_record(rec: dict) -> dict:
    """Запись Picooc → канонический замер (ключи как в body_measurements)."""
    body_time = rec.get("bodyTime")
    if body_time in (None, "", 0):
        raise PicoocError(f"запись без bodyTime: {rec!r}")
    return {
        "measured_at_utc": datetime.fromtimestamp(int(body_time), tz=timezone.utc),
        "weight_kg": _num(rec.get("weight"), zero_is_null=True),
        "bmi": _num(rec.get("bmi"), zero_is_null=True),
        "body_fat_pct": _num(rec.get("body_fat"), zero_is_null=True),
        "body_water_pct": _num(rec.get("water_race"), zero_is_null=True),
        "bone_mass_kg": _num(rec.get("bone_mass"), zero_is_null=True),
        "bmr_kcal": _num(rec.get("basic_metabolism"), zero_is_null=True),
        "visceral_fat": _num(rec.get("visceral_fat_level"), zero_is_null=True),
        "metabolic_age": _num(rec.get("body_age"), zero_is_null=True),
        # Picooc: skeletal_muscle — кг (часто 0 = нет данных), muscle_race — доля %
        "muscle_mass_kg": _num(rec.get("skeletal_muscle"), zero_is_null=True),
        "skeletal_muscle_pct": _num(rec.get("muscle_race"), zero_is_null=True),
        "subcutaneous_fat_pct": _num(rec.get("subcutaneous_fat"), zero_is_null=True),
        "impedance_ohm": _num(rec.get("electric_resistance"), zero_is_null=True),
        "device_id": rec.get("mac"),
        "source": "picooc_api",
    }


@dataclass
class PicoocSession:
    device_id: str
    user_id: str
    role_ids: dict[str, str] = field(default_factory=dict)

    def role(self, name: str = "") -> str:
        if name not in self.role_ids:
            raise PicoocError(f"picooc: неизвестный профиль {name!r}, есть {list(self.role_ids)}")
        return self.role_ids[name]


class PicoocClient:
    """Одна сессия на прогон. Использовать как async context manager."""

    def __init__(self, email: str, password: str, *,
                 base_url: str = API_BASE,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 device_id: str | None = None) -> None:
        self.email = email
        self.password = password
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.device_id = device_id or str(uuid.uuid4()).upper()
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout)
        self.session: PicoocSession | None = None

    async def __aenter__(self) -> "PicoocClient":
        return self

    async def __aexit__(self, *exc) -> None:  # noqa: ANN002
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- login
    async def login(self) -> PicoocSession:
        timestamp = str(int(time.time()))
        form = base_params(self.device_id, "user_login_new", timestamp)
        form["reqData"] = json.dumps(
            {
                "appver": APPVER,
                "timestamp": timestamp,
                "lang": "en",
                "method": "user_login_new",
                "timezone": "",
                "sign": form["sign"],
                "push_token": form["push_token"],
                "device_id": self.device_id,
                "req": {
                    "app_channel": "",
                    "app_version": APPVER,
                    "email": self.email,
                    "password": self.password,
                    "phone": "",
                    "phone_system": "",
                    "phone_type": "",
                },
            },
            separators=(",", ":"),
        )
        body = await self._request(
            "POST", "account/login", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = body.get("resp") or {}
        if not resp.get("user_id") or not resp.get("role_id"):
            raise PicoocError(f"picooc login: в ответе нет user_id/role_id: {body!r}")

        role_ids = {"": str(resp["role_id"])}
        for role in resp.get("roles") or []:
            role_ids[role.get("role_name", "")] = str(role["role_id"])
        self.session = PicoocSession(device_id=self.device_id,
                                     user_id=str(resp["user_id"]), role_ids=role_ids)
        return self.session

    # ----------------------------------------------------------- измерения
    async def fetch_measurements(self, role_name: str = "", page_size: int = 1000,
                                 max_pages: int = 50) -> list[dict]:
        session = self.session or await self.login()
        timestamp = str(int(time.time()))
        params = base_params(session.device_id, "bodyIndexList", timestamp)
        params.update({
            "pageSize": str(page_size),
            "time": timestamp,
            "userId": session.user_id,
            "roleId": session.role(role_name),
        })

        out: list[dict] = []
        for _ in range(max_pages):
            body = await self._request("GET", "bodyIndex/bodyIndexList", params=params)
            resp = body.get("resp") or {}
            for rec in resp.get("records") or []:
                if rec.get("abnormal_flag") or rec.get("is_del"):
                    continue          # чужие замеры и удалённые не берём
                out.append(map_record(rec))
            if not resp.get("continue"):
                return out
            params["time"] = str(resp.get("lastTime") or 0)
        raise PicoocError("picooc: достигнут лимит страниц, данные могут быть неполными")

    # ------------------------------------------------------------- helpers
    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self._client.request(method, self.base_url + path, **kwargs)
        except httpx.HTTPError as e:
            raise PicoocError(f"picooc: сеть недоступна ({type(e).__name__})") from e
        if response.status_code != 200:
            raise PicoocError(f"picooc: HTTP {response.status_code} на {path}")
        try:
            body = response.json()
        except ValueError as e:
            raise PicoocError(f"picooc: не JSON в ответе на {path}") from e
        if body.get("code") != 0:
            raise PicoocError(f"picooc: {path} → code={body.get('code')} "
                              f"msg={body.get('msg')!r}")
        return body
