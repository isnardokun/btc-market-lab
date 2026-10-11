"""Offline parent/child lock supervision and process-group cleanup (POSIX only)."""
import os
from pathlib import Path
import tempfile
import time
import unittest

from scripts.package_a_writer_cutover import (
    MARKER, MARKER_TEXT, run_offline_parent_locked_child,
)
from scripts.market_r10e11_writer_exclusion import hold_writer_lock


class ParentLockProcessGroupTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="package-a-child-")
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.locks=self.root/"locks"
        self.locks.mkdir()
        (self.root/MARKER).write_text(MARKER_TEXT,encoding="utf-8")
        self.worker=self.root/"worker.py"

    def script(self, code):
        self.worker.write_text(code,encoding="utf-8")

    def run_worker(self, *, timeout=2):
        return run_offline_parent_locked_child(self.root,self.locks,timeout=timeout)

    def child_program(self, *, exit_code=0, pause=3):
        # All descendants share the worker's distinct POSIX process group;
        # stdio redirected so the direct worker's communicate can finish.
        return (
            "import subprocess,sys,time\n"
            "from pathlib import Path\n"
            "Path('started').write_text('yes')\n"
            "code = 'import time,pathlib;time.sleep(0.6);"
            "pathlib.Path(\"unsafe_descendant_write\").write_text(\"unsafe\")'\n"
            "subprocess.Popen([sys.executable,'-B','-c',code],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
            "stdin=subprocess.DEVNULL,close_fds=True)\n"
            + ("time.sleep(%s)\n"%pause if pause else "")
            + ("raise SystemExit(%d)\n"%exit_code if exit_code else "")
        )

    def test_normal_single_child_succeeds_with_no_group_kill(self):
        self.script("from pathlib import Path\nPath('finished').write_text('yes')\n")
        r=self.run_worker()
        self.assertEqual(r["state"],"OFFLINE_CHILD_COMPLETED")
        self.assertTrue(r["parent_flock_held_through_reap"])
        self.assertFalse(r["synthetic_process_group_sigkill_requested"])
        self.assertEqual(r["exit_code"],0)
        self.assertTrue((self.root/"finished").is_file())

    def test_timeout_kills_descendants_before_global_flock_release(self):
        self.script(self.child_program(pause=4))
        r=self.run_worker(timeout=.2)
        self.assertEqual(r["state"],"OFFLINE_CHILD_TIMEOUT")
        self.assertTrue(r["child_killed_by_timeout"])
        self.assertTrue(r["parent_flock_held_through_reap"])
        self.assertTrue(r["synthetic_process_group_sigkill_requested"])
        self.assertTrue((self.root/"started").exists())
        time.sleep(.72)
        self.assertFalse((self.root/"unsafe_descendant_write").exists())
        self.script("pass\n")
        self.assertEqual(self.run_worker()["state"],"OFFLINE_CHILD_COMPLETED")

    def test_successful_worker_leaving_descendant_is_blocked_not_succeeded(self):
        self.script(self.child_program(pause=0))
        r=self.run_worker()
        self.assertEqual(r["state"],"OFFLINE_CHILD_DESCENDANTS_BLOCKED")
        self.assertEqual(r["child_exit_code"],0)
        self.assertTrue(r["parent_flock_held_through_reap"])
        self.assertTrue(r["synthetic_process_group_sigkill_requested"])
        time.sleep(.72)
        self.assertFalse((self.root/"unsafe_descendant_write").exists())

    def test_failed_worker_leaving_descendant_is_also_blocked(self):
        self.script(self.child_program(exit_code=7,pause=0))
        r=self.run_worker()
        self.assertEqual(r["state"],"OFFLINE_CHILD_DESCENDANTS_BLOCKED")
        self.assertEqual(r["child_exit_code"],7)
        time.sleep(.72)
        self.assertFalse((self.root/"unsafe_descendant_write").exists())

    def test_nonzero_child_without_descendants_releases_lock(self):
        self.script("raise SystemExit(7)\n")
        r=self.run_worker()
        self.assertEqual(r["state"],"OFFLINE_CHILD_FAILED")
        self.assertEqual(r["exit_code"],7)
        self.assertFalse(r["synthetic_process_group_sigkill_requested"])
        self.script("pass\n")
        self.assertEqual(self.run_worker()["state"],"OFFLINE_CHILD_COMPLETED")

    def test_global_lock_busy_stops_child_before_start(self):
        self.script("from pathlib import Path\nPath('unsafe_start').write_text('BAD')\n")
        with hold_writer_lock(self.locks,namespace="btc-research-global-writer-v1"):
            r=self.run_worker()
        self.assertEqual(r["state"],"BLOCKED_WRITER_BUSY")
        self.assertFalse(r["child_started"])
        self.assertFalse((self.root/"unsafe_start").exists())

    def test_child_does_not_inherit_arbitrary_parent_environment_secret(self):
        self.script("import os\nfrom pathlib import Path\n"
                    "Path('environment_keys').write_text('|'.join(os.environ))\n")
        old=os.environ.get("INHERITANCE_TEST_FAKE_TOKEN")
        os.environ["INHERITANCE_TEST_FAKE_TOKEN"]="FAKE_SECRET"
        try:
            self.assertEqual(self.run_worker()["state"],"OFFLINE_CHILD_COMPLETED")
        finally:
            if old is None:
                os.environ.pop("INHERITANCE_TEST_FAKE_TOKEN",None)
            else:
                os.environ["INHERITANCE_TEST_FAKE_TOKEN"]=old
        keys=(self.root/"environment_keys").read_text()
        self.assertNotIn("INHERITANCE_TEST_FAKE_TOKEN",keys)
        self.assertIn("PYTHONDONTWRITEBYTECODE",keys)


if __name__=="__main__":
    unittest.main()
