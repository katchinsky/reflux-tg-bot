from __future__ import annotations

from sqlalchemy import select

from app.core.i18n import Lang
from app.db.models import User
from app.db.session import get_session


def _norm_lang(v: str | None) -> Lang:
    vv = (v or "").strip().lower()
    if vv == "ru":
        return "ru"
    return "en"


def ensure_user(telegram_user_id: int, *, default_timezone: str) -> User:
    with get_session() as session:
        user = (
            session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
            .scalar_one_or_none()
        )
        if user:
            return user
        user = User(telegram_user_id=telegram_user_id, timezone=default_timezone, language="en")
        session.add(user)
        session.flush()
        return user


def get_user(telegram_user_id: int) -> User | None:
    with get_session() as session:
        return (
            session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
            .scalar_one_or_none()
        )


def get_user_language(telegram_user_id: int) -> Lang:
    user = get_user(telegram_user_id)
    if not user:
        return "en"
    return _norm_lang(getattr(user, "language", None))


def set_user_language(telegram_user_id: int, *, lang: str, default_timezone: str) -> User:
    """
    Persist language preference for a user (creates user if missing).
    """
    ll = _norm_lang(lang)
    with get_session() as session:
        user = (
            session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
            .scalar_one_or_none()
        )
        if not user:
            user = User(telegram_user_id=telegram_user_id, timezone=default_timezone, language=ll)
            session.add(user)
            session.flush()
            return user
        user.language = ll
        session.add(user)
        session.flush()
        return user



