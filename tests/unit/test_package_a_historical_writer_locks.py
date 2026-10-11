"""Package A: one shared opt-in OS flock across SIX historical collectors.

All potential worker functions are patched, so these tests NEVER open any
database, issue HTTP or trigger production tasks. Uses only TemporaryDirectory
lock files and a local synthetic subprocess holder.
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

from ingestion import bitview_history, yahoo_history, researchbitcoin_archive
from scripts import market_history_forward, market_history_batch, market_history_pilot
from scripts import package_a_writer_guard as guard
from scripts.market_r10e11_writer_exclusion import (
    hold_writer_lock, WriterLockBusy, WriterLockUnsafe
)

CHILD = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1],namespace=sys.argv[2]):
    print("HELD",flush=True)
    sys.stdin.readline()
"""
CASES = (
    ("bitview_history", bitview_history, "_run_history_args",
     (), ("--apply",)),
    ("yahoo_history", yahoo_history, "_run_yahoo_history_args",
     (), ("--apply",)),
    ("researchbitcoin_archive", researchbitcoin_archive, "_run_rbn_history_args",
     ("--history",), ("--history","--apply")),
    ("market_history_forward", market_history_forward, "_run_forward_args",
     ("--provider","binance","--metric","open_interest"),
     ("--provider","binance","--metric","open_interest","--apply")),
    ("market_history_pilot", market_history_pilot, "_run_pilot_args",
     ("--provider","bybit","--metric","funding_settled"),
     ("--provider","bybit","--metric","funding_settled","--apply")),
    ("market_history_batch", market_history_batch, "_run_batch_args",
     ("--provider","binance","--metric","open_interest"),
     ("--provider","binance","--metric","open_interest","--apply")),
)


class HistoricalGlobalWriterLockTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="package-a-history-lock-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.locks=self.root/"global-locks"
        self.locks.mkdir()
        self.repo=Path(__file__).resolve().parents[2]

    def call(self, case, *, apply, lock=True, callback=None):
        name,mod,delegate,plan_args,apply_args=case
        argv=list(apply_args if apply else plan_args)
        if lock: argv.extend(("--global-lock-dir",str(self.locks)))
        with patch.object(mod,delegate,side_effect=callback or (lambda *a,**kw:17)) as worker:
            with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()) as errors:
                result=mod.main(argv)
        return result, worker, errors.getvalue()

    def child_holder(self):
        env=dict(os.environ)
        env["PYTHONPATH"]=str(self.repo)+os.pathsep+env.get("PYTHONPATH","")
        child=subprocess.Popen(
            [sys.executable,"-c",CHILD,str(self.locks),guard.GLOBAL_WRITER_NAMESPACE],
            cwd=str(self.repo),env=env,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        def cleanup():
            if child.poll() is None: child.kill()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=5),"Synthetic child lock timeout")
        self.assertEqual(child.stdout.readline().strip(),"HELD")
        return child

    def test_six_history_commands_expose_a_single_explicit_lock(self):
        self.assertEqual(len(CASES),6)
        self.assertEqual(guard.GLOBAL_WRITER_NAMESPACE,"btc-research-global-writer-v1")
        for case in CASES:
            with self.subTest(source=case[0]):
                rc,worker,_=self.call(case,apply=True)
                self.assertEqual(rc,17)
                worker.assert_called_once()
        lock=self.locks/(guard.GLOBAL_WRITER_NAMESPACE+".lock")
        self.assertTrue(lock.exists())
        self.assertEqual(lock.stat().st_mode & 0o777,0o600)

    def test_every_apply_command_fail_closed_when_another_local_fd_owns_lock(self):
        with hold_writer_lock(self.locks,namespace=guard.GLOBAL_WRITER_NAMESPACE):
            for case in CASES:
                with self.subTest(source=case[0]):
                    rc,worker,error=self.call(
                        case,apply=True,
                        callback=lambda *a,**k: self.fail("Worker must never run"))
                    self.assertEqual(rc,3)
                    worker.assert_not_called()
                    self.assertIn("BLOCKED_WRITER_BUSY",error)

    def test_all_six_plan_commands_ignore_optional_lock_without_db_or_http(self):
        with patch.object(guard,"hold_writer_lock",
                          side_effect=AssertionError("PLAN must never acquire lock")):
            for case in CASES:
                with self.subTest(source=case[0]):
                    rc,worker,_=self.call(case,apply=False)
                    self.assertEqual(rc,17)
                    worker.assert_called_once()
        self.assertFalse((self.locks/(guard.GLOBAL_WRITER_NAMESPACE+".lock")).exists())

    def test_unflagged_apply_remains_legacy_unlocked_behavior(self):
        with hold_writer_lock(self.locks,namespace=guard.GLOBAL_WRITER_NAMESPACE):
            for case in CASES:
                with self.subTest(source=case[0]):
                    rc,worker,_=self.call(case,apply=True,lock=False)
                    self.assertEqual(rc,17)
                    worker.assert_called_once()

    def test_global_lock_stays_held_for_entire_worker_callback(self):
        def assert_locked(*args,**kwargs):
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.locks,namespace=guard.GLOBAL_WRITER_NAMESPACE):
                    pass
            return 17
        for case in CASES:
            with self.subTest(source=case[0]):
                rc,worker,_=self.call(case,apply=True,callback=assert_locked)
                self.assertEqual(rc,17)
                worker.assert_called_once()

    def test_one_child_os_process_blocks_all_six_historical_apply_commands(self):
        child=self.child_holder()
        for case in CASES:
            rc,worker,_=self.call(case,apply=True)
            self.assertEqual(rc,3)
            worker.assert_not_called()
        self.assertIsNone(child.poll())

    def test_child_release_reopens_six_historical_writer_commands(self):
        child=self.child_holder()
        self.assertEqual(self.call(CASES[0],apply=True)[0],3)
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),0)
        for case in CASES:
            self.assertEqual(self.call(case,apply=True)[0],17)

    def test_child_crash_releases_shared_lock(self):
        child=self.child_holder()
        child.kill()
        child.wait(timeout=5)
        self.assertEqual(self.call(CASES[4],apply=True)[0],17)

    def test_invalid_absent_lock_directory_blocks_all_apply_operations(self):
        self.locks.rmdir()
        for case in CASES:
            with self.subTest(source=case[0]):
                with self.assertRaises(WriterLockUnsafe):
                    self.call(case,apply=True,callback=lambda *a,**kw:self.fail("worker ran"))
        self.assertFalse(self.locks.exists())

    def test_unsafe_symlink_lock_directory_blocks_before_worker(self):
        link=self.root/"unsafe-link"
        link.symlink_to(self.locks,target_is_directory=True)
        for case in CASES:
            name,mod,delegate,plan_args,apply_args=case
            with self.subTest(source=name):
                with patch.object(mod,delegate,side_effect=AssertionError("worker ran")):
                    with self.assertRaises(WriterLockUnsafe):
                        mod.main([*apply_args,"--global-lock-dir",str(link)])

    def test_an_independent_namespace_does_not_protect_the_global_writer(self):
        with hold_writer_lock(self.locks,namespace="another-physical-writer"):
            for case in CASES:
                rc,worker,_=self.call(case,apply=True)
                self.assertEqual(rc,17)
                worker.assert_called_once()

    def test_empty_string_lock_dir_is_not_implicitly_production_safe(self):
        with self.assertRaises(WriterLockUnsafe):
            guard.run_with_optional_writer_lock(
                self.root/"missing-dir",lambda:self.fail("unexpected call"),
                label="history")

    def test_worker_exception_releases_global_lock_for_other_provider(self):
        def die(*args,**kwargs):
            raise RuntimeError("simulated parser error")
        with self.assertRaisesRegex(RuntimeError,"simulated"):
            self.call(CASES[0],apply=True,callback=die)
        self.assertEqual(self.call(CASES[1],apply=True)[0],17)

    def test_source_files_do_not_modify_daily_shell_or_enable_scheduler(self):
        shell=(self.repo/"scripts"/"daily.sh").read_text(encoding="utf-8")
        self.assertNotIn("--global-lock-dir",shell)
        self.assertNotIn("package_a_writer_guard",shell)
        for name in ("ingestion/bitview_history.py","ingestion/yahoo_history.py",
                     "ingestion/researchbitcoin_archive.py","scripts/market_history_forward.py",
                     "scripts/market_history_pilot.py","scripts/market_history_batch.py"):
            source=(self.repo/name).read_text(encoding="utf-8")
            self.assertIn("--global-lock-dir",source)
            self.assertIn("run_with_optional_writer_lock",source)

    def test_no_default_db_path_or_http_client_in_lock_wrapper(self):
        p=self.repo/"scripts"/"package_a_writer_guard.py"
        tree=ast.parse(p.read_text(encoding="utf-8"))
        modules=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node,ast.ImportFrom):
                modules.add(node.module)
        for forbidden in ("sqlite3","requests","urllib","socket","subprocess"):
            self.assertNotIn(forbidden,modules)
        self.assertFalse(any(isinstance(node,ast.FunctionDef) and node.name=="main"
                             for node in tree.body))

    def test_current_repo_test_runner_registers_new_suite_once(self):
        tree=ast.parse((self.repo/"tests"/"run_all.py").read_text(encoding="utf-8"))
        entries=[ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=="TESTS" for t in node.targets)]
        self.assertEqual(len(entries),1)
        self.assertGreaterEqual(len(entries[0]),55)
        self.assertEqual(sum("test_package_a_historical_writer_locks" in command
                             for _,command in entries[0]),1)


if __name__=="__main__":
    unittest.main()
