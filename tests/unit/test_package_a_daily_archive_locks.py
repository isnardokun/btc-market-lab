"""Package A: protect real daily ARCHIVE / SNAPSHOT / DASHBOARD entrypoints.

Every invoked collector or file writer is patched. Only locks in a synthetic
TemporaryDirectory and an independent lock-holder subprocess are used.
Absolutely no live SQLite, HTTP, cron, report publishing or file write.
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

from validation import archive_metrics as metrics
from rendering import archive_report as reports
from rendering import export_dashboard as dashboard
from ingestion import snapshot
from ingestion import backfill_metrics as derived_backfill
from ingestion import backfill_onchain_metrics as onchain_backfill
from scripts.market_r10e11_writer_exclusion import (
    WriterLockBusy, WriterLockUnsafe, hold_writer_lock
)
from scripts.package_a_writer_guard import GLOBAL_WRITER_NAMESPACE as NAMESPACE

HOLDER = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1],namespace=sys.argv[2]):
    print("HELD",flush=True)
    sys.stdin.readline()
"""


class DailyArchiveWriterLocks(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="package-a-daily-writer-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.locks=self.root/"locks"
        self.locks.mkdir()
        self.repo=Path(__file__).resolve().parents[2]
        self.targets=(
            ("daily-metrics",metrics,"archive_all",[]),
            ("report-archive",reports,"_archive_report_cli",["--html","/fixture/offline.html"]),
            ("dashboard",dashboard,"_export_dashboard_unlocked",[]),
            ("snapshot",snapshot,"create_snapshot",[]),
            ("derived-backfill",derived_backfill,"backfill",[]),
            ("onchain-backfill",onchain_backfill,"backfill_onchain",[]),
        )

    def invoke(self, target, *, opt_in=True, callback=None):
        name,mod,delegate,original_args=target
        args=list(original_args)
        if opt_in:
            args.extend(["--global-lock-dir",str(self.locks)])
        with patch.object(mod,delegate,side_effect=callback or (lambda *a,**k:41)) as worker:
            with contextlib.redirect_stdout(StringIO()),contextlib.redirect_stderr(StringIO()) as err:
                outcome=mod.main(args)
            return outcome,worker,err.getvalue()

    def holder(self):
        env=dict(os.environ)
        env["PYTHONPATH"]=str(self.repo)+os.pathsep+env.get("PYTHONPATH","")
        proc=subprocess.Popen(
            [sys.executable,"-c",HOLDER,str(self.locks),NAMESPACE],
            cwd=str(self.repo),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,bufsize=1)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(cleanup)
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout,selectors.EVENT_READ)
            self.assertTrue(sel.select(timeout=5),"lock-holder process timed out")
        self.assertEqual(proc.stdout.readline().strip(),"HELD")
        return proc

    def test_all_six_optin_commands_run_inside_same_lock(self):
        def worker(*a,**kw):
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.locks,namespace=NAMESPACE):
                    pass
            return 41
        for target in self.targets:
            with self.subTest(target=target[0]):
                rc,mock,_=self.invoke(target,callback=worker)
                self.assertEqual(rc,41)
                mock.assert_called_once()
        p=self.locks/(NAMESPACE+".lock")
        self.assertTrue(p.is_file())
        self.assertEqual(p.stat().st_mode & 0o777,0o600)

    def test_all_six_busy_lock_deny_all_workers_before_db_or_files(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            for target in self.targets:
                with self.subTest(target=target[0]):
                    rc,worker,err=self.invoke(target,callback=lambda *a,**k:
                                               self.fail("work started"))
                    self.assertEqual(rc,3)
                    worker.assert_not_called()
                    self.assertIn("BLOCKED_WRITER_BUSY",err)

    def test_separate_process_holding_lock_blocks_all_six_writers(self):
        child=self.holder()
        for target in self.targets:
            rc,worker,_=self.invoke(target)
            self.assertEqual(rc,3)
            worker.assert_not_called()
        self.assertIsNone(child.poll())

    def test_lock_release_allows_all_six_writers_again(self):
        child=self.holder()
        self.assertEqual(self.invoke(self.targets[0])[0],3)
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),0)
        for target in self.targets:
            self.assertEqual(self.invoke(target)[0],41)

    def test_lock_holder_crash_releases_fd(self):
        child=self.holder()
        child.kill()
        self.assertNotEqual(child.wait(timeout=5),None)
        self.assertEqual(self.invoke(self.targets[1])[0],41)

    def test_unflagged_legacy_commands_still_run_without_shared_lock(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            for target in self.targets:
                rc,worker,_=self.invoke(target,opt_in=False)
                self.assertEqual(rc,41)
                worker.assert_called_once()

    def test_nonexistent_lock_directory_rejects_without_running_workers(self):
        self.locks.rmdir()
        for target in self.targets:
            name,mod,delegate,args=target
            with self.subTest(target=name):
                with patch.object(mod,delegate,side_effect=AssertionError("worker ran")):
                    with self.assertRaises(WriterLockUnsafe):
                        mod.main([*args,"--global-lock-dir",str(self.locks)])
        self.assertFalse(self.locks.exists())

    def test_symlink_lock_directory_fails_closed_for_all_four(self):
        link=self.root/"symlink"
        link.symlink_to(self.locks,target_is_directory=True)
        for target in self.targets:
            name,mod,delegate,args=target
            with self.subTest(target=name):
                with patch.object(mod,delegate,side_effect=AssertionError("worker ran")):
                    with self.assertRaises(WriterLockUnsafe):
                        mod.main([*args,"--global-lock-dir",str(link)])

    def test_arbitrary_other_namespace_does_not_protect_shared_lock(self):
        with hold_writer_lock(self.locks,namespace="different-namespace"):
            for target in self.targets:
                self.assertEqual(self.invoke(target)[0],41)

    def test_worker_failure_releases_lock_before_next_writer(self):
        def fail(*a,**kw):
            raise RuntimeError("fixture writer crashed")
        with self.assertRaisesRegex(RuntimeError,"fixture writer crashed"):
            self.invoke(self.targets[0],callback=fail)
        self.assertEqual(self.invoke(self.targets[3])[0],41)

    def test_delegates_are_not_invoked_at_import_time(self):
        for name,relpath in (
            ("daily-metrics","validation/archive_metrics.py"),
            ("report-archive","rendering/archive_report.py"),
            ("dashboard","rendering/export_dashboard.py"),
            ("snapshot","ingestion/snapshot.py"),
            ("derived-backfill","ingestion/backfill_metrics.py"),
            ("onchain-backfill","ingestion/backfill_onchain_metrics.py"),
        ):
            with self.subTest(source=name):
                content=(self.repo/relpath).read_text(encoding="utf-8")
                self.assertIn("--global-lock-dir",content)
                self.assertIn("run_with_optional_writer_lock",content)
                tree=ast.parse(content)
                self.assertTrue(any(isinstance(n,ast.FunctionDef) and n.name=="main"
                                    for n in tree.body))
                self.assertTrue(any(isinstance(n,ast.If) for n in tree.body))

    def test_daily_shell_and_auto_publication_remain_unmodified(self):
        shell=(self.repo/"scripts"/"daily.sh").read_text(encoding="utf-8")
        self.assertNotIn("--global-lock-dir",shell)
        self.assertNotIn("package_a_writer_guard",shell)
        for relpath in ("validation/archive_metrics.py","rendering/archive_report.py",
                        "rendering/export_dashboard.py","ingestion/snapshot.py"):
            self.assertIn(relpath,shell)

    def test_default_report_archive_cli_does_not_write_when_no_html_argument(self):
        with patch.object(reports,"archive_report",side_effect=AssertionError("archived")):
            with contextlib.redirect_stdout(StringIO()) as out:
                self.assertIsNone(reports.main([]))
        self.assertIn("Uso: python archive_report.py",out.getvalue())

    def test_current_offline_runner_registers_daily_writer_suite_exactly_once(self):
        tree=ast.parse((self.repo/"tests"/"run_all.py").read_text(encoding="utf-8"))
        entries=[ast.literal_eval(x.value) for x in tree.body
                 if isinstance(x,ast.Assign) and
                 any(isinstance(t,ast.Name) and t.id=="TESTS" for t in x.targets)]
        self.assertEqual(len(entries),1)
        self.assertEqual(sum("test_package_a_daily_archive_locks" in cmd
                             for _,cmd in entries[0]),1)
        self.assertGreaterEqual(len(entries[0]),58)


if __name__=="__main__":
    unittest.main()
