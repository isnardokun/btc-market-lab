from dataclasses import replace
import importlib.util
import os
import unittest
from unittest.mock import patch

from tests.unit.live_fixture_helpers import new_fixture,grant_for,capability,CODE_SHA,PLAN_SHA,ASOF
from scripts.package_a_parent_lock import hold_offline_parent_lease
from scripts.market_r10e11_writer_exclusion import WriterLockBusy


class LivePolicyTests(unittest.TestCase):
    def setUp(self): self.root,self.path,self.locks=new_fixture(self)
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.installed_live_policy'),'Live policy missing')
        from scripts import installed_live_policy as p
        return p
    def grant(self): self.module();return grant_for(self.root,self.path,self.locks)
    def validate(self,g,**kw):
        args=dict(code_sha=CODE_SHA,plan_sha=PLAN_SHA,now=ASOF,operation='read',approved_artifact_sha=g.artifact_sha)
        args.update(kw);return self.module().validate_runtime_grant(g,**args)
    def test_unpinned_grant_never_authorizes(self):
        with self.assertRaisesRegex(ValueError,'APPROVAL'): self.validate(self.grant(),approved_artifact_sha=None)
    def test_stale_or_changed_grant_is_denied(self):
        g=self.grant()
        for bad in (replace(g,expires_utc=ASOF),replace(g,plan_sha='c'*64),replace(g,max_requests=17)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):self.validate(bad)
    def test_wrong_code_plan_and_operation_are_denied(self):
        for kw in ({'code_sha':'c'*40},{'plan_sha':'d'*64},{'operation':'publish'}):
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError): self.validate(self.grant(),**kw)
    def test_unsafe_limits_and_naive_time_are_denied(self):
        for kw in ({'max_requests':129},{'max_attempts':4},{'max_raw_bytes':4000001},
                   {'deadline_seconds':901},{'socket_timeout':16},{'issued_utc':'2026-10-12'}):
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError): self.validate(replace(self.grant(),**kw))
    def test_session_reads_only_the_pinned_db_and_closes(self):
        g=self.grant();from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(self.validate(g)) as session:
            with session.connect(readonly=True) as (db,verify):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM price_btc').fetchone()[0],3)
                with self.assertRaises(Exception):db.execute('DELETE FROM price_btc')
                verify()
        with self.assertRaises(Exception):db.execute('SELECT 1')
    def test_contender_starts_zero_sql(self):
        g=self.grant();from scripts.installed_live_guard import hold_runtime_session
        with hold_offline_parent_lease(self.locks):
            with patch('storage.installed_connection.sqlite3.connect',side_effect=AssertionError('SQL started')):
                with self.assertRaises(WriterLockBusy):
                    with hold_runtime_session(self.validate(g)): pass
    def test_symlink_uid_and_inode_drift_are_blocked(self):
        g=self.grant();from scripts.installed_live_guard import hold_runtime_session
        old=self.path.with_suffix('.kept');self.path.rename(old);self.path.symlink_to(old)
        with self.assertRaises(ValueError):
            with hold_runtime_session(self.validate(g)): pass
    def test_wrong_uid_is_blocked_before_connect(self):
        g=replace(self.grant(),owner_uid=os.getuid()+1);g=replace(g,approval_artifact_sha=g.artifact_sha)
        from scripts.installed_live_guard import hold_runtime_session
        with self.assertRaises(ValueError):
            with hold_runtime_session(self.validate(g)):pass
    def test_released_session_cannot_be_reused(self):
        g=self.grant();from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(self.validate(g)) as session: session.verify('read')
        with self.assertRaises(ValueError):session.verify('read')
    def test_installed_target_needs_operational_receipts(self):
        g=replace(self.grant(),target_kind='INSTALLED_DB',approval_reference='https://github.com/isnardokun/btc-market-lab/issues/60#issuecomment-1')
        g=replace(g,approval_artifact_sha=g.artifact_sha)
        with self.assertRaisesRegex(ValueError,'INSTALLED'):self.validate(g)
    def test_capability_is_not_a_boolean(self):
        self.module();from scripts.installed_live_guard import hold_runtime_session
        with self.assertRaises(ValueError):
            with hold_runtime_session(True):pass

if __name__=='__main__':unittest.main()
