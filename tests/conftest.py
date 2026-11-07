"""Общие настройки тестов: временная рабочая папка и конфиг без реального .env."""
import pytest

from app import config


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch):
    """Тесты не должны читать боевой .env (токены, whitelist, модель провайдера)."""
    # Старые тесты проверяют конвейер записи еды из чата: он включается chat_logs_food. Режим
    # «чат только для разговора» (значение по умолчанию) покрыт в tests/test_chat_discussion.py.
    monkeypatch.setattr(config, "_config", config.Config(_env_file=None, chat_logs_food=True,
                                                       allow_public_bot=True))


@pytest.fixture(autouse=True)
def _clear_text_parse_cache():
    from app.services import food
    food._TEXT_CACHE.clear()
    yield
    food._TEXT_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_network_llm(monkeypatch):
    """По умолчанию модель «недоступна»: тесты не ходят в сеть и проверяют запасной путь.
    Тест, которому нужен ответ модели, подменяет chat_json сам."""
    from app.llm import client

    async def offline(*a, **k):
        raise client.LLMError("сеть отключена в тестах")
    monkeypatch.setattr(client, "chat_json", offline)
