"""R10-C.6: bind shared audit connections to the actual SQLite main file.

No production paths, network, filesystem beyond tempfile, or schema changes
outside test fixtures. Every successful operation is read-only.
"""
from pathlib import Path
import shutil
import sqlite3
import unittest

from scripts.market_readonly_snapshot import verify_read_snapshot
from scripts import market_v3_preflight as preflight
from scripts import market_raw_reconcile as raw
from scripts import market_temporal_quality as temporal
from scripts import market_request_lineage_audit as lineage
from scripts import market_r10c_quality_gate as gate
from tests.unit.test_market_r10c_real_auditors_e2e import (
    RealAuditorsOnCoherentV2Fixture, RECEIPTS,
)


class SharedConnectionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealAuditorsOnCoherentV2Fixture(
            "test_positive_real_auditors_validate_102_backward_receipts")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path
        self.other = self.path.parent / "other.db"
        shutil.copyfile(self.path, self.other)

    def _auditors(self, db):
        return (
            lambda: preflight.inspect(
                self.path, phase="pre", expected_backward=RECEIPTS,
                connection=db),
            lambda: raw.audit(self.path, connection=db),
            lambda: temporal.inspect(self.path, connection=db),
            lambda: lineage.audit(self.path, connection=db),
        )

    def test_wrong_database_path_rejected_even_if_contents_identical(self):
        with sqlite3.connect(self.other) as foreign:
            foreign.execute("PRAGMA query_only=ON")
            foreign.execute("BEGIN")
            for invoke in self._auditors(foreign):
                with self.subTest(auditor=repr(invoke)):
                    with self.assertRaisesRegex(ValueError, "path mismatch"):
                        invoke()
            foreign.rollback()

    def test_attached_database_rejected_even_if_main_path_matches(self):
        with sqlite3.connect(self.path) as db:
            db.execute("ATTACH DATABASE ? AS unrelated", (str(self.other),))
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            for invoke in self._auditors(db):
                with self.subTest(auditor=repr(invoke)):
                    with self.assertRaisesRegex(ValueError, "one SQLite main"):
                        invoke()
            db.rollback()

    def test_memory_database_rejected_for_existing_file_path(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            with self.assertRaisesRegex(ValueError, "path mismatch"):
                verify_read_snapshot(db, self.path)
            db.rollback()

    def test_invalid_connection_type_and_non_query_only_rejected(self):
        with self.assertRaisesRegex(ValueError, "SQLite Connection"):
            verify_read_snapshot(None, self.path)
        with sqlite3.connect(self.path) as db:
            with self.assertRaisesRegex(ValueError, "active transaction"):
                verify_read_snapshot(db, self.path)
            db.execute("BEGIN")
            with self.assertRaisesRegex(ValueError, "query_only"):
                verify_read_snapshot(db, self.path)
            db.rollback()

    def test_symlink_alias_to_same_main_file_is_accepted(self):
        alias = self.path.parent / "alias.db"
        alias.symlink_to(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            verify_read_snapshot(db, alias)
            self.assertEqual(
                preflight.inspect(
                    alias, phase="pre",
                    expected_backward=RECEIPTS,
                    connection=db)["quality_state"],
                "PASS_READ_ONLY_PRECHECK")
            db.rollback()

    def test_live_gate_output_still_identifies_snapshot_and_cannot_authorize(self):
        result = gate.evaluate(self.path, expected_backward=RECEIPTS)
        self.assertEqual(result["state"], "PASS_OFFLINE_PRE_EVIDENCE",
                         result["issues"])
        self.assertEqual(
            result["consistency"]["mode"], "SHARED_SQLITE_SNAPSHOT")
        self.assertEqual(result["authorization"],
                         "EVIDENCE_ONLY_NO_MIGRATION_NO_HTTP")
        self.assertEqual(result["historical_completeness"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
