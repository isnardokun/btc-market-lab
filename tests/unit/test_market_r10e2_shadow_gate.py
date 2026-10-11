"""R10-E.2 SHADOW policy gate regression tests. Synthetic JSON; zero live IO."""
import ast
import contextlib
import copy
import datetime as dt
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from scripts.market_r10e_plan_only import UTC, plan_stream
from scripts.market_r10e_dispatch_offline import dispatch_offline
from scripts.market_r10e2_shadow_gate import evaluate_shadow_gate, main

NOW = dt.datetime(2026, 10, 10, 22, 0, tzinfo=UTC)


def stamp(minute):
    return (dt.datetime(2026, 10, 10, 21, 0, tzinfo=UTC)
            + dt.timedelta(minutes=minute)).isoformat(timespec="seconds")


def make_dispatch(binance=(40,), bybit=(10,)):
    coverage = {
        "state": "PLAN_ONLY_READ_ONLY",
        "historical_completeness": "NOT_VERIFIED",
        "http_requests": 0,
        "sqlite_writes": 0,
        "now_utc": NOW.isoformat(),
        "sources": [
            plan_stream([stamp(x) for x in binance], "binance", now=NOW),
            plan_stream([stamp(x) for x in bybit], "bybit", now=NOW),
        ],
    }
    return dispatch_offline(coverage)


def fixture(**updates):
    runtime = {
        "mode": "synthetic_fixture",
        "remaining_request_budget": 1,
        "max_requests_per_cycle": 1,
        "minimum_cooldown_seconds": 60,
        "circuit_open": False,
        "writer_exclusion_simulated": True,
        "last_attempt_utc": None,
    }
    runtime.update(updates)
    return runtime


