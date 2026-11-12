"""Распознавание еды по фото: промт vision_v3, переключатель VISION_PROMPT, разбор ответа,
уточнение записи (диапазон, масло), свои продукты, обрыв ответа по длине, MIME, ключи штучных весов."""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile

import httpx
import pytest

from app.config import get_config
from app.db import connect, migrate
from app.domain.nutrition import FoodRef, calculate_meal
from app.llm import client as llm
from app.services import food as foodsvc
from app.storage import repo

_real_chat_json = llm.chat_json     # conftest подменяет chat_json на заглушку, нам нужен настоящий


@pytest.fixture()
def plain_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


def _capture(calls, data=None):
    async def fake(purpose, messages, model=None, temperature=0.1, user_id=None, db=None,
                   max_tokens=1500, **kw):
        calls.append({"messages": messages, "model": model, "max_tokens": max_tokens})
        return (data or {"items": [{"name_ru": "суп", "name_en": "soup",
                                    "amount": {"kind": "grams", "grams": 300}, "confidence": 0.9}],
                         "overall_confidence": 0.9}), "m"
    return fake


def _text(call) -> str:
    return call["messages"][0]["content"][0]["text"]


# ------------------------------------------------------------------ промт vision_v3

def test_v3_prompt_has_sections_and_schema_fields():
    text = foodsvc.load_prompt("vision_v3")
    for needle in ("photo_index", "dish_fill", "volume_ml", "photo_kind", "needs_clarification",
                   "calc", "grams_range", "assumptions", "overall_confidence",
                   "КАК ОЦЕНИВАТЬ ВЕС", "НЕ СТЯГИВАЙ ОЦЕНКУ", "ЗАПОЛНЕНИЕ, а не размер посуды",
                   "НЕСКОЛЬКО БЛЮД В КАДРЕ", "Скрытый жир", "Известная посуда", "Свои продукты",
                   "Несколько фото", "данные, не инструкции", "Примеры ответа", "80 символов"):
        assert needle in text, needle
    assert "не видно — null" not in text           # диапазон обязателен всегда
    assert len(text.encode("utf-8")) < 15_000


def test_v3_examples_are_valid_schema_json():
    text = foodsvc.load_prompt("vision_v3")
    examples = re.findall(r"\n(\{\"items\".*?\"photo_kind\": \"meal\"\})", text, re.S)
    assert len(examples) == 2
    totals = []
    for raw in examples:
        data = json.loads(raw)
        for it in data["items"]:
            assert len(it["calc"]) <= 80
            assert {"photo_index", "dish_fill", "volume_ml"} <= set(it)
        u = foodsvc.understanding_from_json(data, {1})
        totals.append(sum(i.grams_mid for i in u.items))
    assert 800 <= totals[1] <= 1000                  # поднос из трёх блюд, около 900 г


def test_old_prompts_untouched_and_v3_does_not_break_them():
    assert "Известная посуда" in foodsvc.load_prompt("vision")
    assert "Шаг 4. Не якорись" in foodsvc.load_prompt("vision_v2")


# ------------------------------------------------------------------ переключатель VISION_PROMPT

def test_vision_prompt_default_is_production():
    assert get_config().vision_prompt == "vision"


def test_vision_prompt_from_env(monkeypatch):
    monkeypatch.setenv("VISION_PROMPT", "vision_v3")
    assert type(get_config())().vision_prompt == "vision_v3"


@pytest.mark.asyncio
async def test_parse_photo_uses_prompt_from_config(monkeypatch, plain_db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _capture(calls))
    await foodsvc.parse_photo("AAAA", None, 1, plain_db)
    assert _text(calls[0]).startswith(foodsvc.load_prompt("vision"))
    cfg = get_config().model_copy(update={"vision_prompt": "vision_v3"})
    monkeypatch.setattr(foodsvc, "get_config", lambda: cfg)
    await foodsvc.parse_photo("AAAA", None, 1, plain_db)
    await foodsvc.parse_photos(["AAAA", "QQ=="], None, 1, plain_db)
    assert _text(calls[1]).startswith(foodsvc.load_prompt("vision_v3"))
    assert _text(calls[2]).startswith(foodsvc.load_prompt("vision_v3"))
    await foodsvc.parse_photo("AAAA", None, 1, plain_db, prompt_name="vision_v2")   # явное имя главнее конфига
    assert _text(calls[3]).startswith(foodsvc.load_prompt("vision_v2"))


