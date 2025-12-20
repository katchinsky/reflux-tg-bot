from __future__ import annotations

import logging
import os
import threading
import warnings

from http.server import BaseHTTPRequestHandler, HTTPServer

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


def _start_health_server_from_env() -> None:
    """
    Start a minimal HTTP server for Render-style health checks.

    Render Web Services expect the process to listen on $PORT. Our bot uses polling,
    so we spin up a tiny in-process server only when PORT is provided.
    """
    port_raw = os.getenv("PORT", "").strip()
    if not port_raw:
        return

    port = int(port_raw)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/health", "/healthz"):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            # Silence default http.server logs; our bot has its own logger.
            return

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server listening on 0.0.0.0:%s", port)


def main() -> None:
    # PTB warns about ConversationHandler per_* settings when using CallbackQueryHandler.
    # Our flows intentionally use per_message=False because they also rely on text/photo messages.
    warnings.filterwarnings(
        "ignore",
        category=PTBUserWarning,
        message=r"If 'per_message=False'.*",
    )

    settings = load_settings()
    init_db(settings.database_url, settings.db_path)

    _start_health_server_from_env()

    app = Application.builder().token(settings.bot_token).build()
    build_handlers(app, default_timezone=settings.default_timezone)

    logger.info("Starting bot (polling)...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()


