"""Клиент OpenRouter: JSON-режим, терпимый парсер, 1 retry, лог в llm_calls."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.config import get_config, mask_secret

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMLimitError(LLMError):
    """Дневной лимит обращений к модели у этого человека исчерпан (подкласс LLMError: места,
    где умеют жить без модели, по-прежнему уходят на запасной путь)."""


LIMIT_TEXT = ("На сегодня лимит распознаваний исчерпан, завтра всё снова заработает. "
              "Записать можно вручную в приложении.")
# Разговор о тяге — поддержка, а не «распознавание»: его лимит не режет (он и так ограничен rate-limit).
LIMIT_EXEMPT_PURPOSES = frozenset({"craving_dialog"})
# Бот выставляет сюда функцию «сообщить человеку про лимит»: llm-слой сам в Telegram не пишет.
limit_hook: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar(
    "llm_limit_hook", default=None)

MAX_CONCURRENT_REQUESTS = 6   # одновременных исходящих запросов к модели на процесс (память и сеть)
RETRY_PAUSE_S = 0.7           # пауза перед повтором
LENGTH_RETRY_FACTOR = 1.6     # ответ оборван по длине (finish_reason=length): один повтор с max_tokens × 1.6
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None
_sem: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None


def _get_sem() -> asyncio.Semaphore:
    """Семафор привязан к текущему loop (в тестах loop меняется)."""
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem, _sem_loop = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS), loop
    return _sem


def used_today(db: Any, user_id: int) -> int:
    """Сколько удачных обращений к модели записано у человека за сегодня (UTC-сутки, как в логе)."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = db.execute("SELECT COALESCE(SUM(requests), 0) FROM model_usage_daily "
                     "WHERE day=? AND user_id=?", (day, user_id)).fetchone()
    return int(row[0] or 0)


def check_limit(db: Any, user_id: int | None, purpose: str = "") -> None:
    """Бросает LLMLimitError, если DAILY_LLM_LIMIT на сегодня выбран. 0 = лимит выключен.
    Без user_id или БД (системные вызовы) не проверяет."""
    limit = get_config().daily_llm_limit
    if limit <= 0 or user_id is None or db is None or purpose in LIMIT_EXEMPT_PURPOSES:
        return
    try:
        used = used_today(db, user_id)
    except Exception:  # noqa: BLE001 — сбой счётчика не должен класть распознавание
        log.warning("не удалось прочитать дневной счётчик LLM", exc_info=True)
        return
    if used >= limit:
        log.info("LLM: user_id=%s выбрал дневной лимит %s", user_id, limit)
        hook = limit_hook.get()
        if hook is not None:
            try:
                hook()
            except Exception:  # noqa: BLE001
                log.debug("limit_hook упал", exc_info=True)
        raise LLMLimitError("дневной лимит обращений к модели исчерпан")


def _get_client() -> httpx.AsyncClient:
    """Общий клиент: keep-alive и TLS-сессия живут между вызовами.

    Создаётся лениво внутри работающего loop; если loop сменился (тесты) или клиент
    закрыт — делаем новый (старый от чужого loop закрыть уже нельзя, просто бросаем).
    """
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient()
        _client_loop = loop
    return _client


async def close_client() -> None:
    """Закрыть общий клиент (при остановке бота)."""
    global _client, _client_loop
    client, _client, _client_loop = _client, None, None
    if client is not None and not client.is_closed:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — закрытие не должно мешать остановке
            log.debug("закрытие LLM-клиента не удалось", exc_info=True)


def _timeout(cfg) -> httpx.Timeout:
    return httpx.Timeout(cfg.llm_timeout_s, connect=5.0)


def _merge_usage(first: dict, second: dict) -> dict:
    """Расход двух запросов (повтор при обрыве по длине) в одну запись лога: токены и стоимость суммируются."""
    if not first:
        return second
    merged = dict(second)
    for key in ("prompt_tokens", "completion_tokens", "cost"):
        a, b = first.get(key), second.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            merged[key] = a + b
    return merged


