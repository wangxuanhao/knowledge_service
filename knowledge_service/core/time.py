"""UTC、左闭右开区间时间戳工具。"""
from datetime import date, datetime, timezone


def normalize_time(value, allow_none=True):
    if value is None:
        if allow_none:
            return None
        raise ValueError('时间戳为必填项')
    if isinstance(value, str):
        try:
            if len(value) == 10:
                value = datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc)
            else:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError('无效的 ISO 时间戳') from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('时间戳必须包含时区（或使用 YYYY-MM-DD）')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def utc_now():
    return normalize_time(datetime.now(timezone.utc))