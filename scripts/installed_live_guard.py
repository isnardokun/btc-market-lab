"""Explicit, owner-controlled DB boundary under the actual parent flock."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from scripts.installed_live_policy import ValidatedGrant,digest
from scripts.package_a_parent_lock import hold_offline_parent_lease,verify_offline_parent_lease
from storage.installed_connection import attested_connection

PRIVATE_BINDING='.installed-live-private-target.json'


def owned(path,uid,*,directory=False):
    try:info=os.stat(path,follow_symlinks=False)
    except OSError as e:raise ValueError('RUNTIME_PATH_UNAVAILABLE') from e
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
        info.st_uid!=uid or info.st_mode&0o022 or (not directory and info.st_nlink!=1)):
        raise ValueError('RUNTIME_PATH_UNSAFE')
    return info


def _identity(info):return (info.st_dev,info.st_ino)


def _read_owned(path,uid,limit=262144):
    initial=owned(path,uid);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        if _identity(os.fstat(fd))!=_identity(initial):raise ValueError('RUNTIME_ARTIFACT_IDENTITY_DRIFT')
        body=os.read(fd,limit+1)
        if len(body)>limit:raise ValueError('RUNTIME_ARTIFACT_BUDGET')
        if _identity(owned(path,uid))!=_identity(initial):raise ValueError('RUNTIME_ARTIFACT_IDENTITY_DRIFT')
        return body
    finally:os.close(fd)


def _private_binding(cap):
    g=cap.grant;root=Path(g.root)
    parent=owned(root.parent,g.owner_uid,directory=True)
    if _identity(parent)!=g.creation_parent_identity:raise ValueError('CREATION_PARENT_IDENTITY_DRIFT')
    if not root.exists():
        cap.verify('migrate');root.mkdir(mode=0o700)
        Path(g.lock).mkdir(mode=0o700)
        fd=os.open(g.db,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        body={'approval_sha':g.artifact_sha,'root':list(_identity(owned(root,g.owner_uid,directory=True))),
              'db':list(_identity(owned(g.db,g.owner_uid))),
              'lock':list(_identity(owned(g.lock,g.owner_uid,directory=True)))}
        fd=os.open(root/PRIVATE_BINDING,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:os.write(fd,json.dumps(body,sort_keys=True).encode());os.fsync(fd)
        finally:os.close(fd)
        d=os.open(root,os.O_DIRECTORY|os.O_RDONLY);os.fsync(d);os.close(d)
    body=json.loads(_read_owned(root/PRIVATE_BINDING,g.owner_uid))
    if type(body) is not dict or set(body)!={'approval_sha','root','db','lock'} or body['approval_sha']!=g.artifact_sha:
        raise ValueError('PRIVATE_TARGET_APPROVAL_DRIFT')
    return tuple(body['root']),tuple(body['db']),tuple(body['lock'])


@dataclass
class RuntimeSession:
    capability:ValidatedGrant
    identities:tuple
    active:bool=False
    started:float=0

    @property
    def db(self):return Path(self.capability.grant.db)
    @property
    def lock(self):return Path(self.capability.grant.lock)
    @property
    def root(self):return Path(self.capability.grant.root)
    @property
    def target_kind(self):return self.capability.grant.target_kind

    def _paths(self):
        g=self.capability.grant
        for path,identity,directory in ((self.root,self.identities[0],True),(self.db,self.identities[1],False),(self.lock,self.identities[2],True)):
            info=owned(path,g.owner_uid,directory=directory)
            if _identity(info)!=identity:raise ValueError('RUNTIME_PATH_IDENTITY_DRIFT')
        if self.target_kind=='FIXTURE_TEST':
            from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE
            if _read_owned(self.root/MARKER,g.owner_uid).decode()!=MARKER_VALUE:
                raise ValueError('FIXTURE_TEST_MARK_REQUIRED')
        if self.target_kind=='INSTALLED_DB':
            for name,sha in (('.live-schema-receipt.json',g.installed_schema_sha),
                             ('.live-writer-window.json',g.writer_window_sha),
                             ('.live-backup-receipt.json',g.backup_manifest_sha)):
                data=_read_owned(self.root/name,g.owner_uid)
                if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('INSTALLED_OPERATIONAL_RECEIPT_DRIFT')
                value=json.loads(data)
                if value.get('code_sha')!=g.code_sha or value.get('db_identity')!=list(self.identities[1]):
                    raise ValueError('INSTALLED_OPERATIONAL_IDENTITY_DRIFT')
                from ingestion.package_a_contracts import utc
                if self.capability.verify('read')>=utc(value['expires_utc']):
                    raise ValueError('INSTALLED_OPERATIONAL_RECEIPT_EXPIRED')

    def verify(self,operation='read'):
        self.capability.verify(operation)
        if not self.active:raise ValueError('RUNTIME_ACTIVE_SESSION_REQUIRED')
        if time.monotonic()-self.started>=self.capability.grant.deadline_seconds:
            raise ValueError('RUNTIME_DEADLINE_EXCEEDED')
        self._paths();verify_offline_parent_lease(self.lock)

    @contextmanager
    def connect(self,*,readonly):
        self.verify('read' if readonly else 'migrate')
        with attested_connection(self.db,identity=self.identities[1],check_path=self.verify,readonly=readonly) as pair:
            yield pair


@contextmanager
def hold_runtime_session(capability):
    if type(capability) is not ValidatedGrant:raise ValueError('RUNTIME_CAPABILITY_REQUIRED')
    capability.verify('read');g=capability.grant
    identities=(_private_binding(capability) if g.target_kind=='LIVE_PRIVATE_PILOT' else
                (g.root_identity,g.db_identity,g.lock_identity))
    if g.target_kind=='LIVE_PRIVATE_PILOT' and any(wanted and wanted!=actual for wanted,actual in zip((g.root_identity,g.db_identity,g.lock_identity),identities)):
        raise ValueError('PRIVATE_DECLARED_IDENTITY_DRIFT')
    session=RuntimeSession(capability,identities)
    session._paths()
    with hold_offline_parent_lease(session.lock,identity_check=session._paths):
        session.active=True;session.started=time.monotonic()
        try:session.verify();yield session;session.verify()
        finally:session.active=False
