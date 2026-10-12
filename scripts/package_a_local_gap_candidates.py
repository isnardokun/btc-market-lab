"""Offline, fail-closed historical gap evidence over caller-owned SQLite.

Only evidence from the Package A ledger is read. A missing slot NEVER proves
that upstream can supply it. This module has no CLI, network, API key, SQLite
default path, enqueue/apply operation or production side effect.
"""
import datetime as dt

from ingestion.package_a_contracts import SeriesSpec, utc

SUPPORTED = frozenset({"5m", "d1"})


def _stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _blocked(spec, state, *, invalid_count=0, sampled_invalid=None):
    """Make denial of request-readiness consistent on every fail-closed path."""
    return {
        "state": state,
        "provider": spec.provider,
        "metric": spec.metric,
        "symbol": spec.symbol,
        "frequency": spec.frequency,
        "candidates": [],
        "invalid_observation_count": invalid_count,
        "invalid_observation_sample": (sampled_invalid or [])[:5],
        "provider_availability": "NOT_CHECKED",
        "historical_completeness": "NOT_VERIFIED",
        "request_ready": False,
        "execution_authorized": False,
    }


def scan_archived_gaps(conn, spec, start, end, *, max_expected=10000, max_windows=100):
    """Locate locally missing timestamps without treating anomalies as gaps.

    Fixed grids apply only to BTC Yahoo 1d, ResearchBitcoin/Bitview 1d and
    Binance/Bybit 5m OI. FRED calendars, equity sessions and settled-funding
    events must NEVER be projected as daily or five-minute observations.

    Every selected local observation must be UTC-canonical, on grid, scalar,
    unit/scale-consistent and linked to a successful matching receipt whose
    request window contains it. On drift/corruption return BLOCKED, not a
    misleading negative or zero missing-slot count.
    """
    if not isinstance(spec, SeriesSpec):
        raise ValueError("A typed SeriesSpec is required")
    if spec.provider == "yahoo" and spec.symbol != "BTC-USD":
        raise ValueError("Calendar-aware Yahoo symbols require external schedule")
    native_grid = (
        (spec.frequency == "5m" and spec.provider in ("binance", "bybit")
         and spec.metric == "open_interest")
        or (spec.frequency == "d1" and spec.provider in
            ("yahoo", "bitview", "researchbitcoin"))
    )
    if not native_grid:
        return _blocked(spec, "UNSUPPORTED_CADENCE_NO_INFERRED_GAPS")
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
        raise ValueError("Local gap scan exceeds allowed budget")

    key = (spec.provider, spec.metric, spec.symbol, spec.frequency)
    # Prefix bounds also see a noncanonical +00:00 or fractional stored time
    # so it is DENIED as drift, not silently hidden by lexical comparisons.
    selected = list(conn.execute(
        "SELECT o.observed_utc,o.dimension_key,o.unit,o.raw_scale,"
        "r.provider,r.metric,r.symbol,r.frequency,r.start_utc,"
        "r.end_exclusive_utc,r.status FROM package_a_observations o "
        "LEFT JOIN package_a_receipts r ON r.receipt_id=o.receipt_id "
        "WHERE o.provider=? AND o.metric=? AND o.symbol=? AND o.frequency=? "
        "AND o.observed_utc>=? AND o.observed_utc<? "
        "ORDER BY o.observed_utc,o.dimension_key LIMIT ?",
        (*key, _stamp(begin)[:19], _stamp(stop)[:19], slots + 1),
    ))
    if len(selected) > slots:
        return _blocked(spec, "BLOCKED_SOURCE_OBSERVATION_DRIFT",
                        invalid_count=len(selected),
                        sampled_invalid=[row[0] for row in selected[:5]])
    stamps = set()
    invalid = []
    for row in selected:
        raw, dimension, unit, scale, rp, rm, rs, rf, left, right, status = row
        try:
            moment = utc(raw)
            canonical = raw == _stamp(moment)
            grid_aligned = (moment - begin) % step == dt.timedelta(0)
            receipt_bounds = (left is not None and right is not None
                              and utc(left) <= moment < utc(right))
        except (TypeError, ValueError, OverflowError):
            canonical = grid_aligned = receipt_bounds = False
            moment = None
        if not (canonical and begin <= moment < stop and grid_aligned
                and dimension == "" and unit == spec.unit
                and scale == spec.raw_scale and (rp, rm, rs, rf) == key
                and status == "ok" and receipt_bounds
                and moment not in stamps):
            invalid.append(raw)
        else:
            stamps.add(moment)
    if invalid:
        return _blocked(spec, "BLOCKED_SOURCE_OBSERVATION_DRIFT",
                        invalid_count=len(invalid), sampled_invalid=invalid)

    missing = []
    head = None
    for index in range(slots):
        moment = begin + step * index
        if moment not in stamps:
            if head is None:
                head = moment
        elif head is not None:
            missing.append((head, moment))
            head = None
    if head is not None:
        missing.append((head, stop))
    if len(missing) > max_windows:
        result = _blocked(spec, "BLOCKED_GAP_BUDGET")
        result["candidate_window_count"] = len(missing)
        return result
    return {
        "state": "OFFLINE_LOCAL_GAP_CANDIDATES",
        "provider": spec.provider, "metric": spec.metric,
        "symbol": spec.symbol, "frequency": spec.frequency,
        "expected_grid_slots": slots,
        "observed_distinct_timestamps": len(stamps),
        "missing_candidate_slots": slots - len(stamps),
        "candidates": [{
            "start_utc": _stamp(a), "end_exclusive_utc": _stamp(b),
            "status": "CANDIDATE_NOT_PROVIDER_VERIFIED",
            "request_ready": False,
        } for a, b in missing],
        "provider_availability": "NOT_CHECKED",
        "historical_completeness": "NOT_VERIFIED",
        "request_ready": False,
        "execution_authorized": False,
    }
