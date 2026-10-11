"""Package A: real legacy request builders + STRICTLY frozen fake HTTP bytes.

The module has NO live transport function or public apply CLI. A synthetic
opener can only return constructor-supplied bytes, and rejects any unexpected
endpoint, query, host, HTTP method, additional call, redirect or auth token.
The original upstream client/request builder is invoked with this opener,
then the same RAW bytes enter Package A's transactional fixture ledger.

This proves compatibility with source request construction under mock HTTP,
NOT real upstream retention, entitlement, API schema or deployment readiness.
"""
import datetime as dt
import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from ingestion import bitview_history, config, ingest, researchbitcoin_v2, yahoo_history
from ingestion.market_context_sources import download, SOURCE_URLS
from ingestion.package_a_contracts import SeriesSpec, utc
from ingestion.researchbitcoin_catalog import BASE_URL, CATALOG
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy
from scripts.package_a_ingest_fixture import (
    FixtureResponse, _fixture_guard, run_fixture_page, LOCK_NAMESPACE,
)

FAKE_KEY = "PACKAGE_A_OFFLINE_FAKE_TOKEN_NO_EXTERNAL_ACCESS"
MAX_RAW = 2_000_000
ALLOWED_STATUS = frozenset((200, 429, 500, 503))


class _FrozenResponse:
    """Response implementing urllib's context/read/url contract; no sockets."""
    def __init__(self, url, body):
        self.url = url
        self._buffer = io.BytesIO(body)

    def read(self, size=-1):
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._buffer.close()


class FrozenLegacyHTTP:
    """Fail-closed one-shot opener. Never delegates to urllib's real opener."""
    def __init__(self, *, url, params, body, status, rbn=False):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.query
                or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("Frozen transport requires a literal HTTPS endpoint")
        if (not isinstance(params, dict) or
                any(type(k) is not str or type(v) is not str for k, v in params.items())):
            raise ValueError("Frozen transport requires exact scalar query params")
        if (type(body) is not bytes or
                (status is None and body != b"") or
                (status is not None and not 0 < len(body) <= MAX_RAW)):
            raise ValueError("Only bounded in-memory frozen bytes permitted")
        if status is not None and (type(status) is not int or status not in ALLOWED_STATUS):
            raise ValueError("Unknown mocked HTTP status")
        self._endpoint = (parsed.scheme, parsed.netloc, parsed.path)
        self._params = params
        self.body = body
        self.status = status
        self._rbn = rbn
        self.calls = 0

    def open(self, req, timeout=None):
        if not isinstance(req, Request) or req.get_method() != "GET":
            raise ValueError("Frozen transport rejects unrecognized HTTP method")
        parsed = urlsplit(req.full_url)
        params = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
        if ((parsed.scheme, parsed.netloc, parsed.path) != self._endpoint
                or parsed.fragment or parsed.username or parsed.password
                or params != {key: [value] for key, value in self._params.items()}):
            raise ValueError("BLOCKED_FROZEN_HTTP_ENDPOINT_OR_QUERY_DRIFT")
        if (self._rbn and req.get_header("X-api-token") != FAKE_KEY):
            raise ValueError("BLOCKED_NON_FIXTURE_AUTH_TOKEN")
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError("Frozen HTTP timeout must be bounded")
        if self.calls:
            raise ValueError("BLOCKED_FROZEN_HTTP_MULTI_REQUEST")
        self.calls += 1
        if self.status is None:
            raise TimeoutError("INJECTED_OFFLINE_TRANSPORT_TIMEOUT")
        if self.status != 200:
            raise HTTPError(req.full_url, self.status, "INJECTED_OFFLINE_STATUS", None, None)
        return _FrozenResponse(req.full_url, self.body)


def _utc_ms(value):
    return str(int(utc(value).timestamp() * 1000))