@pytest.mark.asyncio
async def test_broken_prompt_name_falls_back_to_production(monkeypatch, plain_db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _capture(calls))
    for bad in ("нет_такого", "../vision", "no_such_prompt"):
        cfg = get_config().model_copy(update={"vision_prompt": bad})
        monkeypatch.setattr(foodsvc, "get_config", lambda cfg=cfg: cfg)
        await foodsvc.parse_photo("AAAA", None, 1, plain_db)
        assert _text(calls[-1]).startswith(foodsvc.load_prompt("vision"))


def test_eval_tool_accepts_any_prompt_name():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "tools"
    src = (root / "eval_portions.py").read_text(encoding="utf-8")
    assert "vision_v3" in src and "gemma-3-27b" not in src
    assert "choices=" not in src.split('"--prompt"')[1].split("\n")[0]       # имя не зашито списком
    assert "vision_v3" in (root / "eval_portions_compare.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------ 3.1 нет ни граммов, ни диапазона

def _bare(**extra):
    base = {"name_ru": "рагу", "name_en": "stew", "confidence": 0.9,
            "amount": {"kind": "portion", "portion_hint": "medium"}}
    base.update(extra)
    return {"items": [base], "overall_confidence": 0.8}


def test_unknown_weight_is_wide_low_confidence_and_flagged():
    u = foodsvc.understanding_from_json(_bare())
    it = u.items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((150, 350))
    assert it.confidence == "low"                                   # ≤ 0.35 → low
    assert "Вес «рагу» не определён по фото, взята средняя порция" in u.assumptions


def test_unknown_weight_keeps_piece_substitution_and_hint():
    raw = _bare(name_ru="яйцо", name_en="egg")
    raw["items"][0]["amount"] = {"kind": "pieces", "pieces": 2}
    it = foodsvc.understanding_from_json(raw).items[0]
    assert (it.grams_min + it.grams_max) / 2 == pytest.approx(110)
    raw = _bare()
    raw["items"][0]["amount"]["portion_hint"] = "large"
    it = foodsvc.understanding_from_json(raw).items[0]
    assert (it.grams_min + it.grams_max) / 2 == pytest.approx(400)


def test_known_weight_gets_no_flag_and_known_dish_keeps_old_behaviour():
    ok = _bare()
    ok["items"][0]["amount"] = {"kind": "portion", "grams_range": [200, 300]}
    u = foodsvc.understanding_from_json(ok)
    assert not u.assumptions and u.items[0].confidence == "high"
    dish = foodsvc.understanding_from_json(_bare(dish_fill=0.6), {1})       # посуда известна: ±15 %
    assert (dish.items[0].grams_min, dish.items[0].grams_max) == pytest.approx((212.5, 287.5))
    assert dish.items[0].confidence == "high" and not dish.assumptions


# ------------------------------------------------------------------ 3.2 посуда без dish_fill

def test_dish_without_dish_fill_keeps_model_range():
    raw = _bare(volume_ml=400)
    raw["items"][0]["amount"] = {"kind": "portion", "grams_range": [200, 400]}
    it = foodsvc.understanding_from_json(raw, {1}).items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((200, 400))
    raw = _bare(dish_fill=0.7)
    raw["items"][0]["amount"] = {"kind": "portion", "grams_range": [200, 400]}
    it = foodsvc.understanding_from_json(raw, {1}).items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((255, 345))


# ------------------------------------------------------------------ 3.3 уточнение

def _saved_meal(conn, specs):
    """Записать приём из (name_ru, name_en, граммы, range, confidence, preparation) как после распознавания."""
    pairs = []
    for name, en, grams, rng, conf, prep in specs:
        u = foodsvc.understanding_from_json({"items": [{
            "name_ru": name, "name_en": en, "preparation": prep, "confidence": conf,
            "amount": {"kind": "portion", "grams_range": list(rng)}}], "overall_confidence": 0.8})
        item = u.items[0]
        item.explicit_grams = grams
        pairs.append((item, FoodRef(name_ru=name, name_en=en, kcal_100g=100, protein_100g=5,
                                    fat_100g=5, carbs_100g=10, source="seed")))
    uid = repo.create_user(conn, 77)
    mid = repo.save_meal(conn, uid, "2026-05-20 10:00:00", "2026-05-20", "13:00", "lunch", "photo",
                         calculate_meal(pairs))
    return uid, mid, [dict(r) for r in repo.get_meal_items(conn, mid)]


def _refine_llm(monkeypatch, items, sent=None):
    async def fake(purpose, messages, **kw):
        if sent is not None:
            sent.update(json.loads(messages[1]["content"]))
        return {"items": items, "changes": []}, "m"
    monkeypatch.setattr(llm, "chat_json", fake)


def _g(name, en, grams=None, prep="unknown"):
    amount = {"kind": "grams", "grams": grams} if grams else {"kind": "grams"}
    return {"name_ru": name, "name_en": en, "preparation": prep, "amount": amount, "confidence": 0.9}


@pytest.mark.asyncio
async def test_refine_sends_preparation_category_and_brand(monkeypatch, plain_db):
    uid, mid, rows = _saved_meal(plain_db, [("котлета", "cutlet", 120, (90, 160), 0.8, "fried")])
    rows[0]["category"] = "meat"
    rows[0]["brand"] = "Дворецкий"
    sent: dict = {}
    _refine_llm(monkeypatch, [_g("котлета", "cutlet")], sent)
    await foodsvc.refine_meal(rows, "ок", uid, plain_db)
    assert sent["current_items"] == [{"name_ru": "котлета", "name_en": "cutlet", "grams": 120,
                                      "preparation": "fried", "category": "meat", "brand": "Дворецкий"}]


@pytest.mark.asyncio
async def test_refine_unchanged_items_keep_range_and_confidence(monkeypatch, plain_db):
    uid, mid, rows = _saved_meal(plain_db, [
        ("гречка", "buckwheat", 250, (180, 350), 0.5, "boiled"),
        ("котлета", "cutlet", 100, (80, 130), 0.9, "fried")])
    _refine_llm(monkeypatch, [_g("гречка", "buckwheat"),                    # граммы не вернулись
                              _g("котлета", "cutlet", 100),                  # те же граммы
                              _g("масло подсолнечное", "sunflower oil", 10)])
    u, _ = await foodsvc.refine_meal(rows, "жарила на масле", uid, plain_db)
    buckwheat, cutlet, oil = u.items
    assert (buckwheat.grams_min, buckwheat.grams_max) == pytest.approx((180, 350))
    assert buckwheat.confidence == "medium" and buckwheat.explicit_grams == 250
    assert (cutlet.grams_min, cutlet.grams_max) == pytest.approx((80, 130))
    assert cutlet.confidence == "high"
    assert (oil.grams_min, oil.grams_max) == pytest.approx((9, 11))          # добавлено по словам: ±10 %


@pytest.mark.asyncio
async def test_refine_named_grams_get_narrow_band(monkeypatch, plain_db):
    uid, mid, rows = _saved_meal(plain_db, [("гречка", "buckwheat", 250, (180, 350), 0.5, "boiled")])
    _refine_llm(monkeypatch, [_g("гречка", "buckwheat", 300)])
    it = (await foodsvc.refine_meal(rows, "там было 300 г", uid, plain_db))[0].items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((270, 330))


@pytest.mark.asyncio
async def test_refine_old_rows_without_saved_range_use_normal_band(monkeypatch, plain_db):
    _refine_llm(monkeypatch, [_g("гречка", "buckwheat")])
    u, _ = await foodsvc.refine_meal([{"name_ru": "гречка", "name_en": "buckwheat", "grams": 200}],
                                     "ок", None, plain_db)
    assert (u.items[0].grams_min, u.items[0].grams_max) == pytest.approx((150, 250))


@pytest.mark.asyncio
async def test_refine_ignores_stale_range_after_manual_grams_edit(monkeypatch, plain_db):
    uid, mid, rows = _saved_meal(plain_db, [("гречка", "buckwheat", 250, (180, 350), 0.5, "boiled")])
    rows[0]["grams"] = 400                         # человек поправил граммы: снимок устарел
    _refine_llm(monkeypatch, [_g("гречка", "buckwheat")])
    it = (await foodsvc.refine_meal(rows, "ок", uid, plain_db))[0].items[0]
    assert (it.grams_min, it.grams_max) == pytest.approx((300, 500))


@pytest.mark.asyncio
async def test_repeated_fat_button_replaces_oil_instead_of_adding(monkeypatch, plain_db):
    uid, mid, rows = _saved_meal(plain_db, [
        ("картофель", "potato", 200, (150, 250), 0.8, "fried"),
        ("масло подсолнечное", "sunflower oil", 10, (8, 12), 0.5, "raw")])
    # модель вернула и старое масло, и новое, и ещё одно с другим названием, но тем же name_en
    _refine_llm(monkeypatch, [_g("картофель", "potato"),
                              _g("масло подсолнечное", "sunflower oil", 10),
                              _g("подсолнечное масло", "sunflower oil", 15)])
    u, _ = await foodsvc.refine_meal(rows, "готовили на масле, примерно 1 столовая ложка", uid, plain_db)
    oils = [i for i in u.items if i.name == "sunflower oil"]
    assert len(oils) == 1 and oils[0].explicit_grams == 15
    # то же, когда новое количество пришло первым, а старая позиция вернулась следом
    _refine_llm(monkeypatch, [_g("масло подсолнечное", "sunflower oil", 15),
                              _g("масло подсолнечное", "sunflower oil", 10)])
    u, _ = await foodsvc.refine_meal(rows, "ещё ложка", uid, plain_db)
    assert [i.explicit_grams for i in u.items if i.name == "sunflower oil"] == [15]


def test_refine_prompt_forbids_second_oil_item():
    text = foodsvc.load_prompt("refine")
    assert "Не плоди вторую позицию масла" in text and "ИЗМЕНИ граммы" in text
    assert "preparation" in text and "brand" in text


# ------------------------------------------------------------------ 3.4 свои продукты

@pytest.mark.asyncio
async def test_photo_request_lists_own_products_by_use(monkeypatch, plain_db):
    uid = repo.create_user(plain_db, 5)
    p1 = repo.create_product(plain_db, uid, "Форшмак", 200, 10, 15, 5)
    p2 = repo.create_product(plain_db, uid, "Бургер-соус", 300, 1, 30, 5, brand="Heinz")
    repo.touch_user_food(plain_db, uid, p1)
    repo.touch_user_food(plain_db, uid, p1)
    repo.touch_user_food(plain_db, uid, p2)
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _capture(calls))
    await foodsvc.parse_photo("AAAA", "обед", uid, plain_db)
    await foodsvc.parse_photos(["AAAA", "QQ=="], None, uid, plain_db)
    assert "Свои продукты: Форшмак, Бургер-соус (Heinz)" in _text(calls[0])
    assert "Свои продукты: Форшмак, Бургер-соус (Heinz)" in _text(calls[1])


