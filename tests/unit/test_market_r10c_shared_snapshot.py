"""R10-C.5: test one frozen SQLite view shared by all REAL audit functions.

All tests create ephemeral SQLite files and never open production databases,
make provider HTTP calls, create production backups or authorize schema DDL.
"""
import contextlib
from io import StringIO
import sqlite3
import unittest
from unittest.mock import patch

from scripts import market_r10c_quality_gate as gate
from scripts import market_v3_preflight as preflight
from scripts import market_raw_reconcile as raw
from scripts import market_temporal_quality as temporal
from scripts import market_request_lineage_audit as lineage
from tests.unit.test_market_r10c_real_auditors_e2e import (
    RealAuditorsOnCoherentV2Fixture, RECEIPTS,
)


class ConsistentSharedSnapshotTests(unittest.TestCase):
    def setUp(self):
        # Reuse the fully source-backed 102-receipt fixture, not mocks.
        self.fixture = RealAuditorsOnCoherentV2Fixture(
            "test_positive_real_auditors_validate_102_backward_receipts")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path

    def test_all_real_auditors_share_exact_same_query_only_connection(self):
        calls = []
        originals = {
            "raw": raw.audit, "temporal": temporal.inspect,
            "lineage": lineage.audit, "preflight": preflight.inspect,
        }

        def wrap(name, function):
            def run(*args, **kwargs):
                conn = kwargs.get("connection")
                self.assertIsInstance(conn, sqlite3.Connection)
                self.assertTrue(conn.in_transaction)
                self.assertEqual(
                    conn.execute("PRAGMA query_only").fetchone()[0], 1)
                calls.append((name, conn))
                return function(*args, **kwargs)
            return run

        with (
            patch.object(raw, "audit", side_effect=wrap("raw", originals["raw"])),
            patch.object(temporal, "inspect",
                         side_effect=wrap("temporal", originals["temporal"])),
            patch.object(lineage, "audit",
                         side_effect=wrap("lineage", originals["lineage"])),
            patch.object(preflight, "inspect",
                         side_effect=wrap("preflight", originals["preflight"])),
        ):
            report = gate.evaluate(self.path, expected_backward=RECEIPTS)

        self.assertEqual(report["state"], "PASS_OFFLINE_PRE_EVIDENCE",
                         report["issues"])
        self.assertEqual(report["consistency"]["mode"],
                         "SHARED_SQLITE_SNAPSHOT")
        self.assertTrue(report["consistency"]["external_commits_checked"])
        self.assertEqual([name for name, _ in calls],
                         ["preflight", "raw", "temporal", "lineage", "preflight"])
        self.assertTrue(all(conn is calls[0][1] for _, conn in calls))

    def test_wal_commit_during_audit_is_detected_even_when_snapshot_consistent(self):
        with sqlite3.connect(self.path) as db:
            self.assertEqual(
                db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")

        actual_raw = raw.audit
        def insert_mid_audit(path, *, connection=None):
            response = actual_raw(path, connection=connection)
            # Separate writer; the audited reader retains its frozen WAL view.
            with sqlite3.connect(self.path, timeout=3) as writer:
                writer.execute(
                    "INSERT INTO market_source_cursors "
                    "(provider, stream, cursor, updated_utc) VALUES(?,?,?,?)",
                    ("bybit", "history_backward_v1_open_interest", "123",
                     "2026-10-10T01:00:00+00:00"))
            return response

        with patch.object(raw, "audit", side_effect=insert_mid_audit):
            report = gate.evaluate(self.path, expected_backward=RECEIPTS)
        self.assertEqual(report["state"], "BLOCKED")
        self.assertIn(
            "EXTERNAL_SQLITE_COMMIT_DURING_AUDIT", report["issues"])
        self.assertNotIn("SQLITE_CHANGED_DURING_AUDIT", report["issues"])
        self.assertTrue(report["consistency"]["external_commits_checked"])

    def test_unsafe_supplied_connection_rejected_by_all_real_auditors(self):
        with sqlite3.connect(self.path) as db:
            for call in (
                lambda: preflight.inspect(
                    self.path, phase="pre", expected_backward=RECEIPTS,
                    connection=db),
                lambda: raw.audit(self.path, connection=db),
                lambda: temporal.inspect(self.path, connection=db),
                lambda: lineage.audit(self.path, connection=db),
            ):
                with self.subTest(call=repr(call)), self.assertRaises(ValueError):
                    call()

    def test_standalone_real_auditor_backwards_compatibility(self):
        # Standalone auditors must still be callable with only a DB path.
        self.assertEqual(raw.audit(self.path)["state"], "PASS_SQLITE_TO_RAW")
        self.assertEqual(
            temporal.inspect(self.path)["quality_state"],
            "SNAPSHOT_INTERNAL_QA_OK")
        self.assertEqual(
            lineage.audit(self.path)["quality_state"], "PASS_REQUEST_LINEAGE")
        self.assertEqual(preflight.inspect(
            self.path, phase="pre",
            expected_backward=RECEIPTS)["quality_state"],
            "PASS_READ_ONLY_PRECHECK")

    def test_cli_includes_shared_snapshot_mode_but_no_authorization(self):
        out = StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gate.main([
                "--db", str(self.path),
                "--expected-backward", str(RECEIPTS)]), 0)
        import json
        document = json.loads(out.getvalue())
        self.assertEqual(document["consistency"]["mode"],
                         "SHARED_SQLITE_SNAPSHOT")
        self.assertEqual(document["authorization"],
                         "EVIDENCE_ONLY_NO_MIGRATION_NO_HTTP")
        self.assertEqual(document["historical_completeness"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
