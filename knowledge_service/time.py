"""UTC, half-open interval timestamp utilities."""
from datetime import date, datetime, timezone


def normalize_time(value, allow_none=True):
    if value is None:
        if allow_none:
            return None
        raise ValueError('Timestamp is required')
    if isinstance(value, str):
        try:
            if len(value) == 10:
                value = datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc)
            else:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError('Invalid ISO timestamp') from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timestamp must include a timezone (or use YYYY-MM-DD)')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def utc_now():
    return normalize_time(datetime.now(timezone.utc))
