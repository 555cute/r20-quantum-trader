"""Beijing display/business-date contract; wire signatures and epoch remain unchanged.

Legacy business wall-clock strings are Beijing. Known UTC fields must explicitly
pass naive_tz=timezone.utc. Never truncate an input offset before conversion.
"""
from datetime import datetime, timedelta, timezone
import re

BJ_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def parse_beijing(value, *, naive_tz=BJ_TZ):
    """Return an aware Beijing datetime, or None for missing/invalid input."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)) or re.fullmatch(r"-?\d+(?:\.\d+)?", str(value).strip()):
            epoch = float(value)
            if abs(epoch) >= 1e11:
                epoch /= 1000
            dt = datetime.fromtimestamp(epoch, timezone.utc)
        else:
            text = str(value).strip()
            text = re.sub(r"\s+UTC$", "+00:00", text)
            text = re.sub(r"\s*(?:\(北京时间\)|北京时间)$", "+08:00", text)
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=naive_tz)
        return dt.astimezone(BJ_TZ)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def beijing_day(value, *, naive_tz=BJ_TZ):
    dt = parse_beijing(value, naive_tz=naive_tz)
    return dt.date().isoformat() if dt else ""


def beijing_text(value, *, naive_tz=BJ_TZ):
    dt = parse_beijing(value, naive_tz=naive_tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
