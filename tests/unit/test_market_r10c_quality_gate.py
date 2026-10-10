"""R10-C.3 offline gate tests; all SQLite files are disposable fixtures."""
from copy import deepcopy
import contextlib
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import market_r10c_quality_gate as gate
from storage import market_context as dbm


class ReadOnlyAggregateGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "market-test.db"
        with sqlite3.connect(self.db) as cx:
            cx.executescript(dbm.DDL)
            cx.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) "
                "VALUES(1,?)", ("2026-10-10T00:00:00+00:00",)
            )
            dbm.install_request_lineage_v2_only(cx)
            cx.execute(
                "INSERT INTO market_source_runs("
                "run_id,provider,started_utc,ended_utc,status,requests,points,error_code)"
                "VALUES(?,?,?,?,?,?,?,?)",
                ("run1", "bybit", "2026-10-10T00:00:00Z",
                 "2026-10-10T00:00:01Z", "failed", 1, 0, "HTTPError_429")
            )
            cx.execute(
                "INSERT INTO market_request_lineage("
                "request_id,run_id,provider,stream,symbol,interval_label,"
                "endpoint_path,requested_end_ms,requested_limit,attempted_utc,"
                "ended_utc,http_attempts,status,error_class,returned_rows,persisted_rows)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("request1", "run1", "bybit", "open_interest", "BTCUSDT",
                 "5m", "/v5/market/open-interest", 1791590400000, 200,
                 "2026-10-10T00:00:00Z", "2026-10-10T00:00:01Z",
                 1, "failed", "HTTPError_429", 0, 0)
            )

    @staticmethod
    def outputs():
        return {
            "RAW": {
                "state": "PASS_SQLITE_TO_RAW", "issues_count": 0,
                "issues": [], "rows": 1, "verified": 1,
                "blobs_seen": 1, "blob_sha256_verified": 1,
            },
            "TEMPORAL": {
                "quality_state": "SNAPSHOT_INTERNAL_QA_OK",
                "historical_completeness": "NOT_VERIFIED", "issues": [],
                "streams": {"bybit_open_interest": {"rows": 1}},
                "source_rows_unstored": [],
            },
            "LINEAGE": {
                "quality_state": "PASS_REQUEST_LINEAGE",
                "historical_completeness": "NOT_VERIFIED", "issues": [],
                "requests_total": 1, "requests_verified": 1,
                "backward_requests": 1, "forward_requests": 0,
            },
        }

    def audits(self, override=None):
        reports = deepcopy(self.outputs())
        if override:
            for name, update in override.items():
                reports[name].update(update)
        return {k: (lambda path, outcome=v: outcome) for k, v in reports.items()}

    def test_mocked_independent_auditors_combine_without_writes(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = gate.evaluate(self.db, expected_backward=1,
                               auditors=self.audits())
        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(result["state"], "PASS_OFFLINE_PRE_EVIDENCE")
        self.assertEqual(result["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual(result["preflight"]["quality_state"],
                         "PASS_READ_ONLY_PRECHECK")
        self.assertEqual(result["audits"]["RAW"]["verified"], 1)
        self.assertEqual(before, after)

    def test_actual_auditors_cannot_pass_empty_raw_and_observations(self):
        result = gate.evaluate(self.db, expected_backward=1)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("RAW_AUDIT_BLOCKED", result["issues"])
        self.assertIn("TEMPORAL_AUDIT_BLOCKED", result["issues"])

    def test_raw_reconciled_count_and_blob_count_must_match(self):
        result = gate.evaluate(self.db, expected_backward=1, auditors=self.audits({
            "RAW": {"verified": 0, "blob_sha256_verified": 0}
        }))
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("RAW_AUDIT_BLOCKED", result["issues"])

    def test_temporal_stale_or_not_verified_flag_missing_blocks(self):
        result = gate.evaluate(self.db, expected_backward=1, auditors=self.audits({
            "TEMPORAL": {"quality_state": "REVIEW_REQUIRED",
                         "historical_completeness": "VERIFIED"}
        }))
        self.assertIn("TEMPORAL_AUDIT_BLOCKED", result["issues"])

    def test_lineage_no_receipts_yet_is_not_a_pass(self):
        result = gate.evaluate(self.db, expected_backward=1, auditors=self.audits({
            "LINEAGE": {"quality_state": "NO_REQUEST_RECEIPTS_YET",
                        "requests_total": 0, "requests_verified": 0,
                        "backward_requests": 0}
        }))
        self.assertIn("LINEAGE_AUDIT_BLOCKED", result["issues"])

    def test_lineage_fewer_verified_than_total_blocks(self):
        result = gate.evaluate(self.db, expected_backward=1, auditors=self.audits({
            "LINEAGE": {"requests_verified": 0}
        }))
        self.assertIn("LINEAGE_AUDIT_BLOCKED", result["issues"])

    def test_pre_v2_count_disagree_stops_before_audits(self):
        invoked = []
        fake_auditors = {
            name: lambda _: invoked.append("called") for name in
            ("RAW", "TEMPORAL", "LINEAGE")
        }
        result = gate.evaluate(
            self.db, expected_backward=102, auditors=fake_auditors)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["issues"], ["PRE_FINGERPRINT_BLOCKED"])
        self.assertFalse(invoked)

    def test_drift_after_first_fingerprint_is_detected(self):
        audits = self.audits()
        def mutate_db(_):
            with sqlite3.connect(self.db) as cx:
                cx.execute(
                    "INSERT INTO market_source_cursors "
                    "(provider,stream,cursor,updated_utc) VALUES(?,?,?,?)",
                    ("bybit", "history_backward_v1_open_interest", "123",
                     "2026-10-10T00:00:00Z")
                )
            return self.outputs()["RAW"]
        audits["RAW"] = mutate_db
        result = gate.evaluate(self.db, expected_backward=1, auditors=audits)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("SQLITE_CHANGED_DURING_AUDIT", result["issues"])

    def test_failed_sub_audit_is_sanitized_and_fails_closed(self):
        audits = self.audits()
        def denied(_):
            raise RuntimeError("Contains private path /secret/raw.json")
        audits["RAW"] = denied
        result = gate.evaluate(self.db, expected_backward=1, auditors=audits)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("RAW_AUDIT_UNAVAILABLE", result["issues"])
        self.assertEqual(result["audits"]["RAW"]["error_class"], "RuntimeError")
        self.assertNotIn("/secret/raw.json", json.dumps(result))

    def test_unexpected_auditor_payload_cannot_pass(self):
        audits = self.audits()
        audits["TEMPORAL"] = lambda _: "not a dictionary"
        result = gate.evaluate(self.db, expected_backward=1, auditors=audits)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["audits"]["TEMPORAL"]["error_class"],
                         "InvalidAuditOutput")

    def test_db_absent_never_created(self):
        missing = Path(self.temp.name) / "not-exist.db"
        with self.assertRaises(FileNotFoundError):
            gate.evaluate(missing, expected_backward=1)
        self.assertFalse(missing.exists())
        with contextlib.redirect_stderr(StringIO()):
            rc = gate.main(["--db", str(missing), "--expected-backward", "1"])
        self.assertEqual(rc, 2)
        self.assertFalse(missing.exists())

    def test_cli_expected_count_is_required(self):
        with contextlib.redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                gate.main(["--db", str(self.db)])
        self.assertEqual(ctx.exception.code, 2)

    def test_cli_rejects_wrong_positive_count(self):
        out = StringIO()
        with contextlib.redirect_stdout(out):
            rc = gate.main([
                "--db", str(self.db), "--expected-backward", "102"
            ])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out.getvalue())["state"], "BLOCKED")

    def test_nonpositive_and_bool_receipt_count_rejected(self):
        for count in (0, -1, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                gate.evaluate(self.db, expected_backward=count, auditors=self.audits())


if __name__ == "__main__":
    unittest.main()
