from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str | None
    db_path: str
    default_timezone: str
    webhook_url: str | None
    webhook_path: str
    webhook_secret_token: str | None
    openai_api_key: str | None
    openai_model_extract: str
    openai_model_rerank: str


def load_settings() -> Settings:
    # Supports running with either `.env` present or purely env-driven.
    load_dotenv(override=False)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required (set it in environment or .env).")

    database_url = os.getenv("DATABASE_URL", "").strip() or None

    return Settings(
        bot_token=bot_token,
        database_url=database_url,
        db_path=os.getenv("DB_PATH", "reflux.db").strip() or "reflux.db",
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Belgrade").strip()
        or "Europe/Belgrade",
        webhook_url=os.getenv("WEBHOOK_URL", "").strip() or None,
        webhook_path=os.getenv("WEBHOOK_PATH", "/telegram").strip() or "/telegram",
        webhook_secret_token=os.getenv("WEBHOOK_SECRET_TOKEN", "").strip() or None,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
        openai_model_extract=os.getenv("OPENAI_MODEL_EXTRACT", "gpt-4o-mini").strip() or "gpt-4o-mini",
        openai_model_rerank=os.getenv("OPENAI_MODEL_RERANK", "gpt-4o-mini").strip() or "gpt-4o-mini",
    )


