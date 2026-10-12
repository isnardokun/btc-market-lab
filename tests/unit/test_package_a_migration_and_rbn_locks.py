"""Package A: RBN v2 + both migration CLIs share explicit advisory lock.

All callable mutators are mocked; no production DB, backup, network or
scheduler is invoked. Contention uses only TempDirectory and a local child.
"""
import ast
import contextlib
from io import StringIO
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ingestion import researchbitcoin_v2 as rbn
from scripts import archive_db_migrate as archive
from scripts import market_context_migrate as market
from scripts.market_r10e11_writer_exclusion import (
    hold_writer_lock, WriterLockBusy, WriterLockUnsafe,
)
from scripts.package_a_writer_guard import GLOBAL_WRITER_NAMESPACE as NAMESPACE

HOLDER = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1],namespace=sys.argv[2]):
    print("HELD",flush=True)
    sys.stdin.readline()
"""


class PackageAMigrationAndRbnLockTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="package-a-source-lock-")
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.locks=self.root/"global-lock"
        self.locks.mkdir()
        self.repo=Path(__file__).resolve().parents[2]

    def callers(self):
        return (
            ("rbn-init",rbn,"_run_rbn_v2_args",["--init-db"]),
            ("rbn-sync",rbn,"_run_rbn_v2_args",["--sync","sopr_sth"]),
            ("archive-migrate",archive,"run",["--apply"]),
            ("market-migrate",market,"_apply_market_schema_args",["--apply"]),
        )

    def call(self,case,*,optin=True,handler=None):
        label,mod,delegate,argv=case
        params=[*argv,*((["--global-lock-dir",str(self.locks)]) if optin else [])]
        if label=="archive-migrate":
            # archive.run is permitted to be invoked with explicit fixture
            # arguments but is always mocked, so it never performs a backup.
            output={"mode":"FAKE_APPLY"}
        else:
            output=71
        with patch.object(mod,delegate,side_effect=handler or (lambda *a,**kw:output)) as worker:
            with contextlib.redirect_stdout(StringIO()),contextlib.redirect_stderr(StringIO()) as err:
                ret=mod.main(params)
            return ret,worker,err.getvalue()

    def holder(self):
        env=dict(os.environ)
        env["PYTHONPATH"]=str(self.repo)+os.pathsep+env.get("PYTHONPATH","")
        proc=subprocess.Popen(
            [sys.executable,"-c",HOLDER,str(self.locks),NAMESPACE],
            cwd=str(self.repo),env=env,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        def clean():
            if proc.poll() is None:proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(clean)
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout,selectors.EVENT_READ)
            self.assertTrue(sel.select(timeout=5))
        self.assertEqual(proc.stdout.readline().strip(),"HELD")
        return proc

    def test_all_four_mutations_guard_before_delegated_db_or_http(self):
        def check(*a,**kw):
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.locks,namespace=NAMESPACE):
                    pass
            return 71
        for case in self.callers():
            with self.subTest(case=case[0]):
                ret,delegate,_=self.call(case,handler=check)
                self.assertEqual(ret,0 if case[0]=="archive-migrate" else 71)
                delegate.assert_called_once()

    def test_all_four_mutex_busy_fail_rc3_before_any_mutator(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            for case in self.callers():
                with self.subTest(case=case[0]):
                    rc,delegate,err=self.call(
                        case,handler=lambda *a,**kw:self.fail("mutator ran"))
                    self.assertEqual(rc,3)
                    delegate.assert_not_called()
                    self.assertIn("BLOCKED_WRITER_BUSY",err)

    def test_child_process_blocks_all_four_unrelated_sqlite_writers(self):
        child=self.holder()
        for case in self.callers():
            rc,worker,_=self.call(case)
            self.assertEqual(rc,3)
            worker.assert_not_called()
        self.assertIsNone(child.poll())

    def test_child_release_allows_multiple_mutator_bodies(self):
        child=self.holder()
        self.assertEqual(self.call(self.callers()[0])[0],3)
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),0)
        for case in self.callers():
            self.assertIn(self.call(case)[0],(0,71))

    def test_child_crash_releases_flock_without_unlink(self):
        child=self.holder()
        child.kill()
        child.wait(timeout=5)
        self.assertEqual(self.call(self.callers()[0])[0],71)
        self.assertTrue((self.locks/(NAMESPACE+".lock")).is_file())

    def test_legacy_unflagged_mutator_commands_preserve_old_behavior(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            for case in self.callers():
                ret,delegate,_=self.call(case,optin=False)
                self.assertIn(ret,(0,71))
                delegate.assert_called_once()

    def test_bad_optin_directory_blocks_before_work(self):
        invalid=self.root/"nonexistent"
        for case in self.callers():
            label,mod,delegate,argv=case
            with self.subTest(case=label):
                with patch.object(mod,delegate,side_effect=AssertionError("ran")):
                    with contextlib.redirect_stderr(StringIO()):
                        if label=="archive-migrate":
                            # Legacy migration catches ValueError and returns
                            # RC2, still without opening SQLite or backup.
                            self.assertEqual(
                                mod.main([*argv,"--global-lock-dir",str(invalid)]),2)
                        else:
                            with self.assertRaises(WriterLockUnsafe):
                                mod.main([*argv,"--global-lock-dir",str(invalid)])
        self.assertFalse(invalid.exists())

    def test_market_schema_plan_has_no_lock_and_no_backup(self):
        with patch.object(market,"_apply_market_schema_args",side_effect=AssertionError("called")):
            with patch("scripts.package_a_writer_guard.hold_writer_lock",
                       side_effect=AssertionError("lock acquired")):
                with contextlib.redirect_stdout(StringIO()) as out:
                    self.assertEqual(market.main(["--global-lock-dir",str(self.locks)]),0)
        self.assertIn("PLAN:",out.getvalue())

    def test_archive_schema_plan_remains_ro_and_does_not_lock(self):
        fake={"mode":"PLAN","existing":{}}
        with patch.object(archive,"run",return_value=fake) as worker:
            with patch("scripts.package_a_writer_guard.hold_writer_lock",
                       side_effect=AssertionError("unexpected lock")):
                with contextlib.redirect_stdout(StringIO()):
                    self.assertEqual(archive.main(["--global-lock-dir",str(self.locks)]),0)
        worker.assert_called_once()
        self.assertFalse(worker.call_args.kwargs["apply"])

    def test_rbn_catalog_uses_no_sqlite_network_or_lock(self):
        with patch.object(rbn,"_run_rbn_v2_args",return_value=0) as worker:
            with patch("scripts.package_a_writer_guard.hold_writer_lock",
                       side_effect=AssertionError("unexpected lock")):
                self.assertEqual(rbn.main(["--catalog","--global-lock-dir",str(self.locks)]),0)
        worker.assert_called_once()

    def test_rbn_missing_database_uses_argparse_error_before_mutation(self):
        with patch.object(rbn, "initialize_database", side_effect=AssertionError("DB opened")):
            with contextlib.redirect_stderr(StringIO()) as stderr:
                with self.assertRaises(SystemExit) as exit_result:
                    rbn.main(["--init-db", "--db", str(self.root / "missing.db")])
        self.assertEqual(exit_result.exception.code, 2)
        self.assertIn("La base local no existe", stderr.getvalue())

    def test_rbn_fetch_rejection_uses_controlled_error_without_nameerror(self):
        with patch.object(rbn, "fetch", side_effect=ValueError("synthetic rejected request")):
            with contextlib.redirect_stderr(StringIO()) as stderr:
                with self.assertRaises(SystemExit) as exit_result:
                    rbn.main(["--sample", "sopr_sth"])
        self.assertEqual(exit_result.exception.code, 1)
        self.assertIn("RECHAZADO: synthetic rejected request", stderr.getvalue())

    def test_rbn_inventory_and_api_sample_are_non_mutating_paths(self):
        for argv in (["--inventory"],["--sample","sopr_sth"]):
            with self.subTest(argv=argv):
                with patch.object(rbn,"_run_rbn_v2_args",return_value=0):
                    with patch("scripts.package_a_writer_guard.hold_writer_lock",
                               side_effect=AssertionError("lock without write")):
                        self.assertEqual(rbn.main(argv),0)

    def test_unprotected_library_entrypoints_are_not_counted_as_operationally_safe(self):
        from scripts.package_a_writer_adoption_review import source_only_writer_adoption_review
        report=source_only_writer_adoption_review(self.repo)
        self.assertFalse(report["all_operational_writers_coordinated"])
        for path in ("ingestion/news_archive.py","storage/archive_schema.py",
                     "storage/repositories.py"):
            self.assertNotIn(path,report["optin_candidate_paths"])
        self.assertFalse(report["daily_shell_optin_flag_present"])

    def test_imports_and_cli_no_automatic_apply_on_module_load(self):
        for rel in ("ingestion/researchbitcoin_v2.py","scripts/archive_db_migrate.py",
                    "scripts/market_context_migrate.py"):
            tree=ast.parse((self.repo/rel).read_text(encoding="utf-8"))
            self.assertTrue(any(isinstance(n,ast.FunctionDef) and n.name=="main"
                                for n in tree.body))
            self.assertTrue(any(isinstance(n,ast.If) for n in tree.body))

    def test_runner_registered_source_lock_suite_once(self):
        tree=ast.parse((self.repo/"tests"/"run_all.py").read_text(encoding="utf-8"))
        rows=[ast.literal_eval(x.value) for x in tree.body if isinstance(x,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=="TESTS" for t in x.targets)]
        self.assertEqual(len(rows),1)
        self.assertEqual(sum("test_package_a_migration_and_rbn_locks" in cmd
                             for _,cmd in rows[0]),1)
        self.assertGreaterEqual(len(rows[0]),60)


if __name__=="__main__":
    unittest.main()
