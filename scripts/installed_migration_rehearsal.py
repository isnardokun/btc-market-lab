"""Disposable installed-schema WAL backup/migration/restore proof only.

Requires BOTH fixture marks plus C's attested original inode. Never restores
the source in place, never cleans an existing backup folder, no CLI/defaults.
"""
import json
from pathlib import Path
import sqlite3
import uuid

from ingestion.package_a_contracts import utc
from scripts import package_c_state as state
from scripts.package_a_migration_rehearsal import _fixture_paths
from scripts.package_a_parent_lock import hold_offline_parent_lease, verify_offline_parent_lease
from storage import compatibility_ledger as ledger
from storage.installed_schema import inspect_installed_schema, legacy_fingerprint
from storage.installed_connection import attested_connection


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
        folder=root/('installed-backup-'+uuid.uuid4().hex);folder.mkdir(mode=0o700)
        folder_info=state._owned(folder,directory=True)
        source_info=state._owned(path);source_identity=(source_info.st_dev,source_info.st_ino)
        def check_target(target):
            current=state._owned(folder,directory=True)
            if (current.st_dev,current.st_ino)!=(folder_info.st_dev,folder_info.st_ino):
                raise ValueError('PRIVATE_BACKUP_DIRECTORY_IDENTITY_DRIFT')
            state._owned(target)
            _guard(db_path,lock_dir)
        def target_connection(target,*,readonly):
            info=state._owned(target)
            return attested_connection(target,identity=(info.st_dev,info.st_ino),readonly=readonly,
                check_path=lambda:check_target(target))
        backup=folder/('btc_research_'+stamp.strftime('%Y%m%dT%H%M%SZ')+'.db')
        backup.touch(mode=0o600,exist_ok=False)
        # SQLite online backup with BOTH actual descriptors attested; a new
        # private folder means no previous backup retention/deletion happens.
        with attested_connection(path,identity=source_identity,check_path=lambda:_guard(db_path,lock_dir),readonly=True) as (active,check_source):
            active.execute('BEGIN')
            if active.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('SOURCE_INTEGRITY_FAILED')
            with target_connection(backup,readonly=False) as (target,check_backup):
                active.backup(target,pages=256)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('BACKUP_INTEGRITY_FAILED')
                check_source();check_backup()
        with target_connection(backup,readonly=True) as (conn,check_backup):
            conn.execute('BEGIN');inspect_installed_schema(conn);backup_fingerprint=legacy_fingerprint(conn)
        restored=folder/'restored-fixture.db';restored.touch(mode=0o600,exist_ok=False)
        with target_connection(backup,readonly=True) as (source,check_backup):
            with target_connection(restored,readonly=False) as (target,check_restored):
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('RESTORE_INTEGRITY_FAILED')
                restored_fingerprint=legacy_fingerprint(target)
                check_backup();check_restored()
        if restored_fingerprint!=backup_fingerprint: raise ValueError('RESTORE_LEGACY_FINGERPRINT_DRIFT')
        with attested_connection(path,identity=source_identity,check_path=lambda:_guard(db_path,lock_dir),readonly=False) as (conn,check_actual):
            conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
            try:
                before=legacy_fingerprint(conn)
                if before!=backup_fingerprint: raise ValueError('SOURCE_CHANGED_AFTER_BACKUP')
                ledger.initialize(conn,now=now,fault=fault)
                after=legacy_fingerprint(conn)
                if before!=after: raise ValueError('MIGRATION_LEGACY_FINGERPRINT_DRIFT')
                if fault=='before_commit': raise ValueError('INJECTED_MIGRATION_CRASH')
                verify_offline_parent_lease(lock_dir);check_actual();conn.commit()
            except BaseException: conn.rollback();raise
        return {'state':'INSTALLED_MIGRATION_REHEARSAL_PASS','legacy_before':before,'legacy_after':after,
            'backup_fingerprint':backup_fingerprint,'restored_fingerprint':restored_fingerprint,
            'backup':str(backup),'restored_fixture':str(restored),'production_authorized':False,
            'real_http_requests':0,'global_writer_exclusion':'NOT_VERIFIED'}
