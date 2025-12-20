from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import ConversationState, User
from app.db.session import get_session


UTC = ZoneInfo("UTC")


def _as_utc_aware(dt: datetime) -> datetime:
    """
    Normalize datetimes for safe comparisons & persistence.

    - If `dt` is naive, we assume it is UTC and attach UTC tzinfo.
    - If `dt` is aware, we convert it to UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class DraftState:
    flow: str
    step: str
    draft: dict


class StateStore:
    def __init__(self, *, ttl_hours: int = 24) -> None:
        self._ttl = timedelta(hours=ttl_hours)

    def load(self, user: User, *, flow: str, now_utc: datetime) -> DraftState | None:
        now = _as_utc_aware(now_utc)
        with get_session() as session:
            row = (
                session.execute(
                    select(ConversationState).where(
                        ConversationState.user_id == user.id,
                        ConversationState.flow == flow,
                    )
                )
                .scalar_one_or_none()
            )
            if not row:
                return None
            expires_at = _as_utc_aware(row.expires_at)
            if expires_at < now:
                session.delete(row)
                return None
            try:
                draft = json.loads(row.draft_json or "{}")
            except Exception:
                draft = {}
            return DraftState(flow=flow, step=row.step, draft=draft)

    def save(self, user: User, *, flow: str, step: str, draft: dict, now_utc: datetime) -> None:
        now = _as_utc_aware(now_utc)
        with get_session() as session:
            row = (
                session.execute(
                    select(ConversationState).where(
                        ConversationState.user_id == user.id,
                        ConversationState.flow == flow,
                    )
                )
                .scalar_one_or_none()
            )
            if not row:
                row = ConversationState(user_id=user.id, flow=flow)
                session.add(row)
            row.step = step
            row.draft_json = json.dumps(draft, ensure_ascii=False)
            row.updated_at = now
            row.expires_at = now + self._ttl

    def clear(self, user: User, *, flow: str) -> None:
        with get_session() as session:
            row = (
                session.execute(
                    select(ConversationState).where(
                        ConversationState.user_id == user.id,
                        ConversationState.flow == flow,
                    )
                )
                .scalar_one_or_none()
            )
            if row:
                session.delete(row)


