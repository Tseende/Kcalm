"""Open Food Facts: продукт по штрихкоду и осторожный поиск по бренду.

База краудсорсная: полей может не быть, значения бывают ошибочными. Поэтому берём продукт
только с полным набором ккал/Б/Ж/У, проверяем границы, а ошибки сети и поиска (OFF часто
отвечает 503) никогда не роняют бота. Все такие функции возвращают None, а не исключение.
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://world.openfoodfacts.org"
USER_AGENT = "SlimBot/1.0 (personal nutrition diary; Telegram bot)"
TIMEOUT = 8.0
FIELDS = "code,product_name,product_name_ru,generic_name,brands,nutriments,serving_quantity"
MAX_KCAL_100G = 900


def valid_barcode(code: str) -> bool:
    """EAN-8/UPC-A/EAN-13/GTIN-14 с проверкой контрольной цифры GS1."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def _num(value, high: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= high else None


def parse_product(product: dict) -> dict | None:
    """Поля продукта OFF → наш формат. None, если данных недостаточно или они подозрительны."""
    nutr = product.get("nutriments") or {}
    kcal = _num(nutr.get("energy-kcal_100g"), MAX_KCAL_100G)
    if kcal is None:
        kj = _num(nutr.get("energy_100g"), MAX_KCAL_100G * 4.184)
        kcal = None if kj is None else round(kj / 4.184, 1)
    protein = _num(nutr.get("proteins_100g"), 100)
    fat = _num(nutr.get("fat_100g"), 100)
    carbs = _num(nutr.get("carbohydrates_100g"), 100)
    if None in (kcal, protein, fat, carbs) or protein + fat + carbs > 105:
        return None
    name = (product.get("product_name_ru") or product.get("product_name")
            or product.get("generic_name") or "").strip()
    if not name:
        return None
    brand = (product.get("brands") or "").split(",")[0].strip()
    serving = _num(product.get("serving_quantity"), 2000)
    return {
        "code": str(product.get("code") or ""),
        "name": name, "brand": brand,
        "kcal_100g": kcal, "protein_100g": protein, "fat_100g": fat, "carbs_100g": carbs,
        "serving_g": serving or None,
    }


_shared: httpx.AsyncClient | None = None
_shared_loop: asyncio.AbstractEventLoop | None = None


def _client(transport) -> tuple[httpx.AsyncClient, bool]:
    """(клиент, временный ли он). С явным transport (тесты) — одноразовый,
    иначе общий: создаётся лениво в работающем loop и пересоздаётся при смене loop."""
    global _shared, _shared_loop
    if transport is not None:
        return (httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                                  transport=transport), True)
    loop = asyncio.get_running_loop()
    if _shared is None or _shared.is_closed or _shared_loop is not loop:
        _shared = httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        _shared_loop = loop
    return _shared, False


async def close_client() -> None:
    """Закрыть общий клиент (при остановке бота)."""
    global _shared, _shared_loop
    client, _shared, _shared_loop = _shared, None, None
    if client is not None and not client.is_closed:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            log.debug("закрытие OFF-клиента не удалось", exc_info=True)


async def fetch_barcode(code: str, transport: httpx.AsyncBaseTransport | None = None) -> dict | None:
    """Продукт по штрихкоду или None (не найден, неполные данные, сеть недоступна)."""
    if not valid_barcode(code):
        return None
    client, temporary = _client(transport)
    try:
        resp = await client.get(f"{BASE_URL}/api/v2/product/{code}.json",
                                params={"fields": FIELDS})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — сеть и формат ответа не должны ронять бота
        log.warning("OFF barcode %s: %s: %s", code, type(e).__name__, e)
        return None
    finally:
        if temporary:
            await client.aclose()
    if data.get("status") != 1 or not isinstance(data.get("product"), dict):
        return None
    product = dict(data["product"])
    product.setdefault("code", code)
    return parse_product(product)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е")) if len(t) > 2}


async def search_branded(name: str, brand: str,
                         transport: httpx.AsyncBaseTransport | None = None) -> dict | None:
    """Продукт известного бренда по названию. Принимаем результат только если бренд совпал
    и название пересекается с запросом хотя бы наполовину. Иначе None (пусть решает LLM-оценка)."""
    if not name or not brand:
        return None
    client, temporary = _client(transport)
    try:
        resp = await client.get(f"{BASE_URL}/cgi/search.pl", params={
            "search_terms": f"{brand} {name}", "search_simple": 1, "action": "process",
            "json": 1, "page_size": 8, "fields": FIELDS})
        resp.raise_for_status()
        products = resp.json().get("products") or []
    except Exception as e:  # noqa: BLE001
        log.warning("OFF search: %s", type(e).__name__)   # без названия и текста ошибки: в них бывает URL с запросом
        return None
    finally:
        if temporary:
            await client.aclose()
    want = _tokens(name)
    brand_l = brand.lower().replace("ё", "е")
    for raw in products:
        parsed = parse_product(raw)
        if not parsed:
            continue
        if brand_l not in (raw.get("brands") or "").lower().replace("ё", "е"):
            continue
        have = _tokens(parsed["name"])
        if want and len(want & have) / len(want) >= 0.5:
            return parsed
    return None
