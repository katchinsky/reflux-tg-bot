from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.models import LoginCode
from app.db.session import get_session


ALPHABET = "23456789" + string.ascii_uppercase  # omit 0/1 to reduce confusion


@dataclass(frozen=True)
class CreatedLoginCode:
    code: str
    expires_at: datetime


def _now_utc() -> datetime:
    # Keep consistent with the rest of the codebase: treat naive as UTC-ish.
    return datetime.utcnow()


def _gen_code(*, length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def create_login_code(*, user_id: str, ttl_minutes: int = 15, length: int = 6) -> CreatedLoginCode:
    """
    Create a short-lived, one-time login code.
    Stored uppercase; validation is case-insensitive.
    """
    length = max(6, min(int(length), 10))
    ttl_minutes = max(1, min(int(ttl_minutes), 60))
    now = _now_utc()
    expires_at = now + timedelta(minutes=ttl_minutes)

    # Collision is unlikely, but handle it defensively.
    for _ in range(12):
        code = _gen_code(length=length).upper()
        with get_session() as session:
            exists = (
                session.execute(select(LoginCode.id).where(LoginCode.code == code)).scalar_one_or_none()
                is not None
            )
            if exists:
                continue
            row = LoginCode(user_id=user_id, code=code, expires_at=expires_at, used_at=None, created_at=now)
            session.add(row)
            session.flush()
            return CreatedLoginCode(code=code, expires_at=expires_at)

    raise RuntimeError("Failed to generate a unique login code (too many collisions).")


def consume_login_code(*, code: str, now_utc: datetime | None = None) -> str | None:
    """
    If valid, mark code as used and return user_id.
    """
    now = now_utc or _now_utc()
    normalized = (code or "").strip().upper()
    if not normalized:
        return None

    with get_session() as session:
        row = (
            session.execute(
                select(LoginCode).where(
                    LoginCode.code == normalized,
                    LoginCode.used_at.is_(None),
                    LoginCode.expires_at >= now,
                )
            )
            .scalars()
            .first()
        )
        if not row:
            return None
        row.used_at = now
        session.add(row)
        session.flush()
        return row.user_id


