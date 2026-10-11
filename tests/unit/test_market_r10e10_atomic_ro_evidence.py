"""R10-E.10 synthetic SQLite tests for ONE real read transaction."""
import ast
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import market_r10e10_atomic_ro_evidence as atomic
from scripts import market_r10e8_ro_internal_gap_audit as gap
from scripts.market_r10e9_evidence_review_queue import reconcile_review_queue
from tests.unit.test_market_r10e8_ro_internal_gap_audit import fixture

NOW = dt.datetime(2026, 10, 10, 22, tzinfo=dt.timezone.utc)


class TestR10E10AtomicEvidence(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="r10e10-atomic-synthetic-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "only_synthetic.db"
        fixture(self.path)

    def run_atomic(self, **kw):
        return atomic.inspect_atomic_fixture(self.path, now=NOW, **kw)

    def test_one_transaction_asserted_with_both_reports(self):
        r = self.run_atomic()
        self.assertEqual(r["state"], "ATOMIC_LOCAL_ARCHIVE_REVIEW_ONLY")
        self.assertEqual(r["snapshot_scope"], "ONE_READ_TRANSACTION")
        self.assertEqual(r["source_connection_count"], 1)
        self.assertEqual(r["archived_range_manifest"]["state"], "ARCHIVED_RANGES_READ_ONLY")
        self.assertEqual(r["gap_classification"]["state"], "INTERNAL_GAP_CANDIDATES_READ_ONLY")

    def test_e9_accepts_both_reports_and_default_does_not_assume_atomicity(self):
        r = self.run_atomic()
        separate = reconcile_review_queue(
            r["archived_range_manifest"], r["gap_classification"])
        self.assertFalse(separate["atomic_cross_report_snapshot_verified"])
        self.assertTrue(r["review"]["atomic_cross_report_snapshot_verified"])
        self.assertEqual(r["review"]["snapshot_evidence"],
                         "ONE_SQLITE_READ_TRANSACTION_AND_ROW_SCAN")
        self.assertFalse(r["review"]["independent_reports_consistent_on_aggregates_only"])

    def test_rbn_bitview_and_fred_month_review_queue(self):
        r = self.run_atomic()
        rows = r["review"]["review_queue"]
        self.assertEqual(len(rows), 3)
        self.assertEqual([x["provider"] for x in rows],
                         ["bitview", "fred", "researchbitcoin"])
        self.assertEqual([x["sample_missing_slots"] for x in rows],
                         [["2026-10-08"], ["2026-08"], ["2026-10-08"]])
        self.assertFalse(any(x["request_ready"] for x in rows))

    def test_no_false_fred_daily_or_yahoo_gap(self):
        r = self.run_atomic()
        by_provider = {s["provider"]: s for s in r["gap_classification"]["sources"]}
        self.assertTrue(all(x["gap_method"] == "EXTERNAL_PUBLICATION_CALENDAR_REQUIRED"
                            for x in by_provider["yahoo"]["series"]))
        dgs10 = next(x for x in by_provider["fred"]["series"] if x["metric"] == "DGS10")
        self.assertIsNone(dgs10["internal_gap_candidates"])
        self.assertFalse(any(x["provider"] == "yahoo" for x in r["review"]["review_queue"]))

    def test_catalog_missing_rows_remain_zero_not_fabricated(self):
        r = self.run_atomic()
        rbn = r["archived_range_manifest"]["sources"][0]
        self.assertEqual(len(rbn["series"]), 13)
        absent = next(x for x in rbn["series"] if x["metric"] == "mvrv_sth")
        self.assertEqual(absent["observations"], 0)
        self.assertEqual(absent["coverage_state"], "NO_OBSERVATIONS")

    def test_no_api_or_production_rights_even_with_local_database_tier2_flag(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE archive_sources("
                       "source_id TEXT, plan_tier INTEGER, entitlement_verified INTEGER)")
            db.execute("INSERT INTO archive_sources VALUES ('researchbitcoin',2,1)")
        r = self.run_atomic()
        self.assertTrue(r["archived_range_manifest"]["sources"][0]["entitlement_flag_db"])
        self.assertFalse(r["real_tier2_entitlement_verified"])
        self.assertFalse(r["review"]["real_tier2_entitlement_verified"])
        self.assertFalse(r["request_ready"])
        self.assertFalse(r["execution_authorized"])
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual(r["http_requests"], 0)
        self.assertEqual(r["sqlite_writes"], 0)

    def test_zero_archive_rows_results_in_no_review_work(self):
        with sqlite3.connect(self.path) as db:
            for table in ("onchain_external_observations", "daily", "macro_fred",
                          "market_ohlc_history"):
                db.execute("DELETE FROM " + table)
        r = self.run_atomic()
        self.assertEqual(r["review"]["decision"], "NO_INTERNAL_REVIEW_CANDIDATES")
        self.assertEqual(r["review"]["total_review_candidates"], 0)

    def test_missing_tables_report_not_initialized(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE daily")
            db.execute("DROP TABLE macro_fred")
        r = self.run_atomic()
        for i in (1, 2):
            self.assertEqual(r["archived_range_manifest"]["sources"][i]["schema_state"],
                             "NOT_INITIALIZED")
            self.assertEqual(r["gap_classification"]["sources"][i]["schema_state"],
                             "NOT_INITIALIZED")

    def test_partial_schema_fails_closed_without_report(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE macro_fred")
            db.execute("CREATE TABLE macro_fred(date TEXT)")
        with self.assertRaisesRegex(ValueError, "schema"):
            self.run_atomic()

    def test_malformed_stored_date_fails_closed(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO macro_fred VALUES ('CPIAUCSL','bad-date')")
        with self.assertRaises(ValueError):
            self.run_atomic()

    def test_low_total_observation_budget_blocks_all_reports(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            self.run_atomic(max_observations=1)

    def test_invalid_budget_and_boolean_block_before_sqlite_connection(self):
        for n in (0, -1, 1000001, True, False, 1.5, "5"):
            with self.subTest(value=n), self.assertRaises(ValueError):
                self.run_atomic(max_observations=n)

    def test_missing_file_and_naive_clock_rejected(self):
        with self.assertRaises(FileNotFoundError):
            atomic.inspect_atomic_fixture(self.path.parent / "missing.db", now=NOW)
        with self.assertRaises(ValueError):
            atomic.inspect_atomic_fixture(
                self.path, now=dt.datetime(2026, 10, 10, 22))
        with self.assertRaises(ValueError):
            atomic.inspect_atomic_fixture(
                self.path, now=NOW.astimezone(dt.timezone(dt.timedelta(hours=-5))))

    def test_database_bytes_unchanged_no_wal_produced_by_read(self):
        original = self.path.read_bytes()
        r = self.run_atomic()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.path.parent / "only_synthetic.db-wal").exists())
        self.assertEqual(r["db_location"], "REDACTED")
        self.assertEqual(r["archived_range_manifest"]["db_location"], "REDACTED")
        self.assertEqual(r["gap_classification"]["db_location"], "REDACTED")

    def test_only_one_connection_created_for_e7_e8_and_e9(self):
        original_connect = sqlite3.connect
        calls = []

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original_connect(*args, **kwargs)

        with patch.object(atomic.sqlite3, "connect", side_effect=spy):
            r = self.run_atomic()
        self.assertEqual(len(calls), 1)
        self.assertIn("mode=ro", calls[0][0][0])
        self.assertIs(calls[0][1]["uri"], True)
        self.assertEqual(r["source_connection_count"], 1)

    def test_concurrent_wal_writer_does_not_shift_initial_read_snapshot(self):
        # A separate writer adds the missing Bitview day AFTER E10 has
        # established the SQLite read snapshot, but BEFORE querying Bitview.
        # This demonstrates E10 does not mix before/after data.
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")

        original_classify = gap._classify
        writes = []

        def mutate_on_first_classification(*args, **kwargs):
            if not writes:
                with sqlite3.connect(self.path, timeout=2) as writer:
                    writer.execute("INSERT INTO daily VALUES (1,1791417600)")
                writes.append("committed")
            return original_classify(*args, **kwargs)

        with patch.object(gap, "_classify", side_effect=mutate_on_first_classification):
            r = self.run_atomic()
        self.assertEqual(writes, ["committed"])
        old = next(x for x in r["archived_range_manifest"]["sources"][1]["series"]
                   if x["metric"] == "mvrv")
        self.assertEqual(old["observations"], 2)
        self.assertTrue(r["review"]["atomic_cross_report_snapshot_verified"])
        later = self.run_atomic()
        new = next(x for x in later["archived_range_manifest"]["sources"][1]["series"]
                   if x["metric"] == "mvrv")
        self.assertEqual(new["observations"], 3)
        self.assertEqual(new["first_archived_utc"], "2026-10-07")

    def test_api_and_request_code_not_imported(self):
        file = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e10_atomic_ro_evidence.py"
        tree = ast.parse(file.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertNotIn("requests", imports)
        self.assertNotIn("urllib.request", imports)
        self.assertNotIn("socket", imports)
        self.assertFalse(any(isinstance(n, ast.FunctionDef) and n.name == "main"
                             for n in tree.body))
        self.assertFalse(any(isinstance(n, ast.FunctionDef) and
                             any(x.arg in ("api_key", "apply", "fetch") for x in n.args.args)
                             for n in tree.body))

    def test_manifest_and_gap_observations_from_same_row_scan(self):
        r = self.run_atomic()
        for archived, candidates in zip(
            r["archived_range_manifest"]["sources"],
            r["gap_classification"]["sources"]
        ):
            g = {x["metric"]: x for x in candidates["series"]}
            for row in archived["series"]:
                if row["observations"]:
                    self.assertEqual(row["observations"], g[row["metric"]]["observations"])
                    self.assertEqual(row["first_archived_utc"],
                                     g[row["metric"]]["first_archived_utc"])
                    self.assertEqual(row["last_archived_utc"],
                                     g[row["metric"]]["last_archived_utc"])

    def test_runner_registry_extensible_unique_and_complete(self):
        runner = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(runner.read_text(encoding="utf-8"))
        lists = [ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign)
                 and any(isinstance(x, ast.Name) and x.id == "TESTS"
                         for x in n.targets)]
        self.assertEqual(len(lists), 1)
        suites = lists[0]
        self.assertGreaterEqual(len(suites), 48)
        self.assertEqual(len({name for name, _ in suites}), len(suites))
        self.assertEqual(len({cmd for _, cmd in suites}), len(suites))
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
        ):
            self.assertEqual(sum(name in command for _, command in suites), 1)


if __name__ == "__main__":
    unittest.main()
