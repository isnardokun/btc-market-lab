"""Package A shared opt-in lock boundary for legacy historical writer CLIs.

This helper does NOT activate the lock for existing unsuffixed commands and
does NOT authorize API or SQLite access. ALL cooperating writers must share
a physical lock file (same directory + namespace). It is advisory.
"""
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy

GLOBAL_WRITER_NAMESPACE = "btc-research-global-writer-v1"


def run_with_optional_writer_lock(lock_dir, worker, *, label):
    """Invoke a zero-argument worker inside OS flock when explicitly selected.

    Must be called BEFORE any network or SQLite access on the protected apply
    path. Falsey/None must not be used as a directory shortcut: only None
    denotes legacy unguarded behavior; invalid paths fail closed.
    """
    if not callable(worker):
        raise TypeError("Callable ingestion worker required")
    if not isinstance(label, str) or not label or len(label) > 64:
        raise ValueError("Named writer required")
    if lock_dir is None:
        return worker()
    try:
        with hold_writer_lock(lock_dir, namespace=GLOBAL_WRITER_NAMESPACE):
            return worker()
    except WriterLockBusy:
        print("BLOCKED_WRITER_BUSY: " + label + " did not start SQL or HTTP",
              file=sys.stderr)
        return 3
