"""R10-E.3 full synthetic v3 forward test: mocked HTTP and temp SQLite only."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.market_r10e3_mock_e2e import TABLES, run_mock_cycle


class TestR10E3MockOnePage(unittest.TestCase):
    def test_bybit_success_all_auditors_and_receipt(self):
        r = run_mock_cycle("bybit", "success")
        self.assertEqual(r["state"], "MOCK_FIXTURE_PASS", r)
        self.assertEqual(r["apply_result"]["status"], "success")
        self.assertEqual(r["apply_result"]["points"], 2)
        self.assertTrue(r["apply_result"]["cursor_advanced"])
        self.assertEqual(r["mock_fetch_calls"], 1)
        self.assertEqual(r["request_receipts"], 1)
        self.assertEqual(r["verified_receipts"], 1)

    def test_binance_success_all_auditors_and_receipt(self):
        r = run_mock_cycle("binance", "success")
        self.assertEqual(r["state"], "MOCK_FIXTURE_PASS", r)
        self.assertEqual(r["apply_result"]["status"], "success")
        self.assertEqual(r["apply_result"]["requests"], 1)

    def test_three_independent_quality_gates(self):
        for provider in ("binance", "bybit"):
            r = run_mock_cycle(provider)
            with self.subTest(provider=provider):
                self.assertEqual(r["audit_gates"], {
                    "RAW": "PASS_SQLITE_TO_RAW",
                    "TEMPORAL": "SNAPSHOT_INTERNAL_QA_OK",
                    "LINEAGE": "PASS_REQUEST_LINEAGE",
                    "SQLITE": "ok",
                    "FOREIGN_KEY_ERRORS": 0,
                })

    def test_ten_tables_preserve_old_primary_keys_and_row_hashes(self):
        r = run_mock_cycle("bybit")
        self.assertEqual(set(r["table_deltas"]), set(TABLES))
        self.assertEqual(len(TABLES), 10)
        for table, v in r["table_deltas"].items():
            with self.subTest(table=table):
                self.assertEqual(v["deleted"], 0)
                self.assertEqual(v["modified"], 0)

    def test_success_expected_atomic_deltas(self):
        r = run_mock_cycle("binance", "success")
        counts = {k: v["added"] for k, v in r["table_deltas"].items()}
        self.assertEqual(counts["market_derivatives"], 2)
        for table in ("market_raw_payloads", "market_source_runs",
                      "market_request_lineage", "market_request_acquisitions",
                      "market_source_cursors"):
            self.assertEqual(counts[table], 1, table)
        for table in ("market_context_migrations", "market_etf_flows",
                      "market_calendar_events", "market_context_revisions"):
            self.assertEqual(counts[table], 0, table)

    def test_empty_response_keeps_cursor_and_no_derivatives(self):
        r = run_mock_cycle("bybit", "empty")
        self.assertEqual(r["state"], "MOCK_FIXTURE_PASS", r)
        self.assertEqual(r["apply_result"]["status"], "empty")
        self.assertFalse(r["apply_result"]["cursor_advanced"])
        self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)
        self.assertEqual(r["table_deltas"]["market_source_cursors"]["added"], 0)
        self.assertEqual(r["table_deltas"]["market_request_lineage"]["added"], 1)

    def test_http_429_records_failed_receipt_without_data(self):
        for provider in ("binance", "bybit"):
            r = run_mock_cycle(provider, "http429")
            with self.subTest(provider=provider):
                self.assertEqual(r["state"], "MOCK_FIXTURE_PASS", r)
                self.assertEqual(r["apply_result"]["status"], "failed")
                self.assertFalse(r["apply_result"]["cursor_advanced"])
                self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)
                self.assertEqual(r["table_deltas"]["market_request_acquisitions"]["added"], 0)
                self.assertEqual(r["table_deltas"]["market_request_lineage"]["added"], 1)

    def test_gap_response_rejected_without_cursor_advance(self):
        r = run_mock_cycle("bybit", "gap")
        self.assertEqual(r["state"], "MOCK_FIXTURE_PASS", r)
        self.assertEqual(r["apply_result"]["status"], "failed")
        self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)
        self.assertEqual(r["table_deltas"]["market_source_cursors"]["added"], 0)

    def test_audits_only_synthetic_fixture_and_no_live_requests(self):
        for provider in ("binance", "bybit"):
            for scenario in ("success", "empty", "http429", "gap"):
                with self.subTest(provider=provider, scenario=scenario):
                    r = run_mock_cycle(provider, scenario)
                    self.assertEqual(r["http_real_requests"], 0)
                    self.assertEqual(r["mock_fetch_calls"], 1)
                    self.assertEqual(r["production_db_reads"], 0)
                    self.assertEqual(r["production_db_writes"], 0)
                    self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")

    def test_unsupported_provider_and_scenario_block_before_io(self):
        for provider, scenario in (("coinbase", "success"), ("bybit", "live"),
                                   ("bybit", "apply")):
            with self.subTest(provider=provider, scenario=scenario):
                with self.assertRaises(ValueError):
                    run_mock_cycle(provider, scenario)

    def test_no_cli_or_remote_db_default_on_fixture_harness(self):
        target = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e3_mock_e2e.py"
        tree = ast.parse(target.read_text(encoding="utf-8"))
        imported = {
            alias.name for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for alias in n.names
        }
        self.assertNotIn("requests", imported)
        self.assertNotIn("urllib.request", imported)
        self.assertFalse(any(isinstance(n, ast.FunctionDef) and n.name == "main"
                             for n in tree.body))
        self.assertFalse(any(isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Attribute)
                             and n.func.attr == "urlopen" for n in ast.walk(tree)))

    def test_runner_propagates_failed_suite_exit_code(self):
        # Reproduce the offline runner with a synthetic failing command,
        # preserving the mandatory live-only registry entry. That live suite
        # is explicitly skipped, never imported or run by this fixture.
        # No test here calls APIs or opens project databases.
        runner = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(runner.read_text(encoding="utf-8"))
        declarations = [
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TESTS"
                    for t in node.targets)
        ]
        self.assertEqual(len(declarations), 1)
        declarations[0].value = ast.parse(
            repr([("Synthetic failing suite",
                   sys.executable + " -c 'import sys;sys.exit(7)'"),
                  ("Integration: Pipeline",
                   "python3 tests/integration/test_pipeline.py")]),
            mode="eval").body
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "tests"
            test_dir.mkdir()
            synthetic_runner = test_dir / "run_all.py"
            synthetic_runner.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(synthetic_runner)],
                cwd=temp_dir, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("Some tests failed", proc.stdout)
        self.assertIn("Synthetic failing suite", proc.stdout)

    def test_runner_registers_exactly_one_e3_and_preserves_predecessors(self):
        target = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(target.read_text(encoding="utf-8"))
        lists = [ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "TESTS"
                         for t in n.targets)]
        self.assertEqual(len(lists), 1)
        # Preserve the E.3 minimum without forbidding future E.4/E.5 suites.
        self.assertGreaterEqual(len(lists[0]), 41)
        labels = [name for name, _ in lists[0]]
        commands = [cmd for _, cmd in lists[0]]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(len(commands), len(set(commands)))
        for suite in ("test_market_r10e_plan_only",
                      "test_market_r10e_dispatch_offline",
                      "test_market_r10e2_shadow_gate",
                      "test_market_r10e3_mock_e2e"):
            self.assertEqual(sum(suite in x for x in commands), 1)


if __name__ == "__main__":
    unittest.main()
