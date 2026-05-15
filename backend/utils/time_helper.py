from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytz

ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")


def now_israel() -> datetime:
    return datetime.now(ISRAEL_TZ)


def format_israel_time(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now(ISRAEL_TZ)
    elif dt.tzinfo is None:
        dt = ISRAEL_TZ.localize(dt)
    else:
        dt = dt.astimezone(ISRAEL_TZ)
    return dt.strftime("%H:%M | %d/%m/%Y")


def is_market_hours_israel() -> bool:
    """US market 9:30-16:00 EST mapped to Israel time.

    Summer: 16:30-23:00 Israel. Winter: 17:30-00:00 Israel.
    Uses 16:00-23:30 as a safe envelope around both.
    """
    now = datetime.now(ISRAEL_TZ)
    if now.weekday() >= 5:
        return False
    time_val = now.hour * 60 + now.minute
    return 960 <= time_val <= 1410