def _request_for(spec, start, end):
    """An independently checked HTTP contract for selected legacy clients."""
    first, last = utc(start), utc(end)
    if first >= last:
        raise ValueError("Invalid half-open source request")
    a, b = first.date().isoformat(), last.date().isoformat()
    if spec.provider in ("researchbitcoin", "bitview", "fred", "yahoo"):
        if first.time() != dt.time() or last.time() != dt.time():
            raise ValueError("Daily/vintage request boundaries must be UTC-midnight")
    if spec.provider == "researchbitcoin":
        item = CATALOG[spec.metric]
        if (spec.symbol != "BTC" or spec.frequency != "d1" or
                last - first > dt.timedelta(days=14)):
            raise ValueError("RBN fixture source scope mismatch")
        return (BASE_URL + item.endpoint + "/" + item.slug,
                {"from_time": a, "to_time": b, "resolution": "d1",
                 "output_format": "json"})
    if spec.provider == "bitview":
        item = config.SERIES.get(spec.metric)
        if (spec.symbol != "BTC" or not item or item[1] != "1d"
                or spec.frequency != "d1"):
            raise ValueError("Bitview fixture series must exist in legacy d1 catalog")
        # Bitview's legacy fetch_series() accepts inclusive query end.
        return (config.API_BASE + "/series/" + item[0] + "/1d",
                {"start": a, "end": (last - dt.timedelta(days=1)).date().isoformat()})
    if spec.provider == "yahoo":
        if (spec.symbol != "BTC-USD" or spec.metric != "close"
                or spec.frequency != "d1" or spec.unit != "USD"):
            raise ValueError("Yahoo BTC-close legacy fixture only")
        return ("https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD",
                {"interval": "1d", "range": "max"})
    if spec.provider == "fred":
        aliases = {"daily": "d1", "monthly": "monthly", "weekly": "weekly"}
        approved = {name: freq for name, _, freq, _ in config.FRED_SERIES}
        if (spec.symbol != "USA" or approved.get(spec.metric) is None
                or aliases.get(approved[spec.metric]) != spec.frequency):
            raise ValueError("FRED fixture series/frequency mismatch")
        return (config.FRED_ENDPOINT,
                {"series_id": spec.metric, "api_key": FAKE_KEY,
                 "observation_start": a,
                 "observation_end": (last - dt.timedelta(days=1)).date().isoformat(),
                 "file_type": "json"})
    if spec.provider in ("binance", "bybit"):
        if spec.symbol != "BTCUSDT":
            raise ValueError("Derivative source scope mismatch")
        root = SOURCE_URLS[spec.provider]
        if spec.provider == "binance":
            if spec.metric == "open_interest":
                return (root + "/futures/data/openInterestHist",
                        {"symbol": "BTCUSDT", "period": "5m", "limit": "500",
                         "startTime": _utc_ms(start),
                         "endTime": str(int(utc(end).timestamp() * 1000) - 1)})
            return (root + "/fapi/v1/fundingRate",
                    {"symbol": "BTCUSDT", "limit": "1000",
                     "startTime": _utc_ms(start),
                     "endTime": str(int(utc(end).timestamp() * 1000) - 1)})
        base = {"category": "linear", "symbol": "BTCUSDT",
                "startTime": _utc_ms(start),
                "endTime": str(int(utc(end).timestamp() * 1000) - 1),
                "limit": "200"}
        if spec.metric == "open_interest":
            base["intervalTime"] = "5min"
            return root + "/v5/market/open-interest", base
        return root + "/v5/market/funding/history", base
    raise ValueError("Unsupported offline legacy source")


def _legacy_fake_fetch(spec, start, end, frozen):
    """Execute existing source client, strictly with the one-shot fake opener."""
    if spec.provider == "researchbitcoin":
        return researchbitcoin_v2.fetch(
            spec.metric, start_day=utc(start).date(), end_day=utc(end).date(),
            token=FAKE_KEY, opener=frozen.open)
    if spec.provider == "yahoo":
        return yahoo_history.fetch_max(spec.symbol, opener=frozen.open)
    if spec.provider == "bitview":
        # Preserve the original fetch_series URL/path/query construction.
        def fake_api(path):
            with frozen.open(Request(config.API_BASE + path), timeout=30) as r:
                return json.loads(r.read(MAX_RAW + 1))
        return ingest.fetch_series(
            spec.metric, "1d", start=utc(start).date().isoformat(),
            end=(utc(end) - dt.timedelta(days=1)).date().isoformat(),
            client=fake_api)
    # The existing allowlisted downloader validates host, redirect and payload
    # size; only its opener is substituted with frozen in-memory bytes.
    url, params = _request_for(spec, start, end)
    with_payload = download(url, params, opener=frozen.open)
    return json.loads(with_payload)


def run_mocked_legacy_page(db_path, lock_directory, spec, start, end,
                           *, raw_bytes, status_code=200, now, fault=None):
    """Full lock -> real legacy builder -> frozen HTTP -> RAW/SQL on temp DB.

    Not a network client. Never accepts an HTTP callable or arbitrary URL.
    Returns no live-capability flag, even on successful synthetic ingestion.
    """
    if not isinstance(spec, SeriesSpec):
        raise ValueError("Exact source contract required")
    _fixture_guard(db_path, lock_directory)
    utc(now)
    endpoint, query = _request_for(spec, start, end)
    frozen = FrozenLegacyHTTP(
        url=endpoint, params=query, body=raw_bytes, status=status_code,
        rbn=(spec.provider == "researchbitcoin"))
    try:
        # Required global lock begins BEFORE even the fake HTTP operation.
        # The fixture-only page writer is explicitly told the parent holds it.
        with hold_writer_lock(lock_directory, namespace=LOCK_NAMESPACE):
            try:
                parsed = _legacy_fake_fetch(spec, start, end, frozen)
            except (HTTPError, RuntimeError, TimeoutError):
                if status_code == 200 or frozen.calls != 1:
                    raise
                # 429/5xx bodies are frequently HTML/plain text, not JSON.
                # The upstream client must see the HTTP error, but the page
                # ledger retains original opaque bytes and does not advance.
                parsed = None
            if (frozen.calls != 1 or
                    (status_code == 200 and parsed != json.loads(raw_bytes))):
                raise ValueError("BLOCKED_LEGACY_PARSER_RAW_MISMATCH")
            response = FixtureResponse(
                status_code, parsed, raw_bytes=(raw_bytes if status_code is not None else None))
            outcome = run_fixture_page(
                db_path, lock_directory, spec, start, end, response,
                now=now, fault=fault, _parent_lock_held=True)
            return {
                **outcome,
                "frozen_http_requests": frozen.calls,
                "legacy_source_request_builder_used": True,
                "original_raw_bytes_preserved": status_code is not None,
                "upstream_api_verified": False,
                "provider_entitlement_verified": False,
                "live_http_requests": 0,
            }
    except WriterLockBusy:
        return {
            "state": "BLOCKED_WRITER_BUSY", "frozen_http_requests": frozen.calls,
            "live_http_requests": 0, "sqlite_connections_started": 0,
            "production_authorized": False,
            "historical_completeness": "NOT_VERIFIED",
        }
