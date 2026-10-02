from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_today(tz_name: str) -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """UTC [start, end) of a calendar day in the given timezone (handles DST-length days)."""
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def overlap_hours(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> float:
    seconds = (min(end, window_end) - max(start, window_start)).total_seconds()
    return max(seconds, 0) / 3600
