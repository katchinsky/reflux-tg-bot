from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from zoneinfo import ZoneInfo

from app.core.i18n import fat_label, portion_label, posture_label, symptom_type_label, t
from app.db.models import Meal, MorningCheck, Symptom, User
from app.db.session import get_session


@dataclass(frozen=True)
class AssocRow:
    label: str
    meals_total: int
    meals_with_symptom: int
    p: float
    avg_intensity: float | None


def _dt_range(now_utc: datetime, *, days: int) -> tuple[datetime, datetime]:
    start = now_utc - timedelta(days=days)
    return (start, now_utc)


def last_7_days_summary(user: User, *, now_utc: datetime, lang: str | None = None) -> str:
    start, end = _dt_range(now_utc, days=7)
    with get_session() as session:
        meals = (
            session.execute(
                select(Meal).where(Meal.user_id == user.id, Meal.occurred_at >= start, Meal.occurred_at <= end)
            )
            .scalars()
            .all()
        )
        symptoms = (
            session.execute(
                select(Symptom).where(
                    Symptom.user_id == user.id, Symptom.started_at >= start, Symptom.started_at <= end
                )
            )
            .scalars()
            .all()
        )
        morning_checks = (
            session.execute(select(MorningCheck).where(MorningCheck.user_id == user.id))
            .scalars()
            .all()
        )

    sym_count = len(symptoms)
    avg_intensity = (sum(s.intensity for s in symptoms) / sym_count) if sym_count else 0.0
    common = Counter([s.symptom_type for s in symptoms]).most_common(5)
    common_str = (
        ", ".join([f"{symptom_type_label(lang, st)} ({c})" for st, c in common])
        if common
        else t(lang, "common.none")
    )

    tz = ZoneInfo(user.timezone)
    meals_per_day: dict[str, int] = defaultdict(int)
    for m in meals:
        d = m.occurred_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date().isoformat()
        meals_per_day[d] += 1

    stress_vals = [mc.stress_level for mc in morning_checks[-7:]]
    avg_stress = (sum(stress_vals) / len(stress_vals)) if stress_vals else None

    lines = [
        t(lang, "reports.last_7_days.title"),
        t(lang, "reports.last_7_days.symptoms", count=sym_count, avg=avg_intensity),
        t(lang, "reports.last_7_days.most_common", common=common_str),
        t(lang, "reports.last_7_days.meals", count=len(meals)),
    ]
    if avg_stress is not None:
        lines.append(t(lang, "reports.last_7_days.stress_avg", avg=avg_stress))
    return "\n".join(lines)


def association_signals(
    user: User,
    *,
    now_utc: datetime,
    window_hours: int = 4,
    min_support: int = 3,
    lang: str | None = None,
) -> tuple[str, list[AssocRow]]:
    """
    Simple window association:
    for each meal, check if any symptom starts in [meal, meal+window].
    """
    start, end = _dt_range(now_utc, days=30)  # use more history to stabilize
    window = timedelta(hours=window_hours)

    with get_session() as session:
        meals = (
            session.execute(
                select(Meal).where(Meal.user_id == user.id, Meal.occurred_at >= start, Meal.occurred_at <= end)
            )
            .scalars()
            .all()
        )
        symptoms = (
            session.execute(
                select(Symptom).where(
                    Symptom.user_id == user.id, Symptom.started_at >= start, Symptom.started_at <= end
                )
            )
            .scalars()
            .all()
        )

    # Index symptoms by start time (naive compare is fine for sqlite; we store UTC-ish consistently)
    symptoms_sorted = sorted(symptoms, key=lambda s: s.started_at)

    def meal_has_symptom(meal_at: datetime) -> tuple[bool, list[Symptom]]:
        end_at = meal_at + window
        hits = [s for s in symptoms_sorted if meal_at <= s.started_at <= end_at]
        return (len(hits) > 0, hits)

    # Baseline
    total_meals = len(meals)
    meals_with_any = 0
    for m in meals:
        has, _ = meal_has_symptom(m.occurred_at)
        meals_with_any += 1 if has else 0
    baseline = (meals_with_any / total_meals) if total_meals else 0.0

    def compute(feature_name: str, get_value) -> list[AssocRow]:
        buckets: dict[str, list[tuple[bool, list[int]]]] = defaultdict(list)
        for m in meals:
            v = str(get_value(m) or "unknown")
            has, hits = meal_has_symptom(m.occurred_at)
            buckets[v].append((has, [s.intensity for s in hits]))

        out: list[AssocRow] = []
        for v, rows in buckets.items():
            if len(rows) < min_support:
                continue
            meals_total_v = len(rows)
            meals_with = sum(1 for has, _ in rows if has)
            intensities = [i for has, ints in rows for i in ints]
            avg_i = (sum(intensities) / len(intensities)) if intensities else None
            feature_local = t(lang, f"reports.signals.feature.{feature_name}")
            if feature_name == "portion":
                value_local = portion_label(lang, v)
            elif feature_name == "fat":
                value_local = fat_label(lang, v)
            elif feature_name == "posture":
                value_local = posture_label(lang, v)
            else:
                value_local = v
            out.append(
                AssocRow(
                    label=t(lang, "reports.signals.label", feature=feature_local, value=value_local),
                    meals_total=meals_total_v,
                    meals_with_symptom=meals_with,
                    p=(meals_with / meals_total_v) if meals_total_v else 0.0,
                    avg_intensity=avg_i,
                )
            )
        out.sort(key=lambda r: (r.p, r.meals_with_symptom, r.meals_total), reverse=True)
        return out

    rows = []
    rows.extend(compute("portion", lambda m: m.portion_size))
    rows.extend(compute("fat", lambda m: m.fat_level))
    rows.extend(compute("posture", lambda m: m.posture_after))

    header = t(
        lang,
        "reports.signals.header",
        window_hours=window_hours,
        baseline=baseline,
        with_any=meals_with_any,
        total=total_meals,
    )
    return header, rows[:6]


