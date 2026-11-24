"""Поток OpenRouter → проверенный текст → Telegram draft → один постоянный ответ."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest

from app import streaming
from app.config import get_config
from app.llm import client as llm
from tests.test_topics import build, feed
from tests.test_routers_smoke import db  # noqa: F401

_real_chat_json = llm.chat_json


def event(content=None, *, usage=None, finish=None, reasoning=None):
    delta = {'content': content} if content is not None else {'reasoning': reasoning or ''}
    obj = {'model': 'm', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
    if usage:
        obj['usage'] = usage
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


class SSE(httpx.AsyncByteStream):
    def __init__(self, pieces):
        self.pieces = pieces

    async def __aiter__(self):
        for piece in self.pieces:
            if isinstance(piece, Exception):
                raise piece
            yield piece.encode()
            await asyncio.sleep(0)


@pytest.mark.parametrize('raw,expected', [
    ('{"reply":"Приве', 'Приве'),
    ('{"reply":"Скажи \\"да\\".\\nЕщё', 'Скажи "да".\nЕщё'),
    ('{"reply":"\\u041f\\u0440\\u0', 'Пр'),
    ('{"reply":"\\ud83d', ''),
    ('{"reply":"\\ud83d\\ude42', '🙂'),
    ('{"reply":"Я рядом.","risk":false}', 'Я рядом.'),
    ('{"reasoning":"секрет","summary":"память"', ''),
])
def test_partial_json_exposes_only_reply_and_decodes_complete_escapes(raw, expected):
    assert llm.reply_prefix(raw) == expected


async def test_sse_streams_reply_and_logs_final_usage_once(monkeypatch):
    seen, requests, logs = [], [], []
    async def sink(text):
        seen.append(text)
    async def log_call(*args):
        logs.append(args)
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SSE([
            ': OPENROUTER PROCESSING\n\n', event(reasoning='секретные рассуждения'),
            event('{"reply":"Я рядом.'), event(' Давай поговорим?","risk":false}'),
            event(finish='stop', usage={'prompt_tokens': 11, 'completion_tokens': 9, 'cost': 0.002}),
            'data: [DONE]\n\n']))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(llm, '_get_client', lambda: client)
        monkeypatch.setattr(llm, '_log_call', log_call)
        token = llm.reply_sink.set(sink)
        try:
            data, model = await _real_chat_json('craving_dialog', [], model='m')
        finally:
            llm.reply_sink.reset(token)
    assert seen[0] == '' and 'Я рядом.' in seen
    assert seen[-1] == data['reply'] == 'Я рядом. Давай поговорим?'
    assert not any('секрет' in s or 'risk' in s or '{' in s for s in seen)
    assert requests[0]['stream'] is True and requests[0]['response_format']['type'] == 'json_object'
    assert model == 'm' and len(logs) == 1
    assert logs[0][4]['completion_tokens'] == 9 and logs[0][4]['cost'] == 0.002


async def test_stream_guard_blocks_invalid_preview(monkeypatch):
    seen = []
    async def sink(text):
        seen.append(text)
    def handle(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SSE([
            event('{"reply":"Ешь 9000 ккал."}', finish='stop'), 'data: [DONE]\n\n']))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(llm, '_get_client', lambda: client)
        token = llm.reply_sink.set(sink)
        try:
            with llm.guard_stream(lambda text: '9000' not in text):
                await _real_chat_json('craving_dialog', [], model='m')
        finally:
            llm.reply_sink.reset(token)
    assert seen and all('9000' not in text for text in seen)


async def test_provider_without_streaming_uses_normal_completion(monkeypatch):
    requests = []
    async def sink(text):
        pass
    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get('stream'):
            return httpx.Response(400, json={'error': 'stream unsupported'})
        return httpx.Response(200, json={'model': 'm', 'choices': [{'message': {'content': '{"reply":"Я рядом."}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(llm, '_get_client', lambda: client)
        token = llm.reply_sink.set(sink)
        try:
            data, _ = await _real_chat_json('craving_dialog', [], model='m')
        finally:
            llm.reply_sink.reset(token)
    assert data['reply'] == 'Я рядом.' and len(requests) == 2
    assert 'stream' not in requests[1]


async def test_broken_stream_retries_from_empty_preview(monkeypatch):
    requests, seen = [], []
    async def sink(text):
        seen.append(text)
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            pieces = [event('{"reply":"Начало.'), httpx.ReadError('disconnect')]
        else:
            pieces = [event('{"reply":"Я рядом."}', finish='stop'), 'data: [DONE]\n\n']
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SSE(pieces))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(llm, '_get_client', lambda: client)
        monkeypatch.setattr(llm, 'RETRY_PAUSE_S', 0)
        token = llm.reply_sink.set(sink)
        try:
            data, _ = await _real_chat_json('craving_dialog', [], model='m')
        finally:
            llm.reply_sink.reset(token)
    assert len(requests) == 2 and seen.count('') == 2
    assert data['reply'] == 'Я рядом.'


async def fake_model(purpose, messages, **kwargs):
    sink = llm.reply_sink.get()
    if sink:
        await sink('')
        await sink('Я рядом.')
        await asyncio.sleep(0.01)
        await sink('Я рядом. Расскажи, как ты сейчас?')
        await asyncio.sleep(0.01)
    return {'reply': 'Я рядом. Расскажи, как ты сейчас?', 'risk': False, 'food_eaten': '',
            'outcome': 'none', 'summary': ''}, 'm'


async def stream_bot(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    bot.session.middleware(streaming.DraftRequestMiddleware())
    monkeypatch.setattr(llm, 'chat_json', fake_model)
    monkeypatch.setattr(streaming, 'UPDATE_INTERVAL', 0)
    return bot, dp, service, session


async def test_support_drafts_thinking_then_reply_in_correct_topic_and_persists_once(db, monkeypatch):
    bot, dp, service, session = await stream_bot(db, monkeypatch)
    await feed(dp, bot, 'мне хочется всё съесть', service.stored(1)['diary'])
    drafts = [m for m in session.requests if m.__api_method__ == 'sendMessageDraft']
    assert len(drafts) >= 2 and drafts[0].text == ''
    assert any('Я рядом.' in m.text for m in drafts)
    assert len({m.draft_id for m in drafts}) == 1 and drafts[0].draft_id != 0
    assert all(m.message_thread_id == service.stored(1)['support'] for m in drafts)
    final = [m for m in session.sent() if 'Расскажи, как ты сейчас?' in (getattr(m, 'text', '') or '')]
    assert len(final) == 1 and final[0].message_thread_id == service.stored(1)['support']
    assert llm.reply_sink.get() is None and streaming._current.get() is None


async def test_voice_status_replaced_by_persistent_answer_after_draft(db, monkeypatch):
    bot, dp, service, session = await stream_bot(db, monkeypatch)
    from aiogram.types import Voice
    async def transcribe(*args, **kwargs):
        return 'трудный день'
    monkeypatch.setattr(llm, 'transcribe_ogg', transcribe)
    await feed(dp, bot, None, service.stored(1)['support'], voice=Voice(file_id='f', file_unique_id='u', duration=2))
    assert any(m.__api_method__ == 'sendMessageDraft' for m in session.requests)
    assert any(m.__api_method__ == 'deleteMessage' for m in session.requests)
    assert session.sent()[-1].text == 'Я рядом. Расскажи, как ты сейчас?'


async def test_unsupported_telegram_drafts_delivers_regular_final_answer(db, monkeypatch):
    bot, dp, service, session = await stream_bot(db, monkeypatch)
    original = session.make_request
    async def reject(bot_, method, timeout=None):
        if method.__api_method__ == 'sendMessageDraft':
            raise TelegramBadRequest(method, 'method unsupported')
        return await original(bot_, method, timeout)
    monkeypatch.setattr(session, 'make_request', reject)
    await feed(dp, bot, 'трудный день', service.stored(1)['support'])
    assert session.sent()[-1].text == 'Я рядом. Расскажи, как ты сейчас?'


async def test_stream_flag_off_and_llm_failure_leave_final_answer(db, monkeypatch):
    bot, dp, service, session = await stream_bot(db, monkeypatch)
    monkeypatch.setattr(get_config(), 'chat_streaming', False)
    await feed(dp, bot, 'трудный день', service.stored(1)['support'])
    assert not any(m.__api_method__ == 'sendMessageDraft' for m in session.requests)
    monkeypatch.setattr(get_config(), 'chat_streaming', True)
    async def fail(*args, **kwargs):
        await llm.reply_sink.get()('')
        raise llm.LLMError('offline')
    monkeypatch.setattr(llm, 'chat_json', fail)
    await feed(dp, bot, 'пока трудно', service.stored(1)['support'])
    assert 'Я тебя слышу' in session.sent()[-1].text
    assert llm.reply_sink.get() is None


async def test_final_draft_message_is_not_converted_to_rich(db, monkeypatch):
    bot, dp, service, session = await stream_bot(db, monkeypatch)
    from app import rich
    bot.session.middleware(rich.RichMiddleware())
    await feed(dp, bot, 'трудный день', service.stored(1)['support'])
    assert any(m.__api_method__ == 'sendMessageDraft' for m in session.requests)
    assert session.sent()[-1].__api_method__ == 'sendMessage'
    assert session.sent()[-1].text == 'Я рядом. Расскажи, как ты сейчас?'


async def test_long_sentence_streams_before_punctuation_and_coalesces_bursts():
    """A sentence without punctuation must already be visible while generation continues."""
    from types import SimpleNamespace
    import time

    sent = asyncio.Queue()
    async def send_message_draft(**kwargs):
        sent.put_nowait((kwargs['text'], time.monotonic()))
    msg = SimpleNamespace(bot=SimpleNamespace(send_message_draft=send_message_draft),
                          chat=SimpleNamespace(id=1), message_thread_id=102)
    draft = streaming.Draft(msg)
    try:
        await draft('Давай вместе')
        thinking, started = await sent.get()
        assert thinking == ''
        first, first_at = await asyncio.wait_for(sent.get(), timeout=0.6)
        assert first == 'Давай вместе'
        assert first_at - started >= streaming.UPDATE_INTERVAL - 0.01
        # Many model deltas arrive during the next interval: deliver the freshest prefix.
        for text in ('Давай вместе раз', 'Давай вместе разберём', 'Давай вместе разберём это'):
            await draft(text)
        second, second_at = await asyncio.wait_for(sent.get(), timeout=0.6)
        assert second == 'Давай вместе разберём это'
        assert second_at - first_at >= streaming.UPDATE_INTERVAL - 0.01
        # No redundant draft follows the coalesced deltas.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sent.get(), timeout=streaming.UPDATE_INTERVAL * 1.5)
    finally:
        await draft.close()
    assert draft._worker.done()


async def test_telegram_throttle_resumes_with_freshest_prefix(monkeypatch):
    from types import SimpleNamespace
    import time
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessageDraft

    monkeypatch.setattr(streaming, 'UPDATE_INTERVAL', 0.01)
    calls, delivered = [], asyncio.Queue()
    async def send_message_draft(**kwargs):
        calls.append((kwargs['text'], time.monotonic()))
        if len(calls) == 2:
            raise TelegramRetryAfter(SendMessageDraft(**kwargs), 'slow down', retry_after=1)
        delivered.put_nowait(kwargs['text'])
    msg = SimpleNamespace(bot=SimpleNamespace(send_message_draft=send_message_draft),
                          chat=SimpleNamespace(id=1), message_thread_id=102)
    draft = streaming.Draft(msg)
    try:
        await draft('Давай')
        assert await delivered.get() == ''
        # Wait until the rejected update has happened, then queue a newer prefix.
        async with asyncio.timeout(0.5):
            while len(calls) < 2:
                await asyncio.sleep(0.005)
        await draft('Давай разберём вместе')
        assert await asyncio.wait_for(delivered.get(), timeout=1.5) == 'Давай разберём вместе'
        assert calls[2][1] - calls[1][1] >= 1
        assert draft.active and not draft.disabled
    finally:
        await draft.close()
