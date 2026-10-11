"""R10-E.6 tests: ResearchBitcoin catalog & synthetic d1 history planning only."""
import ast
import copy
import datetime as dt
from pathlib import Path
import unittest

from ingestion.researchbitcoin_catalog import CATALOG
from scripts.market_r10e6_rbn_tier2_shadow import (
    inventory_static, plan_rbn_tier2_fixture,
)

NOW = dt.datetime(2026, 10, 10, 22, 0, tzinfo=dt.timezone.utc)
FIRST = "2026-10-01"
CUTOFF = "2026-10-09"


def data_fixture():
    return {"mode": "synthetic_fixture",
            "present_dates": {slug: [] for slug in CATALOG}}


def tier_fixture(**changes):
    evidence = {"mode": "synthetic_fixture", "provider": "researchbitcoin",
                "reported_tier": 2, "network_authorized": False,
                "attestation": "SIMULATED"}
    evidence.update(changes)
    return evidence


def run_plan(data=None, tier=None, **options):
    return plan_rbn_tier2_fixture(
        data_fixture() if data is None else data,
        tier_fixture() if tier is None else tier,
        start_day=options.pop("start_day", FIRST),
        now_utc=options.pop("now_utc", NOW),
        **options,
    )


class TestR10E6RbnTier2Shadow(unittest.TestCase):
    def test_catalog_exact_existing_scalar_metrics(self):
        catalogue = inventory_static()
        self.assertEqual(len(catalogue), len(CATALOG))
        self.assertEqual(len(catalogue), 13)
        self.assertEqual({m["metric"] for m in catalogue}, set(CATALOG))
        self.assertTrue(all(m["shape"] == "scalar" and m["frequency"] == "d1"
                            for m in catalogue))
        self.assertTrue(all(m["tier2_entitlement_verified"] is False
                            for m in catalogue))

    def test_simulated_attestation_never_verifies_entitlement(self):
        r = run_plan()
        self.assertEqual(r["decision"], "SIMULATED_WINDOWS_PROPOSED")
        self.assertIs(r["execution_authorized"], False)
        self.assertIs(r["real_entitlement_verified"], False)
        self.assertIs(r["real_provider_window_verified"], False)
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual((r["http_requests"], r["sqlite_reads"], r["sqlite_writes"]), (0, 0, 0))

    def test_missing_entitlement_yields_zero_windows_and_no_execution(self):
        r = run_plan(tier=tier_fixture(attestation="MISSING"))
        self.assertEqual(r["decision"], "BLOCKED_NO_ENTITLEMENT_EVIDENCE")
        self.assertEqual(r["proposed_windows"], [])
        self.assertFalse(r["execution_authorized"])
        self.assertEqual(r["catalog_metric_count"], 13)

    def test_invalid_tier_or_production_permission_rejected(self):
        for field in (
            {"mode": "production"}, {"reported_tier": 0},
            {"reported_tier": 1}, {"reported_tier": True},
            {"network_authorized": True}, {"network_authorized": None},
            {"provider": "bitview"}, {"attestation": "VERIFIED"},
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                run_plan(tier=tier_fixture(**field))

    def test_thirteen_metric_windows_are_bounded_and_complete_in_fixture(self):
        r = run_plan()
        windows = r["proposed_windows"]
        self.assertEqual(len(windows), 13)
        self.assertEqual({w["metric"] for w in windows}, set(CATALOG))
        self.assertTrue(all(w["from_time"] == FIRST and
                            w["to_time_exclusive"] == "2026-10-10" for w in windows))
        self.assertTrue(all(w["expected_days"] == 9 for w in windows))
        self.assertTrue(all(w["window_is_synthetic_only"] is True for w in windows))
        self.assertEqual(r["closed_cutoff_day"], CUTOFF)

    def test_day_14_and_15_are_split_correctly(self):
        r = run_plan(start_day="2026-09-25", request_budget=13)
        self.assertTrue(all(w["expected_days"] == 14 for w in r["proposed_windows"]))
        self.assertEqual(r["metrics"][0]["proposed_windows_total"], 2)
        # With budget=13, only the earliest missing 14d per metric are proposed.
        r = run_plan(start_day="2026-09-25", request_budget=1)
        self.assertEqual(len(r["proposed_windows"]), 1)

    def test_nonconsecutive_missing_days_make_separate_windows(self):
        data = data_fixture()
        # All days stored except for 10/01 and 10/03 in one metric.
        metric = "sopr_sth"
        data["present_dates"][metric] = [
            f"2026-10-{day:02d}" for day in range(1, 10) if day not in (1, 3)
        ]
        r = run_plan(data=data)
        row = next(x for x in r["metrics"] if x["metric"] == metric)
        self.assertEqual(row["missing_days_in_requested_range"], 2)
        self.assertEqual(row["proposed_windows_total"], 2)
        self.assertEqual(row["stored_dates_in_range"], 7)

    def test_zero_missing_returns_no_work_in_fixture(self):
        data = data_fixture()
        days = [f"2026-10-{d:02d}" for d in range(1, 10)]
        for slug in CATALOG:
            data["present_dates"][slug] = days[:]
        r = run_plan(data=data)
        self.assertEqual(r["decision"], "NO_MISSING_DATES_IN_FIXTURE")
        self.assertEqual(r["proposed_windows"], [])
        self.assertFalse(r["real_provider_window_verified"])

    def test_partial_catalog_inventory_fails_closed(self):
        data = data_fixture()
        del data["present_dates"]["sopr_sth"]
        with self.assertRaises(ValueError):
            run_plan(data=data)
        data["present_dates"]["unknown_metric"] = []
        with self.assertRaises(ValueError):
            run_plan(data=data)

    def test_duplicate_dates_future_or_early_dates_rejected(self):
        for days in (
            ["2026-10-01", "2026-10-01"],
            ["2026-10-10"],
            ["2008-12-31"],
            ["2026-10-01T00:00:00Z"],
            ["2026-10-1"],
        ):
            data = data_fixture()
            data["present_dates"]["sopr_sth"] = days
            with self.subTest(days=days), self.assertRaises(ValueError):
                run_plan(data=data)

    def test_invalid_start_and_naive_clock_rejected(self):
        for date in ("2026-10-10", "2008-12-31", "not-a-date"):
            with self.subTest(date=date), self.assertRaises(ValueError):
                run_plan(start_day=date)
        with self.assertRaises(ValueError):
            run_plan(now_utc=dt.datetime(2026, 10, 10, 22))
        with self.assertRaises(ValueError):
            run_plan(now_utc=NOW.astimezone(dt.timezone(dt.timedelta(hours=-5))))

    def test_boolean_zero_or_excessive_request_budget_rejected(self):
        for n in (-1, 0, True, 14, "13"):
            with self.subTest(value=n), self.assertRaises(ValueError):
                run_plan(request_budget=n)
        self.assertEqual(len(run_plan(request_budget=1)["proposed_windows"]), 1)

    def test_endpoint_unit_and_scale_match_live_catalog_definitions(self):
        r = run_plan()
        for w in r["proposed_windows"]:
            metric = CATALOG[w["metric"]]
            with self.subTest(metric=metric.slug):
                self.assertEqual(w["endpoint_path"], metric.endpoint + "/" + metric.slug)
                self.assertEqual(w["raw_unit"], metric.unit)
                self.assertEqual(w["raw_scale"], metric.raw_scale)
                self.assertEqual(w["resolution"], "d1")
        fractions = [w for w in r["proposed_windows"]
                     if w["raw_scale"] == "fraction_0_1"]
        self.assertEqual(len(fractions), 2)

    def test_no_io_import_cli_or_api_apply(self):
        path = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e6_rbn_tier2_shadow.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        for module in ("sqlite3", "requests", "urllib.request", "socket", "os"):
            self.assertNotIn(module, imported)
        self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name == "main"
                             for node in tree.body))
        self.assertFalse(any(isinstance(node, ast.Call)
                             and isinstance(node.func, ast.Name)
                             and node.func.id == "urlopen" for node in ast.walk(tree)))

    def test_all_previous_suites_registered_once(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] /
                          "run_all.py").read_text(encoding="utf-8"))
        values = [ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "TESTS"
                          for t in node.targets)]
        self.assertEqual(len(values), 1)
        items = values[0]
        self.assertGreaterEqual(len(items), 44)
        labels = [name for name, _ in items]
        commands = [command for _, command in items]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(len(commands), len(set(commands)))
        for name in (
            "test_market_r10e_plan_only", "test_market_r10e_dispatch_offline",
            "test_market_r10e2_shadow_gate", "test_market_r10e3_mock_e2e",
            "test_market_r10e4_synthetic_cycle",
            "test_market_r10e5_backward_fixture_plan",
            "test_market_r10e6_rbn_tier2_shadow",
        ):
            self.assertEqual(sum(name in command for command in commands), 1)


if __name__ == "__main__":
    unittest.main()