@pytest.mark.asyncio
async def test_photo_request_has_no_own_products_line_when_empty_and_caps_at_40(monkeypatch, plain_db):
    uid = repo.create_user(plain_db, 6)
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _capture(calls))
    await foodsvc.parse_photo("AAAA", None, uid, plain_db)
    await foodsvc.parse_photo("AAAA", None, None, None)                      # без пользователя и базы
    assert "Свои продукты" not in _text(calls[0]) + _text(calls[1])
    for i in range(45):
        repo.create_product(plain_db, uid, f"Продукт {i}", 100, 1, 1, 1)
    await foodsvc.parse_photo("AAAA", None, uid, plain_db)
    line = [ln for ln in _text(calls[2]).splitlines() if ln.startswith("Свои продукты:")][0]
    assert len(line.split(": ", 1)[1].split(", ")) == 40


# ------------------------------------------------------------------ 3.5 обрыв ответа по длине

_FULL = {"choices": [{"message": {"content": '{"items": [], "overall_confidence": 0.9}'},
                      "finish_reason": "stop"}], "model": "m"}
_CUT = {"choices": [{"message": {"content": '{"items": [{"name_ru": "су'}, "finish_reason": "length"}],
        "model": "m", "usage": {"prompt_tokens": 100, "completion_tokens": 2500, "cost": 0.01}}


