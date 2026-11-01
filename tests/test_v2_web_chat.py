"""Чат Mini App как чат бота: разговор о тяге с кнопками-действиями, память ассистента,
обсуждение приёма (meal_id), сон/шаги/тренировки и посуда фразами. Сети нет: LLM отключён
в conftest, ассистент и диалог подменяются."""
from __future__ import annotations

import json

import pytest

from app.llm import client as llm
from app.services import assistant
from app.services import craving as craving_svc
from app.storage import repo
from tests.test_web_api import H, add_user, auth, cfg, client, db, fake_food, make_client, user_id  # noqa: F401
from tests.test_web_api import _fixed_now  # noqa: F401  (autouse: время и справочник)

pytestmark = pytest.mark.asyncio

CRAVING = "срываюсь, не могу остановиться"          # сильная фраза: срабатывает в любое время


async def chat(client, headers=H, **payload):
    resp = await client.post("/api/chat", json=payload, headers=headers)
    return resp.status, await resp.json()


async def act(client, action_id: str, headers=H):
    resp = await client.post("/api/chat/action", json={"id": action_id}, headers=headers)
    return resp.status, await resp.json()


def ids(body) -> list[str]:
    return [a["id"] for a in body["actions"]]


def event_row(db, user_id):
    return db.execute("SELECT * FROM craving_events WHERE user_id=? ORDER BY id DESC LIMIT 1",
                      (user_id,)).fetchone()


@pytest.fixture()
def fake_dialog(monkeypatch):
    calls = []

    async def dialog_reply(db, user, event, text):  # noqa: ANN001
        calls.append(text)
        return "Я рядом, давай дышать &amp; ждать", False

    monkeypatch.setattr(craving_svc, "dialog_reply", dialog_reply)
    return calls


# ------------------------------------------------------------------ тяга: вход и диалог

async def test_craving_phrase_opens_episode_with_actions(client, db, user_id):
    status, body = await chat(client, text=CRAVING)
    assert status == 200
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["logged_meal"] is None
    event = event_row(db, user_id)
    assert event["outcome"] == "open" and event["mode"] == "craving"
    got = ids(body)
    for cat in ("hunger", "emotion", "habit", "tired", "overate"):
        assert f"cr:cat:{event['id']}:{cat}" in got
    assert f"cr:wait:{event['id']}" in got
    assert all(a["label"] for a in body["actions"])
    assert "<" not in body["messages"][1]["text"]
    hist = await (await client.get("/api/chat", headers=H)).json()
    assert len(hist["messages"]) == 2


async def test_plain_message_has_no_actions(client):
    status, body = await chat(client, text="привет")
    assert status == 200 and body["actions"] == []


async def test_message_in_open_episode_goes_to_dialog(client, db, user_id, fake_dialog):
    await chat(client, text=CRAVING)
    event = event_row(db, user_id)
    status, body = await chat(client, text="мне грустно и одиноко")
    assert fake_dialog == ["мне грустно и одиноко"]
    assert body["messages"][1]["text"] == "Я рядом, давай дышать & ждать"
    assert ids(body) == [f"cr:out:{event['id']}:resisted", f"cr:out:{event['id']}:ate_planned",
                         f"cr:out:{event['id']}:overate", f"cr:wait:{event['id']}"]


async def test_food_log_inside_episode_still_logs_meal(client, db, fake_food, fake_dialog):
    await chat(client, text=CRAVING)
    status, body = await chat(client, text="съел гречку 150 г")
    assert fake_dialog == []
    assert body["logged_meal"] is not None and "Записал" in body["messages"][1]["text"]
    # разговор о тяге узнал о записи (как _food_logged_hook в боте)
    assert "без оценок" in body["messages"][1]["text"]
    roles = [r["text"] for r in db.execute("SELECT text FROM craving_messages").fetchall()]
    assert any(t.startswith("[записано в дневник") for t in roles)


async def test_water_and_commands_inside_episode_skip_dialog(client, db, fake_dialog):
    await chat(client, text=CRAVING)
    _, body = await chat(client, text="выпил 2 стакана воды")
    assert "500 мл" in body["messages"][1]["text"]
    _, body = await chat(client, text="что ты умеешь")
    assert "дневник" in body["messages"][1]["text"]
    assert fake_dialog == []


async def test_risk_phrase_in_episode_gets_specialist_text(client, db):
    await chat(client, text=CRAVING)
    _, body = await chat(client, text="после еды вызвала рвоту")
    assert "врач" in body["messages"][1]["text"].lower() or "специалист" in \
        body["messages"][1]["text"].lower()


async def test_talk_mode_explicit_entry(client, db, user_id, fake_dialog):
    status, body = await chat(client, mode="talk")
    assert status == 200
    assert [m["role"] for m in body["messages"]] == ["assistant"]       # текста нет: нет и реплики
    assert body["actions"] == []
    assert event_row(db, user_id)["mode"] == "talk"
    _, again = await chat(client, mode="talk")
    assert "продолжаем" in again["messages"][0]["text"]
    _, body = await chat(client, text="просто хочется поговорить")
    assert fake_dialog == ["просто хочется поговорить"] and body["actions"] == []


