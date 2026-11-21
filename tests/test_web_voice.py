"""Диктовка в Mini App: POST /api/transcribe, POST /api/products/parse, запасная ASR-модель.
Сети нет: транскрипция подменена."""
from __future__ import annotations

from io import BytesIO

import aiohttp
import pytest

from app.llm import client as llm
from app.web import server
from tests.test_web_api import H, add_user, make_config  # noqa: F401
from tests.test_web_api import _fixed_now, cfg, client, db, make_client, user_id  # noqa: F401

WEBM = b"\x1a\x45\xdf\xa3" + b"x" * 64
MP4 = b"\x00\x00\x00\x18ftypM4A " + b"x" * 64


def audio_form(data: bytes | None, content_type: str = "audio/webm;codecs=opus"):
    form = aiohttp.FormData(default_to_multipart=True)
    if data is not None:
        form.add_field("audio", BytesIO(data), filename="voice.bin", content_type=content_type)
    else:
        form.add_field("other", "x")
    return form


# ------------------------------------------------------------------ /api/transcribe

async def test_transcribe_ok(client, monkeypatch):
    seen = {}

    async def fake(data, filename, mime, **kw):
        seen.update(data=data, filename=filename, mime=mime, **kw)
        return "  овсянка и банан  "
    monkeypatch.setattr(llm, "transcribe_audio", fake)
    resp = await client.post("/api/transcribe", data=audio_form(WEBM), headers=H)
    assert resp.status == 200 and await resp.json() == {"text": "овсянка и банан"}
    assert seen["filename"] == "voice.webm" and seen["mime"] == "audio/webm"
    assert seen["purpose"] == "asr_web" and seen["model"] == "openai/gpt-4o-mini-transcribe"
    assert seen["data"] == WEBM and seen["user_id"]


async def test_transcribe_ios_mp4_becomes_m4a(client, monkeypatch):
    seen = {}

    async def fake(data, filename, mime, **kw):
        seen.update(filename=filename, mime=mime)
        return "привет"
    monkeypatch.setattr(llm, "transcribe_audio", fake)
    resp = await client.post("/api/transcribe", data=audio_form(MP4, "audio/mp4"), headers=H)
    assert resp.status == 200
    assert seen == {"filename": "voice.m4a", "mime": "audio/mp4"}


async def test_transcribe_empty_text(client, monkeypatch):
    async def fake(*a, **kw):
        return ""
    monkeypatch.setattr(llm, "transcribe_audio", fake)
    resp = await client.post("/api/transcribe", data=audio_form(WEBM), headers=H)
    assert resp.status == 200 and await resp.json() == {"text": ""}


async def test_transcribe_rejects_empty_missing_and_unknown(client):
    for form in (audio_form(b""), audio_form(None)):
        resp = await client.post("/api/transcribe", data=form, headers=H)
        assert resp.status == 400
    resp = await client.post("/api/transcribe", data=audio_form(b"not audio at all", "text/plain"),
                             headers=H)
    assert resp.status == 400 and (await resp.json())["error"] == "bad_audio"


async def test_transcribe_too_large(client):
    big = WEBM + b"\0" * (server.MAX_AUDIO_BYTES + 1)
    resp = await client.post("/api/transcribe", data=audio_form(big), headers=H)
    assert resp.status == 413 and (await resp.json())["error"] == "too_large"


async def test_transcribe_limit_and_failure(client, monkeypatch):
    async def limit(*a, **kw):
        raise llm.LLMLimitError("лимит")
    monkeypatch.setattr(llm, "transcribe_audio", limit)
    resp = await client.post("/api/transcribe", data=audio_form(WEBM), headers=H)
    assert resp.status == 429 and (await resp.json())["error"] == "llm_limit"

    async def broken(*a, **kw):
        raise llm.LLMError("сбой")
    monkeypatch.setattr(llm, "transcribe_audio", broken)
    resp = await client.post("/api/transcribe", data=audio_form(WEBM), headers=H)
    assert resp.status == 502 and (await resp.json())["error"] == "llm_unavailable"


async def test_transcribe_needs_auth(client):
    resp = await client.post("/api/transcribe", data=audio_form(WEBM))
    assert resp.status == 401


# ------------------------------------------------------------------ /api/products/parse

async def test_products_parse(client):
    resp = await client.post("/api/products/parse", headers=H, json={
        "text": "творог 5% Савушкин, 121 ккал, белки 17, жиры 5, углеводы 1.8"})
    body = await resp.json()
    assert resp.status == 200 and body["per"] == "100g"
    assert (body["kcal"], body["protein"], body["fat"], body["carbs"]) == (121, 17, 5, 1.8)
    assert "творог" in body["name"].lower()


async def test_products_parse_no_numbers_and_bad_input(client):
    resp = await client.post("/api/products/parse", headers=H, json={"text": "какой-то йогурт"})
    body = await resp.json()
    assert resp.status == 200 and body["kcal"] is None and body["name"] is None
    assert (await client.post("/api/products/parse", headers=H, json={"text": " "})).status == 400


# ------------------------------------------------------------------ клиент: запасная модель

async def test_transcribe_audio_falls_back(monkeypatch):
    calls = []

    async def once(cfg, data, filename, mime, model, *a):
        calls.append((model, filename, mime))
        if model == "openai/gpt-4o-mini-transcribe":
            raise llm.LLMError("упала")
        return "из запасной"
    monkeypatch.setattr(llm, "_transcribe_once", once)
    text = await llm.transcribe_audio(b"x", "voice.webm", "audio/webm",
                                      model="openai/gpt-4o-mini-transcribe")
    assert text == "из запасной"
    assert [c[0] for c in calls] == ["openai/gpt-4o-mini-transcribe", "openai/whisper-1"]


async def test_transcribe_audio_no_fallback_when_same_or_limit(monkeypatch):
    calls = []

    async def once(cfg, data, filename, mime, model, *a):
        calls.append(model)
        raise llm.LLMError("упала")
    monkeypatch.setattr(llm, "_transcribe_once", once)
    with pytest.raises(llm.LLMError):
        await llm.transcribe_audio(b"x", "v.webm", "audio/webm", model="openai/whisper-1")
    assert calls == ["openai/whisper-1"]          # совпала с запасной: второго запроса нет

    calls.clear()

    async def limited(cfg, *a):
        calls.append(1)
        raise llm.LLMLimitError("лимит")
    monkeypatch.setattr(llm, "_transcribe_once", limited)
    with pytest.raises(llm.LLMLimitError):
        await llm.transcribe_audio(b"x", "v.webm", "audio/webm", model="m")
    assert calls == [1]


async def test_transcribe_ogg_wrapper(monkeypatch):
    seen = {}

    async def once(cfg, data, filename, mime, model, language_hint, user_id, db, purpose):
        seen.update(data=data, filename=filename, mime=mime, model=model, purpose=purpose)
        return "голос"
    monkeypatch.setattr(llm, "_transcribe_once", once)
    assert await llm.transcribe_ogg(b"ogg", model="m1") == "голос"
    assert seen == {"data": b"ogg", "filename": "voice.ogg", "mime": "audio/ogg",
                    "model": "m1", "purpose": "asr"}
