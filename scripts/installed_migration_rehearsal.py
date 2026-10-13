"""Disposable installed-schema WAL backup/migration/restore proof only.

Requires BOTH fixture marks plus C's attested original inode. Never restores
the source in place, never cleans an existing backup folder, no CLI/defaults.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import uuid

from ingestion.package_a_contracts import utc
from scripts.backup_db import create_backup
from scripts import package_c_state as state
from scripts.package_a_migration_rehearsal import _fixture_paths
from scripts.package_a_parent_lock import hold_offline_parent_lease, verify_offline_parent_lease
from storage import compatibility_ledger as ledger
from storage.installed_schema import inspect_installed_schema, legacy_fingerprint


def _guard(db_path,locks):
    path,root=_fixture_paths(db_path,locks)
    r=state._owned(root,directory=True);d=state._owned(path);state._owned(Path(locks),directory=True)
    flag=json.loads(state.bounded_read(root/state.FLAG,state.MAX_STATE))
    if (type(flag) is not dict or set(flag)!={'version','run_id','root_identity','db_identity'} or
        flag['version']!=1 or flag['root_identity']!=[r.st_dev,r.st_ino] or
        flag['db_identity']!=[d.st_dev,d.st_ino]): raise ValueError('REHEARSAL_FIXTURE_IDENTITY_DRIFT')
    return path,root


def rehearsal(db_path,lock_dir,*,now,fault=None):
    if fault not in (None,'after_first_ddl','before_commit'): raise ValueError('UNKNOWN_REHEARSAL_FAULT')
    stamp=utc(now)
    path,root=_guard(db_path,lock_dir)
    with hold_offline_parent_lease(lock_dir,identity_check=lambda:_guard(db_path,lock_dir)):
        verify_offline_parent_lease(lock_dir)
        # ALWAYS a new empty private destination; existing backup retention is
        # not exercised against somebody else's snapshots.
        folder=root/('installed-backup-'+uuid.uuid4().hex);folder.mkdir(mode=0o700)
        backup=create_backup(db_path=path,backup_dir=folder,stamp=stamp,retain=1)
        with closing(sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
            inspect_installed_schema(conn);backup_fingerprint=legacy_fingerprint(conn)
        restored=folder/'restored-fixture.db';restored.touch(mode=0o600,exist_ok=False)
        with closing(sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(restored)) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('RESTORE_INTEGRITY_FAILED')
                restored_fingerprint=legacy_fingerprint(target)
        if restored_fingerprint!=backup_fingerprint: raise ValueError('RESTORE_LEGACY_FINGERPRINT_DRIFT')
        with closing(sqlite3.connect(path,isolation_level=None)) as conn:
            conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
            try:
                before=legacy_fingerprint(conn)
                if before!=backup_fingerprint: raise ValueError('SOURCE_CHANGED_AFTER_BACKUP')
                ledger.initialize(conn,now=now,fault=fault)
                after=legacy_fingerprint(conn)
                if before!=after: raise ValueError('MIGRATION_LEGACY_FINGERPRINT_DRIFT')
                if fault=='before_commit': raise ValueError('INJECTED_MIGRATION_CRASH')
                verify_offline_parent_lease(lock_dir);conn.commit()
            except BaseException: conn.rollback();raise
        return {'state':'INSTALLED_MIGRATION_REHEARSAL_PASS','legacy_before':before,'legacy_after':after,
            'backup_fingerprint':backup_fingerprint,'restored_fingerprint':restored_fingerprint,
            'backup':str(backup),'restored_fixture':str(restored),'production_authorized':False,
            'real_http_requests':0,'global_writer_exclusion':'NOT_VERIFIED'}
