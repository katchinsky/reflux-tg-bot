from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: str
    default_timezone: str


def load_settings() -> Settings:
    # Supports running with either `.env` present or purely env-driven.
    load_dotenv(override=False)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required (set it in environment or .env).")

    return Settings(
        bot_token=bot_token,
        db_path=os.getenv("DB_PATH", "reflux.db").strip() or "reflux.db",
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Belgrade").strip()
        or "Europe/Belgrade",
    )


