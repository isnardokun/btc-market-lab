"""Granted migration and online copy. Rehearsal is exclusively FIXTURE_TEST."""
from contextlib import contextmanager
from dataclasses import asdict
import os
import json
import uuid
from storage.installed_connection import attested_connection
from storage.installed_live_preservation import stream_legacy_fingerprint
from storage.installed_live_ledger import initialize_live
from scripts.installed_live_guard import owned,_read_owned

def migrate_installed(session,*,fault=None):
    session.verify('migrate')
    if fault not in (None,'after_first_ddl','before_commit'):raise ValueError('LIVE_MIGRATION_FAULT_INVALID')
    with session.connect(readonly=False) as (db,verify):
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        try:
            before=stream_legacy_fingerprint(db,cancel=lambda:(session.verify('migrate') or False))
            if session.target_kind=='INSTALLED_DB':
                receipt=json.loads(_read_owned(session.root/'.live-schema-receipt.json',session.capability.grant.owner_uid))
                if receipt.get('legacy_schema_sha')!=before.schema_sha:raise ValueError('LIVE_APPROVED_LEGACY_DDL_DRIFT')
            initialize_live(db,session=session,now=session.capability.validated_utc,fault=fault)
            after=stream_legacy_fingerprint(db,cancel=lambda:(session.verify('migrate') or False))
            if before!=after:raise ValueError('LIVE_MIGRATION_LEGACY_DRIFT')
            if fault=='before_commit':raise ValueError('INJECTED_LIVE_MIGRATION_CRASH')
            verify();session.verify('migrate');db.commit()
        except BaseException:db.rollback();raise
    return {'state':'LIVE_ADDITIVE_MIGRATION_PRESERVED','before':asdict(before),'after':asdict(after),
            'fixture_test':session.target_kind=='FIXTURE_TEST'}

@contextmanager
def _new_target(session,path):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
    info=owned(path,session.capability.grant.owner_uid);identity=(info.st_dev,info.st_ino)
    def check():
        session.verify('migrate');now=owned(path,session.capability.grant.owner_uid)
        if (now.st_dev,now.st_ino)!=identity:raise ValueError('LIVE_COPY_TARGET_IDENTITY_DRIFT')
    with attested_connection(path,identity=identity,check_path=check,readonly=False) as pair:yield pair

def rehearse_live_migration(fixture_session,*,fault=None):
    session=fixture_session;session.verify('migrate')
    if session.target_kind!='FIXTURE_TEST':raise ValueError('LIVE_REHEARSAL_FIXTURE_ONLY')
    if fault not in (None,'after_first_ddl','before_commit'):raise ValueError('LIVE_REHEARSAL_FAULT_INVALID')
    folder=session.root/('live-rehearsal-'+uuid.uuid4().hex);folder.mkdir(mode=0o700)
    with session.connect(readonly=True) as (source,verify):
        source.execute('BEGIN');before=stream_legacy_fingerprint(source)
        with _new_target(session,folder/'backup.db') as (target,check):
            source.backup(target,pages=256)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('LIVE_BACKUP_INTEGRITY')
            backup=stream_legacy_fingerprint(target);check();verify()
            with _new_target(session,folder/'restore.db') as (restored,check_restored):
                target.backup(restored,pages=256);restore=stream_legacy_fingerprint(restored)
                if restored.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('LIVE_RESTORE_INTEGRITY')
                check_restored()
    if before!=backup or before!=restore:raise ValueError('LIVE_COPY_FINGERPRINT_DRIFT')
    with session.connect(readonly=False) as (db,verify):
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        try:
            current=stream_legacy_fingerprint(db)
            if current!=before:raise ValueError('LIVE_SOURCE_CHANGED_AFTER_BACKUP')
            initialize_live(db,session=session,now=session.capability.validated_utc,fault=fault)
            after=stream_legacy_fingerprint(db)
            if after!=before:raise ValueError('LIVE_MIGRATION_LEGACY_DRIFT')
            if fault=='before_commit':raise ValueError('INJECTED_LIVE_MIGRATION_CRASH')
            session.verify('migrate');verify();db.commit()
        except BaseException:db.rollback();raise
    return {'state':'LIVE_MIGRATION_REHEARSAL_PASS','before':asdict(before),'after':asdict(after),
            'backup':asdict(backup),'restored':asdict(restore),'folder':str(folder),'production_authorized':False}
