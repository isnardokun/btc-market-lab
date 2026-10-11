"""R10-E.11 cooperative single-writer flock tests: TEMP FILES ONLY."""
import ast
import os
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import tempfile
import unittest

from scripts.market_r10e11_writer_exclusion import (
    WriterLockBusy, WriterLockUnsafe, hold_writer_lock,
)

CHILD = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1], namespace=sys.argv[2]):
    print('HELD', flush=True)
    sys.stdin.readline()
"""


class TestR10E11CooperativeLock(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r10e11-lock-fixture-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lockdir = self.root / "locks"
        self.lockdir.mkdir()

    def spawn_holder(self, namespace="market-history-v3"):
        env = dict(os.environ)
        repo = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(self.lockdir), namespace],
            cwd=repo, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(stop)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=5), "Child lock acquisition timed out")
        self.assertEqual(proc.stdout.readline().strip(), "HELD",
                         "Synthetic lock holder failed to start")
        return proc

    def test_single_holder_acquires(self):
        with hold_writer_lock(self.lockdir) as attestation:
            self.assertEqual(attestation["state"], "COOPERATIVE_LOCK_HELD")
            self.assertIs(attestation["excludes_uncooperative_writers"], False)
            self.assertIs(attestation["production_authorized"], False)

    def test_second_fd_in_same_process_fails_closed(self):
        with hold_writer_lock(self.lockdir):
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.lockdir):
                    self.fail("Second holder unexpectedly acquired")

    def test_independent_child_process_blocks_parent(self):
        self.spawn_holder()
        with self.assertRaises(WriterLockBusy):
            with hold_writer_lock(self.lockdir):
                self.fail("Parent overtook subprocess")

    def test_child_graceful_release_allows_next_holder(self):
        child = self.spawn_holder()
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5), 0)
        with hold_writer_lock(self.lockdir):
            pass

    def test_child_crash_releases_os_lock_without_unlink(self):
        child = self.spawn_holder()
        child.kill()
        child.wait(timeout=5)
        with hold_writer_lock(self.lockdir):
            self.assertTrue((self.lockdir / "market-history-v3.lock").exists())

    def test_different_namespaces_are_independent(self):
        child = self.spawn_holder("binance-only")
        with hold_writer_lock(self.lockdir, namespace="bybit-only"):
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.lockdir, namespace="binance-only"):
                    pass
        self.assertIsNone(child.poll())

    def test_existing_private_lock_is_reused_after_release(self):
        with hold_writer_lock(self.lockdir):
            pass
        f = self.lockdir / "market-history-v3.lock"
        before = f.stat().st_ino
        with hold_writer_lock(self.lockdir):
            self.assertEqual(f.stat().st_ino, before)
        self.assertEqual(f.stat().st_ino, before)

    def test_new_lock_permissions_are_private(self):
        with hold_writer_lock(self.lockdir, namespace="private"):
            file = self.lockdir / "private.lock"
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            self.assertEqual(file.stat().st_uid, os.getuid())

    def test_world_or_group_access_on_existing_file_rejected(self):
        file = self.lockdir / "market-history-v3.lock"
        file.touch()
        file.chmod(0o644)
        with self.assertRaises(WriterLockUnsafe):
            with hold_writer_lock(self.lockdir):
                pass

    def test_symlink_file_never_followed(self):
        outside = self.root / "secret"
        outside.write_text("keep", encoding="utf-8")
        (self.lockdir / "market-history-v3.lock").symlink_to(outside)
        with self.assertRaises(OSError):
            with hold_writer_lock(self.lockdir):
                pass
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_hardlinked_lock_is_rejected(self):
        outside = self.root / "shared"
        outside.touch(mode=0o600)
        os.link(outside, self.lockdir / "market-history-v3.lock")
        with self.assertRaises(WriterLockUnsafe):
            with hold_writer_lock(self.lockdir):
                pass

    def test_named_pipe_is_rejected_without_hanging(self):
        os.mkfifo(self.lockdir / "market-history-v3.lock", mode=0o600)
        with self.assertRaises(WriterLockUnsafe):
            with hold_writer_lock(self.lockdir):
                pass

    def test_symlink_directory_rejected(self):
        link = self.root / "alias"
        link.symlink_to(self.lockdir, target_is_directory=True)
        with self.assertRaises(WriterLockUnsafe):
            with hold_writer_lock(link):
                pass

    def test_missing_directory_not_created_implicitly(self):
        absent = self.root / "nonexistent"
        with self.assertRaises(WriterLockUnsafe):
            with hold_writer_lock(absent):
                pass
        self.assertFalse(absent.exists())

    def test_invalid_namespace_values_are_rejected(self):
        for namespace in ("", "../escape", "/tmp/unsafe", "Market", "name space",
                          ".lock", "a"*65, None, 17, "with\nnewline"):
            with self.subTest(namespace=namespace), self.assertRaises(WriterLockUnsafe):
                with hold_writer_lock(self.lockdir, namespace=namespace):
                    pass

    def test_exception_does_not_leave_lock_owned(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
            with hold_writer_lock(self.lockdir):
                raise RuntimeError("synthetic crash")
        with hold_writer_lock(self.lockdir):
            pass

    def test_lock_cannot_write_sqlite_or_contact_network(self):
        path = Path(__file__).resolve().parents[2] / "scripts" / "market_r10e11_writer_exclusion.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        for forbidden in ("sqlite3", "requests", "urllib", "socket", "subprocess"):
            self.assertNotIn(forbidden, imports)
        self.assertFalse(any(isinstance(n, ast.FunctionDef) and n.name == "main"
                             for n in tree.body))

    def test_runner_includes_every_e_stage_exactly_once(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] /
                          "run_all.py").read_text(encoding="utf-8"))
        records = [ast.literal_eval(n.value) for n in tree.body
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "TESTS"
                           for t in n.targets)]
        self.assertEqual(len(records), 1)
        suites = records[0]
        self.assertGreaterEqual(len(suites), 49)
        self.assertEqual(len({label for label,_ in suites}), len(suites))
        self.assertEqual(len({cmd for _,cmd in suites}), len(suites))
        for name in (
            "test_market_r10e_plan_only",
            "test_market_r10e_dispatch_offline",
            "test_market_r10e2_shadow_gate",
            "test_market_r10e3_mock_e2e",
            "test_market_r10e4_synthetic_cycle",
            "test_market_r10e5_backward_fixture_plan",
            "test_market_r10e6_rbn_tier2_shadow",
            "test_market_r10e7_ro_archive_manifest",
            "test_market_r10e8_ro_internal_gap_audit",
            "test_market_r10e9_evidence_review_queue",
            "test_market_r10e10_atomic_ro_evidence",
            "test_market_r10e11_writer_exclusion",
        ):
            self.assertEqual(sum(name in command for _,command in suites), 1)


if __name__ == "__main__":
    unittest.main()
