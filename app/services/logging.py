from __future__ import annotations

import json
from datetime import date, datetime

from app.db.models import EventAudit, Meal, Medication, MorningCheck, Symptom, User
from app.db.session import get_session


def _audit(user: User, *, event_type: str, payload: dict) -> None:
    with get_session() as session:
        session.add(
            EventAudit(
                user_id=user.id,
                event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )


def create_meal(
    user: User,
    *,
    occurred_at_utc: datetime,
    notes_text: str,
    photo_file_id: str | None,
    portion_size: str,
    fat_level: str,
    posture_after: str,
) -> Meal:
    with get_session() as session:
        meal = Meal(
            user_id=user.id,
            occurred_at=occurred_at_utc,
            notes_text=notes_text,
            photo_file_id=photo_file_id,
            portion_size=portion_size,
            fat_level=fat_level,
            posture_after=posture_after,
        )
        session.add(meal)
        session.flush()
    _audit(user, event_type="meal_created", payload={"meal_id": meal.id})
    return meal


def create_symptom(
    user: User,
    *,
    symptom_type: str,
    intensity: int,
    started_at_utc: datetime,
    duration_minutes: int | None,
    notes: str | None,
) -> Symptom:
    with get_session() as session:
        symptom = Symptom(
            user_id=user.id,
            symptom_type=symptom_type,
            intensity=intensity,
            started_at=started_at_utc,
            duration_minutes=duration_minutes,
            notes=notes,
        )
        session.add(symptom)
        session.flush()
    _audit(user, event_type="symptom_created", payload={"symptom_id": symptom.id})
    return symptom


def create_medication(
    user: User,
    *,
    name: str,
    dosage: str,
    taken_at_utc: datetime,
    is_scheduled: bool = False,
) -> Medication:
    with get_session() as session:
        med = Medication(
            user_id=user.id,
            name=name,
            dosage=dosage,
            taken_at=taken_at_utc,
            is_scheduled=1 if is_scheduled else 0,
        )
        session.add(med)
        session.flush()
    _audit(user, event_type="medication_created", payload={"medication_id": med.id})
    return med


def create_morning_check(
    user: User,
    *,
    local_date: date,
    sleep_position: str,
    stress_level: int,
    activity_level: str,
    activity_notes: str | None,
) -> MorningCheck:
    with get_session() as session:
        mc = MorningCheck(
            user_id=user.id,
            date=local_date,
            sleep_position=sleep_position,
            stress_level=stress_level,
            activity_level=activity_level,
            activity_notes=activity_notes,
        )
        session.add(mc)
        session.flush()
    _audit(user, event_type="morning_check_created", payload={"morning_check_id": mc.id})
    return mc


