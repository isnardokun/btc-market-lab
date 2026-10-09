"""UTC daily-bar cutoffs for publication.

A Yahoo 1d entry stamped at midnight is the OPEN day's current/partial
daily bar until the next UTC day begins. Report only completed UTC bars.
"""
import datetime as dt
import math


def previous_completed_utc_day(now_utc=None):
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    if not isinstance(now, dt.datetime) or now.tzinfo is None:
        raise ValueError("Expected timezone-aware UTC datetime")
    return now.astimezone(dt.timezone.utc).date() - dt.timedelta(days=1)


def closed_daily_bars(rows, *, now_utc=None):
    """Return only complete, valid daily OHLC rows in chronological order.

    Allows previous-trading-session data on non-trading days, with its date
    preserved. Does not force-fill a missing prior-day observation.
    """
    cutoff = previous_completed_utc_day(now_utc)
    filtered = []
    for row in rows or []:
        try:
            date = dt.datetime.fromtimestamp(int(row["ts"]), dt.timezone.utc).date()
            vals = [row.get(k) for k in ("open", "high", "low", "close")]
            if date <= cutoff and all(
                isinstance(v, (float, int)) and math.isfinite(v) and v > 0
                for v in vals
            ):
                filtered.append(row)
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            continue
    return sorted(filtered, key=lambda row: row["ts"])


def last_complete_day_end_timestamp(now_utc=None):
    """Inclusive timestamp cutoff for SQLite 52W/ATH price reference queries."""
    day = previous_completed_utc_day(now_utc)
    return int(dt.datetime.combine(
        day, dt.time(23, 59, 59), tzinfo=dt.timezone.utc
    ).timestamp())
