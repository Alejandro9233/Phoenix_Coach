import os
from datetime import datetime, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Fallback only. The real value normally comes from the athlete's phone, which
# reports its timezone on every app launch — so travelling fixes itself.
# Hermosillo is UTC-7 year-round (Sonora does not observe DST).
DEFAULT_TIMEZONE = "America/Hermosillo"

# Set from the athlete's stored profile at startup, and refreshed whenever the
# device reports a new timezone. Cached in-process so the many callers of
# get_local_today() don't each need a DB session.
_athlete_timezone: str | None = None


def is_valid_timezone(tz_str: str) -> bool:
    """True if tz_str is an IANA identifier this machine can resolve."""
    if not tz_str:
        return False
    try:
        ZoneInfo(tz_str)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def set_athlete_timezone(tz_str: str | None) -> bool:
    """Cache the athlete's timezone. Returns False (and keeps the old value) if invalid."""
    global _athlete_timezone
    if tz_str and is_valid_timezone(tz_str):
        _athlete_timezone = tz_str
        return True
    return False


def get_timezone_name() -> str:
    """Resolve the active timezone: env override → athlete's device → default."""
    override = os.getenv("TIMEZONE")
    if override and is_valid_timezone(override):
        return override
    if _athlete_timezone:
        return _athlete_timezone
    return DEFAULT_TIMEZONE


def get_local_now() -> datetime:
    """Get the current time in the athlete's local timezone."""
    return datetime.now(ZoneInfo(get_timezone_name()))


def get_local_today() -> date:
    """Get the current date in the athlete's local timezone."""
    return get_local_now().date()
