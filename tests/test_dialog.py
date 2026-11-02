"""Живой диалог поддержки: начало от модели, запись еды из разговора, итог словами, память."""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from app.db import connect, migrate
from app.domain import craving as dom
from app.services import craving as svc
from app.storage import repo
from tests.test_craving import _user
from tests.test_routers_smoke import build, onboard, send

FOOD_LLM = {"items": [{"name_ru": "салат из пекинской капусты", "name_en": "napa cabbage salad",
                       "amount": {"kind": "portion", "grams_range": [200, 250]},
                       "preparation": "raw", "confidence": 0.7}],
            "meal_type_guess": "dinner", "overall_confidence": 0.7}


@pytest.fixture()
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


class FakeModel:
    """Отвечает за диалог по сценарию; разбор еды — фикстурой. Запоминает запросы."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.dialog_calls: list[list[dict]] = []

    async def __call__(self, purpose, messages, **kw):
        if purpose == "craving_dialog":
            self.dialog_calls.append(messages)
            return dict(self.replies.pop(0)), "m"
        if purpose == "food_text":
            return json.loads(json.dumps(FOOD_LLM)), "m"
        raise svc.llm.LLMError(purpose)

    def last_context(self) -> dict:
        return json.loads(self.dialog_calls[-1][-1]["content"])["context"]


def _reply(text, **kw):
    return {"reply": text, "risk": False, "food_eaten": "", "outcome": "none", "summary": "", **kw}


@pytest.mark.asyncio
async def test_open_episode_uses_model_and_short_keyboard(db, monkeypatch):
    uid, user = _user(db)
    fake = FakeModel(_reply("Я рядом. Что сейчас происходит, голод или что-то на душе?"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    event_id, text, kb = await svc.open_episode(db, user, "хочу есть")
    assert "Я рядом" in text and "📊" not in text           # без стены цифр
    assert kb is None                                        # никаких кнопок: просто разговор
    ctx = fake.last_context()
    assert ctx["stage"] == "opening" and ctx["sex"] == "male" and ctx["mode"] == "craving"
    msgs = repo.craving_messages(db, event_id)
    assert [m["role"] for m in msgs] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_open_episode_falls_back_offline(db):
    uid, user = _user(db)
    _, text, kb = await svc.open_episode(db, user, "хочу есть")
    assert "Я рядом" in text and kb is None


@pytest.mark.asyncio
async def test_open_episode_risk_never_calls_model(db, monkeypatch):
    uid, user = _user(db)
    fake = FakeModel()
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    _, text, kb = await svc.open_episode(db, user, "не хочу жить")
    assert kb is None and "112" in text and not fake.dialog_calls


@pytest.mark.asyncio
async def test_food_mentioned_in_dialog_is_logged(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    fake = FakeModel(_reply("Салат — отличная идея. Как ты сейчас?",
                            food_eaten="салат: пекинская капуста, помидор, огурец"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    reply, _ = await svc.dialog_reply(db, user, repo.get_craving(db, uid, event_id),
                                      "Приготовила салат из пекинской капусты, помидор, огурец")
    assert "в дневник" in reply and "ккал" in reply
    meals = db.execute("SELECT * FROM meals WHERE user_id=?", (uid,)).fetchall()
    assert len(meals) == 1
    assert any("[записано в дневник" in m["text"] for m in repo.craving_messages(db, event_id))


@pytest.mark.asyncio
async def test_outcome_in_words_closes_episode_and_saves_summary(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    fake = FakeModel(_reply("Здорово, что тяга прошла. Спокойной ночи 🌙", outcome="resisted",
                            summary="тянуло от усталости, помог чай и душ"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    await svc.dialog_reply(db, user, repo.get_craving(db, uid, event_id), "всё, отпустило, иду спать")
    event = repo.get_craving(db, uid, event_id)
    assert event["outcome"] == "resisted" and "чай" in event["summary"]

    # следующий разговор помнит, что помогло
    fake2 = FakeModel(_reply("Помнишь, в прошлый раз помог чай? Может, сейчас тоже?"))
    monkeypatch.setattr(svc.llm, "chat_json", fake2)
    await svc.open_episode(db, user, "опять тянет")
    assert any("чай" in m for m in fake2.last_context()["memory"])


@pytest.mark.asyncio
async def test_bad_outcome_and_summary_are_ignored(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    fake = FakeModel(_reply("Понимаю. Что сейчас чувствуешь?", outcome="maybe",
                            summary="слабоволие и срыв"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    await svc.dialog_reply(db, user, repo.get_craving(db, uid, event_id), "плохо")
    event = repo.get_craving(db, uid, event_id)
    assert event["outcome"] == "open" and not event["summary"]


@pytest.mark.asyncio
async def test_history_is_sent_and_current_message_not_duplicated(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    fake = FakeModel(_reply("Слышу тебя. Что было сегодня?"), _reply("Понимаю, тяжёлый день."))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    event = repo.get_craving(db, uid, event_id)
    await svc.dialog_reply(db, user, event, "устала")
    await svc.dialog_reply(db, user, event, "на работе завал")
    sent = fake.dialog_calls[-1]
    texts = [m["content"] for m in sent[1:-1]]
    assert "устала" in texts
    # свои реплики модель видит в формате ответа, чтобы не сбиваться с JSON
    assert json.dumps({"reply": "Слышу тебя. Что было сегодня?"}, ensure_ascii=False) in texts
    assert "на работе завал" not in texts                     # уходит один раз, с контекстом
    assert json.loads(sent[-1]["content"])["user_message"] == "на работе завал"


def test_diary_maybe_incomplete():
    base = dict(kcal_target=2000, protein_so_far=0, protein_target=100, meals_today=1,
                hours_since_last_meal=None, sleep_min=None, water_ml=0, water_goal_ml=2000,
                craving_count_7d=0, overate_count_14d=0)
    assert svc.diary_maybe_incomplete(dom.Facts(local_time="22:00", kcal_so_far=300, **base))
    assert not svc.diary_maybe_incomplete(dom.Facts(local_time="22:00", kcal_so_far=1500, **base))
    assert not svc.diary_maybe_incomplete(dom.Facts(local_time="10:00", kcal_so_far=300, **base))


@pytest.mark.asyncio
async def test_category_reply_adds_snacks_from_database(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    fake = FakeModel(_reply("Тогда давай спокойно поедим, без борьбы."))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    text = await svc.category_reply(db, user, repo.get_craving(db, uid, event_id), "hunger")
    assert "без борьбы" in text and "ккал" in text
    assert fake.last_context()["stage"] == "category"
    assert repo.get_craving(db, uid, event_id)["category"] == "hunger"


@pytest.mark.asyncio
async def test_category_reply_offline_uses_plan(db):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    text = await svc.category_reply(db, user, repo.get_craving(db, uid, event_id), "emotion")
    assert "вдох" in text


@pytest.mark.asyncio
async def test_followup_message_personal_and_offline(db, monkeypatch):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    event = repo.get_craving(db, uid, event_id)
    assert "Прошло" in await svc.followup_message(db, user, event)
    fake = FakeModel(_reply("Как ты, чай помог?"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    assert "чай помог" in await svc.followup_message(db, user, event)


def test_note_food_logged_only_inside_episode(db):
    uid, user = _user(db)
    assert svc.note_food_logged(db, user, "творог ≈ 180 ккал") is None
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    assert "Записал" in svc.note_food_logged(db, user, "творог ≈ 180 ккал")
    assert "[записано в дневник: творог" in repo.craving_messages(db, event_id)[-1]["text"]


def test_episode_window_follows_last_message(db):
    uid, user = _user(db)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    old = (datetime.now(timezone.utc) - timedelta(minutes=120)).strftime("%Y-%m-%d %H:%M:%S")
    repo.update_craving(db, event_id, started_utc=old)
    assert repo.open_craving(db, uid, 90)["id"] == event_id   # переписка свежая
    db.execute("UPDATE craving_messages SET created_utc=? WHERE event_id=?", (old, event_id))
    assert repo.open_craving(db, uid, 90) is None


def test_female_forms_in_buttons_and_texts(db):
    uid, user = _user(db)
    repo.update_user(db, uid, sex="female")
    user = repo.get_user_by_tg(db, 1)
    event_id, _, kb = svc.start_episode(db, user, "хочу есть")
    assert kb is None
    text = svc.finish_episode(db, user, repo.get_craving(db, uid, event_id), "ate_planned")
    assert "поела" in text and "(а)" not in text


@pytest.mark.asyncio
async def test_router_talk_mode_and_auto_close(db, monkeypatch):
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    fake = FakeModel(_reply("Я здесь. О чём хочется поговорить?"),
                     _reply("Рада, что стало легче. Спокойной ночи.", outcome="resisted"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    await send(dp, bot, "/talk")
    event = db.execute("SELECT * FROM craving_events WHERE user_id=?", (uid,)).fetchone()
    assert event["mode"] == "talk" and fake.last_context()["mode"] == "talk"
    await send(dp, bot, "мне уже лучше, пойду спать")
    assert repo.get_craving(db, uid, event["id"])["outcome"] == "resisted"


@pytest.mark.asyncio
async def test_router_text_food_log_inside_episode_tells_dialog(db, monkeypatch):
    """«Съела творог» во время разговора записывается обычным путём, а разговор об этом знает."""
    bot, dp, session = build(db)
    uid = await onboard(dp, bot)
    user = repo.get_user_by_tg(db, 1)
    event_id, _, _ = svc.start_episode(db, user, "хочу есть")
    monkeypatch.setattr(svc.llm, "chat_json", FakeModel())
    await send(dp, bot, "съел салат из пекинской капусты")
    assert db.execute("SELECT COUNT(*) FROM meals WHERE user_id=?", (uid,)).fetchone()[0] == 1
    assert "[записано в дневник" in repo.craving_messages(db, event_id)[-1]["text"]


@pytest.mark.asyncio
async def test_food_in_first_message_is_logged(db, monkeypatch):
    """«Объелся салатом и хлебом» первым сообщением: запись в дневник, а не только слова."""
    uid, user = _user(db)
    fake = FakeModel(_reply("Записал. Как ты сейчас?", food_eaten="салат; хлеб", outcome="overate"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    event_id, text, _ = await svc.open_episode(db, user, "я уже сильно объелся салатом и хлебом")
    assert "в дневник" in text
    assert db.execute("SELECT COUNT(*) FROM meals WHERE user_id=?", (uid,)).fetchone()[0] == 1
    event = repo.get_craving(db, uid, event_id)
    assert event["outcome"] == "open" and event["category"] == "overate"   # разговор продолжается


@pytest.mark.asyncio
async def test_dialog_without_buttons_sets_category_and_checkin(db, monkeypatch):
    """Кнопок нет: причину и срок проверки «как ты?» определяет модель, эпизод остаётся открытым."""
    uid, user = _user(db)
    fake = FakeModel(_reply("Я рядом. Что сейчас происходит?"),
                     _reply("Понимаю, день был тяжёлый.", category="emotion", check_in_min=20))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    event_id, _, kb = await svc.open_episode(db, user, "хочу есть")
    assert kb is None
    event = repo.get_craving(db, uid, event_id)
    assert event["followup_at_utc"] is not None           # проверка поставлена сама, без «подожду»
    await svc.dialog_reply(db, user, event, "устал, поругался с начальником")
    event = repo.get_craving(db, uid, event_id)
    assert event["category"] == "emotion" and event["outcome"] == "open"
    when = datetime.strptime(event["followup_at_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    left = (when - datetime.now(timezone.utc)).total_seconds() / 60
    assert 18 <= left <= 20.5                              # check_in_min=20 из ответа модели


@pytest.mark.asyncio
async def test_overate_closes_without_followup(db, monkeypatch):
    uid, user = _user(db)
    fake = FakeModel(_reply("Я рядом."), _reply("Бывает. Вода и сон.", outcome="overate"))
    monkeypatch.setattr(svc.llm, "chat_json", fake)
    event_id, _, _ = await svc.open_episode(db, user, "хочу есть")
    await svc.dialog_reply(db, user, repo.get_craving(db, uid, event_id), "переел пельменей")
    assert repo.get_craving(db, uid, event_id)["outcome"] == "overate"
