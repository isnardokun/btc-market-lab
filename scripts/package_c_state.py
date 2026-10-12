"""Durable owner-controlled fixture state. No production path or network."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from scripts.package_a_ingest_fixture import _fixture_guard
from scripts.package_a_parent_lock import verify_offline_parent_lease

FLAG='.package-c-offline-fixture'
STATE='run_state.json'
ARTIFACTS=frozenset(('audit.json','evidence.json','charts.json','report.html','archive.json','gate.json'))
MAX_STATE=1_000_000
MAX_ARTIFACT=16_000_000

def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()

def digest(data):return hashlib.sha256(data).hexdigest()

def _owned(path,*,directory=False):
    info=path.lstat()
    if (path.is_symlink() or info.st_uid!=os.getuid() or
        (directory and (not stat.S_ISDIR(info.st_mode) or info.st_mode&0o022)) or
        (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1))):
        raise ValueError('UNSAFE_FIXTURE_PATH')
    return info

def bounded_read(path,limit):
    _owned(path)
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)
    with os.fdopen(os.open(path,flags),'rb') as stream:
        info=os.fstat(stream.fileno())
        if info.st_nlink!=1 or info.st_size>limit:raise ValueError('OVERSIZE_OR_LINKED_FILE')
        data=stream.read(limit+1)
    if len(data)>limit:raise ValueError('OVERSIZE_FILE')
    return data

def guard(root):
    root=Path(root)
    if not root.name.startswith('package-a-fixture-'):raise ValueError('UNMARKED_FIXTURE_ROOT')
    root_info=_owned(root,directory=True)
    db=root/'package_a_synthetic.db';locks=root/'locks'
    _fixture_guard(db,locks)
    db_info=_owned(db);_owned(locks,directory=True)
    flag=json.loads(bounded_read(root/FLAG,MAX_STATE))
    if (type(flag) is not dict or set(flag)!={'version','run_id','root_identity','db_identity'} or
        type(flag['version']) is not int or flag['version']!=1 or type(flag['run_id']) is not str or len(flag['run_id'])!=32 or
        any(x not in '0123456789abcdef' for x in flag['run_id']) or
        flag['root_identity']!=[root_info.st_dev,root_info.st_ino] or
        flag['db_identity']!=[db_info.st_dev,db_info.st_ino]):
        raise ValueError('FIXTURE_IDENTITY_DRIFT')
    _owned(root/'artifacts',directory=True)
    for name in ARTIFACTS:
        path=root/'artifacts'/name
        if path.exists() or path.is_symlink():_owned(path)
    _owned(root/STATE)
    return root,db,locks,flag

def atomic_bytes(path,data):
    path=Path(path)
    root=path.parent.parent if path.parent.name=='artifacts' else path.parent
    verify_offline_parent_lease(root/'locks')
    _owned(path.parent,directory=True)
    if path.exists() or path.is_symlink():_owned(path)
    if type(data) is not bytes or len(data)>MAX_ARTIFACT:raise ValueError('BOUNDED_ARTIFACT_REQUIRED')
    fd,name=tempfile.mkstemp(prefix='.c-write-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        verify_offline_parent_lease(root/'locks')
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|getattr(os,'O_NOFOLLOW',0))
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)

def save_state(root,body):
    encoded=encode(body)
    if len(encoded)>MAX_STATE:raise ValueError('STATE_SIZE_BUDGET')
    atomic_bytes(Path(root)/STATE,encode({'body':body,'sha256':digest(encoded)}))

def read_state(root):
    payload=json.loads(bounded_read(Path(root)/STATE,MAX_STATE))
    if (type(payload) is not dict or set(payload)!={'body','sha256'} or
        type(payload['body']) is not dict or digest(encode(payload['body']))!=payload['sha256']):
        raise ValueError('STATE_CHECKSUM_MISMATCH')
    return payload['body']

def artifact(root,name):
    if name not in ARTIFACTS:raise ValueError('UNKNOWN_ARTIFACT_NAME')
    return Path(root)/'artifacts'/name

def artifact_sha(root,name):return digest(bounded_read(artifact(root,name),MAX_ARTIFACT))

def write_json(root,name,value):
    atomic_bytes(artifact(root,name),encode(value))
    return artifact_sha(root,name)