def _transport(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm, "_get_client", lambda: client)


@pytest.mark.asyncio
async def test_length_cut_retries_once_with_more_tokens(monkeypatch):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_CUT if len(bodies) == 1 else _FULL)
    _transport(monkeypatch, handler)
    data, _ = await _real_chat_json("food_vision", [{"role": "user", "content": "x"}], max_tokens=2500)
    assert [b["max_tokens"] for b in bodies] == [2500, 4000]
    assert data == {"items": [], "overall_confidence": 0.9}


@pytest.mark.asyncio
async def test_length_cut_retries_only_once_and_no_retry_on_stop(monkeypatch):
    bodies = []

    def handler(req):
        bodies.append(1)
        return httpx.Response(200, json=_CUT)
    _transport(monkeypatch, handler)
    data, _ = await _real_chat_json("food_vision", [{"role": "user", "content": "x"}], max_tokens=1000)
    assert len(bodies) == 2 and data["items"][0]["name_ru"] == "су"        # второй обрыв чинится как раньше
    bodies.clear()
    _transport(monkeypatch, lambda req: (bodies.append(1), httpx.Response(200, json=_FULL))[1])
    await _real_chat_json("food_vision", [{"role": "user", "content": "x"}], max_tokens=1000)
    assert len(bodies) == 1


