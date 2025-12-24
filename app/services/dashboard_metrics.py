from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.i18n import fat_label, portion_label, posture_label, symptom_type_label
from app.db.models import Meal, MealItem, MealItemCategory, Medication, Symptom, User
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


def _medications_in_range(*, user_id: str, start_utc: datetime, end_excl_utc: datetime) -> list[Medication]:
    with get_session() as session:
        return (
            session.execute(
                select(Medication).where(
                    Medication.user_id == user_id,
                    Medication.taken_at >= start_utc,
                    Medication.taken_at < end_excl_utc,
                )
            )
            .scalars()
            .all()
        )


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


def _meal_id_to_item_types(*, user_id: str, start_utc: datetime, end_excl_utc: datetime) -> dict[str, set[str]]:
    with get_session() as session:
        rows = session.execute(
            select(Meal.id, MealItem.item_type)
            .select_from(Meal)
            .join(MealItem, MealItem.meal_id == Meal.id)
            .where(
                Meal.user_id == user_id,
                Meal.occurred_at >= start_utc,
                Meal.occurred_at < end_excl_utc,
            )
        ).all()
    out: dict[str, set[str]] = defaultdict(set)
    for meal_id, item_type in rows:
        if item_type:
            out[str(meal_id)].add(str(item_type))
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


def _pick_category_at_level(
    idx,
    category_id: str,
    *,
    target_level: int,
    max_depth: int = 30,
) -> str:
    """
    Pick a stable ancestor at a given taxonomy level.
    Taxonomy can be a DAG; if multiple candidates exist, pick lexicographically smallest id.
    """
    start = str(category_id)
    if idx.get_level(start) == target_level:
        return start

    seen: set[str] = set()
    cur: set[str] = {start}
    for _ in range(max_depth):
        nxt: set[str] = set()
        for c in cur:
            if c in seen:
                continue
            seen.add(c)
            for p in idx.get_parent_ids(c):
                if not p:
                    continue
                if idx.get_level(p) == target_level:
                    nxt.add(p)
                else:
                    nxt.add(p)
        # If any in nxt are already at the desired level, choose among them.
        candidates = [x for x in nxt if idx.get_level(x) == target_level]
        if candidates:
            return sorted(candidates)[0]
        cur = nxt

    # Fallback: can't reach target; keep original.
    return start


