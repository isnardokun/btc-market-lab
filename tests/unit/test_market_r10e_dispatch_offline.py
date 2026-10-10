"""R10-E.1 offline dispatcher: tested scheduling without real HTTP or production."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from scripts.market_r10e_dispatch_offline import dispatch_offline
from scripts.market_r10e_plan_only import UTC, plan_stream

ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 10, 10, 22, 0, tzinfo=UTC)


def stamp(minute):
    return (dt.datetime(2026, 10, 10, 21, 0, tzinfo=UTC)
            + dt.timedelta(minutes=minute)).isoformat(timespec="seconds")


def coverage(binance=(40,), bybit=(10,)):
    return {"state": "PLAN_ONLY_READ_ONLY", "historical_completeness": "NOT_VERIFIED",
            "http_requests": 0, "sqlite_writes": 0, "now_utc": NOW.isoformat(),
            "sources": [
                plan_stream([stamp(x) for x in binance], "binance", now=NOW),
                plan_stream([stamp(x) for x in bybit], "bybit", now=NOW),
            ]}


class TestR10EOfflineDispatch(unittest.TestCase):
    def test_oldest_provider_selected_by_backlog(self):
        result = dispatch_offline(coverage())
        self.assertEqual(result["decision"], "PROPOSE_ONE_PAGE_ONLY")
        self.assertEqual(result["selected"]["provider"], "bybit")
        self.assertEqual(result["selected"]["next_start_utc"], stamp(15))
        self.assertEqual(result["selected"]["first_page_end_utc"], stamp(50))
        self.assertEqual(result["selected"]["estimated_slots_this_page"], 8)
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["live_apply_available"])
        self.assertEqual(result["http_requests"], 0)
        self.assertEqual(result["sqlite_writes"], 0)
        self.assertEqual(result["closed_bar_cutoff_utc"], stamp(50))

    def test_equal_backlog_tie_break_stable(self):
        r = dispatch_offline(coverage((40,), (40,)))
        self.assertEqual(r["selected"]["provider"], "binance")
        self.assertEqual(r["selected"]["estimated_slots_this_page"], 2)

    def test_both_caught_up_proposes_no_request(self):
        r = dispatch_offline(coverage((50,), (50,)))
        self.assertEqual(r["decision"], "NO_CLOSED_BAR")
        self.assertIsNone(r["selected"])

    def test_internal_gap_blocks_entire_cycle(self):
        r = dispatch_offline(coverage((40,), (0, 10)))
        self.assertEqual(r["decision"], "BLOCKED_SOURCE_QA")
        self.assertEqual(r["blocked_providers"], ["bybit"])
        self.assertIsNone(r["selected"])

    def test_no_baseline_blocks_entire_cycle(self):
        r = dispatch_offline(coverage((40,), ()))
        # An explicit empty tuple in the fixture must not be treated as default.
        self.assertEqual(r["decision"], "BLOCKED_SOURCE_QA")
        self.assertEqual(r["blocked_providers"], ["bybit"])

    def test_rejects_unknown_metadata_and_duplicate(self):
        original = coverage()
        for mutation in (
            lambda r: r.update(state="PASS_LIVE"),
            lambda r: r.update(historical_completeness="COMPLETE"),
            lambda r: r.update(http_requests=1),
            lambda r: r["sources"][0].update(provider_availability="VERIFIED"),
            lambda r: r["sources"][0].update(interval="1m"),
            lambda r: r["sources"][0].update(status="SUCCESS"),
            lambda r: r["sources"].append(copy.deepcopy(r["sources"][0])),
            lambda r: r["sources"][0].update(cutoff_utc="2099-01-01T00:00:00Z"),
        ):
            r = copy.deepcopy(original)
            mutation(r)
            with self.subTest(r=r), self.assertRaises(ValueError):
                dispatch_offline(r)

    def test_sqlite_synthetic_cli_no_db_changes(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "synth.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE market_derivatives (provider TEXT, symbol TEXT, "
                           "metric TEXT, interval_label TEXT, observed_utc TEXT)")
                for provider, tm in (("binance", stamp(40)), ("bybit", stamp(10))):
                    db.execute("INSERT INTO market_derivatives VALUES (?,?,?,?,?)",
                               (provider, "BTCUSDT", "open_interest", "5m", tm))
            before = path.read_bytes()
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            p = subprocess.run(
                [sys.executable, "scripts/market_r10e_dispatch_offline.py",
                 "--db", str(path), "--now-utc", NOW.isoformat()],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stderr)
            doc = json.loads(p.stdout)
            self.assertEqual(doc["decision"], "PROPOSE_ONE_PAGE_ONLY")
            self.assertEqual(doc["selected"]["provider"], "bybit")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(path.with_name(path.name + "-wal").exists())

    def test_cli_explicit_apply_rejected(self):
        p = subprocess.run(
            [sys.executable, "scripts/market_r10e_dispatch_offline.py", "--apply"],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 2)
        self.assertIn("unrecognized arguments", p.stderr)

    def test_unsafe_clock_or_missing_db_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            p = subprocess.run(
                [sys.executable, "scripts/market_r10e_dispatch_offline.py",
                 "--db", str(Path(t) / "missing.db"),
                 "--now-utc", "2026-10-10T22:00:00Z"],
                cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 2)
            r = json.loads(p.stdout)
            self.assertEqual(r["decision"], "BLOCKED")
            self.assertEqual(r["http_requests"], 0)


if __name__ == "__main__":
    unittest.main()
