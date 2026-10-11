"""R10-E.9 tests: reconcile E7/E8 synthetic reports, NEVER issue API requests."""
import ast
import copy
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.market_r10e7_ro_archive_manifest import read_manifest
from scripts.market_r10e8_ro_internal_gap_audit import audit_internal_gaps
from scripts.market_r10e9_evidence_review_queue import reconcile_review_queue
from tests.unit.test_market_r10e8_ro_internal_gap_audit import fixture

NOW = dt.datetime(2026, 10, 10, 22, tzinfo=dt.timezone.utc)


class TestR10E9EvidenceReconciliation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="r10e9-synth-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "synthetic.db"
        fixture(self.path)

    def reports(self):
        return (read_manifest(self.path, now=NOW),
                audit_internal_gaps(self.path, now=NOW))

    def assess(self):
        return reconcile_review_queue(*self.reports())

    def test_three_review_candidates_only(self):
        report = self.assess()
        self.assertEqual(report["decision"], "REVIEW_CANDIDATES_PRESENT")
        self.assertEqual(report["total_review_candidates"], 3)
        self.assertEqual([x["provider"] for x in report["review_queue"]],
                         ["bitview", "fred", "researchbitcoin"])
        self.assertEqual([x["metric"] for x in report["review_queue"]],
                         ["mvrv", "CPIAUCSL", "sopr_sth"])

    def test_fred_month_candidate_is_not_30_daily_gaps(self):
        item = next(x for x in self.assess()["review_queue"] if x["provider"] == "fred")
        self.assertEqual(item["gap_method"], "MONTHLY_OBSERVATION")
        self.assertEqual(item["internal_gap_candidates"], 1)
        self.assertEqual(item["sample_missing_slots"], ["2026-08"])

    def test_researchbitcoin_and_bitview_daily_slots(self):
        rows = [x for x in self.assess()["review_queue"]
                if x["provider"] in ("researchbitcoin", "bitview")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(x["gap_method"] == "UTC_CALENDAR_DAY" for x in rows))
        self.assertTrue(all(x["sample_missing_slots"] == ["2026-10-08"] for x in rows))

    def test_yahoo_and_fred_daily_not_false_missing(self):
        result = self.assess()
        self.assertEqual(result["summary"]["calendar_required"], 3)
        self.assertFalse(any(row["provider"] == "yahoo" for row in result["review_queue"]))
        self.assertFalse(any(row["metric"] == "DGS10" for row in result["review_queue"]))
        self.assertFalse(any(row["metric"] == "NFCI" for row in result["review_queue"]))

    def test_missing_catalog_metrics_do_not_turn_into_api_requests(self):
        result = self.assess()
        self.assertGreater(result["summary"]["no_baseline"], 0)
        self.assertEqual(result["summary"]["archived_series"], 6)
        self.assertEqual(result["summary"]["potential_gap_series"], 3)
        self.assertTrue(all(x["request_ready"] is False for x in result["review_queue"]))

    def test_cross_reports_never_claim_same_transaction(self):
        x = self.assess()
        self.assertFalse(x["atomic_cross_report_snapshot_verified"])
        self.assertTrue(x["source_reports_correlated"])
        self.assertTrue(x["independent_reports_consistent_on_aggregates_only"])
        self.assertIn("independent SQLite", x["caveat"])

    def test_no_entitlement_api_or_cursor_rights(self):
        x = self.assess()
        self.assertEqual(x["historical_completeness"], "NOT_VERIFIED")
        self.assertFalse(x["real_provider_availability_verified"])
        self.assertFalse(x["real_tier2_entitlement_verified"])
        self.assertFalse(x["execution_authorized"])
        self.assertFalse(x["request_ready"])
        self.assertFalse(x["backfill_cursor_advanced"])
        self.assertEqual((x["http_requests"], x["sqlite_reads"], x["sqlite_writes"]), (0, 0, 0))

    def test_queue_budget_one_stable_and_truncates(self):
        a, b = self.reports()
        x = reconcile_review_queue(a, b, max_items=1)
        self.assertEqual(x["total_review_candidates"], 3)
        self.assertTrue(x["review_queue_truncated"])
        self.assertEqual(len(x["review_queue"]), 1)
        self.assertEqual(x["review_queue"][0]["provider"], "bitview")

    def test_bad_budget_bool_and_oversize_rejected(self):
        for bad in (False, True, 0, -1, 41, "2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                reconcile_review_queue(*self.reports(), max_items=bad)

    def test_count_disagreement_blocks_even_if_gap_candidates_present(self):
        a, b = self.reports()
        next(x for x in a["sources"] if x["provider"] == "bitview")["series"][0]["observations"] += 1
        with self.assertRaisesRegex(ValueError, "aggregate conflict"):
            reconcile_review_queue(a, b)

    def test_first_or_last_timestamp_disagreement_blocks(self):
        for field in ("first_archived_utc", "last_archived_utc"):
            a, b = self.reports()
            next(x for x in b["sources"] if x["provider"] == "fred")["series"][0][field] = "2020-01-01"
            with self.subTest(field=field), self.assertRaises(ValueError):
                reconcile_review_queue(a, b)

    def test_missing_or_duplicated_provider_blocks(self):
        for mutation in (
            lambda x: x["sources"].pop(),
            lambda x: x["sources"][0].update(provider="yahoo"),
            lambda x: x["sources"].append(copy.deepcopy(x["sources"][0])),
        ):
            a, b = self.reports()
            mutation(a)
            with self.assertRaises(ValueError):
                reconcile_review_queue(a, b)

    def test_unknown_gap_method_and_fake_trading_day_are_rejected(self):
        for change in (
            {"gap_method": "TRADING_DAY_GUESSED"},
            {"gap_method": "EXTERNAL_PUBLICATION_CALENDAR_REQUIRED",
             "internal_gap_candidates": 1},
            {"sample_missing_slots": ["2026-10-12"]},
            {"sample_missing_slots": ["2026-10-08", "2026-10-08"]},
            {"sample_truncated": True},
        ):
            a, b = self.reports()
            row = next(s for s in b["sources"] if s["provider"] == "bitview")["series"][0]
            row.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                reconcile_review_queue(a, b)

    def test_untrusted_mutation_execution_or_completeness_claims_fail(self):
        for which, update in (
            (0, {"execution_authorized": True}),
            (0, {"historical_completeness": "COMPLETE"}),
            (0, {"real_tier2_entitlement_verified": True}),
            (0, {"http_requests": 1}),
            (1, {"sqlite_writes": 2}),
            (1, {"real_provider_availability_verified": True}),
        ):
            a, b = self.reports()
            [a, b][which].update(update)
            with self.subTest(which=which, update=update), self.assertRaises(ValueError):
                reconcile_review_queue(a, b)

    def test_empty_archives_produce_no_review_queue(self):
        with sqlite3.connect(self.path) as db:
            for table in ("onchain_external_observations", "daily", "macro_fred",
                          "market_ohlc_history"):
                db.execute("DELETE FROM " + table)
        r = self.assess()
        self.assertEqual(r["decision"], "NO_INTERNAL_REVIEW_CANDIDATES")
        self.assertEqual(r["total_review_candidates"], 0)
        self.assertFalse(r["review_queue_truncated"])

    def test_schema_not_initialized_agrees_when_tables_missing(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE daily")
            db.execute("DROP TABLE macro_fred")
        r = self.assess()
        self.assertEqual(r["summary"]["archived_series"], 3)
        self.assertFalse(any(x["provider"] in ("fred", "bitview") for x in r["review_queue"]))

    def test_schema_state_disagreement_blocks(self):
        a, b = self.reports()
        next(x for x in b["sources"] if x["provider"] == "fred")["schema_state"] = "NOT_INITIALIZED"
        with self.assertRaises(ValueError):
            reconcile_review_queue(a, b)

    def test_never_mutates_input_reports(self):
        a, b = self.reports()
        aa, bb = copy.deepcopy(a), copy.deepcopy(b)
        reconcile_review_queue(a, b)
        self.assertEqual(a, aa)
        self.assertEqual(b, bb)

    def test_pure_module_has_no_cli_network_or_sqlite_import(self):
        path = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e9_evidence_review_queue.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            if isinstance(node, ast.ImportFrom):
                names.add(node.module)
        for prohibited in ("sqlite3", "requests", "urllib.request", "os", "socket"):
            self.assertNotIn(prohibited, names)
        self.assertFalse(any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body))

    def test_runner_evergreen_registry_uniqueness(self):
        path = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        specs = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(k, ast.Name) and k.id == "TESTS" for k in n.targets)]
        self.assertEqual(len(specs), 1)
        specs = specs[0]
        self.assertGreaterEqual(len(specs), 47)
        self.assertEqual(len({a for a, _ in specs}), len(specs))
        self.assertEqual(len({b for _, b in specs}), len(specs))
        for suffix in ("test_market_r10e_plan_only", "test_market_r10e_dispatch_offline",
                       "test_market_r10e2_shadow_gate", "test_market_r10e3_mock_e2e",
                       "test_market_r10e4_synthetic_cycle", "test_market_r10e5_backward_fixture_plan",
                       "test_market_r10e6_rbn_tier2_shadow", "test_market_r10e7_ro_archive_manifest",
                       "test_market_r10e8_ro_internal_gap_audit",
                       "test_market_r10e9_evidence_review_queue"):
            self.assertEqual(sum(suffix in command for _, command in specs), 1)


if __name__ == "__main__":
    unittest.main()
