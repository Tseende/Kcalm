"""Онбординг: общий сервис complete(), POST /api/onboarding, /api/me до настройки, вход через /start."""
from __future__ import annotations

import asyncio

import pytest

from app import config as config_mod
from app.services import onboarding as svc
from app.storage import repo
from tests.test_routers_smoke import FakeSession, build, message, onboard, send  # noqa: F401
from tests.test_routers_smoke import db as smoke_db  # noqa: F401
from tests.test_web_api import H, _fixed_now, auth, cfg, client, db, make_client, make_config  # noqa: F401

FEMALE = {"sex": "female", "birth_year": 1991, "height_cm": 168, "weight_kg": 82.4,
          "goal_weight_kg": 72, "rate_kg_week": 0.5, "activity": 2, "eating_cutoff": "21:00",
          "tz": "Europe/Minsk", "care_mode": False, "goal": "lose",
          "reminders": {"morning": True, "evening": True, "water": True, "weigh": True, "weekly": True}}


# ------------------------------------------------------------------ сервис complete()

async def test_complete_saves_profile_target_reminders_and_start_weight(db):
    res = svc.complete(db, 77, dict(FEMALE))
    user = repo.get_user_by_tg(db, 77)
    assert user["onboarded"] == 1 and user["sex"] == "female" and user["goal_weight_kg"] == 72.0
    assert user["target_rate_kg_week"] == -0.5
    assert res["targets"]["kcal"] == repo.current_target(db, user["id"])["kcal_target"] > 1200
    assert res["goal_date"] and res["notes"] == []
    assert res["profile"]["weight_kg"] == 82.4
    row = db.execute("SELECT weight_kg, source FROM body_measurements WHERE user_id=?", (user["id"],)).fetchone()
    assert row["weight_kg"] == 82.4 and row["source"] == "manual"
    kinds = {r["kind"]: r for r in db.execute("SELECT * FROM reminders WHERE user_id=?", (user["id"],))}
    assert set(kinds) == {"morning", "midday", "evening", "weekly", "water", "weigh", "kitchen_close"}
    assert kinds["kitchen_close"]["local_time"] == "20:45" and kinds["weigh"]["enabled"] == 1


async def test_complete_rejects_underage(db):
    with pytest.raises(svc.OnboardingError) as e:
        svc.complete(db, 78, {**FEMALE, "birth_year": 2015})
    assert e.value.code == "underage" and "врач" in str(e.value)
    assert repo.get_user_by_tg(db, 78) is None


async def test_complete_low_bmi_means_no_deficit(db):
    res = svc.complete(db, 79, {**FEMALE, "weight_kg": 48, "goal_weight_kg": 45})
    user = repo.get_user_by_tg(db, 79)
    assert user["target_rate_kg_week"] == 0 and user["goal_weight_kg"] == 48
    assert res["goal_date"] is None and any("ИМТ" in n for n in res["notes"])


async def test_complete_goal_below_bmi_185_is_clamped(db):
    res = svc.complete(db, 80, {**FEMALE, "weight_kg": 56, "goal_weight_kg": 40, "rate_kg_week": 0.7})
    assert repo.get_user_by_tg(db, 80)["goal_weight_kg"] == 52.2        # ИМТ 18,5 при росте 168
    assert any("ИМТ 18.5" in n for n in res["notes"])


async def test_complete_maintain_weight(db):
    res = svc.complete(db, 81, {**FEMALE, "rate_kg_week": 0, "goal_weight_kg": 82.4})
    user = repo.get_user_by_tg(db, 81)
    assert user["target_rate_kg_week"] == 0 and user["goal_weight_kg"] == 82.4 and res["goal_date"] is None


async def test_complete_care_mode(db):
    svc.complete(db, 82, {**FEMALE, "care_mode": True, "rate_kg_week": 0.7})
    user = repo.get_user_by_tg(db, 82)
    assert user["care_mode"] == 1 and user["target_rate_kg_week"] == -0.25
    assert repo.due_reminders(db, user["id"], "weigh")["enabled"] == 0
    assert repo.due_reminders(db, user["id"], "morning")["enabled"] == 1


async def test_complete_reminders_can_be_disabled(db):
    svc.complete(db, 83, {**FEMALE, "reminders": {"water": False, "weekly": False}})
    uid = repo.get_user_by_tg(db, 83)["id"]
    assert repo.due_reminders(db, uid, "water")["enabled"] == 0
    assert repo.due_reminders(db, uid, "weekly")["enabled"] == 0
    assert repo.due_reminders(db, uid, "evening")["enabled"] == 1


