from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime

from sqlalchemy import select

from app.db.models import Meal, Medication, MedSchedule, MorningCheck, Symptom, User
from app.db.session import get_session


def _dt(v: datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _d(v: date | None) -> str | None:
    return v.isoformat() if v is not None else None


def export_json_bytes(user: User) -> bytes:
    with get_session() as session:
        meals = session.execute(select(Meal).where(Meal.user_id == user.id)).scalars().all()
        symptoms = session.execute(select(Symptom).where(Symptom.user_id == user.id)).scalars().all()
        meds = session.execute(select(Medication).where(Medication.user_id == user.id)).scalars().all()
        schedules = session.execute(select(MedSchedule).where(MedSchedule.user_id == user.id)).scalars().all()
        mornings = session.execute(select(MorningCheck).where(MorningCheck.user_id == user.id)).scalars().all()

    payload = {
        "user": {
            "id": user.id,
            "telegram_user_id": user.telegram_user_id,
            "timezone": user.timezone,
            "created_at": _dt(user.created_at),
        },
        "meals": [
            {
                "id": m.id,
                "occurred_at": _dt(m.occurred_at),
                "notes_text": m.notes_text,
                "photo_file_id": m.photo_file_id,
                "portion_size": m.portion_size,
                "fat_level": m.fat_level,
                "posture_after": m.posture_after,
                "created_at": _dt(m.created_at),
            }
            for m in meals
        ],
        "symptoms": [
            {
                "id": s.id,
                "symptom_type": s.symptom_type,
                "intensity": s.intensity,
                "started_at": _dt(s.started_at),
                "duration_minutes": s.duration_minutes,
                "notes": s.notes,
                "created_at": _dt(s.created_at),
            }
            for s in symptoms
        ],
        "medications": [
            {
                "id": m.id,
                "name": m.name,
                "dosage": m.dosage,
                "taken_at": _dt(m.taken_at),
                "is_scheduled": bool(m.is_scheduled),
                "created_at": _dt(m.created_at),
            }
            for m in meds
        ],
        "med_schedules": [
            {
                "id": ms.id,
                "name": ms.name,
                "dosage": ms.dosage,
                "rrule": ms.rrule,
                "start_at": _dt(ms.start_at),
                "is_active": bool(ms.is_active),
                "last_fired_at": _dt(ms.last_fired_at),
                "created_at": _dt(ms.created_at),
            }
            for ms in schedules
        ],
        "morning_checks": [
            {
                "id": mc.id,
                "date": _d(mc.date),
                "sleep_position": mc.sleep_position,
                "stress_level": mc.stress_level,
                "activity_level": mc.activity_level,
                "activity_notes": mc.activity_notes,
                "created_at": _dt(mc.created_at),
            }
            for mc in mornings
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def export_csv_zip_bytes(user: User) -> bytes:
    """
    Returns a zip file containing multiple CSVs (one per table).
    """
    with get_session() as session:
        meals = session.execute(select(Meal).where(Meal.user_id == user.id)).scalars().all()
        symptoms = session.execute(select(Symptom).where(Symptom.user_id == user.id)).scalars().all()
        meds = session.execute(select(Medication).where(Medication.user_id == user.id)).scalars().all()
        mornings = session.execute(select(MorningCheck).where(MorningCheck.user_id == user.id)).scalars().all()

    def write_csv(filename: str, fieldnames: list[str], rows: list[dict]) -> tuple[str, bytes]:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return filename, buf.getvalue().encode("utf-8")

    files: list[tuple[str, bytes]] = []
    files.append(
        write_csv(
            "meals.csv",
            ["id", "occurred_at", "notes_text", "photo_file_id", "portion_size", "fat_level", "posture_after", "created_at"],
            [
                {
                    "id": m.id,
                    "occurred_at": _dt(m.occurred_at),
                    "notes_text": m.notes_text,
                    "photo_file_id": m.photo_file_id,
                    "portion_size": m.portion_size,
                    "fat_level": m.fat_level,
                    "posture_after": m.posture_after,
                    "created_at": _dt(m.created_at),
                }
                for m in meals
            ],
        )
    )
    files.append(
        write_csv(
            "symptoms.csv",
            ["id", "symptom_type", "intensity", "started_at", "duration_minutes", "notes", "created_at"],
            [
                {
                    "id": s.id,
                    "symptom_type": s.symptom_type,
                    "intensity": s.intensity,
                    "started_at": _dt(s.started_at),
                    "duration_minutes": s.duration_minutes,
                    "notes": s.notes,
                    "created_at": _dt(s.created_at),
                }
                for s in symptoms
            ],
        )
    )
    files.append(
        write_csv(
            "medications.csv",
            ["id", "name", "dosage", "taken_at", "is_scheduled", "created_at"],
            [
                {
                    "id": m.id,
                    "name": m.name,
                    "dosage": m.dosage,
                    "taken_at": _dt(m.taken_at),
                    "is_scheduled": bool(m.is_scheduled),
                    "created_at": _dt(m.created_at),
                }
                for m in meds
            ],
        )
    )
    files.append(
        write_csv(
            "morning_checks.csv",
            ["id", "date", "sleep_position", "stress_level", "activity_level", "activity_notes", "created_at"],
            [
                {
                    "id": mc.id,
                    "date": _d(mc.date),
                    "sleep_position": mc.sleep_position,
                    "stress_level": mc.stress_level,
                    "activity_level": mc.activity_level,
                    "activity_notes": mc.activity_notes,
                    "created_at": _dt(mc.created_at),
                }
                for mc in mornings
            ],
        )
    )

    out = io.BytesIO()
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return out.getvalue()


