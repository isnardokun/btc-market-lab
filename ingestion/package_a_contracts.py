"""Package A: strict source-native OFFLINE payload contracts.

This module never opens a database or network connection. It accepts fixture
JSON shapes and does NOT assert that live providers return these schemas.
Exact live response contracts and entitled historical windows remain unverified.
"""
from dataclasses import dataclass
import datetime as dt
import math

from ingestion.researchbitcoin_catalog import CATALOG
from ingestion.bitview_history import EPOCH

UTC = dt.timezone.utc
PROVIDERS = frozenset({"binance", "bybit", "bitview", "researchbitcoin", "fred", "yahoo"})


def utc(value):
    if not isinstance(value, str):
        raise ValueError("UTC timestamp required")
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid UTC timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() != dt.timedelta(0):
        raise ValueError("UTC timestamp with zero offset required")
    return stamp.astimezone(UTC).replace(microsecond=0)


def daystamp(value):
    if not isinstance(value, str):
        raise ValueError("Canonical ISO calendar date required")
    try:
        day = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid date") from exc
    if value != day.isoformat():
        raise ValueError("Noncanonical date")
    return dt.datetime.combine(day, dt.time(), UTC)


def epoch(value, *, millis):
    if type(value) is not int or value < 0:
        raise ValueError("Integer epoch timestamp required")
    try:
        return dt.datetime.fromtimestamp(value / (1000 if millis else 1), UTC).replace(microsecond=0)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Invalid epoch range") from exc


def scalar(value):
    if type(value) not in (str, int, float):
        raise ValueError("Scalar required")
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError("Unparseable number") from exc
    if not math.isfinite(number):
        raise ValueError("Nonfinite observation")
    return number


@dataclass(frozen=True)
class SeriesSpec:
    provider: str
    metric: str
    symbol: str
    frequency: str
    unit: str
    raw_scale: str = "native"

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError("Unknown provider")
        for value in (self.metric, self.symbol, self.frequency, self.unit, self.raw_scale):
            if not isinstance(value, str) or not value or len(value) > 120:
                raise ValueError("Invalid source contract field")
        if self.provider == "researchbitcoin":
            catalog = CATALOG.get(self.metric)
            if (catalog is None or self.frequency != "d1"
                    or catalog.unit != self.unit or catalog.raw_scale != self.raw_scale):
                raise ValueError("RBN catalog/unit/scale mismatch")
        if self.provider == "bitview" and self.frequency != "d1":
            raise ValueError("Only catalogued Bitview daily fixture is supported")
        if self.provider in ("binance", "bybit"):
            supported = (
                (self.metric == "open_interest" and self.frequency == "5m" and self.unit == "BTC")
                or (self.metric == "funding_settled" and self.frequency == "event"
                    and self.unit == "fraction")
            )
            if self.symbol != "BTCUSDT" or not supported or self.raw_scale != "native":
                raise ValueError("Only BTCUSDT OI 5m or native funding settlement accepted")
        if self.provider == "yahoo":
            yahoo_metrics = {"open", "high", "low", "close", "volume"}
            # Explicit fixture-only scope, not an entitlement claim. Shares
            # volumes have no assumed currency conversion; BTC native volume
            # may vary by Yahoo instrument/data contract.
            expected_unit = "native_volume" if self.metric == "volume" else "USD"
            if (self.frequency != "d1" or self.metric not in yahoo_metrics
                    or self.symbol not in ("BTC-USD", "SPY", "GLD")
                    or self.unit != expected_unit or self.raw_scale != "native"):
                raise ValueError("Yahoo supported offline OHLCV scope mismatch")
        if self.provider == "fred" and self.frequency not in ("d1", "monthly", "weekly"):
            raise ValueError("Unsupported FRED cadence")


@dataclass(frozen=True)
class Point:
    timestamp: str
    dimension: str
    value: float


def _point(when, value, dimension=""):
    return Point(when.isoformat().replace("+00:00", "Z"), dimension, scalar(value))


