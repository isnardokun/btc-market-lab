"""Package A read-only, fail-closed source writer inventory attestation.

Computes a reproducible SHA-256 binding of checked-out sources that could
write the local database, all daily.sh-declared children, and cooperative
flock implementation. This is a STATIC review artifact, NEVER a permission
to run jobs, open a production database or certify global exclusion.
"""
import ast
import hashlib
import json
from pathlib import Path
import re

from scripts.market_r10e13_writer_adoption_inventory import (
    ENTRYPOINTS, GLOBAL_NAMESPACE, PYTHON_CALL, SCAN_DIRS, WRITE_SQL,
)
from scripts.package_a_writer_adoption_review import (
    source_only_writer_adoption_review,
)

PROTECT_SOURCE = (
    "scripts/daily.sh",
    "scripts/market_r10e11_writer_exclusion.py",
    "scripts/package_a_writer_guard.py",
    "scripts/package_a_parent_lock.py",
    "scripts/package_a_writer_cutover.py",
    "scripts/package_a_writer_adoption_review.py",
    "scripts/market_r10e13_writer_adoption_inventory.py",
)
DANGEROUS_LIBRARY_CALLS = frozenset({
    "raw_payload","store_derivative","store_etf","store_event",
    "save_cursor","ensure_archive_schema","attach_available_triggers",
    "register_dataset","save_window","archive_news","commit",
    "executescript","executemany",
})
EXPLICIT_DIRECT_WRITE = re.compile(
    r"^\s*(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|VACUUM|"
    r"ATTACH|DETACH)\b",re.I,
)
_MAX_SOURCE_BYTES = 2_000_000
_MAX_INDEXED_FILES = 256


def _read_trusted_source(root, path):
    """Read only bounded checked-out files, reject symlinks in path parents."""
    base=Path(root).resolve(strict=True)
    target=base / path
    if (not isinstance(path,str) or
            not path or path.startswith("/") or
            any(p in ("", ".", "..") for p in Path(path).parts)):
        raise ValueError("BLOCKED_WRITER_SOURCE_PATH")
    if any(piece.is_symlink() for piece in (
            base / Path(*Path(path).parts[:i])
            for i in range(1,len(Path(path).parts)+1))):
        raise ValueError("BLOCKED_WRITER_SOURCE_SYMLINK")
    if not target.is_file() or target.stat().st_size>_MAX_SOURCE_BYTES:
        raise ValueError("BLOCKED_WRITER_SOURCE_MISSING_OR_OVERSIZED")
    try:
        content=target.read_bytes()
        if len(content)>_MAX_SOURCE_BYTES:
            raise ValueError("BLOCKED_WRITER_SOURCE_MISSING_OR_OVERSIZED")
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("BLOCKED_WRITER_SOURCE_NOT_UTF8") from exc


def _write_signals(source):
    """Conservative source-only AST, without importing or running source."""
    tree=ast.parse(source)
    mutation_literals=set()
    called_names=set()
    has_connect=False
    has_execute=False
    for n in ast.walk(tree):
        if isinstance(n,ast.Constant) and isinstance(n.value,str):
            for line in n.value.splitlines():
                if EXPLICIT_DIRECT_WRITE.match(line):
                    verb=line.strip().split(maxsplit=1)[0].upper()
                    mutation_literals.add(verb)
        if isinstance(n,ast.Call):
            fn=n.func
            if isinstance(fn,ast.Attribute):
                called_names.add(fn.attr)
                has_connect|=fn.attr=="connect"
                has_execute|=fn.attr in ("execute","executemany","executescript")
            elif isinstance(fn,ast.Name):
                called_names.add(fn.id)
    indirect=sorted(called_names & DANGEROUS_LIBRARY_CALLS)
    return {
        "direct_sql_mutation_verbs":sorted(mutation_literals),
        "potential_indirect_write_calls":indirect,
        "sqlite_connect_call_detected":has_connect,
        "execute_call_detected":has_execute,
        "possible_writer":bool(mutation_literals or indirect),
        "read_only_certified":False,
    }


