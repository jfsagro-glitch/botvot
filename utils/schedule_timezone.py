"""
Helpers for consistent scheduling timezone handling.

On some platforms (notably Windows), `zoneinfo.ZoneInfo("Europe/Moscow")` may
fail if IANA tzdata is not available. This module provides a robust fallback so
schedulers keep working at the intended local time.
"""

from __future__ import annotations

import re
from datetime import timedelta, timezone, tzinfo

from core.config import Config

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None


_OFFSET_RE = re.compile(
    r"^(?:(?:UTC|GMT)\s*)?(?P<sign>[+-])\s*(?P<h>\d{1,2})(?::?(?P<m>\d{2}))?$",
    re.IGNORECASE,
)


def _parse_utc_offset(value: str) -> timedelta | None:
    s = (value or "").strip().upper()
    if not s:
        return None
    if s in {"UTC", "GMT", "Z"}:
        return timedelta(0)
    m = _OFFSET_RE.match(s)
    if not m:
        return None
    sign = -1 if m.group("sign") == "-" else 1
    hours = int(m.group("h"))
    minutes = int(m.group("m") or "0")
    if hours > 23 or minutes > 59:
        return None
    return sign * timedelta(hours=hours, minutes=minutes)


def get_schedule_timezone() -> tzinfo:
    """
    Return tzinfo to be used for all "local schedule" calculations.

    Preference order:
    1) IANA tz via ZoneInfo(Config.SCHEDULE_TIMEZONE)
    2) Explicit offset formats like "+03:00", "UTC+3", "UTC+0300"
    3) Known safe fallbacks (MSK = UTC+03:00)
    4) UTC
    """
    name = (getattr(Config, "SCHEDULE_TIMEZONE", "") or "").strip()
    if not name:
        return timezone.utc

    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass

    offset = _parse_utc_offset(name)
    if offset is not None:
        return timezone(offset)

    # Common course default: Moscow time. Moscow has no DST (fixed +03:00).
    if name.lower() in {"europe/moscow", "moscow", "msk"}:
        return timezone(timedelta(hours=3), name="MSK")

    return timezone.utc


def format_tz(tz: tzinfo) -> str:
    """Best-effort short display name for logs."""
    key = getattr(tz, "key", None)
    return str(key) if key else str(tz)