def parse_fixture(spec, payload, *, start, end):
    """Parse a single strictly bounded fixture page without silent data drops."""
    if not isinstance(spec, SeriesSpec) or not isinstance(payload, (dict, list)):
        raise ValueError("Invalid fixture contract or JSON payload")
    begin, stop = utc(start), utc(end)
    if begin >= stop:
        raise ValueError("Empty/reversed window")
    if spec.provider == "researchbitcoin" and stop - begin > dt.timedelta(days=14):
        raise ValueError("RBN max fixture window is 14 days")
    out = []

    if spec.provider == "researchbitcoin":
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("RBN fixture requires data list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Malformed RBN row")
            out.append(_point(daystamp(row["date"]), row["value"]))
    elif spec.provider == "bitview":
        if not isinstance(payload, dict) or payload.get("index") != "1d" or type(payload.get("start")) is not int:
            raise ValueError("Bitview requires verified day-index fixture")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ValueError("Bitview array missing")
        first = EPOCH + dt.timedelta(days=payload["start"])
        for offset, value in enumerate(rows):
            if value is not None:
                out.append(_point(dt.datetime.combine(first + dt.timedelta(days=offset), dt.time(), UTC), value))
    elif spec.provider == "fred":
        rows = payload.get("observations") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("FRED observations missing")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("realtime_start"), str):
                raise ValueError("FRED vintage is mandatory")
            # Keep the observation date independent from vintage date.
            vintage = daystamp(row["realtime_start"]).date().isoformat()
            out.append(_point(daystamp(row["date"]), row["value"], vintage))
    elif spec.provider == "yahoo":
        try:
            chart = payload["chart"]
            candle = chart["result"][0]
            ticks = candle["timestamp"]
            quote = candle["indicators"]["quote"][0]
            values = quote[spec.metric]
            if (not isinstance(ticks, list) or not isinstance(values, list)
                    or len(ticks) != len(values) or not ticks):
                raise ValueError("Mismatched Yahoo OHLCV quote arrays")
            # Same bar data supports close-only legacy payloads. But if OHLC
            # fields are present, their lengths and price range must be sane,
            # even when the requested series is a different field.
            fields = ("open", "high", "low", "close", "volume")
            for field in fields:
                part = quote.get(field)
                if part is not None and (
                        not isinstance(part, list) or len(part) != len(ticks)):
                    raise ValueError("Malformed Yahoo quote length: " + field)
            if chart.get("error") is not None:
                raise ValueError("Yahoo chart error response")
            seen_days = set()
            for i, (tick, value) in enumerate(zip(ticks, values)):
                moment = epoch(tick, millis=False)
                # SPY/GLD regular sessions have nonmidnight UTC timestamps;
                # store the UTC trading date, not an invented 00:00 exchange
                # calendar grid. This does NOT certify NYSE holidays.
                when = dt.datetime.combine(moment.date(), dt.time(), UTC)
                if when in seen_days:
                    raise ValueError("Duplicate Yahoo daily session date")
                seen_days.add(when)
                bar = {}
                for field in fields:
                    part = quote.get(field)
                    bar[field] = (
                        scalar(part[i]) if part is not None and
                        part[i] is not None else None)
                if bar["volume"] is not None and bar["volume"] < 0:
                    raise ValueError("Negative Yahoo native volume")
                for field in ("open", "high", "low", "close"):
                    if bar[field] is not None and bar[field] <= 0:
                        raise ValueError("Nonpositive Yahoo " + field)
                low,high = bar["low"],bar["high"]
                if low is not None and high is not None:
                    if low > high:
                        raise ValueError("Yahoo low exceeds high")
                    if any(bar[k] is not None and
                           not low <= bar[k] <= high for k in ("open","close")):
                        raise ValueError("Yahoo open or close outside OHLC range")
                if value is None:
                    continue
                out.append(_point(when, value))
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ValueError("Malformed Yahoo fixture") from exc
    elif spec.provider == "binance":
        if not isinstance(payload, list):
            raise ValueError("Binance derivatives history fixture must be a list")
        for row in payload:
            if not isinstance(row, dict) or row.get("symbol", "BTCUSDT") != "BTCUSDT":
                raise ValueError("Malformed Binance BTCUSDT row")
            if spec.metric == "funding_settled":
                out.append(_point(epoch(row["fundingTime"], millis=True), row["fundingRate"]))
            else:
                out.append(_point(epoch(row["timestamp"], millis=True), row["sumOpenInterest"]))
    elif spec.provider == "bybit":
        try:
            if spec.metric == "funding_settled" and payload.get("retCode") != 0:
                raise ValueError("Bybit settlement requires successful response code")
            part = payload["result"]
            if (spec.metric == "funding_settled"
                    and (part.get("category", "linear") != "linear"
                         or part.get("symbol", "BTCUSDT") != "BTCUSDT")):
                raise ValueError("Bybit funding result scope mismatch")
            rows = part["list"]
            if not isinstance(rows, list):
                raise ValueError("Malformed Bybit list")
            for row in rows:
                key = "fundingRateTimestamp" if spec.metric == "funding_settled" else "timestamp"
                val = "fundingRate" if spec.metric == "funding_settled" else "openInterest"
                millis = int(row[key]) if isinstance(row[key], str) and row[key].isdigit() else row[key]
                out.append(_point(epoch(millis, millis=True), row[val]))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Malformed Bybit derivatives fixture") from exc

    seen = set()
    for point in out:
        stamp = utc(point.timestamp)
        key = (point.timestamp, point.dimension)
        if not begin <= stamp < stop:
            raise ValueError("Observation outside requested half-open window")
        if key in seen:
            raise ValueError("Duplicate (timestamp, dimension) in one provider page")
        seen.add(key)
        if spec.frequency in ("d1", "monthly", "weekly") and stamp.time() != dt.time():
            raise ValueError("Date-based observations require midnight UTC")
        if spec.frequency == "5m" and stamp.timestamp() % 300 != 0:
            raise ValueError("Unaligned OI five-minute timestamp")
    if spec.frequency == "5m" and out:
        ordered = sorted(out, key=lambda p:p.timestamp)
        if ordered[0].timestamp != begin.isoformat().replace("+00:00", "Z"):
            raise ValueError("OI page begins with an unacknowledged gap")
        expected_final = (stop - dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        if (stop - begin).total_seconds() % 300 != 0 or ordered[-1].timestamp != expected_final:
            raise ValueError("Incomplete OI final bar; cannot advance cursor")
        if any(utc(y.timestamp) - utc(x.timestamp) != dt.timedelta(minutes=5)
               for x,y in zip(ordered, ordered[1:])):
            raise ValueError("Discontinuous OI five-minute page")
    return tuple(sorted(out, key=lambda p:(p.timestamp,p.dimension)))
