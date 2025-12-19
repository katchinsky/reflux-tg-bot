from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


_RE_HHMM = re.compile(r"^(?P<h>\d{1,2}):(?P<m>\d{2})$")
_RE_YESTERDAY = re.compile(r"^yesterday\s+(?P<h>\d{1,2}):(?P<m>\d{2})$", re.IGNORECASE)


def parse_user_time(text: str, *, user_tz: str, now_utc: datetime) -> datetime | None:
    """
    Parse limited time inputs:
    - HH:MM => today at HH:MM in user's timezone
    - yesterday HH:MM => yesterday at HH:MM in user's timezone
    Returns an aware datetime in UTC.
    """
    raw = text.strip()
    tz = ZoneInfo(user_tz)
    now_local = now_utc.astimezone(tz)

    m = _RE_HHMM.match(raw)
    if m:
        h = int(m.group("h"))
        mi = int(m.group("m"))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        local_dt = datetime.combine(now_local.date(), time(h, mi), tzinfo=tz)
        return local_dt.astimezone(ZoneInfo("UTC"))

    m = _RE_YESTERDAY.match(raw)
    if m:
        h = int(m.group("h"))
        mi = int(m.group("m"))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        local_date = now_local.date() - timedelta(days=1)
        local_dt = datetime.combine(local_date, time(h, mi), tzinfo=tz)
        return local_dt.astimezone(ZoneInfo("UTC"))

    return None


