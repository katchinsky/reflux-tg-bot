from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.i18n import fat_label, portion_label, posture_label, symptom_type_label
from app.db.models import Meal, MealItem, MealItemCategory, Symptom, User
from app.db.session import get_session
from app.services.taxonomy_index import get_taxonomy_index


UTC = ZoneInfo("UTC")


def _as_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_date(s: str) -> date:
    return date.fromisoformat((s or "").strip())


def _date_range_to_utc(
    user_tz: ZoneInfo,
    from_s: str,
    to_s: str,
    *,
    now_utc: datetime | None = None,
) -> tuple[datetime, datetime, date, date]:
    now = _as_utc_aware(now_utc or datetime.now(tz=UTC))
    try:
        d_from = _parse_date(from_s)
        d_to = _parse_date(to_s)
    except Exception:
        # Default to last 7 days (inclusive) in user's local timezone.
        today_local = now.astimezone(user_tz).date()
        d_to = today_local
        d_from = today_local - timedelta(days=6)
    if d_to < d_from:
        d_from, d_to = d_to, d_from

    start_local = datetime.combine(d_from, time(0, 0), tzinfo=user_tz)
    end_excl_local = datetime.combine(d_to + timedelta(days=1), time(0, 0), tzinfo=user_tz)
    return (start_local.astimezone(UTC), end_excl_local.astimezone(UTC), d_from, d_to)


def _load_user(user_id: str) -> User:
    with get_session() as session:
        user = session.execute(select(User).where(User.id == user_id)).scalar_one()
        return user


def _meals_in_range(*, user_id: str, start_utc: datetime, end_excl_utc: datetime) -> list[Meal]:
    with get_session() as session:
        return (
            session.execute(
                select(Meal).where(
                    Meal.user_id == user_id,
                    Meal.occurred_at >= start_utc,
                    Meal.occurred_at < end_excl_utc,
                )
            )
            .scalars()
            .all()
        )


def _symptoms_in_range(*, user_id: str, start_utc: datetime, end_excl_utc: datetime, symptom_type: str | None = None) -> list[Symptom]:
    stmt = select(Symptom).where(
        Symptom.user_id == user_id,
        Symptom.started_at >= start_utc,
        Symptom.started_at < end_excl_utc,
    )
    if symptom_type:
        stmt = stmt.where(Symptom.symptom_type == symptom_type)
    with get_session() as session:
        return session.execute(stmt).scalars().all()


def _meal_has_symptom_map(
    *,
    meals: list[Meal],
    symptoms: list[Symptom],
    window_hours: int,
) -> dict[str, bool]:
    window = timedelta(hours=window_hours)
    meals_sorted = sorted(meals, key=lambda m: _as_utc_aware(m.occurred_at))
    symptoms_sorted = sorted(symptoms, key=lambda s: _as_utc_aware(s.started_at))

    out: dict[str, bool] = {}
    j = 0
    for m in meals_sorted:
        m_at = _as_utc_aware(m.occurred_at)
        # Advance until symptom >= meal time
        while j < len(symptoms_sorted) and _as_utc_aware(symptoms_sorted[j].started_at) < m_at:
            j += 1
        has = False
        if j < len(symptoms_sorted):
            s_at = _as_utc_aware(symptoms_sorted[j].started_at)
            has = s_at <= (m_at + window)
        out[m.id] = has
    return out


def _category_meals_map(
    *, user_id: str, start_utc: datetime, end_excl_utc: datetime
) -> dict[str, set[str]]:
    """
    category_id -> set(meal_id) for rank=1 categories in the date range.
    """
    with get_session() as session:
        rows = session.execute(
            select(Meal.id, MealItemCategory.category_id)
            .select_from(Meal)
            .join(MealItem, MealItem.meal_id == Meal.id)
            .join(MealItemCategory, MealItemCategory.meal_item_id == MealItem.id)
            .where(
                Meal.user_id == user_id,
                Meal.occurred_at >= start_utc,
                Meal.occurred_at < end_excl_utc,
                MealItemCategory.rank == 1,
            )
        ).all()
    out: dict[str, set[str]] = defaultdict(set)
    for meal_id, cid in rows:
        if cid:
            out[str(cid)].add(str(meal_id))
    return out


