"""R10-E.3: fully synthetic one-page HTTP and SQLite v3 end-to-end QA.

Always creates its own TemporaryDirectory SQLite. No CLI, no --apply, no
production path input, no API client, no real network and no reusable writer.
This code is only an offline test harness for the already existing v3 pilot.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError

from storage import market_context as store
from scripts import market_history_forward as forward
from scripts import market_raw_reconcile as raw_check
from scripts import market_temporal_quality as temporal_check
from scripts import market_request_lineage_audit as lineage_check

UTC = dt.timezone.utc
CLOCK = dt.datetime(2026, 10, 10, 22, 0, tzinfo=UTC)
BASE = dt.datetime(2026, 10, 10, 21, 25, tzinfo=UTC)
FUNDING = dt.datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
TABLES = (
    "market_context_migrations", "market_source_cursors",
    "market_source_runs", "market_raw_payloads",
    "market_derivatives", "market_etf_flows",
    "market_calendar_events", "market_context_revisions",
    "market_request_lineage", "market_request_acquisitions",
)


def _timestamp_ms(at):
    return int(at.timestamp() * 1000)


def _body(provider, metric, points):
    if provider == "binance":
        if metric == "open_interest":
            body = [{"symbol": "BTCUSDT", "timestamp": str(_timestamp_ms(at)),
                     "sumOpenInterest": str(val), "sumOpenInterestValue": str(val * 2)}
                    for at, val in points]
        else:
            body = [{"symbol": "BTCUSDT", "fundingTime": _timestamp_ms(at),
                     "fundingRate": str(val)} for at, val in points]
    else:
        if metric == "open_interest":
            body = [{"timestamp": str(_timestamp_ms(at)), "openInterest": str(val)}
                    for at, val in points]
        else:
            body = [{"fundingRateTimestamp": str(_timestamp_ms(at)),
                     "fundingRate": str(val)} for at, val in points]
        body = {"retCode": 0, "retMsg": "OK",
                "result": {"category": "linear", "symbol": "BTCUSDT", "list": body}}
    return json.dumps(body, separators=(",", ":")).encode()


def _endpoint(provider, metric):
    if provider == "binance":
        root = "https://fapi.binance.com"
        suffix = ("/futures/data/openInterestHist" if metric == "open_interest"
                  else "/fapi/v1/fundingRate")
    else:
        root = "https://api.bybit.com"
        suffix = ("/v5/market/open-interest" if metric == "open_interest"
                  else "/v5/market/funding/history")
    return root + suffix


def _seed(db):
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(store.DDL)
    db.execute("INSERT INTO market_context_migrations VALUES (1,?)",
               (store.now_utc(),))
    store.install_request_lineage(db)
    for provider in ("binance", "bybit"):
        for metric, observed, value in (
            ("open_interest", BASE, 1234.0),
            ("funding_settled", FUNDING, 0.0001),
        ):
            body = _body(provider, metric, [(observed, value)])
            endpoint = _endpoint(provider, metric)
            sha = store.raw_payload(db, provider, endpoint, body, "application/json")
            store.store_derivative(
                db, provider=provider, symbol="BTCUSDT", metric=metric,
                observed_utc=observed.isoformat(), interval_label=(
                    "5m" if metric == "open_interest" else "settlement"),
                value=value, unit=("BTC" if metric == "open_interest" else "fraction"),
                endpoint=endpoint, sha=sha,
                quote_usd=value * 2 if provider == "binance" and metric == "open_interest"
                else None)
    db.commit()


def _fingerprints(db):
    result = {}
    for table in TABLES:
        cols = db.execute("PRAGMA table_info(" + table + ")").fetchall()
        if not cols:
            raise ValueError("Missing fixture table: " + table)
        pk = [name for _, name, _, _, _, order in
              sorted(cols, key=lambda c: c[-1]) if order]
        if not pk:
            raise ValueError("No PK in fixture table: " + table)
        all_cols = [c[1] for c in cols]
        rows = db.execute("SELECT * FROM " + table).fetchall()
        keys = []
        hashes = []
        for row in rows:
            key = tuple(row[all_cols.index(name)] for name in pk)
            keys.append(key)
            hashes.append(hashlib.sha256(repr(row).encode("utf-8")).hexdigest())
        result[table] = dict(zip(keys, hashes))
    return result


def run_mock_cycle(provider, scenario="success"):
    """One mock invocation in a NEW short-lived synthetic DB, never a real path.

    Return only aggregate synthetic evidence. No external DB/network options.
    """
    if provider not in ("binance", "bybit"):
        raise ValueError("Unsupported fixture provider")
    if scenario not in ("success", "empty", "http429", "gap"):
        raise ValueError("Unsupported fixture scenario")
    with tempfile.TemporaryDirectory(prefix="r10e3-only-synthetic-") as tmp:
        path = Path(tmp) / "synthetic.db"
        with sqlite3.connect(path) as db:
            _seed(db)
            before = _fingerprints(db)
            called = []
            next_bar = BASE + dt.timedelta(minutes=5)

            def fake_fetch(url, params):
                called.append((url, dict(params)))
                if url != _endpoint(provider, "open_interest"):
                    raise AssertionError("Unexpected fixture endpoint")
                if (params.get("startTime") != _timestamp_ms(next_bar)
                        or params.get("symbol") != "BTCUSDT"):
                    raise AssertionError("Unexpected fixture boundary")
                if params.get("limit") != (500 if provider == "binance" else 200):
                    raise AssertionError("Unexpected fixture page budget")
                if scenario == "http429":
                    raise HTTPError(url, 429, "synthetic rate-limit", {}, None)
                if scenario == "empty":
                    return _body(provider, "open_interest", [])
                first = next_bar + (dt.timedelta(minutes=5) if scenario == "gap"
                                    else dt.timedelta())
                return _body(provider, "open_interest", [
                    (first, 1250.0),
                    (first + dt.timedelta(minutes=5), 1251.0),
                ])

            cutoff = _timestamp_ms(CLOCK - dt.timedelta(minutes=10))
            with patch.object(forward, "_cutoff_now", return_value=cutoff):
                outcome = forward.execute(
                    db, provider, "open_interest", fetch=fake_fetch)
            if len(called) != 1:
                raise AssertionError("Fixture must have exactly one mocked fetch")
            after = _fingerprints(db)
            changed = {
                t: {"added": len(set(after[t]) - set(before[t])),
                    "deleted": len(set(before[t]) - set(after[t])),
                    "modified": sum(after[t].get(k) != v for k, v in before[t].items()
                                    if k in after[t])}
                for t in TABLES
            }
            db.commit()

        # Independent audits, strictly one shared SQLite read-only snapshot.
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                             uri=True) as ro:
            ro.execute("PRAGMA query_only=ON")
            ro.execute("PRAGMA foreign_keys=ON")
            ro.execute("BEGIN")
            raw = raw_check.audit(path, connection=ro)
            temporal = temporal_check.inspect(path, now=CLOCK, connection=ro)
            lineage = lineage_check.audit(path, connection=ro)
            integrity = ro.execute("PRAGMA integrity_check").fetchone()[0]
            fk_errors = len(ro.execute("PRAGMA foreign_key_check").fetchall())
            ro.rollback()

        gates = {
            "RAW": raw.get("state"),
            "TEMPORAL": temporal.get("quality_state"),
            "LINEAGE": lineage.get("quality_state"),
            "SQLITE": integrity,
            "FOREIGN_KEY_ERRORS": fk_errors,
        }
        quality_ok = (gates["RAW"] == "PASS_SQLITE_TO_RAW"
                      and gates["TEMPORAL"] == "SNAPSHOT_INTERNAL_QA_OK"
                      and gates["LINEAGE"] == "PASS_REQUEST_LINEAGE"
                      and gates["SQLITE"] == "ok"
                      and fk_errors == 0)
        immutability = all(v["deleted"] == v["modified"] == 0
                           for v in changed.values())
        correct_status = outcome.get("status") == (
            "success" if scenario == "success" else
            "empty" if scenario == "empty" else "failed")
        expected_rows = 2 if scenario == "success" else 0
        accepted = (quality_ok and immutability and correct_status
                    and outcome.get("points") == expected_rows)
        return {
            "state": "MOCK_FIXTURE_PASS" if accepted else "MOCK_FIXTURE_FAIL",
            "provider": provider, "scenario": scenario,
            "http_real_requests": 0, "mock_fetch_calls": len(called),
            "production_db_reads": 0, "production_db_writes": 0,
            "historical_completeness": "NOT_VERIFIED",
            "apply_result": outcome,
            "audit_gates": gates, "table_deltas": changed,
            "request_receipts": lineage.get("requests_total"),
            "verified_receipts": lineage.get("requests_verified"),
        }
