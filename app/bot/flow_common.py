from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


UTC = ZoneInfo("UTC")


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def fmt_dt_user(dt_utc: datetime, *, user_tz: str) -> str:
    return dt_utc.astimezone(ZoneInfo(user_tz)).strftime("%Y-%m-%d %H:%M")


def nav_kb(*, flow: str, show_back: bool = True, show_skip: bool = False) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    if show_back:
        row.append(InlineKeyboardButton("Back", callback_data=f"{flow}:nav:back"))
    if show_skip:
        row.append(InlineKeyboardButton("Skip", callback_data=f"{flow}:nav:skip"))
    row.append(InlineKeyboardButton("Cancel", callback_data=f"{flow}:nav:cancel"))
    return InlineKeyboardMarkup([row])


@dataclass(frozen=True)
class Callback:
    flow: str
    kind: str
    value: str


def parse_cb(data: str) -> Callback | None:
    # Format: flow:kind:value
    parts = (data or "").split(":", 2)
    if len(parts) != 3:
        return None
    return Callback(flow=parts[0], kind=parts[1], value=parts[2])


def one_hour_ago(dt: datetime) -> datetime:
    return dt - timedelta(hours=1)


