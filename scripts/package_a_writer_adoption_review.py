"""Package A source-only inventory for cooperative writer-lock adoption.

This is a static CODE REVIEW AID, never a proof of operational safety.
It does not import any source module, call SQLite, touch runtime paths,
request APIs, inspect running processes, or change daily.sh/cron.
"""
import ast
from pathlib import Path

from scripts.market_r10e13_writer_adoption_inventory import (
    scan_writer_adoption_source_tree,
)

FLAG = "--global-lock-dir"
HELPERS = frozenset({"run_with_optional_writer_lock", "hold_writer_lock"})


def _ast_optin_signals(source):
    """Detect a real add_argument() and guard CALL; ignore docstrings/comments."""
    tree = ast.parse(source)
    flag = False
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "add_argument"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == FLAG):
            flag = True
        if isinstance(fn, ast.Name) and fn.id in HELPERS:
            guarded = True
    return {"explicit_optin_argument": flag, "guard_function_called": guarded}


def source_only_writer_adoption_review(root):
    """Explain remaining blockers with no false certified/full-coverage claims."""
    legacy = scan_writer_adoption_source_tree(root)
    base = Path(root)
    script = (base / "scripts" / "daily.sh").read_text(encoding="utf-8")
    # A guarded CLI that is never passed its opt-in flag by daily.sh is
    # still UNSAFE in daily execution. Never infer operational protection
    # from a helper import, AST invocation or an existing lock directory.
    daily_flag_configured = FLAG in script
    manifest = []
    candidates = []
    unresolved = []
    daily_unguarded = []
    for old in legacy["registered_writer_paths"]:
        path = old["source"]
        signals = _ast_optin_signals((base / path).read_text(encoding="utf-8"))
        static_candidate = all(signals.values())
        row = {
            "source": path,
            "role": old["role"],
            "invoked_by_daily_shell": old["invoked_by_daily_shell"],
            **signals,
            "static_status": (
                "STATIC_OPTIN_GUARD_CANDIDATE" if static_candidate
                else "UNGUARDED_OR_UNVERIFIED_ENTRYPOINT"
            ),
            "same_physical_lock_path_verified": False,
            "active_runtime_execution_verified": False,
            "production_writer_protected": False,
            "production_authorized": False,
        }
        manifest.append(row)
        if static_candidate:
            candidates.append(path)
        else:
            unresolved.append(path)
        if row["invoked_by_daily_shell"] and not daily_flag_configured:
            daily_unguarded.append(path)
    return {
        "state": "STATIC_WRITER_ADOPTION_REVIEW_ONLY",
        "decision": "BLOCKED_UNVERIFIED_GLOBAL_WRITER_ADOPTION",
        "registered_writer_contracts": len(manifest),
        "optin_candidate_paths": sorted(candidates),
        "missing_or_unverified_optin_paths": sorted(unresolved),
        "daily_shell_unguarded_registered_paths": sorted(daily_unguarded),
        "daily_shell_optin_flag_present": daily_flag_configured,
        "unknown_potential_writers": legacy["unknown_writer_candidates"],
        "registered_writer_paths": manifest,
        "all_operational_writers_coordinated": False,
        "global_lock_file_identity_verified": False,
        "production_authorized": False,
        "production_db_opened": False,
        "live_http_requests": 0,
        "historical_completeness": "NOT_VERIFIED",
    }