def product_categories(*, user_id: str, from_date: str, to_date: str, now_utc: datetime | None = None) -> dict:
    user = _load_user(user_id)
    tz = ZoneInfo(user.timezone or "UTC")
    start_utc, end_excl_utc, d_from, d_to = _date_range_to_utc(tz, from_date, to_date, now_utc=now_utc)

    meals = _meals_in_range(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    total_meals = len(meals)

    # Need symptoms up to end+window for symptom_window_rate.
    window_hours = 4
    symptoms = _symptoms_in_range(
        user_id=user_id,
        start_utc=start_utc,
        end_excl_utc=end_excl_utc + timedelta(hours=window_hours),
    )
    meal_has = _meal_has_symptom_map(meals=meals, symptoms=symptoms, window_hours=window_hours)

    cat_meals = _category_meals_map(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    idx = get_taxonomy_index()
    prefer_lang = getattr(user, "language", None) or "en"

    cats = []
    for cid, meal_ids in cat_meals.items():
        n = len(meal_ids)
        if n <= 0:
            continue
        with_sym = sum(1 for mid in meal_ids if meal_has.get(mid, False))
        cats.append(
            {
                "name": idx.get_label(cid, prefer_lang=prefer_lang),
                "meal_count": n,
                "share_pct": (100.0 * n / total_meals) if total_meals else 0.0,
                "symptom_window_rate_pct": (100.0 * with_sym / n) if n else 0.0,
            }
        )
    cats.sort(key=lambda r: (r["meal_count"], r["symptom_window_rate_pct"]), reverse=True)

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "total_meals": total_meals,
        "categories": cats[:30],
    }


def symptoms(*, user_id: str, from_date: str, to_date: str, symptom_type: str | None = None) -> dict:
    user = _load_user(user_id)
    tz = ZoneInfo(user.timezone or "UTC")
    start_utc, end_excl_utc, d_from, d_to = _date_range_to_utc(tz, from_date, to_date)
    syms = _symptoms_in_range(
        user_id=user_id,
        start_utc=start_utc,
        end_excl_utc=end_excl_utc,
        symptom_type=symptom_type,
    )

    by_day: dict[str, list[int]] = defaultdict(list)
    by_type_counter: dict[str, list[int]] = defaultdict(list)
    for s in syms:
        started_local = _as_utc_aware(s.started_at).astimezone(tz)
        d = started_local.date().isoformat()
        by_day[d].append(int(s.intensity))
        by_type_counter[str(s.symptom_type or "other")].append(int(s.intensity))

    # Fill missing days with 0s for nicer charts.
    daily_out = []
    cur = d_from
    while cur <= d_to:
        k = cur.isoformat()
        xs = by_day.get(k, [])
        daily_out.append(
            {
                "date": k,
                "count": len(xs),
                "avg_intensity": (sum(xs) / len(xs)) if xs else 0.0,
            }
        )
        cur = cur + timedelta(days=1)

    by_type_out = []
    for st, xs in by_type_counter.items():
        by_type_out.append(
            {
                "type": symptom_type_label(getattr(user, "language", None), st),
                "count": len(xs),
                "avg_intensity": (sum(xs) / len(xs)) if xs else 0.0,
            }
        )
    by_type_out.sort(key=lambda r: r["count"], reverse=True)

    buckets = [("0-3", 0, 3), ("4-6", 4, 6), ("7-10", 7, 10)]
    hist_counts = Counter()
    for s in syms:
        i = int(s.intensity)
        for label, lo, hi in buckets:
            if lo <= i <= hi:
                hist_counts[label] += 1
                break
    hist_out = [{"bucket": label, "count": int(hist_counts.get(label, 0))} for (label, _, _) in buckets]

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "daily": daily_out,
        "by_type": by_type_out,
        "intensity_histogram": hist_out,
    }


