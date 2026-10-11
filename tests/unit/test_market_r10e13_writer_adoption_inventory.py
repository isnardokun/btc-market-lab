"""R10-E.13: static inventory of writer adopters, fail closed, no production IO."""
import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import market_r10e13_writer_adoption_inventory as inventory


class TestR10E13WriterAdoptionInventory(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="r10e13-source-only-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name) / "source"
        self.root.mkdir()
        for folder in inventory.SCAN_DIRS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "daily.sh").write_text(
            "#!/bin/sh\npython3 ingestion/ingest.py\n"
            "python3 ingestion/ingest_fred.py\n"
            "python3 rendering/charts.py\n", encoding="utf-8")
        (self.root / "ingestion" / "config.py").write_text(
            "DB_PATH = '/not/accessed/by/test.db'\n", encoding="utf-8")
        for relative in inventory.ENTRYPOINTS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def existing():\n    return None\n", encoding="utf-8")

    def scan(self):
        return inventory.scan_writer_adoption_source_tree(self.root)

    def test_all_known_sqlite_writer_entrypoints_are_explicit(self):
        self.assertGreaterEqual(len(inventory.ENTRYPOINTS), 24)
        result = self.scan()
        self.assertEqual(result["inventoried_writer_contracts"], len(inventory.ENTRYPOINTS))
        self.assertEqual(set(result["blocked_writer_paths"]), set(inventory.ENTRYPOINTS))

    def test_daily_shell_invocations_identified(self):
        result = self.scan()
        found = {x["source"]: x for x in result["registered_writer_paths"]}
        self.assertTrue(found["ingestion/ingest.py"]["invoked_by_daily_shell"])
        self.assertTrue(found["ingestion/ingest_fred.py"]["invoked_by_daily_shell"])
        self.assertFalse(found["ingestion/bitview_history.py"]["invoked_by_daily_shell"])

    def test_unclassified_daily_script_is_not_silently_ignored(self):
        report = self.scan()
        self.assertIn("rendering/charts.py", report["daily_shell_script_references"])
        self.assertTrue(any(r["source"] == "rendering/charts.py"
                            for r in report["unknown_writer_candidates"]))

    def test_unknown_sqlite_connector_is_discovered(self):
        path = self.root / "ingestion" / "new_writer.py"
        path.write_text("import sqlite3\ndef foo(path):\n"
                        "    return sqlite3.connect(path)\n", encoding="utf-8")
        sources = {r["source"] for r in self.scan()["unknown_writer_candidates"]}
        self.assertIn("ingestion/new_writer.py", sources)

    def test_unknown_write_sql_literal_is_discovered(self):
        path = self.root / "analysis" / "new_writer.py"
        path.write_text("SQL = 'INSERT INTO forecast VALUES(1)'\n", encoding="utf-8")
        self.assertIn("analysis/new_writer.py",
                      {r["source"] for r in self.scan()["unknown_writer_candidates"]})

    def test_sql_read_only_no_mutation_does_not_become_writer(self):
        path = self.root / "analysis" / "reader.py"
        path.write_text("SQL = 'SELECT 1'\n", encoding="utf-8")
        self.assertFalse(any(r["source"] == "analysis/reader.py"
                             for r in self.scan()["unknown_writer_candidates"]))

    def test_lock_documentation_string_does_not_count_as_adoption(self):
        path = self.root / "ingestion" / "ingest.py"
        path.write_text('"""hold_writer_lock namespace btc-research-global-writer-v1"""\n'
                        "def ingest():\n    pass\n", encoding="utf-8")
        found = {r["source"]: r for r in self.scan()["registered_writer_paths"]}
        self.assertEqual(found["ingestion/ingest.py"]["lock_status"],
                         "GLOBAL_LOCK_ADOPTION_MISSING")

    def test_wrong_namespace_does_not_count_as_global_lock(self):
        p = self.root / "ingestion" / "ingest.py"
        p.write_text(
            "from scripts.market_r10e11_writer_exclusion import hold_writer_lock\n"
            "def write(directory):\n"
            "    with hold_writer_lock(directory,namespace='wrong-namespace'):\n"
            "        pass\n", encoding="utf-8")
        source = {x["source"]: x for x in self.scan()["registered_writer_paths"]}
        self.assertEqual(source["ingestion/ingest.py"]["lock_status"],
                         "GLOBAL_LOCK_ADOPTION_MISSING")

    def test_literal_global_namespace_is_only_static_reference_unverified(self):
        p = self.root / "ingestion" / "ingest.py"
        p.write_text(
            "from scripts.market_r10e11_writer_exclusion import hold_writer_lock\n"
            "def write(directory):\n"
            "    with hold_writer_lock(directory,namespace='btc-research-global-writer-v1'):\n"
            "        pass\n", encoding="utf-8")
        row = next(x for x in self.scan()["registered_writer_paths"]
                   if x["source"] == "ingestion/ingest.py")
        self.assertEqual(row["lock_status"], "STATIC_GLOBAL_LOCK_REFERENCE_UNVERIFIED")
        self.assertFalse(row["runtime_writer_exclusion_verified"])

    def test_no_partial_adoption_can_authorize_deployment(self):
        r = self.scan()
        self.assertEqual(r["decision"], "BLOCKED_UNVERIFIED_GLOBAL_WRITER_ADOPTION")
        self.assertFalse(r["all_runtime_writer_exclusion_verified"])
        self.assertFalse(r["operational_writer_discovery_complete"])
        self.assertFalse(r["deployment_authorized"])
        self.assertFalse(r["real_api_authorized"])

    def test_missing_registered_path_fails_closed(self):
        (self.root / "ingestion" / "ingest.py").unlink()
        with self.assertRaisesRegex(ValueError, "Missing/linked"):
            self.scan()

    def test_missing_scope_directory_fails_closed(self):
        (self.root / "analysis").rmdir()
        with self.assertRaisesRegex(ValueError, "Missing scan directory"):
            self.scan()

    def test_linked_known_entrypoint_is_rejected(self):
        p = self.root / "ingestion" / "ingest.py"
        p.unlink()
        p.symlink_to(self.root / "ingestion" / "config.py")
        with self.assertRaisesRegex(ValueError, "Missing/linked"):
            self.scan()

    def test_malformed_python_source_fails_closed(self):
        p = self.root / "ingestion" / "new_writer.py"
        p.write_text("def broken(:\n", encoding="utf-8")
        with self.assertRaises(SyntaxError):
            self.scan()

    def test_report_contains_no_api_or_sqlite_operations(self):
        r = self.scan()
        self.assertFalse(r["sqlite_database_opened"])
        self.assertEqual(r["http_requests"], 0)
        self.assertEqual(r["sqlite_writes"], 0)
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")

    def test_source_tree_scan_is_observably_read_only(self):
        snapshot = {x.relative_to(self.root).as_posix(): x.read_bytes()
                    for x in self.root.rglob("*") if x.is_file()}
        self.scan()
        later = {x.relative_to(self.root).as_posix(): x.read_bytes()
                 for x in self.root.rglob("*") if x.is_file()}
        self.assertEqual(later, snapshot)

    def test_repo_current_source_can_be_inspected_without_db(self):
        root = Path(__file__).resolve().parents[2]
        report = inventory.scan_writer_adoption_source_tree(root)
        self.assertEqual(report["inventoried_writer_contracts"],
                         len(inventory.ENTRYPOINTS))
        self.assertFalse(report["deployment_authorized"])
        self.assertEqual(report["http_requests"], 0)
        self.assertEqual(report["sqlite_writes"], 0)
        discovered = {x["source"] for x in report["registered_writer_paths"]}
        self.assertIn("ingestion/ingest_price.py", discovered)
        self.assertIn("scripts/market_history_forward.py", discovered)

    def test_registry_unique_and_e13_present(self):
        root = Path(__file__).resolve().parents[2]
        tree = ast.parse((root / "tests" / "run_all.py").read_text(encoding="utf-8"))
        lists = [ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign)
                 and any(isinstance(k, ast.Name) and k.id == "TESTS" for k in n.targets)]
        self.assertEqual(len(lists), 1)
        specs = lists[0]
        self.assertGreaterEqual(len(specs), 51)
        self.assertEqual(len({x for x,_ in specs}), len(specs))
        self.assertEqual(len({x for _,x in specs}), len(specs))
        for unit in ("test_market_r10e_plan_only", "test_market_r10e_dispatch_offline",
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
                     "test_market_r10e13_writer_adoption_inventory"):
            self.assertEqual(sum(unit in command for _,command in specs), 1)


if __name__ == "__main__":
    unittest.main()
