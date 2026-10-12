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
    owner_pid = os.getpid()
    try:
        parent_fd = os.open(directory, dir_flags)
        parent_stat = os.fstat(parent_fd)
        # The lock-file inode cannot be protected if another user can
        # unlink and recreate it in a shared writable directory. Owner-only
        # (for writes) lock folders prevent this common cooperative-lock
        # bypass; e.g. mode 0755 is fine, mode 0775 or 1777 is not.
        if (not stat.S_ISDIR(parent_stat.st_mode)
                or parent_stat.st_uid != os.getuid()
                or stat.S_IMODE(parent_stat.st_mode) & 0o022):
            raise WriterLockUnsafe(
                "Lock directory must be owner-controlled, not shared writable")
        # Path may have been swapped between is_dir() and os.open(); require
        # the directory still refers to the same physical inode.
        path_parent = os.stat(directory, follow_symlinks=False)
        if (not stat.S_ISDIR(path_parent.st_mode)
                or (path_parent.st_dev,path_parent.st_ino)!=
                    (parent_stat.st_dev,parent_stat.st_ino)):
            raise WriterLockUnsafe("Lock directory pathname was replaced")
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

        def verify_identity():
            """Verify held FD still has the SAME publicly reachable inode.

            Check at every important HTTP/SQL boundary: no filesystem check
            can eliminate a hostile race between checks, and flock remains
            advisory for processes that do not cooperate.
            """
            # flock and the open-file description are inherited by fork.
            # The parent's ContextVar/closure is copied too, but cannot
            # grant a child a lease after the parent releases its lock.
            if os.getpid() != owner_pid:
                raise WriterLockUnsafe("BLOCKED_WRITER_LOCK_INHERITED_PROCESS")
            own_parent=os.fstat(parent_fd)
            own_lock=os.fstat(fd)
            try:
                current_parent=os.stat(directory,follow_symlinks=False)
                current_lock=os.stat(namespace+".lock",dir_fd=parent_fd,
                                     follow_symlinks=False)
            except FileNotFoundError as exc:
                raise WriterLockUnsafe(
                    "BLOCKED_WRITER_LOCK_INODE_DISAPPEARED") from exc
            if (not stat.S_ISDIR(current_parent.st_mode)
                    or (current_parent.st_dev,current_parent.st_ino)!=
                       (own_parent.st_dev,own_parent.st_ino)
                    or own_parent.st_uid!=os.getuid()
                    or stat.S_IMODE(own_parent.st_mode)&0o022):
                raise WriterLockUnsafe(
                    "BLOCKED_WRITER_LOCK_DIRECTORY_REPLACED")
            if (not stat.S_ISREG(current_lock.st_mode)
                    or (current_lock.st_dev,current_lock.st_ino)!=
                       (own_lock.st_dev,own_lock.st_ino)
                    or own_lock.st_nlink!=1
                    or current_lock.st_uid!=os.getuid()
                    or stat.S_IMODE(current_lock.st_mode)&0o077):
                raise WriterLockUnsafe(
                    "BLOCKED_WRITER_LOCK_INODE_REPLACED")
            return True

        verify_identity()
        lease = {"state": "COOPERATIVE_LOCK_HELD",
                 "namespace": namespace,
                 "physical_lock_device": info.st_dev,
                 "physical_lock_inode": info.st_ino,
                 "owner_pid": owner_pid,
                 "check_identity": verify_identity,
                 "excludes_uncooperative_writers": False,
                 "production_authorized": False}
        try:
            yield lease
        except BaseException:
            # Never suppress/mask the caller's original failure; the lock FD
            # will still be unlocked and closed in the outer finally.
            raise
        else:
            # A rename or unlink during the body is surfaced, NOT accepted
            # as a successful cooperative writer transaction.
            verify_identity()
    finally:
        # A forked child's LOCK_UN would release the parent's flock on
        # their shared open-file description. Close only the child's copy;
        # the acquiring process alone may explicitly unlock it.
        if locked and os.getpid() == owner_pid:
            fcntl.flock(fd, fcntl.LOCK_UN)
        if fd is not None:
            os.close(fd)
        if parent_fd is not None:
            os.close(parent_fd)
