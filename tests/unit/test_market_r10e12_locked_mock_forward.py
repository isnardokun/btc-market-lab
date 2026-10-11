"""R10-E.12 mock v3 writer integration: synthetic SQLite + OS lock only."""
import ast
import contextlib
import datetime as dt
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

from scripts.market_r10e3_mock_e2e import _seed
from scripts import market_r10e12_locked_mock_forward as fixture
from scripts.market_r10e11_writer_exclusion import hold_writer_lock

CHILD_HOLDER = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1], namespace=sys.argv[2]):
    print('HELD',flush=True)
    sys.stdin.readline()
"""


class TestR10E12LockedForward(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r10e12-synthetic-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lockdir = self.root / "locks"
        self.lockdir.mkdir()
        self.path = self.root / "synthetic.db"
        (self.root / fixture.MARKER).write_text(
            fixture.MARKER_CONTENT, encoding="utf-8")
        with sqlite3.connect(self.path) as db:
            _seed(db)

    def run_fixture(self, **kwargs):
        return fixture.run_locked_mock_forward(self.path, self.lockdir, **kwargs)

    def _child_env(self):
        root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        return root, env

    def _holder(self, namespace=fixture.LOCK_NAMESPACE):
        root, env = self._child_env()
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD_HOLDER, str(self.lockdir), namespace],
            cwd=root, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        def clean():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.addCleanup(clean)
        with selectors.DefaultSelector() as select:
            select.register(child.stdout, selectors.EVENT_READ)
            self.assertTrue(select.select(timeout=5), "Fixture holder timed out")
        self.assertEqual(child.stdout.readline().strip(), "HELD")
        return child

    def test_success_binance_actual_v3_mock_is_guarded(self):
        r = self.run_fixture()
        self.assertEqual(r["state"], "MOCK_LOCKED_PAGE_PASS", r)
        self.assertEqual(r["lock_namespace"], fixture.LOCK_NAMESPACE)
        self.assertEqual(r["forward_status"], "success")
        self.assertEqual(r["forward_points"], 2)
        self.assertEqual(r["mock_fetch_calls"], 1)
        self.assertTrue(r["forward_cursor_advanced"])
        self.assertFalse(r["excludes_uncooperative_writers"])

    def test_success_bybit_actual_v3_mock_is_guarded(self):
        r = self.run_fixture(provider="bybit")
        self.assertEqual(r["state"], "MOCK_LOCKED_PAGE_PASS", r)
        self.assertEqual(r["provider"], "bybit")
        self.assertEqual(r["forward_points"], 2)

    def test_all_three_independent_audits_and_sqlite_integrity(self):
        r = self.run_fixture()
        self.assertEqual(r["audit_gates"], {
            "RAW": "PASS_SQLITE_TO_RAW",
            "TEMPORAL": "SNAPSHOT_INTERNAL_QA_OK",
            "LINEAGE": "PASS_REQUEST_LINEAGE",
            "SQLITE": "ok",
            "FOREIGN_KEY_ERRORS": 0,
        })

    def test_ten_original_tables_are_immutable(self):
        r = self.run_fixture()
        self.assertTrue(r["preexisting_rows_immutable"])
        self.assertEqual(len(r["table_deltas"]), 10)
        self.assertTrue(all(x["removed"] == x["modified"] == 0
                            for x in r["table_deltas"].values()))

    def test_success_has_precise_append_delta(self):
        r = self.run_fixture()
        added = {t: v["added"] for t, v in r["table_deltas"].items()}
        self.assertEqual(added["market_derivatives"], 2)
        for t in ("market_source_cursors", "market_source_runs",
                  "market_raw_payloads", "market_request_lineage",
                  "market_request_acquisitions"):
            self.assertEqual(added[t], 1)
        for t in ("market_context_migrations", "market_etf_flows",
                  "market_calendar_events", "market_context_revisions"):
            self.assertEqual(added[t], 0)

    def test_empty_one_request_and_no_cursor(self):
        r = self.run_fixture(scenario="empty")
        self.assertEqual(r["state"], "MOCK_LOCKED_PAGE_PASS", r)
        self.assertEqual(r["forward_status"], "empty")
        self.assertEqual(r["mock_fetch_calls"], 1)
        self.assertFalse(r["forward_cursor_advanced"])
        self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)

    def test_http429_mock_has_receipt_and_does_not_advance(self):
        r = self.run_fixture(scenario="http429")
        self.assertEqual(r["state"], "MOCK_LOCKED_PAGE_PASS", r)
        self.assertEqual(r["forward_status"], "failed")
        self.assertEqual(r["mock_fetch_calls"], 1)
        self.assertFalse(r["forward_cursor_advanced"])
        self.assertEqual(r["table_deltas"]["market_request_lineage"]["added"], 1)
        self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)

    def test_discontinuous_page_fails_without_appending_bars(self):
        r = self.run_fixture(scenario="gap")
        self.assertEqual(r["state"], "MOCK_LOCKED_PAGE_PASS", r)
        self.assertEqual(r["forward_status"], "failed")
        self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)
        self.assertFalse(r["forward_cursor_advanced"])

    def test_parent_holds_global_lock_no_sql_connection_even_attempted(self):
        with hold_writer_lock(self.lockdir, namespace=fixture.LOCK_NAMESPACE):
            with patch.object(fixture.sqlite3, "connect",
                              side_effect=AssertionError("should not open SQLite")):
                r = self.run_fixture()
        self.assertEqual(r["state"], "BLOCKED_WRITER_BUSY")
        self.assertFalse(r["lock_acquired"])
        self.assertEqual(r["mock_fetch_calls"], 0)
        self.assertEqual(r["sqlite_connections_started"], 0)

    def test_separate_process_lock_holder_blocks_mock_call(self):
        child = self._holder()
        r = self.run_fixture()
        self.assertEqual(r["state"], "BLOCKED_WRITER_BUSY")
        self.assertEqual(r["mock_fetch_calls"], 0)
        self.assertIsNone(child.poll())

    def test_child_forward_attempt_cannot_bypass_parent_lock(self):
        root, env = self._child_env()
        script = ("import json,sys\n"
                  "from scripts.market_r10e12_locked_mock_forward "
                  "import run_locked_mock_forward\n"
                  "print(json.dumps(run_locked_mock_forward(sys.argv[1],sys.argv[2])))\n")
        with hold_writer_lock(self.lockdir, namespace=fixture.LOCK_NAMESPACE):
            result = subprocess.run(
                [sys.executable, "-c", script, str(self.path), str(self.lockdir)],
                cwd=root, env=env, capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["state"], "BLOCKED_WRITER_BUSY")
        self.assertEqual(record["sqlite_connections_started"], 0)

    def test_child_unlock_allows_later_mock_forward(self):
        child = self._holder()
        self.assertEqual(self.run_fixture()["state"], "BLOCKED_WRITER_BUSY")
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5), 0)
        self.assertEqual(self.run_fixture()["state"], "MOCK_LOCKED_PAGE_PASS")

    def test_other_namespace_does_not_exclude_global_writer(self):
        # Demonstrates why every production writer must adopt ONE namespace.
        with hold_writer_lock(self.lockdir, namespace="wrong-local-namespace"):
            r = self.run_fixture()
        self.assertEqual(r["state"], "MOCK_LOCKED_PAGE_PASS")
        self.assertFalse(r["excludes_uncooperative_writers"])

    def test_busy_path_does_not_mutate_sqlite(self):
        before = self.path.read_bytes()
        with hold_writer_lock(self.lockdir, namespace=fixture.LOCK_NAMESPACE):
            r = self.run_fixture()
        self.assertEqual(r["state"], "BLOCKED_WRITER_BUSY")
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_marker_blocks_before_writer_attempt(self):
        (self.root / fixture.MARKER).unlink()
        with self.assertRaises(ValueError):
            self.run_fixture()
        self.assertFalse((self.lockdir / (fixture.LOCK_NAMESPACE + ".lock")).exists())

    def test_forged_marker_blocks(self):
        (self.root / fixture.MARKER).write_text("production", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_fixture()

    def test_production_style_database_name_is_rejected(self):
        renamed = self.root / "btc_research.db"
        self.path.rename(renamed)
        with self.assertRaises(ValueError):
            fixture.run_locked_mock_forward(renamed, self.lockdir)

    def test_directory_outside_synthetic_root_is_rejected(self):
        with self.assertRaises(ValueError):
            fixture.run_locked_mock_forward(self.path, self.root)
        other = self.root / "another"
        other.mkdir()
        with self.assertRaises(ValueError):
            fixture.run_locked_mock_forward(self.path, other)

    def test_missing_database_and_invalid_provider_or_scenario(self):
        with self.assertRaises(ValueError):
            fixture.run_locked_mock_forward(self.path.parent / "missing.db", self.lockdir)
        for opt in ({"provider": "fred"}, {"scenario": "live"},
                    {"provider": "bybit", "scenario": "apply"}):
            with self.subTest(opt=opt), self.assertRaises(ValueError):
                self.run_fixture(**opt)

    def test_never_uses_real_apis_or_authorizes_production(self):
        r = self.run_fixture()
        self.assertEqual(r["http_real_requests"], 0)
        self.assertEqual(r["production_db_reads"], 0)
        self.assertEqual(r["production_db_writes"], 0)
        self.assertFalse(r["production_authorized"])
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")
        file = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e12_locked_mock_forward.py"
        code = ast.parse(file.read_text(encoding="utf-8"))
        import_names = set()
        for node in ast.walk(code):
            if isinstance(node, ast.Import):
                import_names.update(k.name for k in node.names)
            if isinstance(node, ast.ImportFrom):
                import_names.add(node.module)
        for disallowed in ("requests", "urllib.request", "socket", "subprocess"):
            self.assertNotIn(disallowed, import_names)
        self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name == "main"
                             for node in code.body))

    def test_runner_registry_extensible_uniqueness(self):
        path = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignments = [ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(k, ast.Name) and k.id == "TESTS"
                               for k in node.targets)]
        self.assertEqual(len(assignments), 1)
        entries = assignments[0]
        self.assertGreaterEqual(len(entries), 50)
        self.assertEqual(len({a for a,_ in entries}), len(entries))
        self.assertEqual(len({b for _,b in entries}), len(entries))
        for module in ("test_market_r10e_plan_only",
                       "test_market_r10e_dispatch_offline",
                       "test_market_r10e2_shadow_gate",
                       "test_market_r10e3_mock_e2e",
                       "test_market_r10e4_synthetic_cycle",
                       "test_market_r10e5_backward_fixture_plan",
                       "test_market_r10e6_rbn_tier2_shadow",
                       "test_market_r10e7_ro_archive_manifest",
                       "test_market_r10e8_ro_internal_gap_audit",
                       "test_market_r10e9_evidence_review_queue",
                       "test_market_r10e10_atomic_ro_evidence",
                       "test_market_r10e11_writer_exclusion",
                       "test_market_r10e12_locked_mock_forward"):
            self.assertEqual(sum(module in command for _,command in entries), 1)


if __name__ == "__main__":
    unittest.main()
