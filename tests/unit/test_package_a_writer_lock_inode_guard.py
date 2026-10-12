"""Physical flock inode identity, directory ownership and PRECOMMIT denial.

Tests use only marked disposable synthetic SQLite and fake HTTP. No host
scheduler, credentials, provider APIs, production DB or real publisher.
"""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.market_r10e11_writer_exclusion import (
    hold_writer_lock,WriterLockUnsafe,WriterLockBusy,
)
from scripts.package_a_parent_lock import (
    hold_offline_parent_lease,verify_offline_parent_lease,
)
from scripts.package_a_ingest_fixture import (
    MARKER,MARKER_VALUE,inspect_fixture,
)
from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
from scripts.package_a_yahoo_fanout import run_offline_yahoo_ohlcv_fanout
from ingestion.package_a_contracts import SeriesSpec

NAMESPACE="btc-research-global-writer-v1"
START="2026-10-08T00:00:00Z"
STOP="2026-10-09T00:00:00Z"
NOW="2026-10-11T00:00:00Z"


class TestPackageALockPhysicalInodeSafety(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.locks=self.root/"locks"
        self.locks.mkdir()
        self.db=self.root/"package_a_synthetic.db"
        (self.root/MARKER).write_text(MARKER_VALUE,encoding="utf-8")
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE old_archive (id INTEGER PRIMARY KEY, status TEXT)")
            db.execute("INSERT INTO old_archive VALUES(1,'UNTOUCHED')")

    @property
    def filename(self):
        return self.locks/(NAMESPACE+".lock")

    def mock_page(self):
        raw=json.dumps({"data":[{"date":"2026-10-08","value":1.03}]}).encode()
        return run_mocked_legacy_page(
            self.db,self.locks,SeriesSpec("researchbitcoin","sopr_sth","BTC",
                                           "d1","ratio"),
            START,STOP,raw_bytes=raw,now=NOW)

    def counts(self):
        with sqlite3.connect(self.db) as db:
            try:
                return db.execute(
                    "SELECT COUNT(*) FROM package_a_receipts").fetchone()[0]
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return 0
                raise

    def check_untouched(self):
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT status FROM old_archive").fetchall(),
                [("UNTOUCHED",)])

    def test_owned_private_directory_can_create_lock_and_validate_exact_inode(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE) as held:
            stat=self.filename.stat()
            self.assertEqual(held["physical_lock_device"],stat.st_dev)
            self.assertEqual(held["physical_lock_inode"],stat.st_ino)
            self.assertIs(held["check_identity"](),True)
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.locks,namespace=NAMESPACE):
                    self.fail("Double lock unexpectedly acquired")
        self.assertEqual(self.mock_page()["state"],"PAGE_COMMITTED_OFFLINE")
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_world_writable_lock_directory_fails_before_creating_file(self):
        self.locks.chmod(0o777)
        with self.assertRaisesRegex(WriterLockUnsafe,
                                    "owner-controlled"):
            with hold_writer_lock(self.locks,namespace=NAMESPACE):
                self.fail("Unsafe world writable lock folder accepted")
        self.assertFalse(self.filename.exists())

    def test_group_writable_lock_directory_fails_closed(self):
        self.locks.chmod(0o775)
        with self.assertRaisesRegex(WriterLockUnsafe,"owner-controlled"):
            with hold_writer_lock(self.locks,namespace=NAMESPACE):
                self.fail("Unsafe group writable folder accepted")
        self.assertFalse(self.filename.exists())

    def test_sticky_world_writable_directory_not_exempt(self):
        self.locks.chmod(0o1777)
        with self.assertRaises(WriterLockUnsafe):
            with hold_writer_lock(self.locks,namespace=NAMESPACE):
                pass
        self.assertFalse(self.filename.exists())

    def test_owner_readable_directory_mode_0755_remains_supported(self):
        self.locks.chmod(0o755)
        with hold_writer_lock(self.locks,namespace=NAMESPACE) as holder:
            self.assertTrue(holder["check_identity"]())
        self.assertEqual(self.locks.stat().st_mode & 0o777,0o755)

    def test_lock_unlink_recreate_is_detected_while_old_fd_held(self):
        with self.assertRaisesRegex(WriterLockUnsafe,
                                    "BLOCKED_WRITER_LOCK_INODE_REPLACED"):
            with hold_writer_lock(self.locks,namespace=NAMESPACE) as holder:
                original=holder["physical_lock_inode"]
                self.filename.unlink()
                self.filename.write_text("ANOTHER INODE",encoding="utf-8")
                self.filename.chmod(0o600)
                self.assertNotEqual(self.filename.stat().st_ino,original)
                # Newly created inode CAN acquire independent flock: this
                # is why an immutable pathname must be rechecked.
                with hold_writer_lock(self.locks,namespace=NAMESPACE) as forged:
                    self.assertNotEqual(forged["physical_lock_inode"],original)
                holder["check_identity"]()
        self.assertEqual(self.counts(),0)

    def test_file_unlinked_without_replacement_is_detected(self):
        with self.assertRaisesRegex(WriterLockUnsafe,
                                    "BLOCKED_WRITER_LOCK_INODE_DISAPPEARED"):
            with hold_writer_lock(self.locks,namespace=NAMESPACE) as holder:
                self.filename.unlink()
                holder["check_identity"]()

    def test_private_lock_file_mode_became_0644_is_denied(self):
        with self.assertRaisesRegex(WriterLockUnsafe,
                                    "BLOCKED_WRITER_LOCK_INODE_REPLACED"):
            with hold_writer_lock(self.locks,namespace=NAMESPACE) as holder:
                self.filename.chmod(0o644)
                holder["check_identity"]()

    def test_parent_directory_mutation_during_lease_is_blocked(self):
        with self.assertRaisesRegex(WriterLockUnsafe,
                                    "BLOCKED_WRITER_LOCK_DIRECTORY_REPLACED"):
            with hold_writer_lock(self.locks,namespace=NAMESPACE) as holder:
                self.locks.chmod(0o777)
                holder["check_identity"]()

    def test_parent_lease_revalidates_live_inode_during_nested_child(self):
        with self.assertRaisesRegex(WriterLockUnsafe,"INODE_REPLACED"):
            with hold_offline_parent_lease(self.locks):
                self.assertTrue(verify_offline_parent_lease(self.locks))
                self.filename.unlink()
                self.filename.write_text("RECREATED",encoding="utf-8")
                self.filename.chmod(0o600)
                verify_offline_parent_lease(self.locks)

    def test_synthetic_one_page_replacement_at_precommit_rolls_back(self):
        from scripts import package_a_ingest_fixture as fixture
        original=fixture.require_offline_precommit_integrity

        def corrupt_path_in_quality(conn):
            result=original(conn)
            self.filename.unlink()
            self.filename.write_text("NEW_LOCK_WHILE_SQL_UNCOMMITTED",encoding="utf-8")
            self.filename.chmod(0o600)
            return result

        with patch.object(fixture,"require_offline_precommit_integrity",
                          side_effect=corrupt_path_in_quality):
            with self.assertRaisesRegex(WriterLockUnsafe,"INODE_REPLACED"):
                self.mock_page()
        self.assertEqual(self.counts(),0)
        self.check_untouched()
        # Next clean attempt can acquire new inode and run safely.
        self.assertEqual(self.mock_page()["state"],"PAGE_COMMITTED_OFFLINE")
        self.assertEqual(self.counts(),1)

    def test_yahoo_five_metric_fanout_replacement_rolls_back_all(self):
        from scripts import package_a_yahoo_fanout as fanout
        original=fanout.require_offline_precommit_integrity
        yahoo=json.dumps({"chart":{"result":[{
            "timestamp":[1791466200],
            "indicators":{"quote":[{"open":[500.],"high":[505.],
                                    "low":[498.],"close":[504.],
                                    "volume":[123.] }]}}],"error":None}}).encode()
        def malicious_before_commit(conn):
            result=original(conn)
            self.filename.unlink()
            self.filename.write_text("DIFFERENT_FLOCK_INODE",encoding="utf-8")
            self.filename.chmod(0o600)
            return result
        with patch.object(fanout,"require_offline_precommit_integrity",
                          side_effect=malicious_before_commit):
            with self.assertRaisesRegex(WriterLockUnsafe,"INODE_REPLACED"):
                run_offline_yahoo_ohlcv_fanout(
                    self.db,self.locks,"SPY",START,STOP,
                    raw_bytes=yahoo,status_code=200,now=NOW)
        self.assertEqual(self.counts(),0)
        self.check_untouched()


if __name__=="__main__":
    unittest.main()
