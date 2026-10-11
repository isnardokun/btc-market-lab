"""R10-E.11: reusable single-writer FILE lock, independent of SQLite/API.

This is a reusable Unix/Linux primitive, NOT integrated into any production
ingestor or scheduler by this PR. All cooperating writers MUST explicitly
use the SAME lock pathname; flock is advisory, not a proof that legacy jobs
are excluded. Tests only use TemporaryDirectory with synthetic child processes.
"""
from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import re
import stat

_NAMESPACE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class WriterLockBusy(RuntimeError):
    """Another cooperating process owns this namespace's lock."""


class WriterLockUnsafe(ValueError):
    """Lock path or permissions are not safe for a cooperative writer."""


@contextmanager
def hold_writer_lock(lock_directory, *, namespace="market-history-v3"):
    """Acquire a fail-closed exclusive NONBLOCKING lock until context exit.

    The parent directory MUST already exist and be explicitly supplied.
    File is mode 0600 on first creation. Never unlink the lock file:
    deleting and recreating it under competing processes breaks exclusion.
    No SQLite or HTTP occurs in this helper.
    """
    if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
        raise WriterLockUnsafe("Invalid lock namespace")
    if not isinstance(lock_directory, (str, Path)) or not str(lock_directory):
        raise WriterLockUnsafe("Explicit lock directory required")
    directory = Path(lock_directory)
    if not directory.is_dir() or directory.is_symlink():
        raise WriterLockUnsafe("Lock directory must already exist and not be a symlink")

    dir_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    open_flags = (os.O_RDWR | os.O_CREAT | os.O_NONBLOCK |
                  getattr(os, "O_CLOEXEC", 0) |
                  getattr(os, "O_NOFOLLOW", 0))
    parent_fd = None
    fd = None
    locked = False
    try:
        parent_fd = os.open(directory, dir_flags)
        parent_stat = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise WriterLockUnsafe("Parent is not a directory")
        fd = os.open(namespace + ".lock", open_flags, 0o600, dir_fd=parent_fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise WriterLockUnsafe("Lock file must be private, owned, single-link regular")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise WriterLockBusy("Writer lock already held") from exc
            raise
        locked = True
        yield {"state": "COOPERATIVE_LOCK_HELD",
               "namespace": namespace,
               "excludes_uncooperative_writers": False,
               "production_authorized": False}
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        if fd is not None:
            os.close(fd)
        if parent_fd is not None:
            os.close(parent_fd)