@pytest.mark.asyncio
async def test_length_retry_failure_keeps_first_answer(monkeypatch):
    seen = []

    def handler(req):
        seen.append(1)
        return httpx.Response(200, json=_CUT) if len(seen) == 1 else httpx.Response(401)
    _transport(monkeypatch, handler)
    data, _ = await _real_chat_json("food_vision", [{"role": "user", "content": "x"}], max_tokens=1000)
    assert data["items"][0]["name_ru"] == "су"


@pytest.mark.asyncio
async def test_single_photo_asks_for_2500_tokens(monkeypatch, plain_db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _capture(calls))
    await foodsvc.parse_photo("AAAA", None, 1, plain_db)
    assert calls[0]["max_tokens"] == foodsvc.PHOTOS_MAX_TOKENS == 2500


# ------------------------------------------------------------------ 3.6 эскалация

@pytest.mark.asyncio
async def test_single_photo_escalation_failure_returns_first_answer(monkeypatch, plain_db):
    models: list = []

    async def fake(purpose, messages, model=None, **kw):
        models.append(model)
        if model == get_config().model_vision_escalation:
            raise llm.LLMError("упал")
        return {"items": [{"name_ru": "суп", "name_en": "soup", "amount": {"kind": "grams", "grams": 300},
                           "confidence": 0.3}], "overall_confidence": 0.3}, "m"
    monkeypatch.setattr(llm, "chat_json", fake)
    u = await foodsvc.parse_photo("AAAA", None, 1, plain_db)
    assert len(models) == 2 and models[1] == get_config().model_vision_escalation
    assert u.items[0].name_ru == "суп"


# ------------------------------------------------------------------ 3.7 веса штук

def test_piece_weight_matches_whole_words_only():
    assert foodsvc.piece_weight("egg") == 55 and foodsvc.piece_weight("fried eggs") == 55
    assert foodsvc.piece_weight("яйцо отварное") == 55
    assert foodsvc.piece_weight("eggplant") is None and foodsvc.piece_weight("баклажан eggplant") is None
    assert foodsvc.piece_weight("наггетсы") == 25
    assert foodsvc.piece_weight("кусок пирога") == 100
    raw = _bare(name_ru="баклажан", name_en="eggplant")
    raw["items"][0]["amount"] = {"kind": "pieces", "pieces": 2}
    it = foodsvc.understanding_from_json(raw).items[0]
    assert (it.grams_min + it.grams_max) / 2 == pytest.approx(250)          # не 2 × 55


# ------------------------------------------------------------------ 3.8 MIME

def _urls(call):
    return [p["image_url"]["url"] for p in call["messages"][0]["content"] if p["type"] == "image_url"]


def test_image_mime_by_signature():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 20).decode()
    webp = base64.b64encode(b"RIFF\x10\0\0\0WEBPVP8 " + b"\0" * 20).decode()
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\0" * 20).decode()
    assert foodsvc.image_mime(png) == "image/png"
    assert foodsvc.image_mime(webp) == "image/webp"
    assert foodsvc.image_mime(jpeg) == "image/jpeg"
    assert foodsvc.image_mime("не base64!!") == "image/jpeg" and foodsvc.image_mime("") == "image/jpeg"


@pytest.mark.asyncio
async def test_request_uses_detected_mime(monkeypatch, plain_db):
    calls: list = []
    monkeypatch.setattr(llm, "chat_json", _capture(calls))
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 20).decode()
    await foodsvc.parse_photo(png, None, 1, plain_db)
    assert _urls(calls[0])[0].startswith("data:image/png;base64,")
    await foodsvc.parse_photo("AAAA", None, 1, plain_db)
    assert _urls(calls[1])[0].startswith("data:image/jpeg;base64,")
