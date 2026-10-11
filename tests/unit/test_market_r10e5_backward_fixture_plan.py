"""R10-E.5: no-network, no-SQL backward history fixture planning."""
import ast
import copy
import datetime as dt
from pathlib import Path
import unittest

from scripts.market_r10e_plan_only import UTC, SLOT_MS, plan_stream
from scripts.market_r10e5_backward_fixture_plan import plan_backward_fixture

NOW = dt.datetime(2026, 10, 10, 22, 0, tzinfo=UTC)


def at(minutes_before):
    return (NOW - dt.timedelta(minutes=minutes_before)).isoformat(timespec="seconds")


def fixture_coverage(binance=(15,), bybit=(20,)):
    return {
        "state": "PLAN_ONLY_READ_ONLY",
        "historical_completeness": "NOT_VERIFIED",
        "provider_availability": "NOT_CHECKED",
        "http_requests": 0,
        "sqlite_writes": 0,
        "sources": [
            plan_stream([at(x) for x in binance], "binance", now=NOW),
            plan_stream([at(x) for x in bybit], "bybit", now=NOW),
        ],
    }


def fixture_availability(binance=180, bybit=180):
    def item(provider, value):
        return {"provider": provider,
                "status": "SYNTHETIC_WINDOW" if value is not None else "UNKNOWN",
                "earliest_available_utc": at(value) if value is not None else None}
    return {"mode": "synthetic_fixture", "permission_for_requests": False,
            "sources": [item("binance", binance), item("bybit", bybit)]}


