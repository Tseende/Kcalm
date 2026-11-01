"""Дополнение к embedding_eval: задержка, более низкие пороги, угон записей еды и классификатор
на LLM (DeepSeek) для сравнения. Использует кэш векторов, новых платных вызовов мало."""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import embedding_eval as ev                      # noqa: E402
import eval_dataset as ds                        # noqa: E402
from app.config import get_config                # noqa: E402
from app.llm import client as llm                # noqa: E402

CLASS_HELP = {
    "today": "итоги сегодняшнего дня по еде и калориям",
    "week": "итоги или статистика за неделю",
    "weight": "вес и его динамика",
    "sleep": "сон",
    "target": "дневная цель/норма калорий и белка",
    "undo": "отменить или удалить последнюю запись еды",
    "water_status": "сколько воды выпито",
    "body": "состав тела с весов: жир, мышцы, висцеральный жир, оценка",
    "foods": "список частых продуктов пользователя",
    "plan_list": "список личных правил «если-то»",
    "care_on": "включить осторожный/щадящий режим",
    "care_off": "выключить осторожный режим",
    "weighin": "настроить напоминание о взвешивании (включить, выключить, время, дни)",
    "help": "что умеет бот, как им пользоваться",
    "food_info": "узнать калорийность или БЖУ конкретного продукта",
    "craving": "тянет переесть, срыв, не может остановиться, хочется еды прямо сейчас",
    "question": "вопрос о питании, похудении, советах, причинах (ответит ассистент)",
    "chat": "приветствие, благодарность, эмоция, реплика или комментарий без запроса",
    "food": "запись того, что человек съел или выпил (приём пищи)",
}


def latency(model: str, n: int = 5) -> tuple[float, float]:
    cfg = get_config()
    times = []
    for i in range(n):
        t = time.time()
        r = httpx.post(f"{cfg.openrouter_base_url}/embeddings",
                       headers={"Authorization": f"Bearer {cfg.openrouter_api_key}"},
                       json={"model": model, "input": [f"тестовая фраза номер {i} про калории"]},
                       timeout=60)
        r.raise_for_status()
        times.append(time.time() - t)
    return statistics.median(times), max(times)


async def llm_classify(tests: list[tuple[str, str]]) -> tuple[list[str], list[float], int, int]:
    system = ("Ты — маршрутизатор сообщений бота-дневника питания. Определи, что хочет человек. "
              "Верни СТРОГО JSON {\"intent\": \"<класс>\"}. Классы:\n" +
              "\n".join(f"- {k}: {v}" for k, v in CLASS_HELP.items()) +
              "\nЕсли сообщение описывает съеденное или выпитое, это food. Реплики без просьбы "
              "и эмоции — chat. Нужно ровно одно имя класса.")
    sem = asyncio.Semaphore(8)
    preds: list[str] = [""] * len(tests)
    lat: list[float] = [0.0] * len(tests)
    toks = [0, 0]

    async def one(i: int, text: str) -> None:
        async with sem:
            t = time.time()
            try:
                data, _ = await llm.chat_json("eval_router", [
                    {"role": "system", "content": system}, {"role": "user", "content": text}],
                    temperature=0.0)
                label = str(data.get("intent", "")).strip()
            except Exception as e:  # noqa: BLE001
                label = f"ERR:{type(e).__name__}"
            lat[i] = time.time() - t
            preds[i] = label if label in CLASS_HELP else "food"

    await asyncio.gather(*(one(i, t) for i, (_, t) in enumerate(tests)))
    return preds, lat, toks[0], toks[1]


def main() -> None:
    models = ["qwen/qwen3-embedding-8b", "perplexity/pplx-embed-v1-0.6b"]
    print("ЗАДЕРЖКА одного запроса эмбеддинга (медиана / максимум, с):")
    for m in models:
        med, mx = latency(m)
        print(f"  {m}: {med:.2f} / {mx:.2f}")

    tests_all = [(l, t) for l, ts in ds.TESTS.items() for t in ts if l != "water_log"]
    for m in models:
        emb = ev.Embedder(m)
        ph = ev.evaluate_phrases(emb)
        print(f"\n{m}: гибрид при более низких порогах / какие записи еды угоняет")
        for thr in (0.25, 0.3, 0.35, 0.4, 0.45, 0.5):
            h = ev.summarize(ph["tests"], ph["hybrid"](thr))
            print(f"  порог {thr:.2f}: точность {h['acc']:.0%} | мимо {h['missed']:.0%} | не та "
                  f"{h['wrong_cmd']:.0%} | угон {h['hijack']:.0%} | угон еды {h['food_hijack']:.0%}")
        for thr in (0.65, 0.7, 0.75):
            preds = ph["hybrid"](thr)
            hijacked = [(t[1], p) for t, p in zip(ph["tests"], preds)
                        if t[0] == "food" and p in ev.COMMANDS]
            if hijacked:
                print(f"  угнанная еда при {thr}: {hijacked}")

    print("\nКЛАССИФИКАТОР НА LLM (DeepSeek), те же фразы без прототипов:")
    t0 = time.time()
    preds, lat, _, _ = asyncio.run(llm_classify(tests_all))
    s = ev.summarize(tests_all, preds)
    print(f"точность {s['acc']:.0%} | мимо {s['missed']:.0%} | не та команда {s['wrong_cmd']:.0%} | "
          f"угон {s['hijack']:.0%} (еда {s['food_hijack']:.0%}) | задержка медиана "
          f"{statistics.median(lat):.1f} с, макс {max(lat):.1f} с | всего {time.time() - t0:.0f} с")
    pc = ev.per_class(tests_all, preds)
    print("по классам:", ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in pc.items()))
    print("ошибки:")
    for (exp, text), got in zip(tests_all, preds):
        if exp != got:
            print(f"  «{text}»: {exp} → {got}")

    print("\nГИБРИД: регулярки → если не решили, LLM-классификатор:")
    hyb = [ev.route_regex(t) if ev.decisive(ev.route_regex(t)) else p
           for (_, t), p in zip(tests_all, preds)]
    s = ev.summarize(tests_all, hyb)
    print(f"точность {s['acc']:.0%} | мимо {s['missed']:.0%} | не та {s['wrong_cmd']:.0%} | "
          f"угон {s['hijack']:.0%} (еда {s['food_hijack']:.0%})")


if __name__ == "__main__":
    main()
