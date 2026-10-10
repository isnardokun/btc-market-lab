"""R10-C pre/post read-only fingerprint — TEMPORARY SQLite fixtures, no API."""
import contextlib
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts import market_v3_preflight as preflight
from storage import market_context as dbm


class ReadOnlyV3PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "fixture.db"
        with sqlite3.connect(self.db) as cx:
            cx.executescript(dbm.DDL)
            cx.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) VALUES(1,?)",
                ("2026-10-10T00:00:00+00:00",),
            )
            dbm.install_request_lineage_v2_only(cx)
            body = b'[{"symbol":"BTCUSDT","timestamp":1791590400000,"sumOpenInterest":"42"}]'
            sha = dbm.raw_payload(
                cx, "binance",
                "https://fapi.binance.com/futures/data/openInterestHist",
                body, "application/json",
            )
            cx.execute(
                "INSERT INTO market_source_runs"
                "(run_id,provider,started_utc,ended_utc,status,requests,points) "
                "VALUES(?,?,?,?,?,?,?)",
                ("run-1", "binance", "2026-10-10T00:05:00+00:00",
                 "2026-10-10T00:06:00+00:00", "success", 1, 1),
            )
            cx.execute(
                "INSERT INTO market_request_lineage"
                "(request_id,run_id,provider,stream,symbol,interval_label,endpoint_path,"
                " requested_end_ms,requested_limit,attempted_utc,ended_utc,http_attempts,"
                " status,raw_sha256,returned_rows,persisted_rows,"
                " first_observed_utc,last_observed_utc)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("req-1", "run-1", "binance", "open_interest", "BTCUSDT", "5m",
                 "/futures/data/openInterestHist", 1791590400000, 500,
                 "2026-10-10T00:05:00+00:00", "2026-10-10T00:06:00+00:00",
                 1, "success", sha, 1, 1,
                 "2026-10-10T00:00:00+00:00", "2026-10-10T00:00:00+00:00"),
            )
            cx.execute(
                "INSERT INTO market_source_cursors(provider,stream,cursor,updated_utc) "
                "VALUES(?,?,?,?)",
                ("binance", "history_backward_v1_open_interest",
                 "2026-10-10T00:00:00+00:00", "2026-10-10T00:06:00+00:00"),
            )

    def _pre(self):
        return preflight.inspect(self.db, phase="pre", expected_backward=1)

    def _migrate_fixture(self):
        # ONLY a SQLite tempfile. Never invokes the production migrator or --apply.
        with sqlite3.connect(self.db) as cx:
            dbm.install_request_lineage(cx)

    def test_pre_fingerprint_and_no_mutation(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        report = self._pre()
        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(report["quality_state"], "PASS_READ_ONLY_PRECHECK")
        self.assertEqual(report["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual(report["checks"]["backward_receipts"], 1)
        self.assertEqual(report["snapshot"]["market_request_lineage"]["rows"], 1)
        self.assertEqual(after, before)

    def test_pre_missing_db_does_not_create_file(self):
        missing = Path(self.temp.name) / "does-not-exist.db"
        with self.assertRaises(FileNotFoundError):
            preflight.inspect(missing)
        self.assertFalse(missing.exists())

    def test_expected_receipt_count_enforced(self):
        result = preflight.inspect(self.db, expected_backward=102)
        self.assertEqual(result["quality_state"], "BLOCKED")
        self.assertTrue(any("Backward receipts" in i for i in result["issues"]))

    def test_post_requires_baseline(self):
        with self.assertRaises(ValueError):
            preflight.inspect(self.db, phase="post")

    def test_pre_rejects_v3_schema(self):
        self._migrate_fixture()
        self.assertEqual(self._pre()["quality_state"], "BLOCKED")

    def test_post_matches_v2_projection_across_v3_additive_schema(self):
        baseline = self._pre()
        self._migrate_fixture()
        result = preflight.inspect(self.db, phase="post",
                                   baseline=baseline, expected_backward=1)
        self.assertEqual(result["quality_state"], "PASS_READ_ONLY_POSTCHECK", result["issues"])
        self.assertEqual(result["snapshot"], baseline["snapshot"])
        self.assertEqual(result["checks"]["acquisitions"], 0)
        self.assertEqual(result["checks"]["changed_backward_receipts"], 0)

    def test_post_rejects_mutated_legacy_raw_blob(self):
        baseline = self._pre()
        self._migrate_fixture()
        with sqlite3.connect(self.db) as cx:
            cx.execute("UPDATE market_raw_payloads SET body=?",
                       (b'{"unexpected":"mutation"}',))
        result = preflight.inspect(self.db, phase="post", baseline=baseline)
        self.assertEqual(result["quality_state"], "BLOCKED")
        self.assertTrue(any("market_raw_payloads" in i for i in result["issues"]))

    def test_post_rejects_modified_backward_receipt(self):
        baseline = self._pre()
        self._migrate_fixture()
        with sqlite3.connect(self.db) as cx:
            cx.execute("UPDATE market_request_lineage SET requested_limit=200")
        result = preflight.inspect(self.db, phase="post", baseline=baseline)
        self.assertEqual(result["quality_state"], "BLOCKED")
        self.assertTrue(any("market_request_lineage" in i for i in result["issues"]))

    def test_post_rejects_forward_cursor(self):
        baseline = self._pre()
        self._migrate_fixture()
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT INTO market_source_cursors(provider,stream,cursor,updated_utc)"
                " VALUES(?,?,?,?)",
                ("bybit", "forward_v1_open_interest", "later", "2026-10-10T00:10:00Z"),
            )
        result = preflight.inspect(self.db, phase="post", baseline=baseline)
        self.assertEqual(result["quality_state"], "BLOCKED")
        self.assertEqual(result["checks"]["forward_cursors"], 1)

    def test_post_rejects_v3_receipt_start_or_direction_changes(self):
        baseline = self._pre()
        self._migrate_fixture()
        with sqlite3.connect(self.db) as cx:
            cx.execute("UPDATE market_request_lineage SET requested_start_ms=12345")
        result = preflight.inspect(self.db, phase="post", baseline=baseline)
        self.assertEqual(result["quality_state"], "BLOCKED")
        self.assertEqual(result["checks"]["changed_backward_receipts"], 1)

    def test_fk_violation_blocks_pre(self):
        with sqlite3.connect(self.db) as cx:
            cx.execute("PRAGMA foreign_keys=OFF")
            cx.execute(
                "UPDATE market_request_lineage SET run_id='missing-run'"
            )
        result = self._pre()
        self.assertEqual(result["quality_state"], "BLOCKED")
        self.assertFalse(result["checks"]["foreign_key_check"])

    def test_pre_cli_exit_status_and_json(self):
        out = StringIO()
        with contextlib.redirect_stdout(out):
            code = preflight.main(["--db", str(self.db),
                                   "--phase", "pre", "--expected-backward", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["quality_state"],
                         "PASS_READ_ONLY_PRECHECK")

    def test_post_cli_baseline_json_read_only(self):
        evidence = Path(self.temp.name) / "pre.json"
        evidence.write_text(json.dumps(self._pre()), encoding="utf-8")
        self._migrate_fixture()
        out = StringIO()
        with contextlib.redirect_stdout(out):
            code = preflight.main(["--db", str(self.db), "--phase", "post",
                                   "--baseline", str(evidence)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["quality_state"],
                         "PASS_READ_ONLY_POSTCHECK")

    def test_falsified_pre_evidence_is_rejected(self):
        baseline = self._pre()
        baseline["quality_state"] = "BLOCKED"
        self._migrate_fixture()
        self.assertEqual(preflight.inspect(self.db, phase="post", baseline=baseline)
                         ["quality_state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
