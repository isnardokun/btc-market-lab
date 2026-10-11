"""A1 strict OFFLINE daily-writer parent-lock simulation; no actual cron/daily run."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts.package_a_writer_cutover import (
    MARKER, MARKER_TEXT, NAMESPACE,
    plan_parent_locked_daily, run_offline_parent_locked_child,
)
from scripts.market_r10e11_writer_exclusion import hold_writer_lock


class TestOfflineParentWriterCutover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[2]

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="package-a-child-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.locks = self.root / "locks"
        self.locks.mkdir()
        (self.root / MARKER).write_text(MARKER_TEXT, encoding="utf-8")
        self.worker = self.root / "worker.py"
        self.worker.write_text(
            "from pathlib import Path\n"
            "base = Path(__file__).resolve().parent\n"
            "(base / 'started').write_text('started')\n"
            "(base / 'finished').write_text('finished')\n",
            encoding="utf-8")

    def invoke(self, **kw):
        return run_offline_parent_locked_child(self.root, self.locks, **kw)

    def test_review_discovers_real_daily_source_children_without_executing(self):
        info = plan_parent_locked_daily(self.repo_root)
        self.assertEqual(info["state"], "SOURCE_ONLY_DAILY_LOCK_CUTOVER_CANDIDATE")
        self.assertEqual(info["candidate_command"], ["bash","scripts/daily.sh"])
        self.assertEqual(info["parent_lock_namespace"], NAMESPACE)
        for item in ("ingestion/ingest_price.py","ingestion/ingest.py",
                     "ingestion/ingest_fred.py","validation/archive_metrics.py",
                     "rendering/archive_report.py","rendering/export_dashboard.py",
                     "ingestion/snapshot.py"):
            self.assertIn(item, info["registered_writer_children"])
        self.assertTrue(info["avoid_nested_child_lock"])
        self.assertTrue(info["requires_explicit_operator_deployment"])
        self.assertFalse(info["all_writers_cooperating"])
        self.assertFalse(info["production_authorized"])
        self.assertFalse(info["source_network_or_sql_executed"])

    def test_uncertified_unknown_manual_and_library_writers_remain_visible(self):
        info = plan_parent_locked_daily(self.repo_root)
        self.assertGreater(len(info["known_unprotected_manual_or_library_writers"]),0)
        self.assertFalse(info["host_cron_checked"])
        self.assertFalse(info["physical_lock_inode_verified_on_host"])
        self.assertFalse(info["alternate_repo_copies_checked"])
        self.assertFalse(info["runtime_invocations_checked"])

    def test_parent_holds_flock_for_exact_synthetic_child_lifetime(self):
        result=self.invoke()
        self.assertEqual(result["state"],"OFFLINE_CHILD_COMPLETED")
        self.assertEqual(result["exit_code"],0)
        self.assertTrue(result["parent_held_single_cooperative_flock"])
        self.assertFalse(result["nested_child_lock_requested"])
        self.assertEqual((self.root/"started").read_text(),"started")
        self.assertEqual((self.root/"finished").read_text(),"finished")
        self.assertFalse(result["production_authorized"])

    def test_already_held_lock_refuses_before_starting_second_child(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            result=self.invoke()
            self.assertEqual(result["state"],"BLOCKED_WRITER_BUSY")
            self.assertFalse(result["child_started"])
            self.assertFalse((self.root/"started").exists())
        self.assertEqual(self.invoke()["state"],"OFFLINE_CHILD_COMPLETED")

    def test_cross_process_parent_contention_blocks_second_fixture_execution(self):
        self.worker.write_text(
            "from pathlib import Path\nimport time\n"
            "base=Path(__file__).resolve().parent\n"
            "(base/'started').write_text('started')\n"
            "time.sleep(1.5)\n"
            "(base/'finished').write_text('finished')\n",encoding="utf-8")
        program=("from scripts.package_a_writer_cutover import "
                 "run_offline_parent_locked_child;"
                 "import sys;"
                 "print(run_offline_parent_locked_child(sys.argv[1],sys.argv[2]))")
        child_env=dict(os.environ)
        child_env["PYTHONPATH"]=str(self.repo_root)
        proc=subprocess.Popen(
            [sys.executable,"-B","-c",program,str(self.root),str(self.locks)],
            env=child_env, stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            for _ in range(100):
                if (self.root/"started").exists():
                    break
                if proc.poll() is not None:
                    break
                time.sleep(.02)
            self.assertTrue((self.root/"started").exists())
            blocked=self.invoke()
            self.assertEqual(blocked["state"],"BLOCKED_WRITER_BUSY")
            self.assertFalse(blocked["child_started"])
            stdout,stderr=proc.communicate(timeout=5)
            self.assertEqual(proc.returncode,0,stderr)
            self.assertIn("OFFLINE_CHILD_COMPLETED",stdout)
            self.assertEqual(self.invoke()["state"],"OFFLINE_CHILD_COMPLETED")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)

    def test_child_failure_returns_nonzero_and_lock_is_released(self):
        self.worker.write_text("raise SystemExit(7)\n", encoding="utf-8")
        failure=self.invoke()
        self.assertEqual(failure["state"],"OFFLINE_CHILD_FAILED")
        self.assertEqual(failure["exit_code"],7)
        self.worker.write_text("pass\n",encoding="utf-8")
        self.assertEqual(self.invoke()["state"],"OFFLINE_CHILD_COMPLETED")

    def test_child_timeout_terminates_synthetic_child_and_releases_lock(self):
        self.worker.write_text("import time\ntime.sleep(5)\n",encoding="utf-8")
        result=self.invoke(timeout=.08)
        self.assertEqual(result["state"],"OFFLINE_CHILD_TIMEOUT")
        self.assertTrue(result["child_killed_by_timeout"])
        self.worker.write_text("pass\n",encoding="utf-8")
        self.assertEqual(self.invoke()["state"],"OFFLINE_CHILD_COMPLETED")

    def test_crashed_external_lock_holder_does_not_stick_inode(self):
        proc=subprocess.run([sys.executable,"-B","-c",
                "import os,sys;"
                "from scripts.market_r10e11_writer_exclusion import hold_writer_lock;"
                "with_stmt='unused';"
                "ctx=hold_writer_lock(sys.argv[1],namespace=sys.argv[2]);"
                "ctx.__enter__();os._exit(13)",
                str(self.locks),NAMESPACE],
                env={**os.environ,"PYTHONPATH":str(self.repo_root)},
                capture_output=True,text=True,timeout=3)
        self.assertEqual(proc.returncode,13)
        self.assertEqual(self.invoke()["state"],"OFFLINE_CHILD_COMPLETED")

    def test_different_lock_directory_is_not_a_global_host_certification(self):
        with tempfile.TemporaryDirectory(prefix="package-a-child-") as other:
            another=Path(other)
            (another/"locks").mkdir()
            (another/MARKER).write_text(MARKER_TEXT,encoding="utf-8")
            (another/"worker.py").write_text("pass\n",encoding="utf-8")
            with hold_writer_lock(self.locks,namespace=NAMESPACE):
                outcome=run_offline_parent_locked_child(another,another/"locks")
                self.assertEqual(outcome["state"],"OFFLINE_CHILD_COMPLETED")
        # This is deliberate demonstration that aliases/other dirs are unsafe
        # if production entrypoints don't share exactly one physical inode.

    def test_path_guard_rejects_non_fixture_project_or_arbitrary_worker(self):
        with self.assertRaises(ValueError):
            run_offline_parent_locked_child(self.repo_root,self.locks)
        self.assertFalse((self.root/"started").exists())

    def test_missing_marker_and_symlink_worker_fail_before_subprocess(self):
        (self.root/MARKER).unlink()
        with self.assertRaises(ValueError):
            self.invoke()
        (self.root/MARKER).write_text(MARKER_TEXT,encoding="utf-8")
        self.worker.unlink()
        self.worker.symlink_to(self.root/MARKER)
        with self.assertRaises(ValueError):
            self.invoke()

    def test_invalid_timeout_cannot_execute_child(self):
        for timeout in (0,-1,11,True,"3"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    self.invoke(timeout=timeout)
        self.assertFalse((self.root/"started").exists())


if __name__ == "__main__":
    unittest.main()