def _retryable(e: Exception) -> bool:
    """Повторяем только сеть/таймауты, 5xx и 429; прочие 4xx — нет смысла."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return code == 429 or code >= 500
    return isinstance(e, httpx.TransportError)


def extract_json(text: str) -> dict[str, Any]:
    """Терпимый парсер: ```json-обёртка, мусор до/после, обрезанный JSON."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise LLMError("в ответе нет JSON-объекта")
    # найти парную скобку с учётом строк
    depth, in_str, esc = 0, False, False
    end = None
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    candidate = text[start:end] if end else text[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # обрезанный JSON: закрываем незакрытые строки и скобки в обратном порядке
    repaired = candidate
    if repaired.count('"') % 2 == 1:
        repaired += '"'
    stack = []
    in_str, esc = False, False
    for ch in repaired:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    repaired = repaired.rstrip().rstrip(",")
    repaired += "".join("]" if c == "[" else "}" for c in reversed(stack))
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        raise LLMError(f"не удалось разобрать JSON: {e}") from e


async def chat_json(purpose: str, messages: list[dict], model: str | None = None,
                    temperature: float = 0.1, user_id: int | None = None,
                    response_json: bool = True,
                    db: Any = None, max_tokens: int | None = 1500) -> tuple[dict, str]:
    """Возвращает (parsed_json, фактически использованная модель).

    db — sqlite-соединение (опционально): каждый вызов пишется в llm_calls
    и сводится в model_usage_daily.
    """
    cfg = get_config()
    check_limit(db, user_id, purpose)
    model = model or cfg.model_text
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "usage": {"include": True},         # OpenRouter вернёт стоимость и токены из кеша
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    if response_json:
        body["response_format"] = {"type": "json_object"}
    ours = model in {cfg.model_text, cfg.model_vision, cfg.model_vision_escalation} \
        and model.startswith("deepseek/")        # DeepSeek читает и картинки: те же настройки
    if (model == cfg.model_text or ours) and not cfg.model_text_reasoning:
        body["reasoning"] = {"enabled": False}
    if cfg.model_text_provider and (model == cfg.model_text or ours):
        # провайдер закреплён, без тихого перехода к другому: данные уходят туда, куда решили
        body["provider"] = {"order": [cfg.model_text_provider], "allow_fallbacks": False}

    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    start = time.monotonic()
    ok, error, usage, last_content = False, None, {}, ""
    finish = None
    client = _get_client()

    async def _request(payload: dict) -> bool:
        """Один запрос с одним повтором на сетевую ошибку; заполняет usage, last_content, finish."""
        nonlocal usage, last_content, finish, model_used, error
        for attempt in (1, 2):                      # 1 retry
            try:
                async with _get_sem():
                    resp = await client.post(f"{cfg.openrouter_base_url}/chat/completions",
                                             headers=headers, json=payload, timeout=_timeout(cfg))
                resp.raise_for_status()
                data = resp.json()
                usage = _merge_usage(usage, data.get("usage") or {})
                choice = data["choices"][0]
                last_content = choice["message"]["content"] or ""
                finish = choice.get("finish_reason")
                model_used = data.get("model", model)
                return True
            except Exception as e:                  # noqa: BLE001 — честный лог любой ошибки
                error = f"{type(e).__name__}: {e}"
                log.warning("LLM %s попытка %s ошибка: %s", purpose, attempt, error)
                if attempt == 1 and _retryable(e):
                    await asyncio.sleep(RETRY_PAUSE_S)
                    continue
                return False
        return False

    model_used = model
    ok = await _request(body)
    if ok and finish == "length" and response_json and max_tokens:
        # ответ оборван по длине: JSON, подчинённый молча, потерял бы позиции. Повторяем один раз с запасом
        bigger = int(max_tokens * LENGTH_RETRY_FACTOR)
        log.warning("LLM %s: ответ оборван по длине (max_tokens=%s), повтор с %s",
                    purpose, max_tokens, bigger)
        first_ok = (last_content, finish)
        if not await _request({**body, "max_tokens": bigger}):
            last_content, finish = first_ok             # повтор не удался: остаётся оборванный первый ответ
            error = None
    if not ok:
        await _log_call(cfg, user_id, purpose, model, usage, start, ok, error, db)
        raise LLMError("LLM недоступен после повтора")
    try:
        parsed = extract_json(last_content)
    except LLMError as e:
        error = str(e)
        ok = False
        parsed = {}
    await _log_call(cfg, user_id, purpose, model_used, usage, start, ok, error, db)
    if not ok:
        raise LLMError(f"LLM вернул невалидный JSON: {error}")
    return parsed, model_used


async def transcribe_ogg(ogg_bytes: bytes, filename: str = "voice.ogg",
                         model: str = "google/gemini-2.5-flash",
                         language_hint: str = "ru", user_id: int | None = None,
                         db: Any = None) -> str:
    """ASR голосовых: OGG/Opus напрямую, без ffmpeg."""
    cfg = get_config()
    check_limit(db, user_id, "asr")
    headers = {"Authorization": f"Bearer {cfg.openrouter_api_key}"}
    start = time.monotonic()
    client = _get_client()
    for attempt in (1, 2):
        try:
            files = {"file": (filename, ogg_bytes, "audio/ogg")}
            data = {"model": model, "language": language_hint}
            async with _get_sem():
                resp = await client.post(f"{cfg.openrouter_base_url}/audio/transcriptions",
                                         headers=headers, files=files, data=data,
                                         timeout=_timeout(cfg))
            resp.raise_for_status()
            await _log_call(cfg, user_id, "asr", model, {}, start, True, None, db)
            return resp.json().get("text", "").strip()
        except Exception as e:                      # noqa: BLE001
            if attempt == 1 and _retryable(e):
                await asyncio.sleep(RETRY_PAUSE_S)
                continue
            await _log_call(cfg, user_id, "asr", model, {}, start, False,
                            f"{type(e).__name__}: {e}", db)
            raise LLMError("распознавание голоса не удалось") from e
    raise LLMError("unreachable")


def cached_tokens(usage: dict) -> int | None:
    """Токены запроса, обслуженные из кеша префикса (OpenRouter или родной формат DeepSeek)."""
    details = usage.get("prompt_tokens_details") or {}
    value = details.get("cached_tokens", usage.get("prompt_cache_hit_tokens"))
    return int(value) if isinstance(value, (int, float)) else None


async def _log_call(cfg, user_id, purpose, model, usage, start, ok, error,
                    db=None) -> None:
    latency = int((time.monotonic() - start) * 1000)
    cached = cached_tokens(usage)
    cost = usage.get("cost")
    log.info("llm_call purpose=%s model=%s ok=%s tokens=%s/%s cached=%s cost=%s latency=%sms err=%s",
             purpose, model, ok, usage.get("prompt_tokens"), usage.get("completion_tokens"),
             cached, cost, latency, (error or "")[:120])
    if db is None:
        return
    from app.storage.repo import log_llm_call  # локальный импорт, без цикла
    log_llm_call(db, user_id=user_id, purpose=purpose, model=model,
                 tokens_in=usage.get("prompt_tokens"),
                 tokens_out=usage.get("completion_tokens"),
                 latency_ms=latency, ok=ok, error=error, tokens_cached=cached,
                 cost_usd=cost if isinstance(cost, (int, float)) else None)
