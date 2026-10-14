"""Linux actual-descriptor attestation, before SQL and through transaction.

Pin O_NOFOLLOW first, connect through that FD, then independently attest the
new SQLite MAIN descriptor. Pathname checks alone cannot detect a swap-and-
restore around sqlite3.connect. Failure to identify MAIN fails closed.
No implicit DB path or permissions: callers enforce marked fixture boundaries.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat


def _fds():
    try: names=os.listdir('/proc/self/fd')
    except OSError as error: raise ValueError('SQLITE_ACTUAL_IDENTITY_UNAVAILABLE') from error
    result={}
    for name in names:
        try:
            fd=int(name);info=os.fstat(fd)
            if stat.S_ISREG(info.st_mode): result[fd]=(info.st_dev,info.st_ino)
        except (OSError,ValueError): continue
    return result


@contextmanager
def attested_connection(path,*,identity,check_path,readonly):
    path=Path(path)
    if type(readonly) is not bool or not callable(check_path): raise ValueError('SQLITE_ATTESTATION_REQUIRED')
    check_path()
    flags=(os.O_RDONLY if readonly else os.O_RDWR)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0)
    pin=os.open(path,flags);conn=None
    try:
        info=os.fstat(pin)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or
            (info.st_dev,info.st_ino)!=tuple(identity)): raise ValueError('SQLITE_PIN_IDENTITY_MISMATCH')
        check_path();before=_fds()
        conn=sqlite3.connect('file:/proc/self/fd/'+str(pin)+('?mode=ro' if readonly else '?mode=rw'),
                            uri=True,isolation_level=None)
        after=_fds();candidates=[]
        for fd,physical in after.items():
            if fd in before: continue
            info=os.fstat(fd)
            try: header=os.pread(fd,16,0)
            except OSError: continue
            if header==b'SQLite format 3\x00' or info.st_size==0:
                candidates.append((fd,physical))
        if len(candidates)!=1 or candidates[0][1]!=tuple(identity):
            raise ValueError('SQLITE_ACTUAL_IDENTITY_MISMATCH')
        main_fd=candidates[0][0]
        def verify():
            check_path()
            current=os.stat(path,follow_symlinks=False)
            if (not stat.S_ISREG(current.st_mode) or current.st_uid!=os.getuid() or current.st_nlink!=1 or
                (current.st_dev,current.st_ino)!=tuple(identity)):
                raise ValueError('SQLITE_PATH_IDENTITY_DRIFT')
            for fd in (pin,main_fd):
                current=os.fstat(fd)
                if (current.st_dev,current.st_ino)!=tuple(identity) or current.st_nlink!=1:
                    raise ValueError('SQLITE_ACTUAL_IDENTITY_DRIFT')
        verify()
        # First SQL occurs only after the descriptor check. Keep the original
        # canonical pathname for SQLite's journal/WAL bookkeeping.
        main=[r[2] for r in conn.execute('PRAGMA database_list') if r[1]=='main']
        if main!=[str(path.resolve(strict=True))]: raise ValueError('SQLITE_MAIN_PATH_IDENTITY_MISMATCH')
        if readonly: conn.execute('PRAGMA query_only=ON')
        yield conn,verify
        verify()
    finally:
        if conn is not None: conn.close()
        os.close(pin)