async def test_craving_mode_with_open_episode_offers_triage(client, db, user_id):
    await chat(client, text=CRAVING)
    _, body = await chat(client, mode="craving")
    assert any(i.startswith("cr:cat:") for i in ids(body))


async def test_chat_validation_modes(client):
    assert (await client.post("/api/chat", json={}, headers=H)).status == 400
    assert (await client.post("/api/chat", json={"mode": "dance"}, headers=H)).status == 400
    assert (await client.post("/api/chat", json={"text": "привет", "meal_id": "x"},
                              headers=H)).status == 400


# ------------------------------------------------------------------ тяга: кнопки

async def test_action_outcome_closes_episode(client, db, user_id):
    await chat(client, text=CRAVING)
    ev = event_row(db, user_id)["id"]
    status, body = await act(client, f"cr:out:{ev}:resisted")
    assert status == 200 and body["logged_meal"] is None
    assert [m["role"] for m in body["messages"]] == ["assistant"]
    assert "справил" in body["messages"][0]["text"].lower()
    assert ids(body) == [f"cr:deb:{ev}"]
    assert event_row(db, user_id)["outcome"] == "resisted"
    again = await act(client, f"cr:out:{ev}:overate")
    assert again[0] == 409 and again[1]["error"] == "episode_closed"
    hist = await (await client.get("/api/chat?limit=50", headers=H)).json()
    assert hist["messages"][-1]["text"] == body["messages"][0]["text"]


async def test_action_category_wait_and_debrief_flow(client, db, user_id):
    await chat(client, text=CRAVING)
    ev = event_row(db, user_id)["id"]
    _, body = await act(client, f"cr:cat:{ev}:hunger")
    assert event_row(db, user_id)["category"] == "hunger"
    assert f"cr:out:{ev}:overate" in ids(body)
    _, wait = await act(client, f"cr:wait:{ev}")
    assert "15 минут" in wait["messages"][0]["text"]
    assert event_row(db, user_id)["followup_at_utc"] is not None
    await act(client, f"cr:out:{ev}:ate_planned")
    _, deb = await act(client, f"cr:deb:{ev}")
    assert f"cr:less:{ev}:0" in ids(deb) and f"cr:less:{ev}:own" in ids(deb)
    _, saved = await act(client, f"cr:less:{ev}:0")
    assert "Записал" in saved["messages"][0]["text"] and ids(saved) == [f"cr:rule:{ev}"]
    assert event_row(db, user_id)["lesson"]
    _, rule = await act(client, f"cr:rule:{ev}")
    assert "правило" in rule["messages"][0]["text"]


async def test_action_own_lesson_then_text(client, db, user_id, fake_dialog):
    await chat(client, text=CRAVING)
    ev = event_row(db, user_id)["id"]
    await act(client, f"cr:out:{ev}:resisted")
    await act(client, f"cr:less:{ev}:own")
    _, body = await chat(client, text="выпью воды и подожду 15 минут")
    assert "Записал" in body["messages"][1]["text"] and fake_dialog == []
    assert event_row(db, user_id)["lesson"] == "выпью воды и подожду 15 минут"


async def test_action_errors_and_isolation(client, db, user_id):
    add_user(db, 1002)
    await chat(client, text=CRAVING)
    ev = event_row(db, user_id)["id"]
    status, _ = await act(client, f"cr:out:{ev}:resisted", headers=auth(1002))
    assert status == 404
    for bad_id in ("", "nope", "cr:out", "cr:out:x:resisted", f"cr:zzz:{ev}", f"cr:cat:{ev}:wat"):
        status, _ = await act(client, bad_id)
        assert status == 400, bad_id
    assert (await act(client, "cr:wait:9999"))[0] == 404
    assert event_row(db, user_id)["outcome"] == "open"


# ------------------------------------------------------------------ память ассистента

async def test_history_passed_to_assistant(client, monkeypatch):
    seen = []

    async def fake_answer(db, user, question, focus=None, history=None):  # noqa: ANN001
        seen.append((question, focus, history))
        return "ответ №" + str(len(seen))

    monkeypatch.setattr(assistant, "answer", fake_answer)
    await chat(client, text="почему вес стоит?")
    await chat(client, text="а что с этим делать?")
    assert seen[0][2] in (None, [])
    assert seen[1][2] == [{"role": "user", "text": "почему вес стоит?"},
                          {"role": "assistant", "text": "ответ №1"}]
    for i in range(8):
        await chat(client, text=f"вопрос номер {i}?")
    assert len(seen[-1][2]) == 10 and seen[-1][2][-1]["role"] == "assistant"