def product_categories(
    *,
    user_id: str,
    from_date: str,
    to_date: str,
    category_level: str | None = None,
    now_utc: datetime | None = None,
) -> dict:
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

    # Decide grouping: "lowest" (default) or integer level.
    observed_levels = {int(idx.get_level(cid)) for cid in cat_meals.keys()}
    max_level = max(observed_levels) if observed_levels else 0
    level_raw = (category_level or "").strip().lower()
    group_level: int | None
    if not level_raw or level_raw == "lowest":
        group_level = None
    else:
        try:
            group_level = int(level_raw)
        except Exception:
            group_level = None

    grouped: dict[str, set[str]] = defaultdict(set)
    for cid, meal_ids in cat_meals.items():
        gid = cid if group_level is None else _pick_category_at_level(idx, cid, target_level=group_level)
        grouped[str(gid)].update(meal_ids)

    cats = []
    for cid, meal_ids in grouped.items():
        n = len(meal_ids)
        if n <= 0:
            continue
        with_sym = sum(1 for mid in meal_ids if meal_has.get(mid, False))
        parent_ids = idx.get_parent_ids(cid)[:3]
        cats.append(
            {
                "category_id": cid,
                "name": idx.get_label(cid, prefer_lang=prefer_lang),
                "level": int(idx.get_level(cid)),
                "parents": [
                    {
                        "id": p,
                        "name": idx.get_label(p, prefer_lang=prefer_lang),
                        "level": int(idx.get_level(p)),
                    }
                    for p in parent_ids
                ],
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
        "category_level": "lowest" if group_level is None else group_level,
        "max_observed_level": int(max_level),
        "categories": cats[:30],
    }


def symptoms(
    *,
    user_id: str,
    from_date: str,
    to_date: str,
    symptom_type: str | None = None,
    bucket_hours: int | None = None,
) -> dict:
    user = _load_user(user_id)
    tz = ZoneInfo(user.timezone or "UTC")
    start_utc, end_excl_utc, d_from, d_to = _date_range_to_utc(tz, from_date, to_date)
    syms = _symptoms_in_range(
        user_id=user_id,
        start_utc=start_utc,
        end_excl_utc=end_excl_utc,
        symptom_type=symptom_type,
    )

    # Bucket choice for the timeline chart:
    # - default: daily
    # - if bucket_hours == 3: 3-hour blocks
    bh = int(bucket_hours) if isinstance(bucket_hours, int) else 24
    if bh not in (3, 6, 12, 24):
        bh = 24

    by_bucket: dict[str, list[int]] = defaultdict(list)
    by_type_counter: dict[str, list[int]] = defaultdict(list)

    def bucket_key(dt_local: datetime) -> str:
        if bh == 24:
            return dt_local.date().isoformat()
        floored_hour = (int(dt_local.hour) // bh) * bh
        start_local = dt_local.replace(hour=floored_hour, minute=0, second=0, microsecond=0)
        # Keep as ISO-like string for stable lexicographic sorting.
        return start_local.strftime("%Y-%m-%d %H:%M")

    for s in syms:
        started_local = _as_utc_aware(s.started_at).astimezone(tz)
        k = bucket_key(started_local)
        by_bucket[k].append(int(s.intensity))
        by_type_counter[str(s.symptom_type or "other")].append(int(s.intensity))

    # Fill missing buckets for nicer charts.
    daily_out = []
    if bh == 24:
        cur = d_from
        while cur <= d_to:
            k = cur.isoformat()
            xs = by_bucket.get(k, [])
            daily_out.append(
                {
                    "date": k,
                    "count": len(xs),
                    "avg_intensity": (sum(xs) / len(xs)) if xs else 0.0,
                }
            )
            cur = cur + timedelta(days=1)
    else:
        start_local = datetime.combine(d_from, time(0, 0), tzinfo=tz)
        end_excl_local = datetime.combine(d_to + timedelta(days=1), time(0, 0), tzinfo=tz)
        cur_dt = start_local
        step = timedelta(hours=bh)
        while cur_dt < end_excl_local:
            k = cur_dt.strftime("%Y-%m-%d %H:%M")
            xs = by_bucket.get(k, [])
            daily_out.append(
                {
                    "date": k,
                    "count": len(xs),
                    "avg_intensity": (sum(xs) / len(xs)) if xs else 0.0,
                }
            )
            cur_dt = cur_dt + step

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
        "bucket_hours": bh,
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

    # Meal item type features (dish/ingredient/drink/product) per meal
    meal_types = _meal_id_to_item_types(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    type_to_meals: dict[str, set[str]] = defaultdict(set)
    for mid, types in meal_types.items():
        for t in types:
            type_to_meals[str(t)].add(mid)
    for t, mids in type_to_meals.items():
        label = t.capitalize()
        r = _bucket("meal_type", label, mids)
        if r:
            # Override label prefix text for readability.
            rows.append(
                _Row(
                    feature_key=r.feature_key,
                    label=f"Meal type: {label}",
                    support_meals=r.support_meals,
                    symptom_rate_pct=r.symptom_rate_pct,
                    delta_pp=r.delta_pp,
                )
            )

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


def timeline(*, user_id: str, from_date: str, to_date: str, now_utc: datetime | None = None) -> dict:
    user = _load_user(user_id)
    tz = ZoneInfo(user.timezone or "UTC")
    start_utc, end_excl_utc, d_from, d_to = _date_range_to_utc(tz, from_date, to_date, now_utc=now_utc)

    meals = _meals_in_range(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    syms = _symptoms_in_range(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    meds = _medications_in_range(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)

    def _short(s: str, n: int = 140) -> str:
        ss = (s or "").strip().replace("\n", " ")
        if len(ss) <= n:
            return ss
        return ss[: n - 1] + "…"

    events: list[dict] = []
    for m in meals:
        dt_local = _as_utc_aware(m.occurred_at).astimezone(tz)
        events.append(
            {
                "kind": "meal",
                "at": dt_local.isoformat(),
                "meal_id": m.id,
                "notes": _short(getattr(m, "notes_text", "") or ""),
                "portion": str(getattr(m, "portion_size", "") or ""),
                "fat": str(getattr(m, "fat_level", "") or ""),
                "posture": str(getattr(m, "posture_after", "") or ""),
            }
        )
    for s in syms:
        dt_local = _as_utc_aware(s.started_at).astimezone(tz)
        events.append(
            {
                "kind": "symptom",
                "at": dt_local.isoformat(),
                "symptom_id": s.id,
                "type": symptom_type_label(getattr(user, "language", None), str(getattr(s, "symptom_type", "") or "other")),
                "intensity": int(getattr(s, "intensity", 0)),
                "duration_minutes": getattr(s, "duration_minutes", None),
            }
        )

    for m in meds:
        dt_local = _as_utc_aware(m.taken_at).astimezone(tz)
        events.append(
            {
                "kind": "medication",
                "at": dt_local.isoformat(),
                "medication_id": m.id,
                "name": str(getattr(m, "name", "") or ""),
                "dosage": str(getattr(m, "dosage", "") or ""),
            }
        )

    events.sort(key=lambda e: e.get("at") or "")

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "timezone": str(tz.key),
        "events": events,
    }


def medications(*, user_id: str, from_date: str, to_date: str, now_utc: datetime | None = None) -> dict:
    user = _load_user(user_id)
    tz = ZoneInfo(user.timezone or "UTC")
    start_utc, end_excl_utc, d_from, d_to = _date_range_to_utc(tz, from_date, to_date, now_utc=now_utc)

    meds = _medications_in_range(user_id=user_id, start_utc=start_utc, end_excl_utc=end_excl_utc)
    total = len(meds)

    # Daily counts (fill missing days)
    by_day: dict[str, int] = defaultdict(int)
    for m in meds:
        d = _as_utc_aware(m.taken_at).astimezone(tz).date().isoformat()
        by_day[d] += 1
    daily = []
    cur = d_from
    while cur <= d_to:
        k = cur.isoformat()
        daily.append({"date": k, "count": int(by_day.get(k, 0))})
        cur = cur + timedelta(days=1)

    # By name stats
    name_rows: dict[str, dict] = {}
    for m in meds:
        name = (getattr(m, "name", "") or "").strip() or "Unknown"
        row = name_rows.get(name)
        taken_local = _as_utc_aware(m.taken_at).astimezone(tz)
        if not row:
            name_rows[name] = {
                "name": name,
                "count": 1,
                "last_taken_at": taken_local.isoformat(),
            }
        else:
            row["count"] = int(row["count"]) + 1
            if str(taken_local.isoformat()) > str(row.get("last_taken_at") or ""):
                row["last_taken_at"] = taken_local.isoformat()

    by_name = list(name_rows.values())
    for r in by_name:
        r["share_pct"] = (100.0 * float(r["count"]) / total) if total else 0.0
    by_name.sort(key=lambda r: (r["count"], r["share_pct"]), reverse=True)

    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "total_taken": total,
        "daily": daily,
        "by_name": by_name[:30],
    }


