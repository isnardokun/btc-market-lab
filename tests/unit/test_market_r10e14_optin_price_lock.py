"""R10-E.14: real price_btc entrypoint with opt-in lock, synthetic Yahoo + SQLite."""
import ast
import contextlib
import datetime as dt
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ingestion import ingest_price as price
from scripts.market_r10e11_writer_exclusion import (
    hold_writer_lock, WriterLockBusy, WriterLockUnsafe,
)

HOLDER_SCRIPT = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1], namespace=sys.argv[2]):
    print('HELD', flush=True)
    sys.stdin.readline()
"""


class TestR10E14OptInPriceLock(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r10e14-price-synthetic-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dbpath = self.root / "price-synthetic.db"
        self.lockdir = self.root / "locks"
        self.lockdir.mkdir()
        with sqlite3.connect(self.dbpath) as db:
            db.execute("CREATE TABLE price_btc(ts INTEGER PRIMARY KEY, price REAL)")
        today = dt.datetime.now(dt.timezone.utc).date()
        self.day0 = today - dt.timedelta(days=2)
        self.day1 = today - dt.timedelta(days=1)
        self.day2 = today
        self.stamps = [int(dt.datetime.combine(d, dt.time.min, tzinfo=dt.timezone.utc).timestamp())
                       for d in (self.day0, self.day1, self.day2)]
        self.payload = json.dumps({"chart":{"result":[{
            "timestamp":self.stamps,
            "indicators":{"quote":[{"close":[70111.0,70222.0,70333.0]}]},
        }]}}).encode("utf-8")

    def mock_request(self):
        return patch("urllib.request.urlopen", side_effect=lambda *a, **k: BytesIO(self.payload))

    def rows(self):
        with sqlite3.connect(self.dbpath) as db:
            return db.execute("SELECT ts,price FROM price_btc ORDER BY ts").fetchall()

    def locked_ingest(self):
        with patch.object(price, "DB_PATH", str(self.dbpath)), self.mock_request() as fetch:
            with contextlib.redirect_stdout(StringIO()):
                price.ingest(lock_directory=self.lockdir)
        return fetch

    def holder(self, namespace=price.GLOBAL_LOCK_NAMESPACE):
        repo = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER_SCRIPT, str(self.lockdir), namespace],
            cwd=repo, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1)
        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(stop)
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            self.assertTrue(sel.select(timeout=5), "Child did not signal acquired")
        self.assertEqual(proc.stdout.readline().strip(), "HELD")
        return proc

    def test_opt_in_success_writes_completed_days_only(self):
        fetch = self.locked_ingest()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.rows(), [(self.stamps[0], 70111.0),
                                       (self.stamps[1], 70222.0)])

    def test_open_day_previously_archived_is_purged_under_lock(self):
        with sqlite3.connect(self.dbpath) as db:
            db.execute("INSERT INTO price_btc VALUES(?,?)", (self.stamps[2], 99999))
        self.locked_ingest()
        self.assertEqual(len(self.rows()), 2)
        self.assertNotIn(self.stamps[2], [x[0] for x in self.rows()])

    def test_repeat_ingest_is_idempotent_on_synthetic_close(self):
        self.locked_ingest()
        before = self.rows()
        self.locked_ingest()
        self.assertEqual(self.rows(), before)

    def test_opt_in_creates_private_global_lock_file(self):
        self.locked_ingest()
        file = self.lockdir / (price.GLOBAL_LOCK_NAMESPACE + ".lock")
        self.assertTrue(file.exists())
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_in_process_busy_blocks_before_http_or_sqlite(self):
        with hold_writer_lock(self.lockdir, namespace=price.GLOBAL_LOCK_NAMESPACE):
            with patch.object(price, "_ingest_unlocked",
                              side_effect=AssertionError("ingest should not start")):
                with self.assertRaises(WriterLockBusy):
                    price.ingest(lock_directory=self.lockdir)
        self.assertEqual(self.rows(), [])

    def test_busy_does_not_touch_underlying_db_bytes(self):
        data = self.dbpath.read_bytes()
        with hold_writer_lock(self.lockdir, namespace=price.GLOBAL_LOCK_NAMESPACE):
            with self.assertRaises(WriterLockBusy):
                price.ingest(lock_directory=self.lockdir)
        self.assertEqual(self.dbpath.read_bytes(), data)

    def test_separate_process_holds_lock_so_price_ingest_stops(self):
        child = self.holder()
        with patch.object(price, "_ingest_unlocked",
                          side_effect=AssertionError("not reached")):
            with self.assertRaises(WriterLockBusy):
                price.ingest(lock_directory=self.lockdir)
        self.assertIsNone(child.poll())

    def test_child_release_allows_real_price_ingestion_with_fake_http(self):
        child = self.holder()
        with self.assertRaises(WriterLockBusy):
            price.ingest(lock_directory=self.lockdir)
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5), 0)
        self.locked_ingest()
        self.assertEqual(len(self.rows()), 2)

    def test_child_crash_allows_next_price_ingestion(self):
        child = self.holder()
        child.kill()
        child.wait(timeout=5)
        self.locked_ingest()
        self.assertEqual(len(self.rows()), 2)

    def test_wrong_namespace_does_not_protect_same_sqlite(self):
        with hold_writer_lock(self.lockdir, namespace="incorrect-independent"):
            self.locked_ingest()
        self.assertEqual(len(self.rows()), 2)

    def test_cli_busy_returns_rc3_and_zero_http(self):
        with hold_writer_lock(self.lockdir, namespace=price.GLOBAL_LOCK_NAMESPACE):
            with patch.object(price, "_ingest_unlocked",
                              side_effect=AssertionError("must not start")):
                with contextlib.redirect_stderr(StringIO()) as output:
                    rc = price.main(["--global-lock-dir", str(self.lockdir)])
        self.assertEqual(rc, 3)
        self.assertIn("BLOCKED_WRITER_BUSY", output.getvalue())
        self.assertEqual(self.rows(), [])

    def test_cli_explicit_lock_directory_with_mocked_api(self):
        with patch.object(price, "DB_PATH", str(self.dbpath)), self.mock_request() as fetch:
            with contextlib.redirect_stdout(StringIO()):
                rc = price.main(["--global-lock-dir", str(self.lockdir)])
        self.assertEqual(rc, 0)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(self.rows()), 2)

    def test_cli_default_remains_unlocked_for_existing_daily_shell(self):
        with patch.object(price, "_ingest_unlocked", return_value=None) as work:
            with patch.object(price, "hold_writer_lock",
                              side_effect=AssertionError("default must not acquire lock")):
                self.assertEqual(price.main([]), 0)
        work.assert_called_once_with()

    def test_missing_directory_refused_before_http(self):
        with patch.object(price, "_ingest_unlocked",
                          side_effect=AssertionError("HTTP must not start")):
            with self.assertRaises(WriterLockUnsafe):
                price.ingest(lock_directory=self.root / "absent")
        self.assertFalse((self.root / "absent").exists())

    def test_unsafe_permissions_on_lock_file_refused(self):
        file = self.lockdir / (price.GLOBAL_LOCK_NAMESPACE + ".lock")
        file.touch()
        file.chmod(0o644)
        with patch.object(price, "_ingest_unlocked",
                          side_effect=AssertionError("must not start")):
            with self.assertRaises(WriterLockUnsafe):
                price.ingest(lock_directory=self.lockdir)

    def test_synthetic_network_exception_does_not_own_lock_forever(self):
        with patch.object(price, "DB_PATH", str(self.dbpath)):
            with patch("urllib.request.urlopen", side_effect=TimeoutError("synthetic")):
                with self.assertRaises(TimeoutError):
                    price.ingest(lock_directory=self.lockdir)
        # Prove acquisition after release with a genuine OS flock.
        with hold_writer_lock(self.lockdir, namespace=price.GLOBAL_LOCK_NAMESPACE):
            pass
        self.assertEqual(self.rows(), [])

    def test_source_contract_has_opt_in_and_no_shell_activation(self):
        root = Path(__file__).resolve().parents[2]
        content = (root / "scripts" / "daily.sh").read_text(encoding="utf-8")
        self.assertIn("python3 ingestion/ingest_price.py", content)
        self.assertNotIn("--global-lock-dir", content)
        code = (root / "ingestion" / "ingest_price.py").read_text(encoding="utf-8")
        self.assertIn("hold_writer_lock(lock_directory, namespace=GLOBAL_LOCK_NAMESPACE)", code)
        self.assertEqual(price.GLOBAL_LOCK_NAMESPACE, "btc-research-global-writer-v1")
        self.assertEqual(price.URL.split("?")[0], "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD")

    def test_only_price_ingest_source_is_modified_no_auto_http(self):
        path = Path(__file__).resolve().parents[2] / "ingestion" / "ingest_price.py"
        t = ast.parse(path.read_text(encoding="utf-8"))
        functions = [n.name for n in t.body if isinstance(n, ast.FunctionDef)]
        self.assertIn("ingest", functions)
        self.assertIn("_ingest_unlocked", functions)
        self.assertIn("main", functions)
        # No top-level urllib.request.urlopen expression.
        self.assertFalse(any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                             and isinstance(n.value.func, ast.Attribute)
                             and n.value.func.attr == "urlopen" for n in t.body))

    def test_registry_count_is_extensible_not_hardcoded_to_previous_phase(self):
        root = Path(__file__).resolve().parents[2]
        tree = ast.parse((root / "tests" / "run_all.py").read_text(encoding="utf-8"))
        lists = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(k, ast.Name) and k.id == "TESTS" for k in n.targets)]
        self.assertEqual(len(lists), 1)
        suites = lists[0]
        self.assertGreaterEqual(len(suites), 52)
        self.assertEqual(len({a for a,_ in suites}), len(suites))
        self.assertEqual(len({b for _,b in suites}), len(suites))
        for name in (
            "test_market_r10e_plan_only", "test_market_r10e_dispatch_offline",
            "test_market_r10e2_shadow_gate", "test_market_r10e3_mock_e2e",
            "test_market_r10e4_synthetic_cycle",
            "test_market_r10e5_backward_fixture_plan",
            "test_market_r10e6_rbn_tier2_shadow",
            "test_market_r10e7_ro_archive_manifest",
            "test_market_r10e8_ro_internal_gap_audit",
            "test_market_r10e9_evidence_review_queue",
            "test_market_r10e10_atomic_ro_evidence",
            "test_market_r10e11_writer_exclusion",
            "test_market_r10e12_locked_mock_forward",
            "test_market_r10e13_writer_adoption_inventory",
            "test_market_r10e14_optin_price_lock",
        ):
            self.assertEqual(sum(name in cmd for _,cmd in suites), 1)


if __name__ == "__main__":
    unittest.main()
