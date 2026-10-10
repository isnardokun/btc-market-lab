"""R10-C.4: genuine auditors, non-mocked, on fully coherent v2 SQLite.

All data are synthetic fixtures in TemporaryDirectory. The three production
auditor FUNCTIONS execute directly, but no production DB or provider API is
touched. Historical completeness is NOT_VERIFIED even when this suite passes.
"""
import contextlib
import datetime as dt
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts import market_r10c_quality_gate as gate
from storage import market_context as m

BASE = dt.datetime(2026, 10, 10, 0, 0, tzinfo=dt.timezone.utc)
BASE_MS = int(BASE.timestamp() * 1000)
INTERVAL_MS = 300_000
RECEIPTS = 102
URLS = {
    ("binance", "open_interest"):
        "https://fapi.binance.com/futures/data/openInterestHist",
    ("binance", "funding_settled"):
        "https://fapi.binance.com/fapi/v1/fundingRate",
    ("bybit", "open_interest"):
        "https://api.bybit.com/v5/market/open-interest",
    ("bybit", "funding_settled"):
        "https://api.bybit.com/v5/market/funding/history",
}


def _iso(ms):
    return dt.datetime.fromtimestamp(
        ms / 1000, dt.timezone.utc).isoformat(timespec="seconds")


class RealAuditorsOnCoherentV2Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic-only.db"
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(m.DDL)
            db.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc)"
                "VALUES(1,?)", ("2026-10-10T00:00:00+00:00",))
            m.install_request_lineage_v2_only(db)
            self._seed_four_snapshot_streams(db)
            self._seed_102_valid_backward_receipts(db)

    @staticmethod
    def _blob(db, provider, metric, payload, *, ts_ms, value, quote=None):
        endpoint = URLS[(provider, metric)]
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sha = m.raw_payload(db, provider, endpoint, body, "application/json")
        m.store_derivative(
            db, provider=provider, symbol="BTCUSDT", metric=metric,
            observed_utc=_iso(ts_ms),
            interval_label="5m" if metric == "open_interest" else "settlement",
            value=value,
            unit="BTC" if metric == "open_interest" else "fraction",
            endpoint=endpoint, sha=sha, quote_usd=quote)
        return sha

    def _seed_four_snapshot_streams(self, db):
        self._blob(
            db, "binance", "open_interest",
            [{"symbol": "BTCUSDT", "timestamp": BASE_MS,
              "sumOpenInterest": "100.5", "sumOpenInterestValue": "8292137.4"}],
            ts_ms=BASE_MS, value=100.5, quote=8292137.4)
        self._blob(
            db, "binance", "funding_settled",
            [{"symbol": "BTCUSDT", "fundingTime": BASE_MS,
              "fundingRate": "0.00005232"}],
            ts_ms=BASE_MS, value=0.00005232)
        self._blob(
            db, "bybit", "open_interest",
            {"retCode": 0, "result": {
                "category": "linear", "symbol": "BTCUSDT",
                "list": [{"timestamp": str(BASE_MS), "openInterest": "33.2"}]}},
            ts_ms=BASE_MS, value=33.2)
        self._blob(
            db, "bybit", "funding_settled",
            {"retCode": 0, "result": {
                "category": "linear", "symbol": "BTCUSDT",
                "list": [{"fundingRateTimestamp": str(BASE_MS),
                          "fundingRate": "-0.00001182", "symbol": "BTCUSDT"}]}},
            ts_ms=BASE_MS, value=-0.00001182)

    def _seed_102_valid_backward_receipts(self, db):
        for index in range(RECEIPTS):
            timestamp = BASE_MS - (index + 1) * INTERVAL_MS
            body = [{
                "symbol": "BTCUSDT", "timestamp": timestamp,
                "sumOpenInterest": str(100.0 + index / 100.0),
                "sumOpenInterestValue": str(8_000_000 + index),
            }]
            sha = self._blob(
                db, "binance", "open_interest", body, ts_ms=timestamp,
                value=100.0 + index / 100.0, quote=8_000_000 + index)
            run_id = "run-backward-%03d" % index
            request_id = "receipt-backward-%03d" % index
            db.execute(
                "INSERT INTO market_source_runs"
                "(run_id,provider,started_utc,ended_utc,status,requests,points)"
                "VALUES(?,?,?,?,?,?,?)",
                (run_id, "binance", "2026-10-10T01:00:00+00:00",
                 "2026-10-10T01:00:01+00:00", "success", 1, 1))
            db.execute(
                "INSERT INTO market_request_lineage"
                "(request_id,run_id,provider,stream,symbol,interval_label,"
                "endpoint_path,requested_end_ms,requested_limit,"
                "attempted_utc,ended_utc,http_attempts,status,raw_sha256,"
                "returned_rows,persisted_rows,first_observed_utc,last_observed_utc)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, run_id, "binance", "open_interest",
                 "BTCUSDT", "5m", "/futures/data/openInterestHist",
                 BASE_MS - index * INTERVAL_MS - 1, 500,
                 "2026-10-10T01:00:00+00:00",
                 "2026-10-10T01:00:01+00:00",
                 1, "success", sha, 1, 1, _iso(timestamp), _iso(timestamp)))
        m.save_cursor(
            db, "binance", "history_backward_v1_open_interest",
            str(BASE_MS - RECEIPTS * INTERVAL_MS - 1))

    def test_positive_real_auditors_validate_102_backward_receipts(self):
        # Crucially: auditors=None means the actual RAW, TEMPORAL and LINEAGE
        # implementations execute, not injected return-value doubles.
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = gate.evaluate(self.path, expected_backward=RECEIPTS)
        after = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(result["state"], "PASS_OFFLINE_PRE_EVIDENCE",
                         result["issues"])
        self.assertEqual(result["preflight"]["quality_state"],
                         "PASS_READ_ONLY_PRECHECK")
        self.assertEqual(result["audits"]["RAW"]["state"], "PASS_SQLITE_TO_RAW")
        self.assertEqual(result["audits"]["RAW"]["verified"], RECEIPTS + 4)
        self.assertEqual(result["audits"]["TEMPORAL"]["quality_state"],
                         "SNAPSHOT_INTERNAL_QA_OK")
        self.assertEqual(result["audits"]["LINEAGE"]["quality_state"],
                         "PASS_REQUEST_LINEAGE")
        self.assertEqual(result["audits"]["LINEAGE"]["requests_verified"],
                         RECEIPTS)
        self.assertEqual(result["audits"]["LINEAGE"]["forward_requests"], 0)
        self.assertEqual(result["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual(before, after, "Read-only CLI must not mutate the DB")

    def test_cli_real_auditors_returns_success_only_for_coherent_data(self):
        out = StringIO()
        with contextlib.redirect_stdout(out):
            rc = gate.main([
                "--db", str(self.path),
                "--expected-backward", str(RECEIPTS)])
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["state"], "PASS_OFFLINE_PRE_EVIDENCE")
        self.assertEqual(payload["authorization"],
                         "EVIDENCE_ONLY_NO_MIGRATION_NO_HTTP")
        self.assertEqual(payload["expected_backward"], RECEIPTS)

    def test_corrupted_raw_value_must_fail_actual_raw_auditor(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE market_derivatives SET raw_value=999999 "
                "WHERE provider='bybit' AND metric='open_interest'")
        result = gate.evaluate(self.path, expected_backward=RECEIPTS)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("RAW_AUDIT_BLOCKED", result["issues"])

    def test_broken_backward_boundary_must_fail_actual_lineage(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE market_request_lineage "
                "SET requested_end_ms=requested_end_ms+300000 "
                "WHERE request_id='receipt-backward-020'")
        result = gate.evaluate(self.path, expected_backward=RECEIPTS)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("LINEAGE_AUDIT_BLOCKED", result["issues"])

    def test_missing_derivative_bar_must_fail_real_temporal_and_raw(self):
        timestamp = BASE_MS - 40 * INTERVAL_MS
        with sqlite3.connect(self.path) as db:
            db.execute(
                "DELETE FROM market_derivatives WHERE provider='binance' "
                "AND metric='open_interest' AND observed_utc=?", (_iso(timestamp),))
        result = gate.evaluate(self.path, expected_backward=RECEIPTS)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("TEMPORAL_AUDIT_BLOCKED", result["issues"])
        self.assertIn("RAW_AUDIT_BLOCKED", result["issues"])

    def test_readonly_preflight_must_reject_wrong_receipt_count(self):
        result = gate.evaluate(self.path, expected_backward=RECEIPTS - 1)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["issues"], ["PRE_FINGERPRINT_BLOCKED"])


if __name__ == "__main__":
    unittest.main()
