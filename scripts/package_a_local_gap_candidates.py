"""Offline historical gap candidate discovery on caller-owned SQLite fixtures.

A locally missing timestamp is a CANDIDATE for research, never proof that an
upstream provider offers that observation. No CLI, SQLite path, API, secrets,
execution queue, entitlement upgrade or writer lock activation.
"""
import datetime as dt
from ingestion.package_a_contracts import SeriesSpec, utc

SUPPORTED = frozenset({"5m", "d1"})


def scan_archived_gaps(conn, spec, start, end, *, max_expected=10000, max_windows=100):
    """Read existing Package A observations and return candidate missing windows.

    Enforces half-open UTC windows and fixed 5m/UTC daily grids ONLY. Funding
    events, FRED monthly/weekly and Yahoo equities require provider calendars:
    they deliberately do not receive invented periodic gap windows here.
    """
    if not isinstance(spec, SeriesSpec):
        raise ValueError("A typed SeriesSpec is required")
    if spec.frequency not in SUPPORTED or spec.metric == "funding_settled":
        return {"state": "UNSUPPORTED_CADENCE_NO_INFERRED_GAPS",
                "provider": spec.provider, "metric": spec.metric,
                "candidates": [], "historical_completeness": "NOT_VERIFIED",
                "execution_authorized": False}
    # Yahoo equity calendar cannot be inferred from fixed UTC days.
    if spec.provider == "yahoo" and spec.symbol != "BTC-USD":
        raise ValueError("Calendar-aware Yahoo symbols require external schedule")
    begin, stop = utc(start), utc(end)
    step = dt.timedelta(minutes=5) if spec.frequency == "5m" else dt.timedelta(days=1)
    if begin >= stop or (stop - begin) % step != dt.timedelta(0):
        raise ValueError("Exactly aligned nonempty window required")
    if spec.frequency == "5m" and (begin.timestamp() % 300 or stop.timestamp() % 300):
        raise ValueError("5m endpoints must be grid-aligned")
    if spec.frequency == "d1" and (begin.time() != dt.time() or stop.time() != dt.time()):
        raise ValueError("Daily endpoints must be 00:00 UTC")
    slots = int((stop - begin) / step)
    if (type(max_expected) is not int or type(max_windows) is not int
            or max_expected < 1 or max_windows < 1 or slots > max_expected):
        raise ValueError("Synthetic candidate scan exceeds allowed budget")
    key=(spec.provider,spec.metric,spec.symbol,spec.frequency)
    observed=list(conn.execute(
        "SELECT observed_utc,dimension_key FROM package_a_observations "
        "WHERE provider=? AND metric=? AND symbol=? AND frequency=? "
        "AND observed_utc>=? AND observed_utc<? ORDER BY observed_utc",
        (*key,start,end)))
    stamps={utc(stamp) for stamp,dimension in observed}
    missing=[]
    head=None
    for index in range(slots):
        moment=begin+step*index
        if moment not in stamps:
            if head is None:
                head=moment
        elif head is not None:
            missing.append((head,moment))
            head=None
    if head is not None:
        missing.append((head,stop))
    if len(missing)>max_windows:
        return {
            "state":"BLOCKED_GAP_BUDGET",
            "candidate_window_count":len(missing),
            "candidates":[],
            "historical_completeness":"NOT_VERIFIED",
            "execution_authorized":False,
        }
    return {
        "state":"OFFLINE_LOCAL_GAP_CANDIDATES",
        "provider":spec.provider,"metric":spec.metric,"symbol":spec.symbol,
        "frequency":spec.frequency,"expected_grid_slots":slots,
        "observed_distinct_timestamps":len(stamps),
        "missing_candidate_slots":slots-len(stamps),
        "candidates":[{
            "start_utc":a.isoformat().replace("+00:00","Z"),
            "end_exclusive_utc":b.isoformat().replace("+00:00","Z"),
            "status":"CANDIDATE_NOT_PROVIDER_VERIFIED",
            "request_ready":False,
        } for a,b in missing],
        "provider_availability":"NOT_CHECKED",
        "historical_completeness":"NOT_VERIFIED",
        "execution_authorized":False,
    }
