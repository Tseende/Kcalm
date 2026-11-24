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
        self.welcome_photos = []
        self.reject_welcome = False

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        api = method.__api_method__
        if api == 'sendPhoto' and getattr(method.photo, 'filename', '') in ('diary.png', 'support.png'):
            self.welcome_photos.append(method)
            if self.reject_welcome:
                raise TelegramBadRequest(method, 'PHOTO_INVALID')
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
        return [m for m in self.requests if m.__api_method__ in ('sendMessage', 'sendRichMessage', 'sendWidget', 'sendPhoto')]


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
    assert [m.name for m in session.created] == ['📒 Дневник', '💬 Поговорить']
    stored = service.stored(1)
    assert set(stored) == {'diary', 'support'}
    await asyncio.gather(*(service.ensure(bot, 1) for _ in range(5)))
    restarted = topics.Topics(db)
    await restarted.setup(bot)
    assert await restarted.ensure(bot, 1) == stored
    assert len(session.created) == 2
    assert migrate(db) == 41


async def test_diary_text_is_discussion_in_support_without_logging(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    diary = service.stored(1)['diary']
    await feed(dp, bot, 'выпил 250 мл воды', diary)
    assert db.execute('SELECT COUNT(*) FROM water_log').fetchone()[0] == 0
    assert session.sent()[-1].message_thread_id == service.stored(1)['support']
    assert topics.current.get() is None and pending_thread.get() is None


async def test_support_never_logs_even_explicit_requests(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    support = service.stored(1)['support']
    await feed(dp, bot, 'я съел тост и расстроился', support)
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM craving_events').fetchone()[0] == 1
    await feed(dp, bot, 'запиши 250 мл воды', support)
    assert db.execute('SELECT COUNT(*) FROM water_log').fetchone()[0] == 0
    assert all(m.message_thread_id == support for m in session.sent())
    # Реплики из Дневника также продолжают разговор, не создавая записи.
    await feed(dp, bot, 'выпил 250 мл воды', service.stored(1)['diary'])
    assert db.execute('SELECT COUNT(*) FROM water_log').fetchone()[0] == 0


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


async def test_voice_explicit_request_stays_discussion(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from app.llm import client
    async def transcribe(*args, **kwargs):
        return 'запиши 250 мл воды'
    monkeypatch.setattr(client, 'transcribe_ogg', transcribe)
    await feed(dp, bot, None, service.stored(1)['support'],
               voice=Voice(file_id='f', file_unique_id='u', duration=2))
    assert db.execute('SELECT COUNT(*) FROM water_log').fetchone()[0] == 0


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


async def test_food_photo_and_text_never_use_food_recognizer(db, monkeypatch):
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
    assert seen == []
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 0
    # Подпись избегает параллельного распознавания этикетки; формат фото — не скриншот.
    await feed(dp, bot, None, support, caption='этикетка банана',
               photo=[PhotoSize(file_id='banana', file_unique_id='banana', width=3000, height=4000)])
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 0
    assert session.sent()[-1].message_thread_id == service.stored(1)['diary']
    assert seen == []


async def test_report_question_state_does_not_consume_support(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from app.routers.reports import Ask
    diary = service.stored(1)['diary']
    support = service.stored(1)['support']
    state = dp.fsm.get_context(bot=bot, chat_id=1, user_id=1, thread_id=support)
    await state.set_state(Ask.question)
    import time
    await state.update_data(kind='today', until=time.time() + 60)
    await feed(dp, bot, 'как прошёл день?', diary)
    assert await state.get_state() is None


async def test_settings_keep_callback_message_and_disable_extra_topics(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    await service.setup(bot)
    setting = next(m for m in session.requests if m.__api_method__ == 'setMySettings')
    assert setting.callback_query_mode == 'default'
    assert setting.has_topic_sidebar and setting.has_topics_enabled
    assert setting.allows_users_to_create_topics is False


async def test_existing_support_topic_is_renamed_once_without_recreation(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    stored = service.stored(1)
    db.execute("UPDATE chat_topics SET name='💬 Поддержка' WHERE chat_id=1 AND kind='support'")
    db.commit()
    await service.ensure(bot, 1)
    await service.ensure(bot, 1)
    edits = [m for m in session.requests if m.__api_method__ == 'editForumTopic']
    assert len(edits) == 1
    assert edits[0].name == '💬 Поговорить' and edits[0].message_thread_id == stored['support']
    assert service.stored(1) == stored and len(session.created) == 2
    assert any('Хочется переесть' in (getattr(m, 'caption', '') or getattr(m, 'text', '') or '')
               for m in session.sent())


async def test_add_command_cannot_recognize_food_in_topics(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    async def forbidden(*args, **kwargs):
        raise AssertionError('Распознавание еды отключено')
    monkeypatch.setattr(foodsvc, 'parse_text', forbidden)
    # Даже старый флаг совместимости не включает распознавание в темах.
    monkeypatch.setattr(config.get_config(), 'chat_logs_food', True)
    await feed(dp, bot, '/add банан 100 г', service.stored(1)['diary'])
    assert db.execute('SELECT COUNT(*) FROM meals').fetchone()[0] == 0


async def test_report_button_opens_question_state_in_conversation_topic(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from aiogram.types import CallbackQuery, User
    from app.routers.reports import Ask
    stored = service.stored(1)
    cb = CallbackQuery(id='ask', from_user=User(id=1, is_bot=False, first_name='Тест'),
                       chat_instance='chat', data='rp:ask:today',
                       message=message('отчёт', message_thread_id=stored['diary']).as_(bot))
    await dp.feed_update(bot, Update(update_id=900001, callback_query=cb))
    state = dp.fsm.get_context(bot=bot, chat_id=1, user_id=1, thread_id=stored['support'])
    assert await state.get_state() == Ask.question.state
    assert session.sent()[-1].message_thread_id == stored['support']
    await feed(dp, bot, 'почему мало белка?', stored['diary'])
    assert await state.get_state() is None
    assert session.sent()[-1].message_thread_id == stored['support']


async def test_old_meal_refinement_button_does_not_arm_food_parser(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from aiogram.types import CallbackQuery, User
    cb = CallbackQuery(id='old-food', from_user=User(id=1, is_bot=False, first_name='Тест'),
                       chat_instance='chat', data='meal_refine:1',
                       message=message('старая еда', message_thread_id=service.stored(1)['diary']).as_(bot))
    await dp.feed_update(bot, Update(update_id=900002, callback_query=cb))
    uid = repo.get_user_by_tg(db, 1)['id']
    assert repo.latest_pending_input(db, uid, 'meal_refine') is None
    assert any(m.__api_method__ == 'answerCallbackQuery' and 'приложении' in (m.text or '')
               for m in session.requests)


@pytest.mark.parametrize('kind', ['evening', 'weekly'])
async def test_scheduled_reports_include_tables_in_diary(db, monkeypatch, kind):
    bot, dp, service, session = await build(db, monkeypatch)
    from app import scheduler
    from app.services import report
    from datetime import datetime
    from zoneinfo import ZoneInfo
    async def no_chart(*args, **kwargs):
        return None
    monkeypatch.setattr(report, 'day_chart_bytes', no_chart)
    monkeypatch.setattr(report, 'week_chart_bytes', no_chart)
    user = repo.get_user_by_tg(db, 1)
    await scheduler._send_reminder(db, bot, user, kind, datetime.now(ZoneInfo('Europe/Minsk')))
    sent = session.sent()[-1]
    assert sent.__api_method__ == 'sendRichMessage'
    assert '<table' in sent.rich_message.html
    assert sent.message_thread_id == service.stored(1)['diary']


async def test_report_command_from_conversation_posts_to_diary(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    from app.services import report
    async def no_chart(*args, **kwargs):
        return None
    monkeypatch.setattr(report, 'day_chart_bytes', no_chart)
    await feed(dp, bot, '/today', service.stored(1)['support'])
    assert session.sent()[-1].__api_method__ == 'sendRichMessage'
    assert session.sent()[-1].message_thread_id == service.stored(1)['diary']


async def test_welcome_images_sent_once_in_correct_topics_after_restart(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    stored = service.stored(1)
    assert len(session.welcome_photos) == 2
    assert {(m.photo.filename, m.message_thread_id) for m in session.welcome_photos} == {
        ('diary.png', stored['diary']), ('support.png', stored['support'])}
    assert all(m.caption == topics.INTRO[kind] and m.disable_notification
               for m in session.welcome_photos for kind in stored if m.message_thread_id == stored[kind])
    await asyncio.gather(*(service.ensure(bot, 1) for _ in range(5)))
    restarted = topics.Topics(db)
    await restarted.setup(bot)
    await restarted.ensure(bot, 1)
    assert len(session.welcome_photos) == 2
    assert not service._welcoming


async def test_existing_topics_receive_new_welcome_without_recreation(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    stored = service.stored(1)
    db.execute('UPDATE chat_topics SET welcome_version=0')
    db.commit()
    await service.ensure(bot, 1)
    assert len(session.welcome_photos) == 4
    assert len(session.created) == 2 and service.stored(1) == stored
    await service.ensure(bot, 1)
    assert len(session.welcome_photos) == 4


async def test_rejected_welcome_photo_falls_back_to_text_without_repeating(db, monkeypatch):
    bot, dp, service, session = await build(db, monkeypatch)
    db.execute('UPDATE chat_topics SET welcome_version=0')
    db.commit()
    session.reject_welcome = True
    await service.ensure(bot, 1)
    messages = [m for m in session.requests if m.__api_method__ == 'sendMessage']
    assert {m.text for m in messages} == set(topics.INTRO.values())
    assert all(m.message_thread_id in service.stored(1).values() for m in messages)
    assert db.execute('SELECT MIN(welcome_version) FROM chat_topics').fetchone()[0] == topics.WELCOME_VERSION
    session.requests.clear()
    await service.ensure(bot, 1)
    assert not session.requests


def test_welcome_image_assets_are_valid_and_captions_fit_telegram():
    from PIL import Image
    for kind in topics.NAMES:
        path = topics.WELCOME_IMAGES / f'{kind}.png'
        with Image.open(path) as img:
            assert img.size == (1536, 1024)
            img.verify()
        assert path.stat().st_size < 10 * 1024 * 1024
        assert len(topics.INTRO[kind]) <= 1024
