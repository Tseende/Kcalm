"""Темы прогоняются через настоящий Dispatcher и middleware запросов, без Telegram."""
from __future__ import annotations

import asyncio

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ForumTopic, Message, Update, Voice

from app import config, rich, topics
from app.bot import create_dispatcher
from app.chat_context import pending_thread
from app.db import migrate
from app.services import assistant, food as foodsvc
from app.storage import repo
from app.topics import TopicRequestMiddleware
from tests.test_routers_smoke import FakeSession, db, message, onboard  # noqa: F401


class Session(FakeSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.created = []
        self.fail_settings = False
        self.fail_support = False
        self.stale = None
        self.closed = None

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        api = method.__api_method__
        if api == "setMySettings" and self.fail_settings:
            raise TelegramBadRequest(method, "unknown method")
        if api == "createForumTopic":
            if self.fail_support and method.name == topics.NAMES['support']:
                raise TelegramBadRequest(method, "unavailable")
            self.created.append(method)
            return ForumTopic(message_thread_id=100 + len(self.created), name=method.name,
                              icon_color=7322096)
        if api == "sendMessage":
            if self.stale is not None and method.message_thread_id == self.stale:
                self.stale = None
                raise TelegramBadRequest(method, "Bad Request: message thread not found")
            if self.closed is not None and method.message_thread_id == self.closed:
                self.closed = None
                raise TelegramBadRequest(method, "Bad Request: TOPIC_CLOSED")
        result = await super().make_request(bot, method, timeout)
        if isinstance(result, Message):
            result = result.model_copy(update={
                'chat': message(uid=getattr(method, 'chat_id', 1) or 1).chat,
                'message_thread_id': getattr(method, 'message_thread_id', None),
                'text': getattr(method, 'text', 'ok'),
            }).as_(bot)
        return result

    def sent(self):
        return [m for m in self.requests if m.__api_method__ in ('sendMessage', 'sendRichMessage', 'sendWidget')]


async def build(db, monkeypatch, *, failure=False):
    monkeypatch.setattr(config, '_config', config.Config(
        _env_file=None, chat_topics_enabled=True, chat_logs_food=False, allow_public_bot=True))
    session = Session()
    session.fail_settings = failure
    bot = Bot('42:TEST-TOKEN', session=session)
    dp = create_dispatcher(db)
    service = dp['topics']
    bot.session.middleware(TopicRequestMiddleware(service))
    await service.setup(bot)
    await onboard(dp, bot)
    session.requests.clear()
    return bot, dp, service, session


async def feed(dp, bot, text, thread, **extra):
    msg = message(text, message_thread_id=thread, is_topic_message=thread is not None, **extra)
    await dp.feed_update(bot, Update(update_id=msg.message_id, message=msg))


async def test_creates_two_topics_once_and_persists(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    assert [m.name for m in session.created] == ['📒 Дневник', '💬 Поддержка']
    stored = service.stored(1)
    assert set(stored) == {'diary', 'support'}
    await asyncio.gather(*(service.ensure(bot, 1) for _ in range(5)))
    restarted = topics.Topics(db)
    await restarted.setup(bot)
    assert await restarted.ensure(bot, 1) == stored
    assert len(session.created) == 2
    assert migrate(db) == 39


async def test_diary_logs_even_with_legacy_logging_disabled(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    diary = service.stored(1)['diary']
    await feed(dp, bot, 'выпил 250 мл воды', diary)
    assert db.execute('SELECT SUM(ml) FROM water_log').fetchone()[0] == 250
    assert session.sent()[-1].message_thread_id == diary
    assert topics.current.get() is None and pending_thread.get() is None


async def test_support_talks_and_explicit_log_confirms_in_same_topic(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    support = service.stored(1)['support']
    await feed(dp, bot, 'я съел тост и расстроился', support)
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM craving_events').fetchone()[0] == 1
    await feed(dp, bot, 'запиши 250 мл воды', support)
    assert db.execute('SELECT SUM(ml) FROM water_log').fetchone()[0] == 250
    assert all(m.message_thread_id == support for m in session.sent())
    # Открытый эпизод поддержки не перехватывает дневник.
    await feed(dp, bot, 'выпил 250 мл воды', service.stored(1)['diary'])
    assert db.execute('SELECT SUM(ml) FROM water_log').fetchone()[0] == 500


async def test_model_cannot_auto_log_food_mentioned_in_support(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from app.services import craving
    async def turn(*args, **kwargs):
        return {'reply': 'Я рядом. Как ты сейчас?', 'food_eaten': 'банан 100 г',
                'summary': '', 'risk': False, 'outcome': 'none'}
    async def forbidden(*args, **kwargs):
        raise AssertionError('Модель не должна записывать упомянутую еду')
    monkeypatch.setattr(craving, '_ask_model', turn)
    monkeypatch.setattr(craving, 'log_food_from_dialog', forbidden)
    await feed(dp, bot, 'я съел банан и расстроился', service.stored(1)['support'])
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 0
    assert 'Как ты сейчас' in session.sent()[-1].text


async def test_talk_command_in_diary_answers_in_support(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    await feed(dp, bot, '/talk', service.stored(1)['diary'])
    assert session.sent()[-1].message_thread_id == service.stored(1)['support']


async def test_craving_in_diary_redirects_support(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    await feed(dp, bot, 'не могу остановиться, переел', service.stored(1)['diary'])
    assert session.sent()[-1].message_thread_id == service.stored(1)['support']


async def test_voice_explicit_log_from_support(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from app.llm import client
    async def transcribe(*args, **kwargs):
        return 'запиши 250 мл воды'
    monkeypatch.setattr(client, 'transcribe_ogg', transcribe)
    await feed(dp, bot, None, service.stored(1)['support'],
               voice=Voice(file_id='f', file_unique_id='u', duration=2))
    assert db.execute('SELECT SUM(ml) FROM water_log').fetchone()[0] == 250


async def test_pending_and_photo_caption_scoped_to_topic(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    uid = repo.get_user_by_tg(db, 1)['id']
    a, b = service.stored(1)['diary'], service.stored(1)['support']
    token = pending_thread.set(a)
    try:
        repo.save_pending_input(db, uid, 'meal_refine', {'meal_id': 3})
        repo.put_photo_session(db, uid, {'note': 'diary'})
        assistant._remember(uid, 'вопрос из дневника', 'ответ')
        pending_thread.set(b)
        assert repo.latest_pending_input(db, uid, 'meal_refine') is None
        assert repo.fresh_photo_session(db, uid) is None
        assert assistant._recent(uid) == []
        repo.clear_pending_inputs(db, uid, 'meal_refine')
        pending_thread.set(a)
        assert repo.latest_pending_input(db, uid, 'meal_refine')
        assert repo.fresh_photo_session(db, uid)
        assert assistant._recent(uid)[-1]['text'] == 'ответ'
    finally:
        pending_thread.reset(token)


async def test_background_messages_and_widgets_use_destination(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    stored = service.stored(1)
    from app.widgets.api import SendWidget
    await bot.send_message(1, 'напоминание')
    assert session.sent()[-1].message_thread_id == stored['diary']
    @topics.destination('support')
    async def pause():
        await bot(SendWidget(chat_id=1, html='<p>Пауза</p>'))
    await pause()
    assert session.sent()[-1].message_thread_id == stored['support']
    # Карточка дня должна остаться в Дневнике даже из обработчика Поддержки.
    @topics.destination('support')
    async def support():
        @topics.destination('diary')
        async def day():
            await bot(SendWidget(chat_id=1, html='<p>День</p>'))
        await day()
    await support()
    assert session.sent()[-1].message_thread_id == stored['diary']


async def test_rich_conversion_keeps_topic(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    bot.session.middleware(rich.RichMiddleware())
    await bot.send_message(1, 'Напоминание')
    assert session.sent()[-1].__api_method__ == 'sendRichMessage'
    assert session.sent()[-1].message_thread_id == service.stored(1)['diary']


async def test_missing_topic_is_recreated_closed_topic_reopened(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    old = service.stored(1)['diary']
    session.stale = old
    await bot.send_message(1, 'вода')
    assert service.stored(1)['diary'] != old
    assert len(session.created) == 3
    current = service.stored(1)['diary']
    session.closed = current
    await bot.send_message(1, 'вода')
    assert any(m.__api_method__ == 'reopenForumTopic' and m.message_thread_id == current
               for m in session.requests)
    assert len(session.created) == 3


async def test_unavailable_api_falls_back_without_topics(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch, failure=True)
    assert not service.ready and not session.created
    await feed(dp, bot, '/talk', None)
    assert session.sent()[-1].message_thread_id is None


async def test_group_and_edits_untouched(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    await bot.send_message(-123, 'группа', message_thread_id=9)
    assert session.sent()[-1].message_thread_id == 9
    await bot.edit_message_text('исправлено', chat_id=1, message_id=5)
    assert session.requests[-1].__api_method__ == 'editMessageText'
    assert len(session.created) == 2


async def test_partial_creation_retries_without_duplicate_diary(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    db.execute("DELETE FROM chat_topics WHERE kind='support'")
    db.commit()
    session.fail_support = True
    old = service.stored(1)['diary']
    await service.ensure(bot, 1)
    await service.ensure(bot, 1)
    assert service.stored(1) == {'diary': old}
    assert len(session.created) == 2
    session.fail_support = False
    service._retry_at.clear()
    await service.ensure(bot, 1)
    assert service.stored(1)['diary'] == old
    assert len(session.created) == 3


async def test_food_and_photo_from_support_are_recorded(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from tests.test_photo_flow import _food
    from aiogram.types import PhotoSize
    seen = []
    async def parse_text(text, *args, **kwargs):
        seen.append(text)
        return _food('банан', 100)
    async def parse_photo(*args, **kwargs):
        return _food('банан', 100)
    monkeypatch.setattr(foodsvc, 'parse_text', parse_text)
    monkeypatch.setattr(foodsvc, 'parse_photo', parse_photo)
    support = service.stored(1)['support']
    await feed(dp, bot, 'запиши банан 100 г', support)
    assert seen == ['банан 100 г']
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 1
    # Подпись избегает параллельного распознавания этикетки; формат фото — не скриншот.
    await feed(dp, bot, None, support, caption='банан',
               photo=[PhotoSize(file_id='banana', file_unique_id='banana', width=3000, height=4000)])
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 2
    assert session.sent() and all(m.message_thread_id == support for m in session.sent())


async def test_report_question_state_does_not_consume_support(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from app.routers.reports import Ask
    diary = service.stored(1)['diary']
    state = dp.fsm.get_context(bot=bot, chat_id=1, user_id=1, thread_id=diary)
    await state.set_state(Ask.question)
    import time
    await state.update_data(kind='today', until=time.time() + 60)
    await feed(dp, bot, 'я расстроился из-за еды', service.stored(1)['support'])
    assert await state.get_state() == Ask.question.state
    await feed(dp, bot, 'как прошёл день?', diary)
    assert await state.get_state() is None


async def test_settings_keep_callback_message_and_disable_extra_topics(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    await service.setup(bot)
    setting = next(m for m in session.requests if m.__api_method__ == 'setMySettings')
    assert setting.callback_query_mode == 'default'
    assert setting.has_topic_sidebar and setting.has_topics_enabled
    assert setting.allows_users_to_create_topics is False
