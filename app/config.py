"""Конфигурация приложения. Секреты — только из .env (pydantic-settings)."""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.timeutils import validate_hhmm, validate_timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore",
        env_ignore_empty=True,         # пустые строки из .env.example не ломают разбор чисел
        populate_by_name=True,
    )

    telegram_bot_token: str = ""
    allowed_tg_ids: str = ""          # "123456,789" — пусто = whitelist выключен (dev)
    allow_public_bot: bool = False
    openrouter_api_key: str = ""
    model_vision: str = "deepseek/deepseek-v4.1-flash-1017"
    model_vision_escalation: str = "deepseek/deepseek-v4.1-flash"
    model_text: str = "deepseek/deepseek-v4.1-flash-1017"   # по умолчанию DeepSeek, а не Google: в облаке .env нет
    model_text_provider: str = "DeepSeek"   # закрепить провайдера (без тихого перехода к другому); пусто — не закреплять
    model_text_reasoning: bool = False   # «думающий» режим текстовой модели: медленнее и дороже
    rich_messages: bool = True        # таблицы и абзацы через sendRichMessage (Bot API 10.1+); false = обычный текст
    model_dialog: str = "openai/gpt-6-luna"   # чат: разговор о тяге и срывах и ответы помощника; пусто — model_text
    model_asr: str = "google/gemini-2.5-flash"   # расшифровка голоса: нужна модель с аудио
    usda_api_key: str = "DEMO_KEY"
    picooc_user: str = ""
    picooc_password: str = ""
    picooc_owner_tg_id: int = 0       # чей это Picooc; 0 = единственный из ALLOWED_TG_IDS

    db_path: str = "./data/health.db"
    turso_database_url: str = ""            # libsql://<db>-<org>.turso.io: облачная БД вместо локального файла
    turso_auth_token: str = ""
    backup_dir: str = "./data/backups"      # лучше внешний диск или синхронизируемая папка
    photos_dir: str = "./data/photos"
    backup_in_process: bool = True          # false, если бэкап запускает systemd-таймер из deploy/
    tz: str = "Europe/Minsk"
    eating_cutoff_default: str = Field("21:00", validation_alias=AliasChoices(
        "EATING_CUTOFF_DEFAULT", "EATING_CUTOFF"))
    daily_kcal_target_default: int = 0  # 0 = посчитать по формулам
    log_level: str = "INFO"

    # поведение
    dedup_window_min: int = 10
    combo_window_sec: int = 90
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_timeout_s: float = 60.0
    max_media_bytes: int = 10 * 1024 * 1024

    # веб-сервер Mini App (рядом с polling в том же процессе)
    web_enabled: bool = False
    web_host: str = "0.0.0.0"
    web_port: int = Field(8080, validation_alias=AliasChoices("WEB_PORT", "PORT"))   # Render задаёт PORT
    webapp_url: str = ""              # публичный HTTPS-адрес приложения (…/app/) для кнопки меню
    webhook_base_url: str = ""        # https://<сервис>.onrender.com: если задан, бот работает по webhook, а не polling
    keepalive_window: str = ""        # «08:00-23:30»: в эти часы (по keepalive_tz) бот пингует сам себя, чтобы Render не усыплял
    keepalive_tz: str = "Europe/Minsk"
    webhook_secret: str = ""          # секрет заголовка Telegram (A-Za-z0-9_-, до 256 символов)
    web_dev_tg_id: int = 0            # только локальная разработка: запросы с 127.0.0.1 без initData

    @field_validator("tz")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        return validate_timezone(value)

    @field_validator("eating_cutoff_default")
    @classmethod
    def _valid_cutoff(cls, value: str) -> str:
        return validate_hhmm(value)

    @property
    def webhook_secret_value(self) -> str:
        """Секрет вебхука: из WEBHOOK_SECRET или производный от токена бота (стабилен между рестартами)."""
        if self.webhook_secret:
            return self.webhook_secret
        import hashlib
        return hashlib.sha256(("wh:" + self.telegram_bot_token).encode()).hexdigest()[:48]

    @property
    def allowed_ids(self) -> set[int]:
        out = set()
        for part in self.allowed_tg_ids.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out

    @property
    def picooc_owner_id(self) -> int | None:
        """Логин Picooc в .env один, поэтому его данные получает только владелец.
        Без явного PICOOC_OWNER_TG_ID владельцем считается единственный пользователь
        из whitelist; при нескольких людях или публичном боте синк закрыт."""
        if self.picooc_owner_tg_id:
            return self.picooc_owner_tg_id
        allowed = self.allowed_ids
        return next(iter(allowed)) if len(allowed) == 1 else None

    def require_runtime_secrets(self) -> None:
        """Падение на старте бота (не check.py) без внятного сообщения недопустимо."""
        missing = [n for n in ("telegram_bot_token", "openrouter_api_key") if not getattr(self, n)]
        if missing:
            raise SystemExit(
                f"Не заполнены обязательные ключи в .env: {', '.join(missing)}. "
                "Смотри .env.example."
            )
        if not self.allowed_ids and not self.allow_public_bot:
            raise SystemExit(
                "ALLOWED_TG_IDS обязателен для приватного дневника. "
                "Для осознанно публичного теста задай ALLOW_PUBLIC_BOT=true."
            )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return value[:3] + "…" + value[-2:] if len(value) > 8 else "…"