async def test_assistant_answer_puts_history_after_static_prefix(db, monkeypatch):
    add_user(db, 1001)
    user = repo.get_user_by_tg(db, 1001)
    captured = {}

    async def chat_json(purpose, messages, **kw):  # noqa: ANN001
        captured["messages"] = messages
        return {"reply": "Ты съела 540 ккал, всё спокойно"}, None

    monkeypatch.setattr(llm, "chat_json", chat_json)
    history = [{"role": "user", "text": "привет"}, {"role": "assistant", "text": "привет!"}]
    text = await assistant.answer(db, user, "что с обедом?", focus="Обед, 540 ккал",
                                  history=history)
    msgs = captured["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "привет"
    assert json.loads(msgs[2]["content"]) == {"reply": "привет!"}
    last = json.loads(msgs[3]["content"])
    assert last["focus"] == "Обед, 540 ккал" and last["question"] == "что с обедом?"
    assert "540" in text                          # число из focus не отбраковано проверкой

    captured.clear()
    await assistant.answer(db, user, "ещё вопрос?")          # без history: как раньше
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]


# ------------------------------------------------------------------ обсудить приём

async def test_meal_id_focus_and_ownership(client, db, fake_food, monkeypatch):
    add_user(db, 1002)
    seen = []

    async def fake_answer(db, user, question, focus=None, history=None):  # noqa: ANN001
        seen.append(focus)
        return "Нормальный обед"

    monkeypatch.setattr(assistant, "answer", fake_answer)
    _, logged = await chat(client, text="съел гречку 150 г")
    meal_id = logged["logged_meal"]["id"]
    status, body = await chat(client, text="это много?", meal_id=meal_id)
    assert status == 200 and body["messages"][1]["text"] == "Нормальный обед"
    assert body["logged_meal"] is None
    focus = seen[-1]
    assert "гречка 150 г" in focus and str(logged["logged_meal"]["kcal"]) in focus
    assert logged["logged_meal"]["time"] in focus
    # чужой и несуществующий приём
    other = await client.post("/api/chat", json={"text": "это много?", "meal_id": meal_id},
                              headers=auth(1002))
    assert other.status == 404
    assert (await chat(client, text="это много?", meal_id=99999))[0] == 404
    # хранится как обычное сообщение; ответ ассистента не привязан к приёму
    assert len(seen) == 1


# ------------------------------------------------------------------ сон, шаги, тренировки, посуда

async def test_sleep_steps_workout_phrases_are_logged(client, db, user_id, monkeypatch):
    async def boom(*a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError("ассистент не должен вызываться")

    monkeypatch.setattr(assistant, "answer", boom)
    _, sleep = await chat(client, text="спал 7 часов")
    assert "😴 Записал сон: 7 ч" in sleep["messages"][1]["text"]
    assert [r["duration_min"] for r in db.execute("SELECT duration_min FROM sleep_sessions")] == [420]
    _, steps = await chat(client, text="10 тысяч шагов")
    assert "👟 Записал шаги: 10 000" in steps["messages"][1]["text"]
    assert db.execute("SELECT steps FROM activity_daily WHERE user_id=?",
                      (user_id,)).fetchone()["steps"] == 10000
    _, workout = await chat(client, text="тренировка бег 30 минут")
    assert "🏃 Записал тренировку" in workout["messages"][1]["text"]
    assert "<" not in workout["messages"][1]["text"]
    assert db.execute("SELECT COUNT(*) c FROM meals").fetchone()["c"] == 0


async def test_dish_and_product_phrases(client, db, user_id, monkeypatch):
    async def boom(*a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError("ассистент не должен вызываться")

    monkeypatch.setattr(assistant, "answer", boom)
    _, body = await chat(client, text="добавь посуду: тарелка 22 см")
    assert "Добавил" in body["messages"][1]["text"]
    assert len(repo.list_dishes(db, user_id)) == 1
    _, body = await chat(client, text="моя посуда")
    assert "Меню → Посуда" in body["messages"][1]["text"]
    _, body = await chat(client, text="мои продукты")
    assert "Мои продукты" in body["messages"][1]["text"]


# ------------------------------------------------------------------ прежнее поведение

async def test_existing_behaviour_still_works(client, db, fake_food):
    _, water = await chat(client, text="выпил 2 стакана воды")
    assert "500 мл" in water["messages"][1]["text"] and water["actions"] == []
    _, meal = await chat(client, text="съел гречку")
    assert meal["logged_meal"] and "Записал" in meal["messages"][1]["text"]
    _, undo = await chat(client, text="отмени последнее")
    assert "Отменил" in undo["messages"][1]["text"]
    assert (await (await client.get("/api/day", headers=H)).json())["meals"] == []


async def test_followup_is_stored_in_app_chat(db):
    from app import scheduler
    add_user(db, 1001)
    uid = repo.get_user_by_tg(db, 1001)["id"]
    scheduler._store_in_app_chat(db, uid, "⏱ Прошло 15 минут. Как ты сейчас?")
    row = db.execute("SELECT role, text FROM chat_messages WHERE user_id=?", (uid,)).fetchone()
    assert row["role"] == "assistant" and row["text"].startswith("⏱")
