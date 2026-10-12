"""Package A source-only writer SHA attestation and fail-closed drift tests.

Copies only text source files into disposable fixtures; never imports
production modules, opens repository DBs, starts daily.sh or performs HTTP.
"""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest

from scripts.package_a_writer_source_attestation import (
    PROTECT_SOURCE, _read_trusted_source, snapshot_source_writer_routes,
    compare_writer_source_snapshots,
)
from scripts.market_r10e13_writer_adoption_inventory import ENTRYPOINTS
from scripts.package_a_writer_adoption_review import source_only_writer_adoption_review


class TestPackageAWriterSourceAttestation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo=Path(__file__).resolve().parents[2]
        cls.actual=snapshot_source_writer_routes(cls.repo)
        cls.review=source_only_writer_adoption_review(cls.repo)

    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="package-a-writer-source-")
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        paths=(set(PROTECT_SOURCE)|set(ENTRYPOINTS)|
               {item["source"] for item in self.review["unknown_potential_writers"]}|
               set(self.actual["daily_python_children"])|
               {"ingestion/config.py"})
        for directory in ("ingestion","scripts","storage",
                          "validation","rendering","analysis"):
            (self.root/directory).mkdir()
        for rel in paths:
            target=self.root/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((self.repo/rel).read_bytes())

    def file_change(self,path,append):
        file=self.root/path
        file.write_text(file.read_text(encoding="utf-8")+append,
                        encoding="utf-8")

    def test_real_source_manifest_is_reproducible_but_never_approves_host(self):
        snap=self.actual
        self.assertEqual(snap["state"],"STATIC_WRITER_SOURCE_SNAPSHOT_ONLY")
        self.assertEqual(snap["registered_writer_contracts"],24)
        self.assertEqual(snap["registered_static_optin_candidates"],20)
        self.assertEqual(snap["registered_without_static_optin"],4)
        self.assertRegex(snap["attestation_sha256"],r"^[0-9a-f]{64}$")
        self.assertGreater(snap["attested_file_count"],24)
        self.assertFalse(snap["production_authorized"])
        self.assertFalse(snap["runtime_cron_verified"])
        self.assertFalse(snap["all_operational_writers_coordinated"])
        self.assertFalse(snap["shared_physical_lock_inode_verified"])
        self.assertFalse(snap["database_opened"])
        self.assertFalse(snap["external_network_accessed"])
        self.assertEqual(snap["attestation_sha256"],
                         snapshot_source_writer_routes(self.repo)["attestation_sha256"])

    def test_disposable_copy_has_exactly_same_writer_source_manifest(self):
        copy=snapshot_source_writer_routes(self.root)
        self.assertTrue(compare_writer_source_snapshots(self.actual,copy)["matched"])
        self.assertEqual(copy["attestation_sha256"],self.actual["attestation_sha256"])
        self.assertEqual(copy["attested_file_count"],self.actual["attested_file_count"])
        self.assertEqual(copy["by_file"],self.actual["by_file"])
        self.assertFalse(copy["production_authorized"])

    def test_source_only_route_classifies_daily_unprotected_and_library_writers(self):
        snap=self.actual
        self.assertIn("ingestion/ingest_price.py",snap["daily_python_children"])
        self.assertIn("ingestion/ingest_fred.py",snap["daily_python_children"])
        self.assertIn("ingestion/market_context_sources.py",
                      snap["by_file"])
        self.assertGreater(len(snap["daily_unverified_children"]),0)
        self.assertTrue(all(p in snap["daily_python_children"] for p
                            in snap["daily_unverified_children"]))
        self.assertIn("scripts/daily.sh",snap["by_file"])
        self.assertEqual(snap["by_file"]["scripts/daily.sh"]["status"],
                         "PARENT_FLOCK_CANDIDATE_UNDEPLOYED")
        self.assertFalse(snap["by_file"]["scripts/daily.sh"]["production_writer_verified"])
        self.assertFalse(snap["all_writer_calls_guarded"])

    def test_read_only_candidate_not_mislabeled_as_locked_or_verified(self):
        snap=self.actual
        repo=snap["by_file"]["storage/repositories.py"]
        self.assertFalse(repo["write_signals"]["possible_writer"])
        self.assertFalse(repo["write_signals"]["read_only_certified"])
        self.assertEqual(repo["role"],"library")
        archive=snap["by_file"]["ingestion/news_archive.py"]
        self.assertIn("INSERT",archive["write_signals"]["direct_sql_mutation_verbs"])
        self.assertTrue(archive["write_signals"]["possible_writer"])
        indirect=snap["by_file"]["ingestion/market_context_sources.py"]
        self.assertIn("store_derivative",
                      indirect["write_signals"]["potential_indirect_write_calls"])
        self.assertTrue(indirect["write_signals"]["possible_writer"])
        self.assertFalse(indirect["production_writer_verified"])

    def test_identical_snapshots_match_and_dont_authorize_daily_execution(self):
        snap=snapshot_source_writer_routes(self.root)
        compared=compare_writer_source_snapshots(snap,
                                                   snapshot_source_writer_routes(self.root))
        self.assertEqual(compared["state"],"OFFLINE_SOURCE_SNAPSHOT_MATCH")
        self.assertTrue(compared["matched"])
        self.assertEqual(compared["changed_files"],[])
        self.assertEqual(compared["added_files"],[])
        self.assertEqual(compared["removed_files"],[])
        self.assertFalse(compared["host_writer_exclusion_verified"])
        self.assertFalse(compared["permission_to_execute_daily"])

    def test_known_writer_source_mutation_invalidates_sha_attestation(self):
        original=snapshot_source_writer_routes(self.root)
        self.file_change("ingestion/ingest_price.py",
                         "\n# reviewer changes guard wiring here\n")
        altered=snapshot_source_writer_routes(self.root)
        outcome=compare_writer_source_snapshots(original,altered)
        self.assertEqual(outcome["state"],"BLOCKED_OFFLINE_WRITER_SOURCE_DRIFT")
        self.assertEqual(outcome["changed_files"],["ingestion/ingest_price.py"])
        self.assertNotEqual(outcome["original_attestation_sha256"],
                            outcome["current_attestation_sha256"])
        self.assertFalse(outcome["production_authorized"])

    def test_daily_shell_flag_or_runner_change_requires_new_offline_review(self):
        original=snapshot_source_writer_routes(self.root)
        self.file_change("scripts/daily.sh","\n# hypothetical scheduling edit\n")
        result=compare_writer_source_snapshots(
            original,snapshot_source_writer_routes(self.root))
        self.assertEqual(result["changed_files"],["scripts/daily.sh"])
        self.assertFalse(result["matched"])

    def test_global_lock_implementation_drift_cannot_keep_source_stamp(self):
        original=snapshot_source_writer_routes(self.root)
        self.file_change("scripts/market_r10e11_writer_exclusion.py",
                         "\n# hypothetical inode policy change\n")
        after=snapshot_source_writer_routes(self.root)
        result=compare_writer_source_snapshots(original,after)
        self.assertEqual(result["changed_files"],
                         ["scripts/market_r10e11_writer_exclusion.py"])
        self.assertFalse(result["matched"])

    def test_new_unknown_shell_writer_is_indexed_not_silently_certified(self):
        before=snapshot_source_writer_routes(self.root)
        p=self.root/"scripts"/"unreviewed_daily_writer.py"
        p.write_text("import sqlite3\n"
                     "def run(db):\n"
                     "    db.execute('INSERT INTO safety_probe VALUES (1)')\n")
        self.file_change("scripts/daily.sh",
                         "\npython3 scripts/unreviewed_daily_writer.py\n")
        after=snapshot_source_writer_routes(self.root)
        result=compare_writer_source_snapshots(before,after)
        self.assertFalse(result["matched"])
        self.assertIn("scripts/unreviewed_daily_writer.py",result["added_files"])
        self.assertIn("scripts/daily.sh",result["changed_files"])
        self.assertIn("scripts/unreviewed_daily_writer.py",
                      after["unclassified_writer_files"])
        self.assertFalse(after["all_operational_writers_coordinated"])

    def test_deleted_writer_source_fails_closed_not_missing_file_silent(self):
        (self.root/"ingestion/ingest.py").unlink()
        with self.assertRaises(ValueError):
            snapshot_source_writer_routes(self.root)

    def test_symlink_writer_path_fails_closed_even_inside_checkout(self):
        x=self.root/"ingestion/ingest.py"
        x.unlink()
        x.symlink_to(self.root/"ingestion/ingest_price.py")
        with self.assertRaises(ValueError):
            snapshot_source_writer_routes(self.root)

    def test_dynamic_shell_eval_is_flagged_with_no_claim_of_completion(self):
        original=snapshot_source_writer_routes(self.root)
        self.file_change("scripts/daily.sh","\neval echo 'unsafe dynamic shell'\n")
        changed=snapshot_source_writer_routes(self.root)
        self.assertGreater(len(changed["dynamic_shell_execution_candidates"]),
                           len(original["dynamic_shell_execution_candidates"]))
        self.assertEqual(
            compare_writer_source_snapshots(original,changed)["state"],
            "BLOCKED_OFFLINE_WRITER_SOURCE_DRIFT")
        self.assertFalse(changed["production_authorized"])

    def test_source_snapshot_never_initializes_sqlite_or_calls_providers(self):
        pre={p.relative_to(self.root).as_posix() for p in self.root.rglob("*")}
        snapshot=snapshot_source_writer_routes(self.root)
        post={p.relative_to(self.root).as_posix() for p in self.root.rglob("*")}
        self.assertEqual(pre,post)
        self.assertEqual(snapshot["database_opened"],False)
        self.assertEqual(snapshot["external_network_accessed"],False)
        self.assertFalse(list(self.root.rglob("*.db")))
        self.assertFalse(list(self.root.rglob("*.lock")))

    def test_invalid_comparison_object_and_source_path_are_denied(self):
        for a,b in ((None,self.actual),(self.actual,None),({},self.actual)):
            with self.assertRaisesRegex(ValueError,"BLOCKED_WRITER_SNAPSHOT_INVALID"):
                compare_writer_source_snapshots(a,b)
        for path in ("../etc/passwd","/etc/passwd",""):
            with self.assertRaisesRegex(ValueError,"BLOCKED_WRITER_SOURCE_PATH"):
                _read_trusted_source(self.root,path)


if __name__=="__main__":
    unittest.main()