# ------------------------------------------------------------------ API

async def test_me_before_onboarding_is_200(client, db):
    repo.create_user(db, 1002)
    resp = await client.get("/api/me", headers=auth(1002, first_name="Аня"))
    assert resp.status == 200
    assert await resp.json() == {"onboarded": False, "user": {"first_name": "Аня"}}
    # пользователя в базе ещё нет — тоже 200
    assert (await client.get("/api/me", headers=auth(1002))).status == 200
    # остальные ручки по-прежнему закрыты
    for path in ("/api/profile", "/api/day", "/api/reminders"):
        r = await client.get(path, headers=auth(1002))
        assert r.status == 409 and (await r.json())["error"] == "not_onboarded", path


async def test_me_onboarded_flag(client):
    assert (await (await client.get("/api/me", headers=H)).json())["onboarded"] is True


async def test_post_onboarding_success_creates_user(client, db):
    assert repo.get_user_by_tg(db, 1002) is None
    r = await client.post("/api/onboarding", json=FEMALE, headers=auth(1002))
    assert r.status == 200
    body = await r.json()
    assert set(body) == {"profile", "targets", "notes", "goal_date"}
    assert body["targets"]["kcal"] > 1200 and body["profile"]["goal_weight_kg"] == 72.0
    assert body["goal_date"] and body["profile"]["tz"] == "Europe/Minsk"
    assert repo.get_user_by_tg(db, 1002)["onboarded"] == 1
    # теперь остальные ручки доступны, а повтор отклоняется
    assert (await client.get("/api/profile", headers=auth(1002))).status == 200
    r = await client.post("/api/onboarding", json=FEMALE, headers=auth(1002))
    assert r.status == 409 and (await r.json())["error"] == "already_onboarded"


@pytest.mark.parametrize("existing", [False, True])
async def test_skip_then_complete_keeps_diary(client, db, existing):
    headers = auth(1002)
    if existing:
        repo.create_user(db, 1002)
    skip = {"tz": "Europe/Warsaw"}
    for _ in range(2):
        assert (await client.post("/api/onboarding/skip", json=skip, headers=headers)).status == 200
    user = repo.get_user_by_tg(db, 1002)
    uid = user["id"]
    assert user["birth_date"] is None and user["start_weight_kg"] is None
    assert repo.current_target(db, uid) is None and repo.due_reminders(db, uid, "morning") is None
    me = await (await client.get("/api/me", headers=headers)).json()
    assert not me["onboarded"] and me["onboarding_skipped"] and me["user"]["tz"] == skip["tz"]
    assert (await client.post("/api/water", json={"ml": 300}, headers=headers)).status == 201
    meal = {"name": "Йогурт", "grams": 100, "kcal": 80, "protein": 5, "fat": 4, "carbs": 6}
    assert (await client.post("/api/meals/product", json=meal, headers=headers)).status == 201
    day = await (await client.get("/api/day", headers=headers)).json()
    assert day["totals"]["kcal"] == 80 and day["totals"]["protein"] == 5
    assert day["water_ml"] == 300 and day["remaining_kcal"] is None
    assert all(day["targets"][key] is None for key in ("kcal", "protein", "fat", "carbs"))
    assert day["activity_hint"] is None
    # Вес, записанный в дневник сегодня, сохраняется и после ввода стартового веса в анкете.
    today = svc.local_today(FEMALE["tz"]).isoformat()
    repo.save_body_measurement(db, uid, today, {"weight_kg": 81.2}, "manual", 1.0)
    for path in ("/api/analysis", "/api/profile"):
        resp = await client.get(path, headers=headers)
        assert resp.status == 409 and (await resp.json())["error"] == "profile_required"
    assert (await client.post("/api/onboarding", json=FEMALE, headers=headers)).status == 200
    assert (await client.post("/api/onboarding/skip", json=skip, headers=headers)).status == 200
    user = repo.get_user_by_tg(db, 1002)
    assert user["id"] == uid and user["onboarded"] and not user["onboarding_skipped"]
    assert repo.weight_series(db, uid, today, today)[0]["weight_kg"] == 81.2
    assert user["tz"] == FEMALE["tz"]
    day = await (await client.get("/api/day", headers=headers)).json()
    assert day["water_ml"] == 300 and day["totals"]["kcal"] == 80
    assert day["remaining_kcal"] == day["targets"]["kcal"] - 80 > 0