class TestR10E2ShadowGate(unittest.TestCase):
    def run_gate(self, dispatch=None, runtime=None, now=NOW):
        return evaluate_shadow_gate(
            make_dispatch() if dispatch is None else dispatch,
            fixture() if runtime is None else runtime, now=now)

    def test_fixture_eligible_never_grants_execution(self):
        r = self.run_gate()
        self.assertEqual(r["decision"], "SHADOW_ELIGIBLE")
        self.assertEqual(r["selected_provider"], "bybit")
        self.assertIs(r["execution_authorized"], False)
        self.assertIs(r["mock_policy_only"], True)
        self.assertEqual(r["simulated_max_requests_next_cycle"], 1)
        self.assertEqual((r["http_requests"], r["sqlite_reads"], r["sqlite_writes"]), (0, 0, 0))

    def test_stale_and_future_plan_block(self):
        old = self.run_gate(now=NOW + dt.timedelta(seconds=301))
        future = self.run_gate(now=NOW - dt.timedelta(seconds=1))
        self.assertEqual(old["decision"], "BLOCKED_STALE_OR_FUTURE_PLAN")
        self.assertEqual(future["decision"], "BLOCKED_STALE_OR_FUTURE_PLAN")
        self.assertFalse(old["execution_authorized"])

    def test_circuit_breaker_block(self):
        self.assertEqual(self.run_gate(runtime=fixture(circuit_open=True))["decision"], "BLOCKED_CIRCUIT_OPEN")

    def test_quota_exhausted_block(self):
        self.assertEqual(self.run_gate(runtime=fixture(remaining_request_budget=0))["decision"], "BLOCKED_QUOTA_EXHAUSTED")

    def test_writer_exclusion_unverified_block(self):
        r = self.run_gate(runtime=fixture(writer_exclusion_simulated=False))
        self.assertEqual(r["decision"], "BLOCKED_WRITER_EXCLUSION_UNVERIFIED")

    def test_cooldown_and_future_attempt_block(self):
        recent = fixture(last_attempt_utc=(NOW - dt.timedelta(seconds=30)).isoformat())
        future = fixture(last_attempt_utc=(NOW + dt.timedelta(seconds=1)).isoformat())
        self.assertEqual(self.run_gate(runtime=recent)["decision"], "BLOCKED_COOLDOWN")
        self.assertEqual(self.run_gate(runtime=future)["decision"], "BLOCKED_FUTURE_ATTEMPT")
        elapsed = fixture(last_attempt_utc=(NOW - dt.timedelta(seconds=60)).isoformat())
        self.assertEqual(self.run_gate(runtime=elapsed)["decision"], "SHADOW_ELIGIBLE")

    def test_bad_runtime_policy_fails_closed(self):
        for mutation in (
            {"mode": "production"},
            {"remaining_request_budget": True},
            {"remaining_request_budget": -1},
            {"max_requests_per_cycle": 2},
            {"minimum_cooldown_seconds": 0},
            {"circuit_open": "false"},
            {"writer_exclusion_simulated": "true"},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.run_gate(runtime=fixture(**mutation))

    def test_tampered_dispatch_rejected(self):
        base = make_dispatch()
        for mutate in (
            lambda d: d.update(execution_authorized=True),
            lambda d: d.update(http_requests=1),
            lambda d: d.update(historical_completeness="COMPLETE"),
            lambda d: d["selected"].update(provider="coinbase"),
            lambda d: d["selected"].update(interval="1m"),
            lambda d: d["selected"].update(estimated_slots_this_page=300),
            lambda d: d["selected"].update(direction="backward"),
            lambda d: d["selected"].update(first_page_end_utc=stamp(49)),
            lambda d: d["selected"].update(first_page_end_utc=stamp(55)),
            lambda d: d["selected"].update(next_start_utc="2026-10-10T21:11:00Z"),
            lambda d: d.update(closed_bar_cutoff_utc="2026-10-10T22:00:00Z"),
        ):
            d = copy.deepcopy(base)
            mutate(d)
            with self.subTest(d=d), self.assertRaises(ValueError):
                self.run_gate(dispatch=d)

    def test_no_work_remains_non_executable(self):
        r = self.run_gate(dispatch=make_dispatch((50,), (50,)))
        self.assertEqual(r["decision"], "NO_WORK")
        self.assertFalse(r["execution_authorized"])

    def test_source_gap_blocks_without_execution(self):
        r = self.run_gate(dispatch=make_dispatch((40,), (0, 10)))
        self.assertEqual(r["decision"], "BLOCKED_SOURCE_QA")
        self.assertIsNone(r["selected_provider"])

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            self.run_gate(now=dt.datetime(2026, 10, 10, 22, 0))

    def test_json_cli_on_fixture_files_does_not_change_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch_path = Path(tmp) / "dispatch.json"
            policy_path = Path(tmp) / "policy.json"
            dispatch_path.write_text(json.dumps(make_dispatch()), encoding="utf-8")
            policy_path.write_text(json.dumps(fixture()), encoding="utf-8")
            before = (dispatch_path.read_bytes(), policy_path.read_bytes())
            with contextlib.redirect_stdout(StringIO()) as captured:
                rc = main(["--dispatch-json", str(dispatch_path),
                           "--fixture-policy-json", str(policy_path),
                           "--now-utc", NOW.isoformat()])
            self.assertEqual(rc, 0)
            result = json.loads(captured.getvalue())
            self.assertEqual(result["decision"], "SHADOW_ELIGIBLE")
            self.assertIs(result["execution_authorized"], False)
            self.assertEqual((dispatch_path.read_bytes(), policy_path.read_bytes()), before)

    def test_cli_missing_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(StringIO()) as captured:
                rc = main(["--dispatch-json", str(Path(tmp) / "absent.json"),
                           "--fixture-policy-json", str(Path(tmp) / "missing.json"),
                           "--now-utc", NOW.isoformat()])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(captured.getvalue())["decision"], "BLOCKED_BAD_INPUT")

    def test_runner_suite_count_and_registration(self):
        runner_path = Path(__file__).resolve().parents[1] / "run_all.py"
        parsed = ast.parse(runner_path.read_text(encoding="utf-8"))
        declared = [
            ast.literal_eval(node.value)
            for node in parsed.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TESTS" for t in node.targets)
        ]
        self.assertEqual(len(declared), 1)
        suites = declared[0]
        self.assertEqual(len(suites), 40)
        self.assertEqual(
            sum("tests.unit.test_market_r10e2_shadow_gate" in command for _, command in suites), 1)

    def test_invalid_json_and_naive_utc_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "dispatch.json"
            b = Path(tmp) / "policy.json"
            a.write_text("invalid{", encoding="utf-8")
            b.write_text(json.dumps(fixture()), encoding="utf-8")
            with contextlib.redirect_stdout(StringIO()) as captured:
                rc = main(["--dispatch-json", str(a), "--fixture-policy-json", str(b),
                           "--now-utc", NOW.isoformat()])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(captured.getvalue())["http_requests"], 0)
            with contextlib.redirect_stdout(StringIO()) as captured:
                rc = main(["--dispatch-json", str(a), "--fixture-policy-json", str(b),
                           "--now-utc", "2026-10-10T22:00:00"])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(captured.getvalue())["decision"], "BLOCKED_BAD_INPUT")


if __name__ == "__main__":
    unittest.main()
