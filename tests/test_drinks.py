"""Напитки с калориями: расчёт, запись приёмом, вода из чая и кофе, отмена."""
from __future__ import annotations

import pytest

from app.domain import drinks
from tests.test_web_api import H, _fixed_now, cfg, client, db, make_client  # noqa: F401  (фикстуры)


def test_catalog_has_requested_drinks():
    names = {d.name for d in drinks.DRINKS}
    assert {"Кола без сахара", "Фанта", "Чай", "Американо", "Капучино", "Флэт уайт"} <= names
    assert drinks.get("cola_zero").kcal_100 == 0
    assert 35 <= drinks.get("cappuccino").kcal_100 <= 45
    assert drinks.get("flat_white").kcal_100 > drinks.get("cappuccino").kcal_100


def test_calculate_scales_with_volume_and_sugar():
    cola = drinks.calculate(drinks.get("cola"), 330)
    assert cola.kcal_total == 140 and cola.items[0].grams == 330
    tea = drinks.calculate(drinks.get("tea"), 300, sugar_tsp=2)
    assert tea.kcal_total == 40 and tea.items[0].name_ru == "Чай, 2 ч. л. сахара"
    assert tea.carbs_g == 10.0
    # сахар у газировки не добавляется: ложек там нет
    assert drinks.calculate(drinks.get("fanta"), 330, sugar_tsp=3).kcal_total == \
        drinks.calculate(drinks.get("fanta"), 330).kcal_total


def test_calculate_rejects_out_of_range():
    with pytest.raises(ValueError):
        drinks.calculate(drinks.get("tea"), 5)
    with pytest.raises(ValueError):
        drinks.calculate(drinks.get("tea"), 300, sugar_tsp=99)


@pytest.mark.asyncio
async def test_catalog_endpoint(client):
    body = await (await client.get("/api/drinks", headers=H)).json()
    assert [d["id"] for d in body["drinks"]][:3] == ["cola_zero", "cola", "fanta"]
    assert all({"name", "emoji", "kcal_100", "sizes", "sugar"} <= set(d) for d in body["drinks"])


@pytest.mark.asyncio
async def test_add_drink_saves_meal_with_kcal(client):
    resp = await client.post("/api/drinks", json={"drink": "cola", "ml": 330}, headers=H)
    assert resp.status == 201
    body = await resp.json()
    assert body["kcal"] == 140 and body["water_ml"] == 0          # кола не идёт в воду
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["totals"]["kcal"] == 140
    meal = day["meals"][0]
    assert meal["id"] == body["id"] and meal["source"] == "manual" and meal["type"] == "snack"
    assert meal["items"][0]["name"] == "Кола" and meal["items"][0]["grams"] == 330


@pytest.mark.asyncio
async def test_coffee_and_tea_count_as_water_and_undo_removes_it(client):
    resp = await client.post("/api/drinks", json={"drink": "cappuccino", "ml": 250}, headers=H)
    body = await resp.json()
    assert body["water_ml"] == 250 and 80 <= body["kcal"] <= 120
    tea = await (await client.post("/api/drinks", json={"drink": "tea", "ml": 300,
                                                         "sugar_tsp": 1}, headers=H)).json()
    assert tea["kcal"] == 20 and tea["water_ml"] == 550
    assert (await client.delete(f"/api/meals/{body['id']}", headers=H)).status == 204
    day = await (await client.get("/api/day", headers=H)).json()
    assert day["water_ml"] == 300 and day["totals"]["kcal"] == 20


@pytest.mark.asyncio
async def test_add_drink_validation(client):
    for payload in ({"drink": "nope", "ml": 200}, {"drink": "tea"}, {"drink": "tea", "ml": 5},
                    {"drink": "tea", "ml": 200, "sugar_tsp": 50}, {"drink": "tea", "ml": "много"}):
        resp = await client.post("/api/drinks", json=payload, headers=H)
        assert resp.status == 400, payload


@pytest.mark.asyncio
async def test_zero_kcal_drinks_are_not_meals_for_day_analysis(client, db):
    from app.services import insights
    from app.storage import repo
    from tests.test_web_api import TODAY
    await client.post("/api/drinks", json={"drink": "americano", "ml": 200}, headers=H)
    await client.post("/api/drinks", json={"drink": "cola_zero", "ml": 330}, headers=H)
    user = repo.get_user_by_tg(db, 1001)
    facts = insights.day_facts(db, user, TODAY)
    assert facts["meals"] == 0 and facts["first_meal"] is None
    await client.post("/api/drinks", json={"drink": "cola", "ml": 330}, headers=H)   # 140 ккал
    assert insights.day_facts(db, user, TODAY)["meals"] == 1
