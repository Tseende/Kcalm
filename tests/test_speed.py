"""Оптимизации скорости: ужатие фото для vision, общий LLM-клиент, повторы."""
from __future__ import annotations

import base64
import io

import httpx
import pytest
from PIL import Image

from app.llm import client as llm
from app.services import food as foodsvc


_real_chat_json = llm.chat_json     # conftest подменяет chat_json на заглушку, нам нужен настоящий


def _jpeg_b64(size: tuple[int, int]) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _size(b64: str) -> tuple[int, int]:
    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        return im.size


def test_shrink_big_photo():
    big = _jpeg_b64((3200, 2400))
    small = foodsvc.shrink_b64(big)
    assert max(_size(small)) == 1600
    assert _size(small) == (1600, 1200)
    assert len(small) < len(big)


def test_shrink_keeps_small_photo_and_garbage():
    ok = _jpeg_b64((800, 600))
    assert foodsvc.shrink_b64(ok) == ok
    assert foodsvc.shrink_b64("не картинка") == "не картинка"


@pytest.fixture
def fast_llm(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)


def _use_transport(monkeypatch, handler):
    client = None

    def get():
        nonlocal client
        if client is None:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(llm, "_get_client", get)


async def _call():
    return await _real_chat_json("t", [{"role": "user", "content": "x"}])


_OK = {"choices": [{"message": {"content": '{"a": 1}'}}], "model": "m"}


@pytest.mark.asyncio
async def test_llm_retries_5xx_and_429(monkeypatch, fast_llm):
    for code in (503, 429):
        calls = []

        def handler(req, code=code):
            calls.append(1)
            return httpx.Response(code) if len(calls) == 1 else httpx.Response(200, json=_OK)
        _use_transport(monkeypatch, handler)
        data, _ = await _call()
        assert data == {"a": 1} and len(calls) == 2


@pytest.mark.asyncio
async def test_llm_does_not_retry_4xx(monkeypatch, fast_llm):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401)
    _use_transport(monkeypatch, handler)
    with pytest.raises(llm.LLMError):
        await _call()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_shared_client_recreated_when_closed():
    a = llm._get_client()
    assert llm._get_client() is a
    await llm.close_client()
    assert a.is_closed
    assert llm._get_client() is not a
    await llm.close_client()


@pytest.mark.asyncio
async def test_album_debounce_waits_for_quiet_and_respects_cap(monkeypatch):
    import time
    from app.routers import intake

    async def run(cap: float, keep_adding: bool) -> float:
        group = (1, "g")
        album = intake._Album(msg=None, user=None, db=None)
        intake._ALBUMS[group] = album
        monkeypatch.setattr(intake, "ALBUM_WAIT_SEC", cap)
        monkeypatch.setattr(intake, "ALBUM_QUIET_SEC", 0.1)

        async def feeder():
            for _ in range(10):                  # «новые фото» каждые 50 мс
                await asyncio.sleep(0.05)
                album.last_seen = time.monotonic()
        import asyncio
        task = asyncio.ensure_future(feeder()) if keep_adding else None
        t0 = time.monotonic()
        await intake._flush_album(group)
        took = time.monotonic() - t0
        if task:
            task.cancel()
        return took

    assert await run(1.0, keep_adding=False) < 0.4       # тишина → закрылись быстро
    assert 0.35 < await run(0.4, keep_adding=True) < 0.8  # фото идут → ждём до потолка, не дольше
    assert not intake._ALBUMS