def snapshot_source_writer_routes(root):
    """Attest all source-discoverable writer contracts with no executable I/O.

    This does not look at active cron/services, symlinked repo aliases,
    database permissions, physical lock inodes, dynamic subprocess targets,
    or manual SQLite console access. A caller must not treat the digest
    as authorization for a production cutover.
    """
    if not isinstance(root,(str,Path)):
        raise ValueError("Explicit source checkout required")
    base=Path(root)
    if base.is_symlink() or not base.is_dir():
        raise ValueError("Unsafe source root")
    review=source_only_writer_adoption_review(base)
    known={entry["source"]:entry for entry in review["registered_writer_paths"]}
    if set(known)!=set(ENTRYPOINTS):
        raise ValueError("BLOCKED_WRITER_INVENTORY_DRIFT")
    unknown={entry["source"] for entry in review["unknown_potential_writers"]}
    daily_text=_read_trusted_source(base,"scripts/daily.sh")
    declared=set(PYTHON_CALL.findall(daily_text))
    rootfiles=set(PROTECT_SOURCE)|set(known)|unknown|declared
    if not rootfiles or len(rootfiles)>_MAX_INDEXED_FILES:
        raise ValueError("BLOCKED_WRITER_SOURCE_FILE_BUDGET")
    by_file={}
    for rel in sorted(rootfiles):
        content=_read_trusted_source(base,rel)
        signals=_write_signals(content) if rel.endswith(".py") else None
        if rel in known:
            mode=known[rel]["role"]
            status=known[rel]["static_status"]
            cli_optin=known[rel]["explicit_optin_argument"]
            guard_call=known[rel]["guard_function_called"]
        elif rel=="scripts/daily.sh":
            mode="daily_shell_parent"
            status="PARENT_FLOCK_CANDIDATE_UNDEPLOYED"
            cli_optin=False
            guard_call=False
        elif rel in unknown:
            mode="unknown_potential_writer"
            status="UNCLASSIFIED_POTENTIAL_WRITER"
            cli_optin=False
            guard_call=False
        elif rel in declared:
            mode="unclassified_daily_child"
            status="UNCLASSIFIED_DAILY_CHILD"
            cli_optin=False
            guard_call=False
        else:
            mode="lock_implementation_or_static_inventory"
            status="SOURCE_ONLY_NOT_OPERATIONAL"
            cli_optin=False
            guard_call=False
        by_file[rel]={
            "sha256":hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "bytes":len(content.encode("utf-8")),
            "role":mode,"status":status,
            "invoked_by_daily_shell":rel in declared,
            "explicit_cli_optin":cli_optin,
            "guard_call_ast_detected":guard_call,
            "write_signals":signals,
            "production_writer_verified":False,
        }

    # Dynamic shell invocations can't be certified by static regex. This
    # checks common unparsed forms only; unknown shell syntax remains unsafe.
    active_lines=[line.strip() for line in daily_text.splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    dynamic=[line[:160] for line in active_lines
             if ("eval " in line or "python3 -c" in line or
                 "python -m " in line or "python3 -m " in line or
                 "source " in line or
                 re.search(r"\bpython3?\s+[\"']?\$",line))]
    unsafe_daily=sorted(
        p for p in declared if
        p not in known or not (known[p]["explicit_optin_argument"] and
                              known[p]["guard_function_called"])
    )
    # Do not falsely claim daily.sh is protected because it contains
    # '--global-lock-dir' in a comment or the CLI's own help text.
    raw= {
        "global_writer_namespace":GLOBAL_NAMESPACE,
        "schema":"PACKAGE_A_WRITER_SOURCE_ATTESTATION_V1",
        "file_sha256":{p:by_file[p]["sha256"] for p in sorted(by_file)},
        "daily_declared_python_children":sorted(declared),
        "daily_unverified_children":unsafe_daily,
        "unclassified_writer_files":sorted(unknown),
        "dynamic_shell_execution_candidates":dynamic,
    }
    digest=hashlib.sha256(json.dumps(
        raw,ensure_ascii=True,sort_keys=True,separators=(",",":")
    ).encode("utf-8")).hexdigest()
    return {
        "state":"STATIC_WRITER_SOURCE_SNAPSHOT_ONLY",
        "attestation_sha256":digest,
        "attested_file_count":len(by_file),
        "registered_writer_contracts":len(known),
        "registered_static_optin_candidates":len(
            review["optin_candidate_paths"]),
        "registered_without_static_optin":len(
            review["missing_or_unverified_optin_paths"]),
        "daily_python_children":sorted(declared),
        "daily_unverified_children":unsafe_daily,
        "dynamic_shell_execution_candidates":dynamic,
        "unclassified_writer_files":sorted(unknown),
        "by_file":by_file,
        "source_root_examined_only":True,
        "runtime_cron_verified":False,
        "all_writer_calls_guarded":False,
        "shared_physical_lock_inode_verified":False,
        "all_operational_writers_coordinated":False,
        "external_network_accessed":False,
        "database_opened":False,
        "production_authorized":False,
        "owner_host_review_required":True,
    }


def compare_writer_source_snapshots(before,after):
    """Pin an offline reviewed snapshot; fail CLOSED when sources drift."""
    for obj in (before,after):
        if (not isinstance(obj,dict) or
                obj.get("state")!="STATIC_WRITER_SOURCE_SNAPSHOT_ONLY" or
                not isinstance(obj.get("by_file"),dict) or
                not isinstance(obj.get("attestation_sha256"),str)):
            raise ValueError("BLOCKED_WRITER_SNAPSHOT_INVALID")
    old,new=before["by_file"],after["by_file"]
    changed=sorted(
        p for p in old.keys() & new.keys()
        if old[p]["sha256"]!=new[p]["sha256"])
    added=sorted(new.keys()-old.keys())
    removed=sorted(old.keys()-new.keys())
    matched=(not changed and not added and not removed and
             before["attestation_sha256"]==after["attestation_sha256"])
    return {
        "state":"OFFLINE_SOURCE_SNAPSHOT_MATCH" if matched
                else "BLOCKED_OFFLINE_WRITER_SOURCE_DRIFT",
        "matched":matched,
        "changed_files":changed,
        "added_files":added,
        "removed_files":removed,
        "original_attestation_sha256":before["attestation_sha256"],
        "current_attestation_sha256":after["attestation_sha256"],
        "host_writer_exclusion_verified":False,
        "permission_to_execute_daily":False,
        "live_http_requests":0,
        "production_authorized":False,
    }
