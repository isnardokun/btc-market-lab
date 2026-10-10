"""R10-C.2: migration must never report success for a broken v3 schema.

All --apply calls operate on disposable TemporaryDirectory SQLite files.
No public HTTP, no real DB paths, no schedulers, no Telegram.
"""
import contextlib
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import market_context_migrate as migrate
from scripts import market_v3_preflight as preflight
from storage import market_context as dbm


class V3MigratorAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "fixture.db"
        with sqlite3.connect(self.path) as db:
            db.executescript(dbm.DDL)
            db.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) VALUES(1,?)",
                ("2026-10-10T00:00:00+00:00",))
            dbm.install_request_lineage_v2_only(db)

    def apply(self):
        with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
            return migrate.main(["--apply", "--db", str(self.path)])

    def test_unmodified_v2_upgrade_and_second_apply_are_idempotent(self):
        original = preflight.inspect(self.path, phase="pre", expected_backward=0)
        self.assertEqual(original["quality_state"], "PASS_READ_ONLY_PRECHECK")
        self.assertEqual(self.apply(), 0)
        outcome = preflight.inspect(self.path, phase="post", baseline=original)
        self.assertEqual(outcome["quality_state"], "PASS_READ_ONLY_POSTCHECK",
                         outcome["issues"])
        self.assertEqual(self.apply(), 0)
        self.assertEqual(len(list((self.path.parent / "backups").glob("*.db"))), 1)

    def test_marked_v3_but_missing_acquisitions_constraints_must_fail(self):
        self.assertEqual(self.apply(), 0)
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE market_request_acquisitions")
            db.execute(
                "CREATE TABLE market_request_acquisitions ("
                "acquired_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
                "provider TEXT NOT NULL, endpoint_path TEXT NOT NULL, "
                "raw_sha256 TEXT NOT NULL, acquired_utc TEXT NOT NULL)"
            )
        self.assertEqual(self.apply(), 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM market_context_migrations WHERE version=3"
            ).fetchone()[0], 1)
        self.assertEqual(len(list((self.path.parent / "backups").glob("*.db"))), 1)

    def test_validation_failure_rolls_back_uncommitted_v3_ddl(self):
        original = preflight.inspect(self.path, phase="pre")
        self.assertEqual(original["quality_state"], "PASS_READ_ONLY_PRECHECK")
        with patch.object(
            migrate, "_integrity_check_connection",
            side_effect=RuntimeError("offline injected validation failure"),
        ):
            self.assertEqual(self.apply(), 1)
        still_v2 = preflight.inspect(self.path, phase="pre")
        self.assertEqual(still_v2["quality_state"], "PASS_READ_ONLY_PRECHECK",
                         still_v2["issues"])
        self.assertEqual(still_v2["snapshot"], original["snapshot"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual({r[0] for r in db.execute(
                "SELECT version FROM market_context_migrations")}, {1, 2})
            self.assertNotIn("direction", {r[1] for r in db.execute(
                "PRAGMA table_info(market_request_lineage)")})
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='market_request_acquisitions'"
            ).fetchone())
        copies = list((self.path.parent / "backups").glob("*.db"))
        self.assertEqual(len(copies), 1)
        saved = preflight.inspect(copies[0], phase="pre")
        self.assertEqual(saved["snapshot"], original["snapshot"])

    def test_real_fk_violation_fails_before_v3_transaction_commits(self):
        with sqlite3.connect(self.path) as db:
            # Seed a syntactically valid v2 receipt with a broken run FK.
            db.execute(
                "INSERT INTO market_request_lineage ("
                "request_id,run_id,provider,stream,symbol,interval_label,"
                "endpoint_path,requested_end_ms,requested_limit,"
                "attempted_utc,ended_utc,http_attempts,status,error_class,"
                "returned_rows,persisted_rows) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("broken", "missing-run", "binance", "open_interest",
                 "BTCUSDT", "5m", "/futures/data/openInterestHist",
                 1791590400000, 500, "2026-10-10T00:00:00Z",
                 "2026-10-10T00:00:01Z", 1, "failed", "HTTPError_429", 0, 0),
            )
        self.assertEqual(self.apply(), 1)
        with sqlite3.connect(self.path) as db:
            self.assertFalse(db.execute(
                "SELECT 1 FROM market_context_migrations WHERE version=3"
            ).fetchall())
            self.assertNotIn("direction", {r[1] for r in db.execute(
                "PRAGMA table_info(market_request_lineage)")})
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='market_request_acquisitions'"
            ).fetchone())


if __name__ == "__main__":
    unittest.main()
