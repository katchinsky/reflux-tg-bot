#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

# Ensure we import the local repo `app/` package (not a site-packages-installed one)
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import load_settings  # noqa: E402
from app.db.models import Meal, MealItem, User  # noqa: E402
from app.db.session import get_session, init_db  # noqa: E402
from app.services.meal_taxonomy import process_meal  # noqa: E402


@dataclass(frozen=True)
class BackfillRow:
    meal_id: str
    user_id: str
    occurred_at: datetime
    notes_text: str
    lang: str


def _iter_meals(*, limit: int | None) -> list[BackfillRow]:
    rows: list[BackfillRow] = []
    with get_session() as session:
        q = (
            select(Meal.id, Meal.user_id, Meal.occurred_at, Meal.notes_text, User.language)
            .join(User, User.id == Meal.user_id)
            .order_by(Meal.occurred_at.asc())
        )
        if limit is not None and limit > 0:
            q = q.limit(limit)
        for meal_id, user_id, occurred_at, notes_text, language in session.execute(q).all():
            lang = (language or "en").strip().lower()
            if lang not in ("en", "ru"):
                lang = "en"
            rows.append(
                BackfillRow(
                    meal_id=str(meal_id),
                    user_id=str(user_id),
                    occurred_at=occurred_at,
                    notes_text=str(notes_text or ""),
                    lang=lang,
                )
            )
    return rows


def _already_processed(meal_id: str) -> bool:
    with get_session() as session:
        got = session.execute(select(MealItem.id).where(MealItem.meal_id == meal_id).limit(1)).first()
        return got is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill meal taxonomy extraction+linking for existing meals.")
    ap.add_argument("--limit", type=int, default=0, help="Process only N meals (0 = all).")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Reprocess meals even if meal_items already exist (will replace existing).",
    )
    ap.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between meals (rate limiting).")
    args = ap.parse_args()

    settings = load_settings()
    init_db(settings.database_url, settings.db_path)

    api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY", "").strip() or None
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required for backfill.")

    limit = args.limit if args.limit and args.limit > 0 else None
    meals = _iter_meals(limit=limit)
    if not meals:
        print("No meals found.")
        return 0

    processed = 0
    skipped = 0
    failed = 0

    for i, m in enumerate(meals, start=1):
        if not args.force and _already_processed(m.meal_id):
            skipped += 1
            continue

        try:
            res = process_meal(
                user_id=m.user_id,
                meal_id=m.meal_id,
                notes_text=m.notes_text,
                lang=m.lang,
                openai_api_key=api_key,
                openai_model_extract=settings.openai_model_extract,
                openai_model_rerank=settings.openai_model_rerank,
            )
            processed += 1
            print(
                f"[{i}/{len(meals)}] meal={m.meal_id} at={m.occurred_at.isoformat()} "
                f"lang={m.lang} items={len(res)}"
            )
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[{i}/{len(meals)}] meal={m.meal_id} FAILED: {e}")

        if args.sleep and args.sleep > 0:
            time.sleep(args.sleep)

    print(
        f"Done. processed={processed} skipped={skipped} failed={failed} total={len(meals)} "
        f"force={bool(args.force)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


