"""Offline-only regression tests for R10-E immutable snapshot planner."""
import contextlib
import datetime as dt
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.market_r10e_plan_only import (
    SLOT_MS, UTC, cutoff_ms, main, plan_snapshot, plan_stream,
)


class TestR10EPlanOnly(unittest.TestCase):
    CLOCK = dt.datetime(2026, 10, 10, 22, 0, tzinfo=UTC)

    @staticmethod
    def iso(minute):
        return (dt.datetime(2026, 10, 10, 21, 40, tzinfo=UTC)
                + dt.timedelta(minutes=minute)).isoformat(timespec="seconds")

    def create_database(self, root: Path, *, binance=None, bybit=None):
        path = root / "synthetic.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE market_derivatives (provider TEXT, symbol TEXT, metric TEXT, "
                       "interval_label TEXT, observed_utc TEXT)")
            for provider, stamps in (("binance", binance or []), ("bybit", bybit or [])):
                db.executemany("INSERT INTO market_derivatives VALUES (?,?,?,?,?)",
                               ((provider, "BTCUSDT", "open_interest", "5m", x) for x in stamps))
        return path

    def test_inclusive_cutoff_one_slot_and_two_slots(self):
        self.assertEqual(cutoff_ms(self.CLOCK), int(dt.datetime(2026, 10, 10, 21, 50, tzinfo=UTC).timestamp()) * 1000)
        r = plan_stream([self.iso(5)], "bybit", now=self.CLOCK)
        self.assertEqual(r["closed_intervals_waiting"], 1)
        self.assertEqual(r["pages_needed"], 1)
        self.assertEqual(r["first_page_end_utc"], self.iso(10))
        r = plan_stream([self.iso(0)], "bybit", now=self.CLOCK)
        self.assertEqual(r["closed_intervals_waiting"], 2)
        self.assertEqual(r["next_start_utc"], self.iso(5))
        self.assertEqual(r["status"], "PLAN_ONLY")

    def test_no_bar_and_future_max_stop(self):
        self.assertEqual(plan_stream([self.iso(10)], "binance", now=self.CLOCK)["status"], "NO_CLOSED_BAR")
        self.assertEqual(plan_stream([self.iso(15)], "binance", now=self.CLOCK)["status"], "BLOCKED_MAX_AFTER_CUTOFF")

    def test_detect_gap_and_duplicate(self):
        r = plan_stream([self.iso(-5), self.iso(5)], "bybit", now=self.CLOCK)
        self.assertEqual(r["status"], "BLOCKED_INTERNAL_GAP")
        self.assertEqual(r["internal_gap_count"], 1)
        self.assertEqual(plan_stream([self.iso(0)] * 2, "bybit", now=self.CLOCK)["status"], "BLOCKED_DUPLICATE_OR_ORDER")

    def test_invalid_alignment_and_timezone(self):
        self.assertEqual(plan_stream([self.iso(0).replace(":00+00:00", ":01+00:00")], "binance", now=self.CLOCK)["status"], "BLOCKED_UNALIGNED_TIMESTAMP")
        with self.assertRaises(ValueError):
            plan_stream(["2026-10-10T21:40:00"], "bybit", now=self.CLOCK)
        with self.assertRaises(ValueError):
            cutoff_ms(dt.datetime(2026, 10, 10, 22, 0))
        self.assertEqual(SLOT_MS, 300_000)

    def test_pagination_exact_200_and_201(self):
        anchor = dt.datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
        now = anchor + dt.timedelta(minutes=1_010)
        r = plan_stream([anchor.isoformat()], "bybit", now=now)
        self.assertEqual(r["closed_intervals_waiting"], 200)
        self.assertEqual(r["pages_needed"], 1)
        r = plan_stream([anchor.isoformat()], "bybit", now=now + dt.timedelta(minutes=5))
        self.assertEqual(r["closed_intervals_waiting"], 201)
        self.assertEqual(r["pages_needed"], 2)
        r = plan_stream([anchor.isoformat()], "binance", now=now + dt.timedelta(minutes=5))
        self.assertEqual(r["pages_needed"], 1)

    def test_explicit_ro_snapshot_and_missing_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.create_database(Path(tmp), binance=[self.iso(0), self.iso(5)])
            before = path.read_bytes()
            report = plan_snapshot(path, now=self.CLOCK)
            self.assertEqual(report["state"], "PLAN_ONLY_READ_ONLY")
            self.assertEqual(report["http_requests"], 0)
            self.assertEqual(report["sqlite_writes"], 0)
            self.assertEqual(report["sources"][0]["closed_intervals_waiting"], 1)
            self.assertEqual(report["sources"][1]["status"], "BLOCKED_NO_BASELINE")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((Path(tmp) / "synthetic.db-wal").exists())

    def test_cli_refuses_apply_and_missing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.create_database(Path(tmp), binance=[self.iso(0)])
            with contextlib.redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    main(["--db", str(path), "--apply"])
            self.assertEqual(cm.exception.code, 2)
            with contextlib.redirect_stdout(StringIO()) as buf:
                rc = main(["--db", str(Path(tmp) / "absent.db"), "--now-utc", "2026-10-10T22:00:00Z"])
            self.assertEqual(rc, 2)
            self.assertIn('"state": "BLOCKED"', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
