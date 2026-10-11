"""Package A: opt-in global writer locking for three existing entrypoints.

Every callable performing ingestion is patched out. Synthetic OS flock on
TemporaryDirectory ONLY: no external network or productive SQLite.
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

from ingestion import ingest as bitview
from ingestion import ingest_all as bulk
from ingestion import ingest_fred as fred
from scripts import market_context_ingest as context
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy

GLOBAL = "btc-research-global-writer-v1"
CHILD = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1],namespace=sys.argv[2]):
    print('HELD',flush=True)
    sys.stdin.readline()
"""


class TestPackageAEntrypointLocks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="package-a-writer-lock-")
        self.addCleanup(tmp.cleanup)
        self.lockdir = Path(tmp.name) / "locks"
        self.lockdir.mkdir()
        self.repo = Path(__file__).resolve().parents[2]

    def call(self, provider, *, locked=True):
        flag = ["--global-lock-dir", str(self.lockdir)] if locked else []
        if provider == "bitview":
            with patch.object(bitview, "_run_ingest_args", return_value=11) as runner:
                output = bitview.main(flag)
        elif provider == "fred":
            with patch.object(fred, "FRED_API_KEY", "fixture-only"), \
                 patch.object(fred, "_run_fred_with_args", return_value=12) as runner:
                output = fred.main(flag)
        elif provider == "context":
            with patch.object(context, "_run_market_context_args", return_value=13) as runner:
                output = context.main(["--apply", *flag])
        else:
            raise AssertionError("Unknown test provider")
        return output, runner

    def _child(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.repo) + os.pathsep + env.get("PYTHONPATH", "")
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(self.lockdir), GLOBAL],
            cwd=str(self.repo), env=env, text=True,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        with selectors.DefaultSelector() as sel:
            sel.register(child.stdout,selectors.EVENT_READ)
            self.assertTrue(sel.select(timeout=5),"Synthetic lock child did not start")
        self.assertEqual(child.stdout.readline().strip(),"HELD")
        return child

    def test_all_three_free_optin_paths_invoke_existing_workers(self):
        for source, expected in (("bitview",11),("fred",12),("context",13)):
            with self.subTest(source=source):
                rc, runner = self.call(source)
                self.assertEqual(rc,expected)
                runner.assert_called_once()

    def test_all_three_unlocked_defaults_preserve_previous_delegates(self):
        for source, expected in (("bitview",11),("fred",12),("context",13)):
            with self.subTest(source=source):
                with patch("scripts.market_r10e11_writer_exclusion.fcntl.flock",
                           side_effect=AssertionError("Default must not acquire lock")):
                    rc,runner=self.call(source,locked=False)
                self.assertEqual(rc,expected)
                runner.assert_called_once()

    def test_one_namespace_global_for_all_real_entrypoints(self):
        self.assertEqual(bitview.GLOBAL_LOCK_NAMESPACE,GLOBAL)
        self.assertEqual(fred.GLOBAL_LOCK_NAMESPACE,GLOBAL)
        self.assertEqual(context.GLOBAL_LOCK_NAMESPACE,GLOBAL)
        self.assertEqual((self.repo / "ingestion" / "ingest_price.py")
                         .read_text().count('GLOBAL_LOCK_NAMESPACE = "'+GLOBAL+'"'),1)

    def test_busy_lock_refuses_bitview_before_getdb_or_api(self):
        with hold_writer_lock(self.lockdir,namespace=GLOBAL):
            with patch.object(bitview,"get_db",
                              side_effect=AssertionError("Bitview must not touch SQLite")):
                rc,runner=self.call("bitview")
        self.assertEqual(rc,3)
        runner.assert_not_called()

    def test_busy_lock_refuses_fred_before_ingest_or_sqlite(self):
        with hold_writer_lock(self.lockdir,namespace=GLOBAL):
            with patch.object(fred.sqlite3,"connect",
                              side_effect=AssertionError("FRED must not open SQLite")):
                rc,runner=self.call("fred")
        self.assertEqual(rc,3)
        runner.assert_not_called()

    def test_busy_lock_refuses_context_before_workers_or_sqlite(self):
        with hold_writer_lock(self.lockdir,namespace=GLOBAL):
            with patch.object(context.sqlite3,"connect",
                              side_effect=AssertionError("Market context must not open SQLite")):
                rc,runner=self.call("context")
        self.assertEqual(rc,3)
        runner.assert_not_called()

    def test_other_process_holds_lock_all_three_return_rc3(self):
        child=self._child()
        with contextlib.redirect_stderr(StringIO()) as err:
            for source in ("bitview","fred","context"):
                rc,runner=self.call(source)
                self.assertEqual(rc,3)
                runner.assert_not_called()
        self.assertEqual(err.getvalue().count("BLOCKED_WRITER_BUSY"),3)
        self.assertIsNone(child.poll())

    def test_child_releases_then_each_writer_may_start(self):
        child=self._child()
        rc,_=self.call("fred")
        self.assertEqual(rc,3)
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),0)
        for source in ("bitview","fred","context"):
            rc,runner=self.call(source)
            self.assertIn(rc,(11,12,13))
            runner.assert_called_once()

    def test_missing_lock_directory_blocks_before_any_worker(self):
        absent=self.lockdir.parent/"absent"
        for source in ("bitview","fred","context"):
            with self.subTest(source=source),self.assertRaises(ValueError):
                flag=["--global-lock-dir",str(absent)]
                if source=="bitview":
                    with patch.object(bitview,"_run_ingest_args",side_effect=AssertionError):
                        bitview.main(flag)
                elif source=="fred":
                    with patch.object(fred,"FRED_API_KEY","fixture"), \
                         patch.object(fred,"_run_fred_with_args",side_effect=AssertionError):
                        fred.main(flag)
                else:
                    with patch.object(context,"_run_market_context_args",side_effect=AssertionError):
                        context.main(["--apply",*flag])
        self.assertFalse(absent.exists())

    def test_fred_plan_history_without_apply_does_not_start_any_worker(self):
        with patch.object(fred,"_run_fred_with_args",
                          side_effect=AssertionError("must remain PLAN")):
            with contextlib.redirect_stdout(StringIO()) as output:
                rc=fred.main(["--history","--global-lock-dir",str(self.lockdir)])
        self.assertEqual(rc,0)
        self.assertIn("PLAN:",output.getvalue())
        self.assertFalse((self.lockdir/(GLOBAL+".lock")).exists())

    def test_market_context_default_plan_has_no_apply(self):
        with patch.object(context,"_run_market_context_args",
                          side_effect=AssertionError("not applied")):
            with contextlib.redirect_stdout(StringIO()) as output:
                self.assertEqual(context.main(["--global-lock-dir",str(self.lockdir)]),0)
        self.assertIn("PLAN only",output.getvalue())
        self.assertFalse((self.lockdir/(GLOBAL+".lock")).exists())

    def test_legacy_daily_shell_unchanged_unsafe_not_yet_guarded(self):
        shell=(self.repo/"scripts"/"daily.sh").read_text(encoding="utf-8")
        for path in ("ingestion/ingest_price.py","ingestion/ingest.py",
                     "ingestion/ingest_fred.py","scripts/market_context_ingest.py"):
            self.assertIn(path,shell)
        self.assertNotIn("--global-lock-dir",shell)

    def test_no_default_prod_directories_created_by_lock_tests(self):
        self.call("bitview")
        self.call("fred")
        self.call("context")
        self.assertTrue((self.lockdir/(GLOBAL+".lock")).exists())
        self.assertFalse((self.repo/"db"/"package_a_synthetic.db").exists())

    def test_parent_lock_retained_during_each_delegate(self):
        for source in ("bitview","fred","context"):
            with self.subTest(source=source):
                def guard(*args,**kwargs):
                    with self.assertRaises(WriterLockBusy):
                        with hold_writer_lock(self.lockdir,namespace=GLOBAL):
                            pass
                    return 11
                if source=="bitview":
                    with patch.object(bitview,"_run_ingest_args",side_effect=guard):
                        self.assertEqual(bitview.main(["--global-lock-dir",str(self.lockdir)]),11)
                elif source=="fred":
                    with patch.object(fred,"FRED_API_KEY","fixture"), \
                         patch.object(fred,"_run_fred_with_args",side_effect=guard):
                        self.assertEqual(fred.main(["--global-lock-dir",str(self.lockdir)]),11)
                else:
                    with patch.object(context,"_run_market_context_args",side_effect=guard):
                        self.assertEqual(context.main(["--apply","--global-lock-dir",str(self.lockdir)]),11)

    def test_entrypoints_are_not_auto_executed_when_imported(self):
        for module in ("ingestion/ingest.py","ingestion/ingest_fred.py",
                       "scripts/market_context_ingest.py"):
            tree=ast.parse((self.repo/module).read_text(encoding="utf-8"))
            names=[item.name for item in tree.body if isinstance(item,ast.FunctionDef)]
            self.assertIn("main",names)
            self.assertTrue(any(isinstance(item,ast.If)
                                and isinstance(item.test,ast.Compare)
                                for item in tree.body))

    def test_bitview_bulk_optin_executes_only_inside_global_lock(self):
        def check_locked(*args, **kwargs):
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.lockdir, namespace=GLOBAL):
                    pass
            return 27
        with patch.object(bulk, "_run_bulk_args", side_effect=check_locked) as worker:
            rc=bulk.main(["--global-lock-dir", str(self.lockdir), "--limit", "5"])
        self.assertEqual(rc,27)
        worker.assert_called_once()

    def test_bitview_bulk_busy_lock_never_starts_db_or_api_workers(self):
        with hold_writer_lock(self.lockdir, namespace=GLOBAL):
            with patch.object(bulk, "_run_bulk_args",
                              side_effect=AssertionError("Bulk must not start")) as worker:
                with contextlib.redirect_stderr(StringIO()) as errors:
                    rc=bulk.main(["--global-lock-dir", str(self.lockdir)])
        self.assertEqual(rc,3)
        worker.assert_not_called()
        self.assertIn("BLOCKED_WRITER_BUSY",errors.getvalue())

    def test_bitview_bulk_busy_other_process_blocks_before_worker(self):
        child=self._child()
        with patch.object(bulk, "_run_bulk_args",
                          side_effect=AssertionError("Bulk must not start")) as worker:
            with contextlib.redirect_stderr(StringIO()):
                rc=bulk.main(["--global-lock-dir", str(self.lockdir)])
        self.assertEqual(rc,3)
        worker.assert_not_called()
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),0)
        with patch.object(bulk, "_run_bulk_args", return_value=0) as worker:
            self.assertEqual(bulk.main(["--global-lock-dir",str(self.lockdir)]),0)
        worker.assert_called_once()

    def test_bitview_bulk_legacy_default_stays_explicitly_unprotected(self):
        with hold_writer_lock(self.lockdir, namespace=GLOBAL):
            with patch.object(bulk, "_run_bulk_args",return_value=19) as worker:
                self.assertEqual(bulk.main([]),19)
            worker.assert_called_once()

    def test_bitview_bulk_disallowed_legacy_backfill_and_dry_run_remain_blocked(self):
        for command in ("--backfill","--dry-run"):
            with self.subTest(command=command):
                with patch.object(bulk, "_run_bulk_args",
                                  side_effect=AssertionError("not authorized")) as worker:
                    with contextlib.redirect_stderr(StringIO()):
                        with self.assertRaises(SystemExit) as error:
                            bulk.main([command,"--global-lock-dir",str(self.lockdir)])
                self.assertEqual(error.exception.code,2)
                worker.assert_not_called()

    def test_bitview_bulk_lock_dir_missing_fails_before_any_worker(self):
        from scripts.market_r10e11_writer_exclusion import WriterLockUnsafe
        missing=self.lockdir.parent/"not-created"
        with patch.object(bulk,"_run_bulk_args",
                          side_effect=AssertionError("worker must not start")) as worker:
            with self.assertRaises(WriterLockUnsafe):
                bulk.main(["--global-lock-dir",str(missing)])
        worker.assert_not_called()
        self.assertFalse(missing.exists())

    def test_bulk_import_never_triggers_network_or_db_and_daily_is_unchanged(self):
        source=(self.repo/"ingestion"/"ingest_all.py").read_text(encoding="utf-8")
        self.assertIn("run_with_optional_writer_lock(",source)
        self.assertIn("--global-lock-dir",source)
        self.assertNotIn("ingestion/ingest_all.py", 
                         (self.repo/"scripts"/"daily.sh").read_text(encoding="utf-8"))

    def test_runner_registry_new_integrated_writer_suite_unique(self):
        tree=ast.parse((self.repo/"tests"/"run_all.py").read_text(encoding="utf-8"))
        found=[ast.literal_eval(n.value) for n in tree.body
               if isinstance(n,ast.Assign)
               and any(isinstance(v,ast.Name) and v.id=="TESTS" for v in n.targets)]
        self.assertEqual(len(found),1)
        entries=found[0]
        self.assertGreaterEqual(len(entries),54)
        self.assertEqual(len({a for a,b in entries}),len(entries))
        self.assertEqual(len({b for a,b in entries}),len(entries))
        self.assertEqual(sum("test_package_a_entrypoint_locks" in cmd for _,cmd in entries),1)


if __name__=="__main__":
    unittest.main()
