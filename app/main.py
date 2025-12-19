from __future__ import annotations

import logging
import warnings

from telegram.ext import Application
from telegram.warnings import PTBUserWarning

from app.bot.handlers import build_handlers
from app.core.config import load_settings
from app.db.session import init_db


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("reflux-bot")


def main() -> None:
    # PTB warns about ConversationHandler per_* settings when using CallbackQueryHandler.
    # Our flows intentionally use per_message=False because they also rely on text/photo messages.
    warnings.filterwarnings(
        "ignore",
        category=PTBUserWarning,
        message=r"If 'per_message=False'.*",
    )

    settings = load_settings()
    init_db(settings.db_path)

    app = Application.builder().token(settings.bot_token).build()
    build_handlers(app, default_timezone=settings.default_timezone)

    logger.info("Starting bot (polling)...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()


