"""Package A OFFLINE-only parent writer-lock candidate and cutover manifest.

Never launches the repository's real daily.sh, cron, API clients or production
SQLite. Demonstrates one global parent flock covering all unmodified child
processes with throwaway marked Python fixtures. A separate operator-approved
production launcher/cutover is intentionally NOT implemented here.
"""
from pathlib import Path
import os
import subprocess
import sys

from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy
from scripts.package_a_writer_adoption_review import source_only_writer_adoption_review
from scripts.market_r10e13_writer_adoption_inventory import PYTHON_CALL

NAMESPACE = "btc-research-global-writer-v1"
MARKER = ".package-a-locked-child-fixture"
MARKER_TEXT = "DISPOSABLE PACKAGE A CHILD LOCK FIXTURE\n"


def plan_parent_locked_daily(repo_root):
    """Produce a source-only design; neither schedule nor execute anything."""
    root = Path(repo_root)
    analysis = source_only_writer_adoption_review(root)
    shell = (root / "scripts" / "daily.sh").read_text(encoding="utf-8")
    calls = sorted(set(PYTHON_CALL.findall(shell)))
    existing = {r["source"]: r for r in analysis["registered_writer_paths"]}
    known = [path for path in calls if path in existing]
    unclassified = [path for path in calls if path not in existing]
    # Parent holds the ONE global lock for the entire unmodified daily child
    # lifetime. Do NOT pass --global-lock-dir into daily children as well,
    # because a nested independent flock on that same lock can deadlock/fail.
    return {
        "state": "SOURCE_ONLY_DAILY_LOCK_CUTOVER_CANDIDATE",
        "candidate_strategy": "PARENT_FLOCK_ACROSS_UNMODIFIED_DAILY_CHILDREN",
        "candidate_command": ["bash", "scripts/daily.sh"],
        "parent_lock_namespace": NAMESPACE,
        "daily_declared_python_children": calls,
        "registered_writer_children": known,
        "unclassified_daily_children": unclassified,
        "registered_writer_contracts": analysis["registered_writer_contracts"],
        "indirect_unknown_writers": analysis["unknown_potential_writers"],
        "known_unprotected_manual_or_library_writers": analysis[
            "missing_or_unverified_optin_paths"],
        "daily_shell_unchanged": True,
        "avoid_nested_child_lock": True,
        "requires_explicit_operator_deployment": True,
        "runtime_invocations_checked": False,
        "host_cron_checked": False,
        "alternate_repo_copies_checked": False,
        "physical_lock_inode_verified_on_host": False,
        "all_writers_cooperating": False,
        "production_authorized": False,
        "source_network_or_sql_executed": False,
        "external_history_verified": False,
    }


def _fixture_guard(root, lock_dir):
    if not isinstance(root, (str, Path)) or not isinstance(lock_dir, (str, Path)):
        raise ValueError("Explicit offline fixture root and locks required")
    base = Path(root)
    locks = Path(lock_dir)
    marker = base / MARKER
    worker = base / "worker.py"
    if (not base.name.startswith("package-a-child-")
            or any(p.is_symlink() for p in (base, locks, marker, worker))
            or not base.is_dir() or not locks.is_dir()
            or locks.resolve() != base.resolve() / "locks"
            or not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_TEXT
            or not worker.is_file()):
        raise ValueError("Refusing unmarked or non-disposable process fixture")
    return base.resolve(), worker.resolve()


def run_offline_parent_locked_child(root, lock_dir, *, timeout=4):
    """Run ONLY an explicit marked Python test child under parent flock.

    No arbitrary argv, DB pathname, remote URL, source import or schedule.
    The child is executable test input controlled by the disposable fixture.
    """
    base, worker = _fixture_guard(root, lock_dir)
    if type(timeout) not in (int, float) or not 0 < timeout <= 10:
        raise ValueError("Strict offline child timeout required")
    try:
        with hold_writer_lock(lock_dir, namespace=NAMESPACE):
            # Child closes parent's lock FD. The parent's flock is held until
            # the process exits; child never attempts nested flock acquisition.
            result = subprocess.run(
                [sys.executable, "-B", str(worker)],
                cwd=base, env={"PATH": os.environ.get("PATH", ""),
                               "PYTHONDONTWRITEBYTECODE": "1"},
                close_fds=True, capture_output=True, text=True,
                timeout=timeout, check=False)
            return {"state": "OFFLINE_CHILD_COMPLETED" if result.returncode == 0
                    else "OFFLINE_CHILD_FAILED",
                    "exit_code": result.returncode,
                    "parent_held_single_cooperative_flock": True,
                    "nested_child_lock_requested": False,
                    "production_authorized": False,
                    "real_api_requests": 0,
                    "productive_sqlite_access": False}
    except WriterLockBusy:
        return {"state": "BLOCKED_WRITER_BUSY", "child_started": False,
                "productive_sqlite_access": False,
                "real_api_requests": 0, "production_authorized": False}
    except subprocess.TimeoutExpired:
        return {"state": "OFFLINE_CHILD_TIMEOUT", "child_killed_by_timeout": True,
                "productive_sqlite_access": False,
                "real_api_requests": 0, "production_authorized": False}
