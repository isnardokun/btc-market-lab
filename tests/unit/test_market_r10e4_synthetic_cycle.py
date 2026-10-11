"""R10-E.4 integrated E0/E1/E2/E3 cycle on ephemeral SQLite only."""
import ast
import datetime as dt
from pathlib import Path
import unittest

from scripts.market_r10e3_mock_e2e import BASE, TABLES
from scripts.market_r10e4_synthetic_cycle import (
    CLOCK, fixture_policy, simulate_synthetic_cycle,
)


class TestR10E4SyntheticCycle(unittest.TestCase):
    def test_success_runs_one_page_all_quality_gates(self):
        r = simulate_synthetic_cycle()
        self.assertEqual(r["state"], "MOCK_ONE_PAGE_ACCEPTED", r)
        self.assertEqual(r["shadow_gate_decision"], "SHADOW_ELIGIBLE")
        self.assertEqual(r["dispatcher_decision"], "PROPOSE_ONE_PAGE_ONLY")
        self.assertEqual(r["chosen_provider"], "binance")
        self.assertEqual(r["mock_fetch_calls"], 1)
        self.assertEqual(r["forward_result"]["status"], "success")
        self.assertEqual(r["forward_result"]["points"], 2)
        self.assertTrue(r["forward_result"]["cursor_advanced"])
        self.assertFalse(r["live_execution_authorized"])
        self.assertEqual(r["http_real_requests"], 0)
        self.assertEqual(r["production_db_reads"], 0)
        self.assertEqual(r["production_db_writes"], 0)
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")

    def test_all_independent_auditors_share_read_snapshot(self):
        for scenario in ("success", "empty", "http429", "gap"):
            with self.subTest(scenario=scenario):
                r = simulate_synthetic_cycle(scenario=scenario)
                self.assertEqual(r["audit_gates"], {
                    "RAW": "PASS_SQLITE_TO_RAW",
                    "TEMPORAL": "SNAPSHOT_INTERNAL_QA_OK",
                    "LINEAGE": "PASS_REQUEST_LINEAGE",
                    "SQLITE": "ok",
                    "FOREIGN_KEY_ERRORS": 0,
                })

    def test_ten_tables_preserve_old_primary_keys(self):
        for scenario in ("success", "empty", "http429", "gap"):
            r = simulate_synthetic_cycle(scenario=scenario)
            self.assertEqual(set(r["table_deltas"]), set(TABLES))
            self.assertTrue(r["immutable_pre_rows"])
            for table, diff in r["table_deltas"].items():
                with self.subTest(table=table, scenario=scenario):
                    self.assertEqual((diff["modified"], diff["deleted"]), (0, 0))

    def test_success_exact_delta_and_single_receipt(self):
        r = simulate_synthetic_cycle()
        table = {name: change["added"] for name, change in r["table_deltas"].items()}
        self.assertEqual(table["market_derivatives"], 2)
        for key in ("market_raw_payloads", "market_source_runs",
                    "market_request_lineage", "market_request_acquisitions",
                    "market_source_cursors"):
            self.assertEqual(table[key], 1)
        for key in ("market_context_migrations", "market_etf_flows",
                    "market_calendar_events", "market_context_revisions"):
            self.assertEqual(table[key], 0)

    def test_empty_records_receipt_without_cursor_advance(self):
        r = simulate_synthetic_cycle(scenario="empty")
        self.assertEqual(r["state"], "MOCK_EMPTY_STOP", r)
        self.assertEqual(r["mock_fetch_calls"], 1)
        self.assertEqual(r["forward_result"]["status"], "empty")
        self.assertFalse(r["forward_result"]["cursor_advanced"])
        self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)
        self.assertEqual(r["table_deltas"]["market_source_cursors"]["added"], 0)
        self.assertEqual(r["table_deltas"]["market_request_lineage"]["added"], 1)

    def test_http429_stops_after_one_attempt_with_failure_receipt(self):
        r = simulate_synthetic_cycle(scenario="http429")
        self.assertEqual(r["state"], "MOCK_FAILURE_STOP", r)
        self.assertEqual(r["mock_fetch_calls"], 1)
        self.assertEqual(r["forward_result"]["status"], "failed")
        self.assertFalse(r["forward_result"]["cursor_advanced"])
        self.assertEqual(r["table_deltas"]["market_request_acquisitions"]["added"], 0)
        self.assertEqual(r["table_deltas"]["market_request_lineage"]["added"], 1)

    def test_gap_rejected_without_new_observations(self):
        r = simulate_synthetic_cycle(scenario="gap")
        self.assertEqual(r["state"], "MOCK_FAILURE_STOP", r)
        self.assertEqual(r["forward_result"]["status"], "failed")
        self.assertEqual(r["table_deltas"]["market_derivatives"]["added"], 0)
        self.assertEqual(r["table_deltas"]["market_source_cursors"]["added"], 0)

    def test_zero_budget_blocks_before_any_mock_fetch_or_sql_write(self):
        r = simulate_synthetic_cycle(policy=fixture_policy(remaining_request_budget=0))
        self.assertEqual(r["state"], "MOCK_PRE_GATE_BLOCKED", r)
        self.assertEqual(r["shadow_gate_decision"], "BLOCKED_QUOTA_EXHAUSTED")
        self.assertIsNone(r["forward_result"])
        self.assertEqual(r["mock_fetch_calls"], 0)
        self.assertTrue(all(x["added"] == 0 for x in r["table_deltas"].values()))

    def test_circuit_breaker_blocks_without_page(self):
        r = simulate_synthetic_cycle(policy=fixture_policy(circuit_open=True))
        self.assertEqual(r["state"], "MOCK_PRE_GATE_BLOCKED", r)
        self.assertEqual(r["shadow_gate_decision"], "BLOCKED_CIRCUIT_OPEN")
        self.assertEqual(r["mock_fetch_calls"], 0)

    def test_unverified_writer_lock_blocks_without_page(self):
        r = simulate_synthetic_cycle(policy=fixture_policy(writer_exclusion_simulated=False))
        self.assertEqual(r["state"], "MOCK_PRE_GATE_BLOCKED", r)
        self.assertEqual(r["shadow_gate_decision"], "BLOCKED_WRITER_EXCLUSION_UNVERIFIED")
        self.assertEqual(r["mock_fetch_calls"], 0)

    def test_stale_plan_blocks_without_page(self):
        r = simulate_synthetic_cycle(elapsed_seconds=301)
        self.assertEqual(r["state"], "MOCK_PRE_GATE_BLOCKED", r)
        self.assertEqual(r["shadow_gate_decision"], "BLOCKED_STALE_OR_FUTURE_PLAN")
        self.assertEqual(r["mock_fetch_calls"], 0)

    def test_cooldown_blocks_and_elapsed_cooldown_succeeds(self):
        recent = (CLOCK - dt.timedelta(seconds=30)).isoformat()
        blocked = simulate_synthetic_cycle(
            policy=fixture_policy(last_attempt_utc=recent))
        self.assertEqual(blocked["state"], "MOCK_PRE_GATE_BLOCKED")
        self.assertEqual(blocked["shadow_gate_decision"], "BLOCKED_COOLDOWN")
        elapsed = (CLOCK - dt.timedelta(seconds=60)).isoformat()
        accepted = simulate_synthetic_cycle(
            policy=fixture_policy(last_attempt_utc=elapsed))
        self.assertEqual(accepted["state"], "MOCK_ONE_PAGE_ACCEPTED")

    def test_no_closed_bar_gives_no_mock_fetch(self):
        now = BASE + dt.timedelta(minutes=10)
        r = simulate_synthetic_cycle(planned_now=now)
        self.assertEqual(r["state"], "MOCK_NO_WORK", r)
        self.assertEqual(r["dispatcher_decision"], "NO_CLOSED_BAR")
        self.assertEqual(r["shadow_gate_decision"], "NO_WORK")
        self.assertIsNone(r["forward_result"])
        self.assertEqual(r["mock_fetch_calls"], 0)
        self.assertTrue(all(x["added"] == 0 for x in r["table_deltas"].values()))

    def test_rejects_unsafe_synthetic_inputs_before_db_creation(self):
        for kwargs in ({"scenario": "real"}, {"scenario": "apply"},
                       {"policy": fixture_policy(mode="production")},
                       {"elapsed_seconds": -1},
                       {"elapsed_seconds": 86401},
                       {"planned_now": dt.datetime(2026, 10, 10, 22, 0)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                simulate_synthetic_cycle(**kwargs)

    def test_no_external_db_path_network_or_cli(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e4_synthetic_cycle.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.assertFalse(any(n.name == "main" for n in defs))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertNotIn("requests", imports)
        self.assertNotIn("urllib.request", imports)
        self.assertFalse(any(n.arg in ("db_path", "api_key", "fetch", "apply")
                             for f in defs for n in f.args.args))

    def test_runner_future_proof_registry(self):
        runner = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(runner.read_text(encoding="utf-8"))
        assignments = [ast.literal_eval(x.value) for x in tree.body
                       if isinstance(x, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "TESTS"
                               for t in x.targets)]
        self.assertEqual(len(assignments), 1)
        suites = assignments[0]
        self.assertGreaterEqual(len(suites), 42)
        labels = [name for name, _ in suites]
        commands = [command for _, command in suites]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(len(commands), len(set(commands)))
        for module in (
            "tests.unit.test_market_r10e_plan_only",
            "tests.unit.test_market_r10e_dispatch_offline",
            "tests.unit.test_market_r10e2_shadow_gate",
            "tests.unit.test_market_r10e3_mock_e2e",
            "tests.unit.test_market_r10e4_synthetic_cycle",
        ):
            self.assertEqual(sum(module in command for command in commands), 1)


if __name__ == "__main__":
    unittest.main()
