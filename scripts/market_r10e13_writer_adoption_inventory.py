"""R10-E.13: fail-closed STATIC inventory of potential SQLite writer entrypoints.

Reads only repository SOURCE TEXT; never opens SQLite, databases, WAL, APIs,
cron, subprocess or credentials. Static detection cannot prove that *all*
running writers cooperate. The output is an adoption backlog, NOT an approval.
"""
import ast
from pathlib import Path
import re

GLOBAL_NAMESPACE = "btc-research-global-writer-v1"
LOCK_IMPORT = "scripts.market_r10e11_writer_exclusion"
SCAN_DIRS = ("ingestion", "scripts", "storage", "validation", "rendering", "analysis")
WRITE_SQL = re.compile(r"^\s*(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", re.I)
PYTHON_CALL = re.compile(r"python3?\s+(?:-u\s+)?([a-zA-Z0-9_./-]+\.py)")
# Every item is a code-review candidate, not proof of a running process.
# Keep indirect writers and manual tools separate from daily.sh children.
ENTRYPOINTS = {
    "ingestion/ingest_price.py": "daily",
    "ingestion/ingest.py": "daily",
    "ingestion/ingest_fred.py": "daily",
    "ingestion/ingest_all.py": "manual",
    "ingestion/bitview_history.py": "manual",
    "ingestion/yahoo_history.py": "manual",
    "ingestion/researchbitcoin_archive.py": "opt_in_daily",
    "ingestion/researchbitcoin_v2.py": "manual",
    "ingestion/backfill_metrics.py": "manual",
    "ingestion/backfill_onchain_metrics.py": "manual",
    "ingestion/snapshot.py": "daily",
    "ingestion/news_archive.py": "library",
    "scripts/market_context_ingest.py": "opt_in_daily",
    "scripts/market_history_batch.py": "manual",
    "scripts/market_history_forward.py": "manual",
    "scripts/market_history_pilot.py": "manual",
    "scripts/market_context_migrate.py": "migration",
    "scripts/archive_db_migrate.py": "migration",
    "validation/archive_metrics.py": "daily",
    "rendering/archive_report.py": "daily",
    "rendering/export_dashboard.py": "daily",
    "ingestion/market_context_sources.py": "library",
    "storage/repositories.py": "library",
    "storage/archive_schema.py": "library",
}
NONPROD_PREFIXES = ("market_r10e",)  # previously approved ONLY synthetic harnesses


def _detect_source(source):
    """Return AST evidence, never execute import, code or any SQL."""
    tree = ast.parse(source)
    module_imported = False
    found_lock_call = False
    found_global_namespace_arg = False
    direct_connect = False
    mutation_lexemes = False
    import_lock = False
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == LOCK_IMPORT:
            import_lock = any(x.name == "hold_writer_lock" for x in n.names)
        if isinstance(n, ast.Import):
            module_imported |= any(x.name == "sqlite3" for x in n.names)
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "connect":
                if isinstance(f.value, ast.Name) and f.value.id == "sqlite3":
                    direct_connect = True
            if isinstance(f, ast.Name) and f.id == "hold_writer_lock":
                found_lock_call = True
                for kw in n.keywords:
                    if (kw.arg == "namespace" and isinstance(kw.value, ast.Constant)
                            and kw.value.value == GLOBAL_NAMESPACE):
                        found_global_namespace_arg = True
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if WRITE_SQL.search(n.value):
                mutation_lexemes = True
    return {
        "sqlite3_imported": module_imported,
        "direct_sqlite_connect": direct_connect,
        "sql_mutation_literal": mutation_lexemes,
        "lock_import_detected": import_lock,
        "lock_call_detected": found_lock_call,
        "literal_global_namespace_detected": found_global_namespace_arg,
    }


