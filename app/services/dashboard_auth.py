from __future__ import annotations

import base64
import hmac
import json
import threading
import time
from dataclasses import dataclass
from hashlib import sha256


COOKIE_NAME = "reflux_dash_session"


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _sign(secret: str, payload_b64: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), sha256).digest()
    return _b64u_encode(mac)


@dataclass(frozen=True)
class SessionData:
    user_id: str
    exp_epoch_s: int


def make_session_cookie_value(*, secret: str, user_id: str, exp_epoch_s: int) -> str:
    payload = json.dumps({"uid": user_id, "exp": int(exp_epoch_s)}, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64u_encode(payload)
    sig = _sign(secret, payload_b64)
    return f"{payload_b64}.{sig}"


def parse_and_verify_session_cookie(*, secret: str, cookie_value: str, now_epoch_s: int | None = None) -> SessionData | None:
    now = int(now_epoch_s if now_epoch_s is not None else time.time())
    raw = (cookie_value or "").strip()
    if "." not in raw:
        return None
    payload_b64, sig = raw.split(".", 1)
    expected = _sign(secret, payload_b64)
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64u_decode(payload_b64))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    uid = str(payload.get("uid") or "").strip()
    exp = payload.get("exp")
    try:
        exp_i = int(exp)
    except Exception:
        return None
    if not uid or exp_i < now:
        return None
    return SessionData(user_id=uid, exp_epoch_s=exp_i)


# --- basic in-memory rate limiter for /auth/code-login ---

_rl_lock = threading.Lock()
_rl_hits: dict[str, list[float]] = {}


def allow_login_attempt(*, key: str, limit: int = 12, window_s: int = 300, now_s: float | None = None) -> bool:
    """
    key: usually an IP string (or ip+ua).
    """
    now = float(now_s if now_s is not None else time.time())
    cutoff = now - float(window_s)
    with _rl_lock:
        xs = _rl_hits.get(key, [])
        xs = [t for t in xs if t >= cutoff]
        if len(xs) >= limit:
            _rl_hits[key] = xs
            return False
        xs.append(now)
        _rl_hits[key] = xs
        return True