class TestR10E5SyntheticBackward(unittest.TestCase):
    def test_backlog_selects_provider_with_larger_historical_gap(self):
        result = plan_backward_fixture(fixture_coverage(), fixture_availability())
        self.assertEqual(result["decision"], "PROPOSE_SYNTHETIC_BACKWARD_PAGE")
        self.assertEqual(result["selected"]["provider"], "binance")
        self.assertEqual(result["selected"]["estimated_points_this_page"], 33)
        self.assertEqual(result["selected"]["direction"], "backward")

    def test_timestamp_endtime_is_oldest_minus_one_millisecond(self):
        result = plan_backward_fixture(fixture_coverage(), fixture_availability())
        selected = result["selected"]
        oldest = dt.datetime.fromisoformat(selected["stored_min_utc"])
        self.assertEqual(selected["requested_end_ms"],
                         int(oldest.timestamp() * 1000) - 1)
        self.assertIsNone(selected["requested_start_ms"])
        self.assertEqual(selected["estimated_last_row_utc"], at(20))
        self.assertEqual(selected["estimated_first_row_utc"], at(180))

    def test_exact_200_bybit_slots_fit_one_page(self):
        cov = fixture_coverage(binance=(15,), bybit=(15,))
        avail = fixture_availability(binance=15, bybit=1015)
        r = plan_backward_fixture(cov, avail)
        self.assertEqual(r["selected"]["provider"], "bybit")
        self.assertEqual(r["selected"]["estimated_points_this_page"], 200)
        self.assertEqual(r["selected"]["estimated_pages_for_fixture_window"], 1)

    def test_201_bybit_slots_require_two_pages(self):
        cov = fixture_coverage(binance=(15,), bybit=(15,))
        avail = fixture_availability(binance=15, bybit=1020)
        r = plan_backward_fixture(cov, avail)
        self.assertEqual(r["selected"]["estimated_points_this_page"], 200)
        self.assertEqual(r["selected"]["estimated_pages_for_fixture_window"], 2)
        self.assertEqual(r["selected"]["estimated_first_row_utc"], at(1015))

    def test_no_backfill_in_synthetic_window(self):
        r = plan_backward_fixture(fixture_coverage(),
                                  fixture_availability(binance=15, bybit=20))
        self.assertEqual(r["decision"], "NO_BACKFILL_IN_FIXTURE_WINDOW")
        self.assertIsNone(r["selected"])
        self.assertFalse(r["execution_authorized"])

    def test_unknown_provider_window_blocks_every_source(self):
        r = plan_backward_fixture(fixture_coverage(),
                                  fixture_availability(binance=None, bybit=180))
        self.assertEqual(r["decision"], "BLOCKED_INCOMPLETE_EVIDENCE")
        self.assertEqual(r["blocked_providers"], ["binance"])
        self.assertIsNone(r["selected"])

    def test_no_oi_baseline_blocks_every_source(self):
        r = plan_backward_fixture(fixture_coverage(bybit=()),
                                  fixture_availability())
        self.assertEqual(r["decision"], "BLOCKED_INCOMPLETE_EVIDENCE")
        self.assertEqual(r["blocked_providers"], ["bybit"])

    def test_internal_gap_blocks_even_if_other_source_ready(self):
        r = plan_backward_fixture(fixture_coverage(binance=(15, 25)),
                                  fixture_availability())
        self.assertEqual(r["decision"], "BLOCKED_INCOMPLETE_EVIDENCE")
        self.assertEqual(r["blocked_providers"], ["binance"])

    def test_nonfixture_or_permission_attempt_fails_closed(self):
        for changes in ({"mode": "production"},
                        {"permission_for_requests": True},
                        {"permission_for_requests": None}):
            evidence = fixture_availability()
            evidence.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan_backward_fixture(fixture_coverage(), evidence)

    def test_unknown_bound_with_timestamp_and_duplicates_block(self):
        base = fixture_availability()
        for mutate in (
            lambda q: q["sources"][0].update(status="UNKNOWN"),
            lambda q: q["sources"][0].update(status="VERIFIED_REAL"),
            lambda q: q["sources"][0].update(provider="bybit"),
            lambda q: q["sources"].append(copy.deepcopy(q["sources"][0])),
        ):
            evidence = copy.deepcopy(base)
            mutate(evidence)
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                plan_backward_fixture(fixture_coverage(), evidence)

    def test_utc_and_five_minute_alignment_are_mandatory(self):
        for bad in ("2026-10-10T19:01:00+00:00",
                    "2026-10-10T19:00:00",
                    "2026-10-10T19:00:00+01:00"):
            evidence = fixture_availability()
            evidence["sources"][0]["earliest_available_utc"] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                plan_backward_fixture(fixture_coverage(), evidence)

    def test_tampered_coverages_rejected(self):
        base = fixture_coverage()
        for mutation in (
            lambda p: p.update(state="LIVE"),
            lambda p: p.update(historical_completeness="COMPLETE"),
            lambda p: p.update(http_requests=1),
            lambda p: p["sources"][0].update(interval="1h"),
            lambda p: p["sources"][0].update(provider="bybit"),
            lambda p: p["sources"][1].update(cutoff_utc="2026-10-10T20:00:00+00:00"),
        ):
            report = copy.deepcopy(base)
            mutation(report)
            with self.subTest(report=report), self.assertRaises(ValueError):
                plan_backward_fixture(report, fixture_availability())

    def test_missing_future_rows_do_not_claim_history_completeness(self):
        report = plan_backward_fixture(fixture_coverage(), fixture_availability())
        self.assertIs(report["execution_authorized"], False)
        self.assertIs(report["real_provider_bound_verified"], False)
        self.assertEqual(report["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual(report["provider_availability"], "NOT_CHECKED")
        self.assertEqual((report["http_requests"],
                          report["sqlite_reads"], report["sqlite_writes"]), (0, 0, 0))
        self.assertIs(report["selected"]["provider_window_is_only_simulated"], True)
        self.assertIs(report["forward_cursor_mutated"], False)
        self.assertEqual(report["backward_cursor_namespace"],
                         "history_backward_v1_open_interest")

    def test_no_cli_network_or_database_dependency(self):
        file = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e5_backward_fixture_plan.py"
        tree = ast.parse(file.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(t, ast.FunctionDef) and t.name == "main"
                             for t in tree.body))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        for disallowed in ("sqlite3", "requests", "urllib.request", "socket"):
            self.assertNotIn(disallowed, imports)

    def test_runner_has_unique_required_registry_with_future_extensions_allowed(self):
        path = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        suites = [ast.literal_eval(t.value) for t in tree.body
                  if isinstance(t, ast.Assign)
                  and any(isinstance(k, ast.Name) and k.id == "TESTS"
                          for k in t.targets)]
        self.assertEqual(len(suites), 1)
        self.assertGreaterEqual(len(suites[0]), 43)
        labels = [name for name, _ in suites[0]]
        commands = [cmd for _, cmd in suites[0]]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(len(commands), len(set(commands)))
        for module in (
            "test_market_r10e_plan_only",
            "test_market_r10e_dispatch_offline",
            "test_market_r10e2_shadow_gate",
            "test_market_r10e3_mock_e2e",
            "test_market_r10e4_synthetic_cycle",
            "test_market_r10e5_backward_fixture_plan",
        ):
            self.assertEqual(sum(module in cmd for cmd in commands), 1)


if __name__ == "__main__":
    unittest.main()
