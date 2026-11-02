"""Уточняющие вопросы кнопками, частые блюда одним нажатием, подсказки про фото и скрытое."""
from __future__ import annotations

import pytest

from app.domain.nutrition import MealUnderstanding
from app.routers import intake
from app.services import food as foodsvc
from app.services import meals as meals_svc
from app.storage import repo
from tests.test_web_api import TODAY, H, _fixed_now, cfg, client, db, make_client  # noqa: F401  (фикстуры)


def _u(**kw):
    return MealUnderstanding(**kw)


def test_options_parsed_from_model_answer():
    data = {"items": [{"name_ru": "Суп", "name_en": "soup",
                       "amount": {"kind": "grams", "grams_range": [300, 400]}}],
            "needs_clarification": {"question": "Какая порция?",
                                    "options": ["Маленькая", " Большая ", "Большая", "", "x" * 60, "Средняя"]},
            "overall_confidence": 0.7}
    u = foodsvc.understanding_from_json(data)
    assert u.clarification_question == "Какая порция?"
    assert u.clarification_options == ["Маленькая", "Большая", "Средняя"]
    # варианты без вопроса теряют смысл
    data["needs_clarification"] = {"options": ["да", "нет"]}
    assert foodsvc.understanding_from_json(data).clarification_options == []


def test_merge_keeps_options_with_their_question():
    a = _u(items=[], clarification_question=None)
    b = _u(items=[], clarification_question="Корочка?", clarification_options=["тонкая", "обычная"])
    merged = meals_svc.merge_understandings([a, b])
    assert merged.clarification_question == "Корочка?"
    assert merged.clarification_options == ["тонкая", "обычная"]


@pytest.mark.asyncio
async def test_quick_rows_options_and_fat_buttons(client, db):
    uid = repo.get_user_by_tg(db, 1001)["id"]
    ask = _u(clarification_question="Какая порция?", clarification_options=["Малая", "Большая"])
    rows = intake._quick_rows(db, uid, 7, ask)
    assert [b.callback_data for r in rows for b in r] == ["meal_ans:7:0", "meal_ans:7:1"]
    assert intake._options_for(db, uid, 7)["options"] == ["Малая", "Большая"]
    assert intake._options_for(db, uid, 8) is None

    class It:   # минимальный item для _may_hide_fat
        name_ru, name, preparation = "Салат Цезарь", "caesar", "raw"
    fat = intake._quick_rows(db, uid, 9, _u(items=[It()] and []))
    assert fat == []          # без позиций прятать нечего
    u = _u()
    u.items.append(type("I", (), {"name_ru": "Жареная картошка", "name": "fries",
                                  "preparation": "fried"})())
    rows = intake._quick_rows(db, uid, 9, u)
    assert len(rows) == 2 and rows[0][0].callback_data == "meal_fat:9:0"
    kb = intake._meal_kb(9, rows)
    assert kb.inline_keyboard[0][0].callback_data == "meal_fat:9:0"
    assert kb.inline_keyboard[-1][0].callback_data == "meal_dishes:9"


@pytest.mark.asyncio
async def test_photo_tip_first_photo_and_low_confidence(client, db):
    user = repo.get_user_by_tg(db, 1001)
    uid = user["id"]
    sure = _u(overall_confidence=0.9)
    assert intake._wants_photo_tip(db, uid, sure, "text") is False
    assert intake._wants_photo_tip(db, uid, _u(overall_confidence=0.3), "photo") is True
    assert intake._wants_photo_tip(db, uid, sure, "photo") is True       # первое фото
    for _ in range(2):
        await client.post("/api/drinks", json={"drink": "cola", "ml": 330}, headers=H)
        db.execute("UPDATE meals SET source='photo' WHERE user_id=?", (uid,))
    db.commit()
    assert intake._wants_photo_tip(db, uid, sure, "photo") is False      # уже не новичок
    assert intake._wants_photo_tip(db, uid, _u(overall_confidence=0.3), "combo") is True


@pytest.mark.asyncio
async def test_frequent_meals_ranked_and_repeat_adds_same_meal(client, db):
    for _ in range(3):
        await client.post("/api/drinks", json={"drink": "cola", "ml": 330}, headers=H)
    await client.post("/api/drinks", json={"drink": "fanta", "ml": 330}, headers=H)
    await client.post("/api/drinks", json={"drink": "cola_zero", "ml": 330}, headers=H)  # 0 ккал: не в счёт
    user = repo.get_user_by_tg(db, 1001)
    often = repo.frequent_meals(db, user["id"], today=TODAY)
    assert [o["names"] for o in often] == [["Кола"], ["Фанта"]]
    assert often[0]["count"] == 3 and often[0]["kcal"] == 140

    before = repo.day_totals(db, user["id"], meals_svc.foodsvc.local_now(user["tz"]).strftime("%Y-%m-%d"))
    new_id = meals_svc.repeat_meal(db, user, often[0]["meal_id"])
    assert new_id and new_id != often[0]["meal_id"]
    items = repo.get_meal_items(db, new_id)
    assert [(i["name_ru"], i["grams"], i["kcal"]) for i in items] == [("Кола", 330, 140)]
    assert repo.get_meal(db, user["id"], new_id)["source"] == "manual"
    after = repo.day_totals(db, user["id"], meals_svc.foodsvc.local_now(user["tz"]).strftime("%Y-%m-%d"))
    assert after["kcal"] == before["kcal"] + 140


@pytest.mark.asyncio
async def test_frequent_api_repeat_and_undo(client):
    for _ in range(2):
        await client.post("/api/drinks", json={"drink": "cola", "ml": 330}, headers=H)
    await client.post("/api/drinks", json={"drink": "fanta", "ml": 330}, headers=H)   # разовое
    items = (await (await client.get("/api/frequent", headers=H)).json())["items"]
    assert [(i["title"], i["kcal"], i["count"]) for i in items] == [("Кола", 140, 2)]

    resp = await client.post(f"/api/meals/{items[0]['meal_id']}/repeat", headers=H)
    assert resp.status == 201
    new = await resp.json()
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["totals"]["kcal"] == 140 * 3 + 132           # две колы, фанта и повтор колы
    assert (await client.delete(f"/api/meals/{new['id']}", headers=H)).status == 204   # «Отменить»
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["totals"]["kcal"] == 140 * 2 + 132
    assert (await client.post("/api/meals/424242/repeat", headers=H)).status == 404


def test_short_title_for_chip():
    from app.web.server import _short_title
    assert _short_title(["гречка", "курица"]) == "Гречка +1"
    assert _short_title(["х" * 40]).endswith("…") and len(_short_title(["х" * 40])) == 28


def test_repeat_rejects_foreign_or_deleted_meal(db):
    user = repo.get_user_by_tg(db, 1001) or {"id": 999, "tz": "UTC"}
    assert meals_svc.repeat_meal(db, {"id": user["id"], "tz": "UTC"}, 424242) is None


def test_prompts_mention_hidden_fat_and_density():
    for name in ("vision", "vision_v2"):
        text = foodsvc.load_prompt(name)
        assert "Скрытое и плотность" in text and "Объём ≠ вес" in text
