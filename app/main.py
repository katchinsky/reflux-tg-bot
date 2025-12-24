from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
    dashboard_session_secret: str | None,
    dashboard_session_days: int,
    dashboard_bot_api_key: str | None,
    dashboard_code_ttl_minutes: int,
    default_timezone: str,
) -> HTTPServer:
    """
    Start a minimal HTTP server for Render-style routing/health checks + Telegram webhook.

    We keep it dependency-free (stdlib `http.server`) and forward webhook updates into
    python-telegram-bot's Application via the running asyncio loop.
    """
    normalized_path = (webhook_path or "/telegram").strip() or "/telegram"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    # Local static asset path (we run from source in this repo).
    static_root = Path(__file__).resolve().parent / "dashboard" / "static"

    class Handler(BaseHTTPRequestHandler):
        def _client_ip(self) -> str:
            # Prefer proxy header if present (common on Render/NGINX).
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if xff:
                return xff
            return str(self.client_address[0])

        def _is_https(self) -> bool:
            proto = (self.headers.get("X-Forwarded-Proto") or "").strip().lower()
            return proto == "https"

        def _parse_cookies(self) -> dict[str, str]:
            raw = self.headers.get("Cookie") or ""
            out: dict[str, str] = {}
            for part in raw.split(";"):
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
            return out

        def _read_body(self) -> bytes:
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError:
                length = 0
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _send_html(self, *, status: int, html: str, extra_headers: dict[str, str] | None = None) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, *, status: int, obj: object, extra_headers: dict[str, str] | None = None) -> None:
            import json

            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, *, location: str, extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()

        def _get_authed_user_id(self) -> str | None:
            if not dashboard_session_secret:
                return None
            from app.services.dashboard_auth import COOKIE_NAME, parse_and_verify_session_cookie

            cookies = self._parse_cookies()
            raw = cookies.get(COOKIE_NAME) or ""
            sess = parse_and_verify_session_cookie(secret=dashboard_session_secret, cookie_value=raw)
            return sess.user_id if sess else None

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path

            if path in ("/", "/health", "/healthz"):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok")
                return

            if path == "/dashboard/login":
                from app.dashboard.templates import render_login

                configured = bool(dashboard_session_secret)
                self._send_html(status=200, html=render_login(configured=configured))
                return

            if path in ("/dashboard", "/dashboard/"):
                user_id = self._get_authed_user_id()
                if not user_id:
                    self._redirect(location="/dashboard/login")
                    return
                from app.dashboard.templates import render_dashboard

                self._send_html(status=200, html=render_dashboard())
                return

            if path == "/static/dashboard.js":
                try:
                    data = (static_root / "dashboard.js").read_bytes()
                except Exception:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            if path.startswith("/api/dashboard/"):
                user_id = self._get_authed_user_id()
                if not user_id:
                    self._send_json(status=401, obj={"error": "unauthorized"})
                    return

                qs = parse_qs(parsed.query or "")
                from_date = (qs.get("from") or [""])[0]
                to_date = (qs.get("to") or [""])[0]
                symptom_type = (qs.get("symptom_type") or [""])[0] or None

                from app.services import dashboard_metrics

                if path == "/api/dashboard/product-categories":
                    self._send_json(
                        status=200,
                        obj=dashboard_metrics.product_categories(
                            user_id=user_id, from_date=from_date, to_date=to_date
                        ),
                    )
                    return
                if path == "/api/dashboard/symptoms":
                    self._send_json(
                        status=200,
                        obj=dashboard_metrics.symptoms(
                            user_id=user_id, from_date=from_date, to_date=to_date, symptom_type=symptom_type
                        ),
                    )
                    return
                if path == "/api/dashboard/correlations":
                    self._send_json(
                        status=200,
                        obj=dashboard_metrics.correlations(user_id=user_id, from_date=from_date, to_date=to_date),
                    )
                    return

                self._send_json(status=404, obj={"error": "not_found"})
                return

            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path

            if path == normalized_path:
                if webhook_secret_token:
                    got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
                    if got != webhook_secret_token:
                        self.send_response(403)
                        self.end_headers()
                        return

                raw = self._read_body()
                if not raw:
                    self.send_response(400)
                    self.end_headers()
                    return
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
                return

            if path == "/auth/logout":
                from app.services.dashboard_auth import COOKIE_NAME

                extra = {
                    "Set-Cookie": f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                }
                if self._is_https():
                    extra["Set-Cookie"] += "; Secure"
                self._redirect(location="/dashboard/login", extra_headers=extra)
                return

            if path == "/auth/code-login":
                from app.dashboard.templates import render_login
                from app.services.dashboard_auth import COOKIE_NAME, allow_login_attempt, make_session_cookie_value
                from app.services.dashboard_codes import consume_login_code

                if not dashboard_session_secret:
                    self._send_html(status=500, html=render_login(configured=False))
                    return

                ip = self._client_ip()
                if not allow_login_attempt(key=ip):
                    self._send_html(status=429, html=render_login(error="Too many attempts. Try again later."))
                    return

                raw = self._read_body()
                ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                code = ""
                if ctype == "application/json":
                    try:
                        import json

                        obj = json.loads(raw.decode("utf-8") or "{}")
                        code = str(obj.get("code") or "")
                    except Exception:
                        code = ""
                else:
                    # Assume form body
                    try:
                        form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                        code = (form.get("code") or [""])[0]
                    except Exception:
                        code = ""

                user_id = consume_login_code(code=code)
                if not user_id:
                    self._send_html(status=401, html=render_login(error="Invalid or expired code."))
                    return

                exp = int((datetime.utcnow() + timedelta(days=max(1, int(dashboard_session_days)))).timestamp())
                cookie_val = make_session_cookie_value(
                    secret=dashboard_session_secret, user_id=user_id, exp_epoch_s=exp
                )
                cookie = f"{COOKIE_NAME}={cookie_val}; Path=/; HttpOnly; SameSite=Lax"
                if self._is_https():
                    cookie += "; Secure"
                self._redirect(location="/dashboard", extra_headers={"Set-Cookie": cookie})
                return

            if path == "/api/dashboard/create-code":
                # Bot-only endpoint (optional; satisfies dashboard.md contract).
                want = (dashboard_bot_api_key or "").strip()
                got = (self.headers.get("X-Dashboard-Bot-Key") or "").strip()
                if not want or got != want:
                    self._send_json(status=403, obj={"error": "forbidden"})
                    return

                raw = self._read_body()
                try:
                    import json

                    obj = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    obj = {}
                try:
                    telegram_user_id = int(obj.get("telegram_user_id"))
                except Exception:
                    self._send_json(status=400, obj={"error": "telegram_user_id is required"})
                    return

                from app.services.users import ensure_user
                from app.services.dashboard_codes import create_login_code

                user = ensure_user(telegram_user_id, default_timezone=default_timezone)
                created = create_login_code(
                    user_id=user.id,
                    ttl_minutes=int(dashboard_code_ttl_minutes),
                    length=6,
                )
                self._send_json(
                    status=200,
                    obj={
                        "code": created.code,
                        "expires_at": created.expires_at.isoformat(),
                    },
                )
                return

            self.send_response(404)
            self.end_headers()

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
        dashboard_session_secret=settings.dashboard_session_secret,
        dashboard_session_days=settings.dashboard_session_days,
        dashboard_bot_api_key=settings.dashboard_bot_api_key,
        dashboard_code_ttl_minutes=settings.dashboard_code_ttl_minutes,
        default_timezone=settings.default_timezone,
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
    # Also start HTTP server for dashboard + health checks in polling mode.
    port_raw = os.getenv("PORT", "").strip() or "8080"
    port = int(port_raw)
    _start_http_server(
        port=port,
        webhook_path=settings.webhook_path,
        loop=asyncio.new_event_loop(),  # webhook path is unused in polling mode
        app=app,
        webhook_secret_token=settings.webhook_secret_token,
        dashboard_session_secret=settings.dashboard_session_secret,
        dashboard_session_days=settings.dashboard_session_days,
        dashboard_bot_api_key=settings.dashboard_bot_api_key,
        dashboard_code_ttl_minutes=settings.dashboard_code_ttl_minutes,
        default_timezone=settings.default_timezone,
    )
    logger.info("Starting bot (polling fallback; set WEBHOOK_URL to enable webhooks)...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
