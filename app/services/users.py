from __future__ import annotations

from sqlalchemy import select

from app.db.models import User
from app.db.session import get_session


def ensure_user(telegram_user_id: int, *, default_timezone: str) -> User:
    with get_session() as session:
        user = (
            session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
            .scalar_one_or_none()
        )
        if user:
            return user
        user = User(telegram_user_id=telegram_user_id, timezone=default_timezone)
        session.add(user)
        session.flush()
        return user


def get_user(telegram_user_id: int) -> User | None:
    with get_session() as session:
        return (
            session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
            .scalar_one_or_none()
        )



