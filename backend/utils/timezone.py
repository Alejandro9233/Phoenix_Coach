import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

def get_local_now() -> datetime:
    """Get the current time in the local timezone (defaults to America/Los_Angeles)."""
    tz_str = os.getenv("TIMEZONE", "America/Los_Angeles")
    return datetime.now(ZoneInfo(tz_str))

def get_local_today() -> date:
    """Get the current date in the local timezone."""
    return get_local_now().date()
