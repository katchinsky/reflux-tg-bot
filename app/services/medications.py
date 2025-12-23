from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from app.db.models import Medication, User
from app.db.session import get_session


def top_medication_names(user: User, *, limit: int = 3, recent_limit: int = 500) -> list[str]:
    """
    Return up to `limit` most common medication names the user has previously logged.

    Notes:
    - Count is case-insensitive and whitespace-trimmed.
    - Display name is the most recent spelling the user used for that medication.
    - We only scan the most recent `recent_limit` rows for speed.
    """
    if limit <= 0:
        return []

    with get_session() as session:
        rows = (
            session.execute(
                select(Medication.name)
                .where(Medication.user_id == user.id)
                .order_by(Medication.taken_at.desc())
                .limit(recent_limit)
            )
            .scalars()
            .all()
        )

    # Keep most-recent spelling for each normalized key.
    display_by_norm: dict[str, str] = {}
    norm_order: list[str] = []
    counts: Counter[str] = Counter()
    for raw in rows:
        name = (raw or "").strip()
        if not name:
            continue
        norm = name.casefold()
        counts[norm] += 1
        if norm not in display_by_norm:
            display_by_norm[norm] = name
            norm_order.append(norm)

    if not counts:
        return []

    # Sort by count desc; tie-break by recency (lower index in norm_order => more recent).
    recency_rank = {norm: i for i, norm in enumerate(norm_order)}
    norms_sorted = sorted(counts.keys(), key=lambda n: (-counts[n], recency_rank.get(n, 10**9)))
    return [display_by_norm[n] for n in norms_sorted[:limit] if n in display_by_norm]


