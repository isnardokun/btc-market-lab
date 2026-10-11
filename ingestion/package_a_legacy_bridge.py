"""Package A compatibility checks against EXISTING vetted historical parsers.

The new integrated ledger must not invent a second interpretation of source
payloads when trusted repository parsers already exist. Cross-check on fixture
bytes only: no fetch, connection, side effect, external entitlement or CLI.
"""
import datetime as dt

from ingestion import bitview_history, yahoo_history, researchbitcoin_v2
from ingestion.package_a_contracts import SeriesSpec, parse_fixture, utc


def reconciled_fixture_points(spec, payload, *, start, end, now):
    """Parse strict page AND check available legacy parser against same points.

    RBN: preserves scalar and fraction scale (never multiplies percentages).
    Bitview: preserves 2009 epoch day-index mapping.
    Yahoo: validates close prices against existing OHLCV parser.

    FRED/Binance/Bybit have no identical pure parser shape shared by
    independent legacy modules, so their new strict contracts remain
    explicitly synthetic and provider-live schema NOT VERIFIED.
    """
    if not isinstance(spec, SeriesSpec):
        raise ValueError("Explicit typed provider contract required")
    current = utc(now)
    begin, stop = utc(start), utc(end)
    points = parse_fixture(spec, payload, start=start, end=end)
    if not points:
        return points
    existing = None
    if spec.provider == "researchbitcoin":
        parsed = researchbitcoin_v2.parse_scalar_rows(
            payload, spec.metric, now=current)
        existing = {
            dt.datetime.combine(dt.date.fromisoformat(day), dt.time(),
                                dt.timezone.utc).isoformat().replace("+00:00","Z"): value
            for day, (_, value) in parsed.items()
        }
    elif spec.provider == "bitview":
        if (begin.time() != dt.time() or stop.time() != dt.time()
                or (stop - begin).total_seconds() % 86400 != 0):
            raise ValueError("Bitview fixture boundaries must be whole UTC days")
        prior = bitview_history.decode_day1_response(
            payload, begin.date(), (stop-dt.timedelta(days=1)).date())
        existing = {
            dt.datetime.fromtimestamp(ts,dt.timezone.utc).isoformat().replace(
                "+00:00","Z"):value for ts,value in prior
        }
    elif spec.provider == "yahoo":
        candles = yahoo_history.extract_daily(payload,now=current)
        existing = {
            dt.datetime.fromtimestamp(ts,dt.timezone.utc).isoformat().replace(
                "+00:00","Z"):float(close)
            for ts,open_,high,low,close,volume in candles
            if begin <= dt.datetime.fromtimestamp(ts,dt.timezone.utc) < stop
        }
    if existing is not None:
        fresh = {point.timestamp:point.value for point in points}
        if existing != fresh or len(existing) != len(points):
            raise ValueError("Existing parser and package adapter disagree; stop")
    return points
