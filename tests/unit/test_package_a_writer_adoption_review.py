"""Package A fail-closed STATIC writer adoption reconciliation.

Pure AST/file reading of checked-out SOURCE. Never import a production
writer, open SQLite, HTTP, cron, scheduler or confidential user files.
"""
from pathlib import Path
import unittest

from scripts.package_a_writer_adoption_review import (
    FLAG, _ast_optin_signals, source_only_writer_adoption_review
)


class TestPackageAWriterAdoptionReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.report = source_only_writer_adoption_review(cls.root)
        cls.entries = {x["source"]: x for x in cls.report["registered_writer_paths"]}

    def test_static_inventory_never_asserts_all_writers_are_locked(self):
        data=self.report
        self.assertEqual(data["state"],"STATIC_WRITER_ADOPTION_REVIEW_ONLY")
        self.assertEqual(data["decision"],"BLOCKED_UNVERIFIED_GLOBAL_WRITER_ADOPTION")
        self.assertFalse(data["all_operational_writers_coordinated"])
        self.assertFalse(data["global_lock_file_identity_verified"])
        self.assertFalse(data["production_authorized"])
        self.assertFalse(data["production_db_opened"])
        self.assertEqual(data["live_http_requests"],0)

    def test_all_original_writer_contracts_remain_inventory_visible(self):
        data=self.report
        self.assertEqual(data["registered_writer_contracts"],24)
        self.assertEqual(len(self.entries),24)
        self.assertEqual(
            len(data["optin_candidate_paths"])+len(data["missing_or_unverified_optin_paths"]),24)

    def test_fifteen_entrypoints_expose_optin_and_a_guard_call(self):
        expected={
            "ingestion/ingest_price.py","ingestion/ingest.py","ingestion/ingest_fred.py",
            "ingestion/ingest_all.py","ingestion/bitview_history.py",
            "ingestion/yahoo_history.py","ingestion/researchbitcoin_archive.py",
            "scripts/market_context_ingest.py","scripts/market_history_batch.py",
            "scripts/market_history_forward.py","scripts/market_history_pilot.py",
            "validation/archive_metrics.py","rendering/archive_report.py",
            "rendering/export_dashboard.py","ingestion/snapshot.py",
        }
        self.assertEqual(set(self.report["optin_candidate_paths"]),expected)

    def test_missing_manual_legacy_backfill_paths_not_false_certified(self):
        for path in ("ingestion/backfill_metrics.py","ingestion/backfill_onchain_metrics.py",
                     "ingestion/researchbitcoin_v2.py"):
            self.assertIn(path,self.report["missing_or_unverified_optin_paths"])
            self.assertFalse(self.entries[path]["production_writer_protected"])

    def test_daily_shell_currently_does_not_request_any_optin_lock(self):
        data=self.report
        self.assertFalse(data["daily_shell_optin_flag_present"])
        self.assertGreater(len(data["daily_shell_unguarded_registered_paths"]),0)
        for path in data["daily_shell_unguarded_registered_paths"]:
            self.assertTrue(self.entries[path]["invoked_by_daily_shell"])

    def test_newly_guarded_daily_archives_remain_unprotected_in_host(self):
        for path in ("validation/archive_metrics.py","rendering/archive_report.py",
                     "rendering/export_dashboard.py","ingestion/snapshot.py"):
            self.assertTrue(self.entries[path]["explicit_optin_argument"])
            self.assertTrue(self.entries[path]["guard_function_called"])
            self.assertFalse(self.entries[path]["production_writer_protected"])
            self.assertIn(path,self.report["daily_shell_unguarded_registered_paths"])

    def test_guard_named_in_comments_does_not_count(self):
        parsed=_ast_optin_signals("""
# foo.add_argument("--global-lock-dir")
# run_with_optional_writer_lock(locked)
def main():
    return 1
""")
        self.assertEqual(parsed,{"explicit_optin_argument":False,"guard_function_called":False})

    def test_argument_in_docstring_does_not_count(self):
        parsed=_ast_optin_signals('def main():\n    """--global-lock-dir hold_writer_lock(foo)"""\n')
        self.assertFalse(parsed["explicit_optin_argument"])
        self.assertFalse(parsed["guard_function_called"])

    def test_optin_cli_without_actual_guard_call_not_adopted(self):
        code='''
def main():
    parser.add_argument("--global-lock-dir")
    return worker()
'''
        result=_ast_optin_signals(code)
        self.assertTrue(result["explicit_optin_argument"])
        self.assertFalse(result["guard_function_called"])

    def test_direct_lock_without_optin_cli_is_not_current_contract(self):
        code='''
def main():
    with hold_writer_lock("/tmp/unsafe"):
        pass
'''
        result=_ast_optin_signals(code)
        self.assertFalse(result["explicit_optin_argument"])
        self.assertTrue(result["guard_function_called"])

    def test_cannot_infer_upstream_entitlement_or_complete_history(self):
        data=self.report
        self.assertEqual(data["historical_completeness"],"NOT_VERIFIED")
        self.assertNotIn("ENTITLEMENT_VERIFIED",str(data))
        self.assertEqual(data["live_http_requests"],0)

    def test_unknown_writer_candidates_are_explicitly_retained(self):
        from scripts.market_r10e13_writer_adoption_inventory import scan_writer_adoption_source_tree
        old=scan_writer_adoption_source_tree(self.root)
        self.assertEqual(self.report["unknown_potential_writers"],
                         old["unknown_writer_candidates"])

    def test_runner_includes_source_only_adoption_suite_once(self):
        import ast
        tree=ast.parse((self.root/"tests"/"run_all.py").read_text(encoding="utf-8"))
        rows=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=="TESTS" for t in n.targets)]
        self.assertEqual(len(rows),1)
        self.assertEqual(sum("test_package_a_writer_adoption_review" in command
                             for name,command in rows[0]),1)
        self.assertGreaterEqual(len(rows[0]),59)


if __name__=="__main__":
    unittest.main()
