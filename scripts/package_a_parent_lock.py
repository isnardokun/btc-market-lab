"""Unforgeable-by-boolean parent flock lease for Package A offline recovery.

Only the context manager actually acquiring the operating-system lock may
grant a child module permission to reuse the lock. Uses Python ContextVar to
bind the lease to the executing thread/context. This is a disposable-fixture
safety guard, not proof of production-wide writer participation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from scripts.market_r10e11_writer_exclusion import hold_writer_lock

_NAMESPACE="btc-research-global-writer-v1"
_ACTIVE_LEASE=ContextVar("package_a_offline_parent_flock_lease",default=None)


@contextmanager
def hold_offline_parent_lease(lock_directory):
    """Acquire real OS flock before granting a child its reuse lease."""
    root=Path(lock_directory).resolve(strict=True)
    with hold_writer_lock(lock_directory,namespace=_NAMESPACE) as evidence:
        # A boolean or borrowed root path is not enough: every nested fake
        # page must prove that the parent's original lock inode is still the
        # physical lock-file pathname before a new SQL commit.
        evidence["check_identity"]()
        token=_ACTIVE_LEASE.set((root,evidence["check_identity"]))
        try:
            yield
            evidence["check_identity"]()
        finally:
            _ACTIVE_LEASE.reset(token)


def verify_offline_parent_lease(lock_directory):
    """Reject a raw _parent_lock_held=True flag without a real held lease."""
    value=_ACTIVE_LEASE.get()
    if (not isinstance(value,tuple) or len(value)!=2 or
            value[0]!=Path(lock_directory).resolve(strict=True) or
            not callable(value[1])):
        raise ValueError("BLOCKED_OFFLINE_PARENT_FLOCK_LEASE_REQUIRED")
    value[1]()
    return True