def correlations(*, user_id: str, from_date: str, to_date: str, now_utc: datetime | None = None) -> dict:
    user = _load_user(user_id)
    tz = ZoneInfo(user.timezone or "UTC")
    start_utc, end_excl_utc, d_from, d_to = _date_range_to_utc(tz, from_date, to_date, now_utc=now_utc)

    meals = _meals_in_range(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    window_hours = 4
    symptoms_all = _symptoms_in_range(
        user_id=user_id,
        start_utc=start_utc,
        end_excl_utc=end_excl_utc + timedelta(hours=window_hours),
    )
    meal_has = _meal_has_symptom_map(meals=meals, symptoms=symptoms_all, window_hours=window_hours)

    total_meals = len(meals)
    with_any = sum(1 for m in meals if meal_has.get(m.id, False))
    baseline = (with_any / total_meals) if total_meals else 0.0

    lang = getattr(user, "language", None)

    @dataclass(frozen=True)
    class _Row:
        feature_key: str
        label: str
        support_meals: int
        symptom_rate_pct: float
        delta_pp: float

    def _bucket(label_prefix: str, value_label: str, meal_ids: set[str]) -> _Row | None:
        n = len(meal_ids)
        if n < 4:
            return None
        with_sym = sum(1 for mid in meal_ids if meal_has.get(mid, False))
        rate = (with_sym / n) if n else 0.0
        return _Row(
            feature_key=f"{label_prefix}:{value_label}",
            label=f"{label_prefix.capitalize()}: {value_label}",
            support_meals=n,
            symptom_rate_pct=rate * 100.0,
            delta_pp=(rate - baseline) * 100.0,
        )

    rows: list[_Row] = []

    # Portion/fat/posture features
    portion_meals: dict[str, set[str]] = defaultdict(set)
    fat_meals: dict[str, set[str]] = defaultdict(set)
    posture_meals: dict[str, set[str]] = defaultdict(set)
    for m in meals:
        portion_meals[str(m.portion_size or "unknown")].add(m.id)
        fat_meals[str(m.fat_level or "unknown")].add(m.id)
        posture_meals[str(m.posture_after or "unknown")].add(m.id)

    for v, mids in portion_meals.items():
        lbl = portion_label(lang, v)
        r = _bucket("portion", lbl, mids)
        if r:
            rows.append(r)
    for v, mids in fat_meals.items():
        lbl = fat_label(lang, v)
        r = _bucket("fat", lbl, mids)
        if r:
            rows.append(r)
    for v, mids in posture_meals.items():
        lbl = posture_label(lang, v)
        r = _bucket("posture", lbl, mids)
        if r:
            rows.append(r)

    # Category features
    cat_meals = _category_meals_map(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    idx = get_taxonomy_index()
    for cid, mids in cat_meals.items():
        lbl = idx.get_label(cid, prefer_lang=lang or "en")
        n = len(mids)
        if n < 4:
            continue
        with_sym = sum(1 for mid in mids if meal_has.get(mid, False))
        rate = (with_sym / n) if n else 0.0
        rows.append(
            _Row(
                feature_key=f"category:{cid}",
                label=f"Category: {lbl}",
                support_meals=n,
                symptom_rate_pct=rate * 100.0,
                delta_pp=(rate - baseline) * 100.0,
            )
        )

    rows.sort(key=lambda r: (r.delta_pp, r.support_meals, r.symptom_rate_pct), reverse=True)

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "baseline_rate_pct": baseline * 100.0,
        "symptom_window_hours": window_hours,
        "features": [
            {
                "feature_key": r.feature_key,
                "label": r.label,
                "support_meals": r.support_meals,
                "symptom_rate_pct": r.symptom_rate_pct,
                "delta_pct_points": r.delta_pp,
            }
            for r in rows[:40]
        ],
    }


