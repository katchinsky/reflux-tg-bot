from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.models import ConversationState, User
from app.db.session import get_session


@dataclass
class DraftState:
    flow: str
    step: str
    draft: dict


class StateStore:
    def __init__(self, *, ttl_hours: int = 24) -> None:
        self._ttl = timedelta(hours=ttl_hours)

    def load(self, user: User, *, flow: str, now_utc: datetime) -> DraftState | None:
        now_naive = now_utc.replace(tzinfo=None) if now_utc.tzinfo else now_utc
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
            if row.expires_at < now_naive:
                session.delete(row)
                return None
            try:
                draft = json.loads(row.draft_json or "{}")
            except Exception:
                draft = {}
            return DraftState(flow=flow, step=row.step, draft=draft)

    def save(self, user: User, *, flow: str, step: str, draft: dict, now_utc: datetime) -> None:
        now_naive = now_utc.replace(tzinfo=None) if now_utc.tzinfo else now_utc
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
            row.updated_at = now_naive
            row.expires_at = now_naive + self._ttl

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