async def test_skip_with_empty_answers(client, db):
    assert (await client.post("/api/onboarding/skip", json={})).status == 401
    assert repo.get_user_by_tg(db, 1002) is None
    assert (await client.post("/api/onboarding/skip", json={}, headers=auth(1002))).status == 200
    assert repo.get_user_by_tg(db, 1002)["birth_date"] is None


@pytest.mark.parametrize("sex", ["male", "female"])
def test_activity_changes_kcal_and_carbs_at_fixed_goal(db, sex):
    targets = [svc.complete(db, 90 + level, {**FEMALE, "sex": sex, "activity": level})["targets"]
               for level in range(1, 5)]
    for low, high in zip(targets, targets[1:]):
        assert low["kcal"] < high["kcal"] and low["carbs"] < high["carbs"]
        assert low["protein"] == high["protein"] and low["fat"] == high["fat"]


async def test_post_onboarding_validation(client, db):
    bad_bodies = [
        {**FEMALE, "sex": "x"}, {**FEMALE, "height_cm": 50}, {**FEMALE, "weight_kg": 20},
        {**FEMALE, "weight_kg": 400}, {**FEMALE, "rate_kg_week": 1.5}, {**FEMALE, "activity": 7},
        {**FEMALE, "eating_cutoff": "25:99"}, {**FEMALE, "tz": "Mars/Base"},
        {**FEMALE, "care_mode": "yes"}, {**FEMALE, "reminders": {"nope": True}},
        {k: v for k, v in FEMALE.items() if k != "weight_kg"},
    ]
    for body in bad_bodies:
        r = await client.post("/api/onboarding", json=body, headers=auth(1002))
        assert r.status == 400, body
    assert repo.get_user_by_tg(db, 1002) is None or not repo.get_user_by_tg(db, 1002)["onboarded"]


async def test_post_onboarding_underage_is_400_with_text(client):
    r = await client.post("/api/onboarding", json={**FEMALE, "birth_year": 2015}, headers=auth(1002))
    body = await r.json()
    assert r.status == 400 and body["error"] == "underage" and "врач" in body["message"]


async def test_post_onboarding_needs_auth(client):
    assert (await client.post("/api/onboarding", json=FEMALE)).status == 401


async def test_post_onboarding_notifies_chat(db, cfg, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from app.web import server

    sent = []
    release_delivery = asyncio.Event()
    delivered = asyncio.Event()

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            await release_delivery.wait()
            sent.append((chat_id, text))
            delivered.set()

    monkeypatch.setattr(config_mod, "_config", cfg)
    c = TestClient(TestServer(server.build_app(db, FakeBot(), cfg)))
    await c.start_server()
    try:
        r = await asyncio.wait_for(c.post("/api/onboarding", json=FEMALE, headers=auth(1002)), 1)
        assert r.status == 200
        assert not sent
        assert repo.get_user_by_tg(db, 1002)["onboarded"]
        release_delivery.set()
        await asyncio.wait_for(delivered.wait(), 1)
        assert sent and sent[0][0] == 1002 and "Профиль готов" in sent[0][1] and "ккал" in sent[0][1]
    finally:
        await c.close()


# ------------------------------------------------------------------ чат

async def test_chat_onboarding_still_works(smoke_db):
    bot, dp, _ = build(smoke_db)
    await onboard(dp, bot)           # /start без WEBAPP_URL → старый текстовый поток


async def test_start_with_webapp_url_offers_app_and_chat_button(smoke_db, monkeypatch):
    monkeypatch.setattr(config_mod, "_config", config_mod.Config(
        _env_file=None, allow_public_bot=True, webapp_url="https://example.com/app/"))
    bot, dp, session = build(smoke_db)
    seen = []
    orig = session.make_request

    async def spy(bot_, method, timeout=None):
        seen.append(method)
        return await orig(bot_, method, timeout)

    session.make_request = spy
    await send(dp, bot, "/start")
    sends = [m for m in seen if type(m).__name__ == "SendMessage"]
    assert sends, "приветствие не отправлено"
    kb = sends[0].reply_markup.inline_keyboard
    assert kb[0][0].web_app.url == "https://example.com/app/" and kb[0][0].text == "Открыть дневник"
    assert kb[1][0].callback_data == "onb:chat"
    assert repo.get_user_by_tg(smoke_db, 1) is None          # профиль не создан, вопросов в чате нет
