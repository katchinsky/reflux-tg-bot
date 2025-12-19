from __future__ import annotations

import uuid
from datetime import date
from datetime import datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Belgrade")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    notes_text: Mapped[str] = mapped_column(Text, default="")
    photo_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    portion_size: Mapped[str] = mapped_column(String(16), default="medium")  # small/medium/large
    fat_level: Mapped[str] = mapped_column(String(16), default="unknown")  # low/medium/high/unknown
    posture_after: Mapped[str] = mapped_column(String(16), default="unknown")  # laying/sitting/walking/standing/unknown
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Symptom(Base):
    __tablename__ = "symptoms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    symptom_type: Mapped[str] = mapped_column(String(32), index=True)  # heartburn, regurgitation, ...
    intensity: Mapped[int] = mapped_column(Integer)  # 0-10
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null => ongoing
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    dosage: Mapped[str] = mapped_column(String(64), default="")
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_scheduled: Mapped[int] = mapped_column(Integer, default=0)  # 0/1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MedSchedule(Base):
    __tablename__ = "med_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    dosage: Mapped[str] = mapped_column(String(64), default="")
    rrule: Mapped[str] = mapped_column(Text, default="FREQ=DAILY")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[int] = mapped_column(Integer, default=1)  # 0/1
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MorningCheck(Base):
    __tablename__ = "morning_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)  # local date in user's timezone
    sleep_position: Mapped[str] = mapped_column(String(16), default="unknown")
    stress_level: Mapped[int] = mapped_column(Integer, default=3)  # 1-5
    activity_level: Mapped[str] = mapped_column(String(16), default="unknown")  # none/light/moderate/intense/unknown
    activity_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ConversationState(Base):
    __tablename__ = "conversation_state"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    flow: Mapped[str] = mapped_column(String(32), primary_key=True)
    step: Mapped[str] = mapped_column(String(32), default="")
    draft_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EventAudit(Base):
    __tablename__ = "events_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