def scan_writer_adoption_source_tree(root):
    """Inspect explicit checked-out repository root WITHOUT filesystem side effects."""
    if not isinstance(root, (str, Path)) or not str(root):
        raise ValueError("Explicit source-only repository root required")
    base = Path(root)
    if (base.is_symlink() or not base.is_dir()
            or not (base / "scripts" / "daily.sh").is_file()
            or not (base / "ingestion" / "config.py").is_file()):
        raise ValueError("Incomplete/unsafe source tree")
    scripts_daily = base / "scripts" / "daily.sh"
    if scripts_daily.is_symlink():
        raise ValueError("Refusing linked shell entrypoint")
    shell = scripts_daily.read_text(encoding="utf-8")
    invoked = set(PYTHON_CALL.findall(shell))
    manifest = []
    blocked = []
    for relpath, kind in sorted(ENTRYPOINTS.items()):
        file = base / relpath
        if not file.is_file() or file.is_symlink():
            raise ValueError("Missing/linked writer contract: " + relpath)
        source = file.read_text(encoding="utf-8")
        evidence = _detect_source(source)
        lock_reference = (
            evidence["lock_import_detected"]
            and evidence["lock_call_detected"]
            and evidence["literal_global_namespace_detected"]
        )
        status = ("STATIC_GLOBAL_LOCK_REFERENCE_UNVERIFIED" if lock_reference
                  else "GLOBAL_LOCK_ADOPTION_MISSING")
        # A single AST call doesn't prove every write callsite is covered.
        # Every entrypoint remains blocked until a separate runtime review.
        manifest.append({
            "source": relpath, "role": kind,
            "invoked_by_daily_shell": relpath in invoked,
            "lock_status": status,
            "runtime_writer_exclusion_verified": False,
            "may_require_a_cooperative_lock": True,
            "static_signals": evidence,
        })
        blocked.append(relpath)

    # Detect additional modules that may contain WRITE SQL or open SQLite.
    # Discovery is heuristic: flag unknown candidates, never certify completeness.
    unknown = []
    for folder in SCAN_DIRS:
        directory = base / folder
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("Missing scan directory: " + folder)
        for path in sorted(directory.rglob("*.py")):
            if path.is_symlink() or not path.is_file():
                raise ValueError("Linked/non-file source in inventory")
            rel = path.relative_to(base).as_posix()
            if (rel in ENTRYPOINTS or
                    (rel.startswith("scripts/") and
                     path.name.startswith(NONPROD_PREFIXES))):
                continue
            evidence = _detect_source(path.read_text(encoding="utf-8"))
            if evidence["direct_sqlite_connect"] or evidence["sql_mutation_literal"]:
                unknown.append({"source": rel, "classification": "REVIEW_POTENTIAL_WRITER",
                                "static_signals": evidence})
    for rel in sorted(invoked):
        if rel.startswith(("ingestion/", "scripts/", "validation/", "rendering/", "analysis/")):
            if rel not in ENTRYPOINTS and not any(x["source"] == rel for x in unknown):
                unknown.append({"source":rel, "classification":"REVIEW_SHELL_INVOKED_UNKNOWN",
                                "static_signals":None})
    unknown.sort(key=lambda x: x["source"])

    return {
        "state": "STATIC_WRITER_ADOPTION_INVENTORY_ONLY",
        "decision": "BLOCKED_UNVERIFIED_GLOBAL_WRITER_ADOPTION",
        "global_namespace_required": GLOBAL_NAMESPACE,
        "inventoried_writer_contracts": len(manifest),
        "registered_writer_paths": manifest,
        "unknown_writer_candidates": unknown,
        "daily_shell_script_references": sorted(invoked),
        "blocked_writer_paths": blocked,
        "all_runtime_writer_exclusion_verified": False,
        "operational_writer_discovery_complete": False,
        "deployment_authorized": False,
        "real_api_authorized": False,
        "sqlite_database_opened": False,
        "http_requests": 0,
        "sqlite_writes": 0,
        "historical_completeness": "NOT_VERIFIED",
        "limits": (
            "AST finds literal SQLite operations only; dynamic calls and subprocesses may be missed",
            "Code references to flock do not prove all callsites are protected",
            "An uncooperative legacy writer can bypass flock",
            "Real host processes, cron jobs and alternate repo copies remain uninspected",
        ),
    }
