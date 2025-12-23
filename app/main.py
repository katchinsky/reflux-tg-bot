from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import warnings

from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
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


def _start_http_server(
    *,
    port: int,
    webhook_path: str,
    loop: asyncio.AbstractEventLoop,
    app: Application,
    webhook_secret_token: str | None,
) -> HTTPServer:
    """
    Start a minimal HTTP server for Render-style routing/health checks + Telegram webhook.

    We keep it dependency-free (stdlib `http.server`) and forward webhook updates into
    python-telegram-bot's Application via the running asyncio loop.
    """
    normalized_path = (webhook_path or "/telegram").strip() or "/telegram"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

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

        def do_POST(self) -> None:  # noqa: N802
            if self.path != normalized_path:
                self.send_response(404)
                self.end_headers()
                return

            if webhook_secret_token:
                got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
                if got != webhook_secret_token:
                    self.send_response(403)
                    self.end_headers()
                    return

            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError:
                length = 0
            if length <= 0:
                self.send_response(400)
                self.end_headers()
                return

            raw = self.rfile.read(length)
            try:
                payload = raw.decode("utf-8")
            except UnicodeDecodeError:
                self.send_response(400)
                self.end_headers()
                return

            try:
                update = Update.de_json(__import__("json").loads(payload), app.bot)
            except Exception:
                logger.exception("Failed to decode incoming webhook update")
                self.send_response(400)
                self.end_headers()
                return

            # Schedule processing on the main asyncio loop and return immediately.
            asyncio.run_coroutine_threadsafe(app.process_update(update), loop)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args) -> None:
            # Silence default http.server logs; our bot has its own logger.
            return

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("HTTP server listening on 0.0.0.0:%s (webhook path: %s)", port, normalized_path)
    return server


async def _run_webhook(settings) -> None:
    port_raw = os.getenv("PORT", "").strip() or "8080"
    port = int(port_raw)

    app = Application.builder().token(settings.bot_token).build()
    build_handlers(app, default_timezone=settings.default_timezone)

    await app.initialize()
    await app.start()

    loop = asyncio.get_running_loop()
    server = _start_http_server(
        port=port,
        webhook_path=settings.webhook_path,
        loop=loop,
        app=app,
        webhook_secret_token=settings.webhook_secret_token,
    )

    webhook_base = (settings.webhook_url or "").rstrip("/")
    webhook_path = settings.webhook_path if settings.webhook_path.startswith("/") else f"/{settings.webhook_path}"
    webhook_full_url = f"{webhook_base}{webhook_path}"

    logger.info("Setting Telegram webhook to %s", webhook_full_url)
    await app.bot.set_webhook(
        url=webhook_full_url,
        drop_pending_updates=True,
        secret_token=settings.webhook_secret_token,
    )

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Some platforms (e.g., Windows) don't support signal handlers in asyncio.
            pass

    await stop_event.wait()

    server.shutdown()
    server.server_close()
    await app.stop()
    await app.shutdown()


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

    if settings.webhook_url:
        logger.info("Starting bot (webhook mode)...")
        asyncio.run(_run_webhook(settings))
        return

    # Local/dev fallback.
    app = Application.builder().token(settings.bot_token).build()
    build_handlers(app, default_timezone=settings.default_timezone)
    logger.info("Starting bot (polling fallback; set WEBHOOK_URL to enable webhooks)...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
