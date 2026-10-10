"""R10-C rehearsal: exercise the REAL migration CLI only on disposable SQLite.

Synthetic backward receipts prove v2 logical preservation, not historical
API coverage. No network, production database access, cron, or Telegram.
"""
import contextlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts import market_context_migrate as migrate
from scripts import market_v3_preflight as preflight
from storage import market_context as dbm


class V3MigrationRehearsalTests(unittest.TestCase):
    RECEIPTS = 102

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "btc_research.db"
        with sqlite3.connect(self.path) as db:
            db.executescript(dbm.DDL)
            db.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) "
                "VALUES(1,?)", ("2026-10-10T00:00:00+00:00",))
            dbm.install_request_lineage_v2_only(db)
            # Synthetic legacy receipts with UNIQUE run_id and valid FKs.
            for i in range(self.RECEIPTS):
                run = f"legacy-run-{i:03d}"
                receipt = f"legacy-request-{i:03d}"
                db.execute(
                    "INSERT INTO market_source_runs"
                    "(run_id,provider,started_utc,ended_utc,status,"
                    "requests,points,error_code) VALUES(?,?,?,?,?,?,?,?)",
                    (run, "bybit", "2026-10-10T00:00:00+00:00",
                     "2026-10-10T00:00:01+00:00", "failed", 1, 0,
                     "HTTPError_429"))
                db.execute(
                    "INSERT INTO market_request_lineage"
                    "(request_id,run_id,provider,stream,symbol,interval_label,"
                    "endpoint_path,requested_end_ms,requested_limit,"
                    "attempted_utc,ended_utc,http_attempts,status,error_class,"
                    "raw_sha256,returned_rows,persisted_rows,"
                    "first_observed_utc,last_observed_utc)"
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (receipt, run, "bybit", "open_interest", "BTCUSDT", "5m",
                     "/v5/market/open-interest", 1791590400000 - i * 300000,
                     200, "2026-10-10T00:00:00+00:00",
                     "2026-10-10T00:00:01+00:00", 1, "failed", "HTTPError_429",
                     None, 0, 0, None, None))
            db.execute(
                "INSERT INTO market_source_cursors(provider,stream,cursor,updated_utc) "
                "VALUES(?,?,?,?)",
                ("bybit", "history_backward_v1_open_interest",
                 "1791590400000", "2026-10-10T00:00:00+00:00"))
            # Include a source BLOB whose bytes also need backup preservation.
            dbm.raw_payload(db, "bybit",
                            "https://api.bybit.com/v5/market/open-interest",
                            b'{"rehearsal": "legacy raw bytes"}',
                            "application/json")

    def test_exact_cli_wal_safe_backup_and_102_receipts_round_trip(self):
        # Keep the WAL writer connection open so an uncheckpointed transaction
        # must be included by sqlite3.Connection.backup, not file copying.
        writer = sqlite3.connect(self.path)
        self.addCleanup(writer.close)
        self.assertEqual(
            writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        dbm.raw_payload(writer, "bybit",
                        "https://api.bybit.com/v5/market/open-interest",
                        b'{"only_in_wal":"must survive backup"}',
                        "application/json")
        writer.commit()
        self.assertTrue(Path(str(self.path) + "-wal").exists())

        before = preflight.inspect(
            self.path, phase="pre", expected_backward=self.RECEIPTS)
        self.assertEqual(before["quality_state"], "PASS_READ_ONLY_PRECHECK",
                         before["issues"])

        out = StringIO()
        with contextlib.redirect_stdout(out):
            rc = migrate.main(["--apply", "--db", str(self.path)])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("v3 added", out.getvalue())

        post = preflight.inspect(
            self.path, phase="post", baseline=before,
            expected_backward=self.RECEIPTS)
        self.assertEqual(post["quality_state"], "PASS_READ_ONLY_POSTCHECK",
                         post["issues"])
        self.assertEqual(before["snapshot"], post["snapshot"])
        self.assertEqual(post["checks"]["backward_receipts"], self.RECEIPTS)
        self.assertEqual(post["checks"]["changed_backward_receipts"], 0)
        self.assertEqual(post["checks"]["acquisitions"], 0)
        self.assertEqual(post["checks"]["forward_cursors"], 0)

        copies = sorted((self.path.parent / "backups").glob("btc_research_*.db"))
        self.assertEqual(len(copies), 1)
        saved = preflight.inspect(
            copies[0], phase="pre", expected_backward=self.RECEIPTS)
        self.assertEqual(saved["quality_state"], "PASS_READ_ONLY_PRECHECK",
                         saved["issues"])
        self.assertEqual(saved["snapshot"], before["snapshot"],
                         "WAL-safe backup must include uncheckpointed source bytes")

        # An idempotent rerun must not generate another backup or modify data.
        with contextlib.redirect_stdout(StringIO()):
            self.assertEqual(migrate.main(["--apply", "--db", str(self.path)]), 0)
        self.assertEqual(
            len(list((self.path.parent / "backups").glob("btc_research_*.db"))), 1)
        same = preflight.inspect(
            self.path, phase="post", baseline=before,
            expected_backward=self.RECEIPTS)
        self.assertEqual(same["quality_state"], "PASS_READ_ONLY_POSTCHECK",
                         same["issues"])

    def test_rehearsal_detects_corruption_after_migration(self):
        baseline = preflight.inspect(
            self.path, phase="pre", expected_backward=self.RECEIPTS)
        self.assertEqual(baseline["quality_state"], "PASS_READ_ONLY_PRECHECK")
        with contextlib.redirect_stdout(StringIO()):
            self.assertEqual(migrate.main(["--apply", "--db", str(self.path)]), 0)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE market_request_lineage SET requested_limit=199 "
                "WHERE request_id='legacy-request-100'")
        post = preflight.inspect(
            self.path, phase="post", baseline=baseline,
            expected_backward=self.RECEIPTS)
        self.assertEqual(post["quality_state"], "BLOCKED")
        self.assertTrue(
            any("market_request_lineage" in x for x in post["issues"]))

    def test_no_production_path_or_network_required(self):
        missing = Path(self.tmp.name) / "nonexistent.db"
        with self.assertRaises(FileNotFoundError):
            preflight.inspect(missing, phase="pre")
        self.assertFalse(missing.exists())
        with contextlib.redirect_stderr(StringIO()):
            self.assertEqual(
                migrate.main(["--apply", "--db", str(missing)]), 2)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
