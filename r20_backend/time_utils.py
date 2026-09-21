"""Beijing display/business-date contract; wire signatures and epoch remain unchanged.

Legacy business wall-clock strings are Beijing. Known UTC fields must explicitly
pass naive_tz=timezone.utc. Never truncate an input offset before conversion.
"""
from datetime import datetime, timedelta, timezone
import re

BJ_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

#: 秒/毫秒分界（第一百八十八刀）：本仓**唯一**一处。
#:
#: ⚠️ 必须是 `1e11` 而不是 `1e9`：epoch **秒**本身已经 ~1.79e9，用 1e9 当分界会把"秒"
#: 误判成"毫秒"再除以 1000 —— 结果"还剩 7 天"被算成"已过期"（本仓真实踩过）。
#: 同一判据此前在本仓有**四处**写法（本文件、`dashboard_payload/multi_venue.py`、
#: `trader/venue_protection.py` 的函数版与内联版）⇒ 统一到这里，其余各处委派。
EPOCH_MS_THRESHOLD = 1e11


def to_seconds(value):
    """时间戳归一成**秒**：毫秒输入（>= 1e11）除以 1000，秒输入原样。`None` 原样返回。"""
    if value is None:
        return None
    return value / 1000.0 if abs(value) >= EPOCH_MS_THRESHOLD else value


def to_millis(value):
    """时间戳归一成**毫秒**：秒输入（< 1e11）乘 1000，毫秒输入原样。`None` 原样返回。"""
    if value is None:
        return None
    return value * 1000 if 0 < abs(value) < EPOCH_MS_THRESHOLD else value


def parse_beijing(value, *, naive_tz=BJ_TZ):
    """Return an aware Beijing datetime, or None for missing/invalid input."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)) or re.fullmatch(r"-?\d+(?:\.\d+)?", str(value).strip()):
            epoch = to_seconds(float(value))     # 第一百八十八刀：委派给唯一分界
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
