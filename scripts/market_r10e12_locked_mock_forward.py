"""R10-E.12: test E.11 global cooperative lock over REAL v3 forward code,
with fixture-only SQLite and injected fake HTTP. NO CLI or production path.

The explicit fixture marker and temporary-directory prefix are defense-in-depth
against accidental use with a production database. Not a production wrapper.
All existing writers must adopt a global lock before live rollout.
"""
import datetime as dt
import sqlite3
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import market_history_forward as forward
from scripts import market_raw_reconcile as raw_audit
from scripts import market_temporal_quality as temporal_audit
from scripts import market_request_lineage_audit as lineage_audit
from scripts.market_r10e3_mock_e2e import (
    BASE, CLOCK, TABLES, _body, _endpoint, _fingerprints, _timestamp_ms,
)
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy

MARKER = ".r10e12-synthetic-only"
MARKER_CONTENT = "R10-E12 SYNTHETIC SQLITE ONLY\n"
LOCK_NAMESPACE = "btc-research-global-writer-v1"
SCENARIOS = frozenset(("success", "empty", "http429", "gap"))
PROVIDERS = frozenset(("binance", "bybit"))


def _check_fixture(db_path, lock_directory):
    if not isinstance(db_path, (str, Path)) or not isinstance(lock_directory, (str, Path)):
        raise ValueError("Explicit synthetic fixture paths required")
    path = Path(db_path)
    root = path.parent
    directory = Path(lock_directory)
    if (path.name != "synthetic.db" or not root.name.startswith("r10e12-synthetic-")
            or path.is_symlink() or root.is_symlink()
            or directory.is_symlink()):
        raise ValueError("Only R10-E12 synthetic fixture locations permitted")
    if not path.is_file() or not directory.is_dir():
        raise ValueError("Synthetic SQLite and lock directory must already exist")
    if directory.resolve() != root.resolve() / "locks":
        raise ValueError("Lock directory must be adjacent to synthetic SQLite")
    marker = root / MARKER
    if (not marker.is_file() or marker.is_symlink()
            or marker.read_text(encoding="utf-8") != MARKER_CONTENT):
        raise ValueError("Synthetic fixture marker missing or invalid")
    # Block obvious productive WAL/SHM inputs, which the tests never create.
    if (path.with_name(path.name + "-wal").exists()
            or path.with_name(path.name + "-shm").exists()):
        raise ValueError("Active WAL fixture not supported by this mock harness")
    return path


def _auditors(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as ro:
        ro.execute("PRAGMA query_only=ON")
        ro.execute("PRAGMA foreign_keys=ON")
        ro.execute("BEGIN")
        raw = raw_audit.audit(path, connection=ro)
        temporal = temporal_audit.inspect(path, now=CLOCK, connection=ro)
        lineage = lineage_audit.audit(path, connection=ro)
        integrity = ro.execute("PRAGMA integrity_check").fetchone()[0]
        fks = len(ro.execute("PRAGMA foreign_key_check").fetchall())
        ro.rollback()
    return {
        "RAW": raw.get("state"),
        "TEMPORAL": temporal.get("quality_state"),
        "LINEAGE": lineage.get("quality_state"),
        "SQLITE": integrity,
        "FOREIGN_KEY_ERRORS": fks,
    }


def run_locked_mock_forward(db_path, lock_directory, *,
                            provider="binance", scenario="success"):
    """One mocked v3 page only after global advisory lock is acquired.

    No endpoint is fetched from the network. The only callable passed to
    forward.execute is the local fake_fetch closure below. When the lock is
    busy, no SQLite connection is established and no fake fetch is called.
    """
    if provider not in PROVIDERS or scenario not in SCENARIOS:
        raise ValueError("Unsupported synthetic scenario/provider")
    path = _check_fixture(db_path, lock_directory)
    try:
        with hold_writer_lock(lock_directory, namespace=LOCK_NAMESPACE) as held:
            if held["production_authorized"] is not False:
                raise AssertionError("Lock cannot grant production authorization")

            called = []
            next_bar = BASE + dt.timedelta(minutes=5)
            endpoint = _endpoint(provider, "open_interest")
            page_limit = 500 if provider == "binance" else 200

            def fake_fetch(url, params):
                called.append((url, dict(params)))
                if (url != endpoint or params.get("symbol") != "BTCUSDT"
                        or params.get("startTime") != _timestamp_ms(next_bar)
                        or params.get("limit") != page_limit):
                    raise AssertionError("Unexpected synthetic request")
                if scenario == "http429":
                    raise HTTPError(url, 429, "synthetic rate limit", {}, None)
                if scenario == "empty":
                    return _body(provider, "open_interest", [])
                first = (next_bar + dt.timedelta(minutes=5)
                         if scenario == "gap" else next_bar)
                return _body(provider, "open_interest", [
                    (first, 1250.0),
                    (first + dt.timedelta(minutes=5), 1251.0),
                ])

            with sqlite3.connect(path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                before = _fingerprints(db)
                cutoff = _timestamp_ms(CLOCK - dt.timedelta(minutes=10))
                with patch.object(forward, "_cutoff_now", return_value=cutoff):
                    result = forward.execute(
                        db, provider, "open_interest", fetch=fake_fetch)
                after = _fingerprints(db)
                db.commit()

            changed = {
                table: {
                    "added": len(set(after[table]) - set(before[table])),
                    "removed": len(set(before[table]) - set(after[table])),
                    "modified": sum(after[table].get(key) != value
                                    for key, value in before[table].items()
                                    if key in after[table]),
                }
                for table in TABLES
            }
            immutable = all(item["removed"] == 0 and item["modified"] == 0
                            for item in changed.values())
            gates = _auditors(path)
            audit_ok = (gates["RAW"] == "PASS_SQLITE_TO_RAW"
                        and gates["TEMPORAL"] == "SNAPSHOT_INTERNAL_QA_OK"
                        and gates["LINEAGE"] == "PASS_REQUEST_LINEAGE"
                        and gates["SQLITE"] == "ok"
                        and gates["FOREIGN_KEY_ERRORS"] == 0)
            expected = ("success" if scenario == "success" else
                        "empty" if scenario == "empty" else "failed")
            status = result.get("status")
            accepted = (len(called) == 1 and immutable and audit_ok
                        and status == expected
                        and changed["market_derivatives"]["added"] ==
                        (2 if scenario == "success" else 0))
            return {
                "state": ("MOCK_LOCKED_PAGE_PASS" if accepted
                          else "MOCK_LOCKED_PAGE_REVIEW"),
                "provider": provider,
                "scenario": scenario,
                "lock_namespace": LOCK_NAMESPACE,
                "lock_acquired": True,
                "excludes_uncooperative_writers": False,
                "mock_fetch_calls": len(called),
                "forward_status": status,
                "forward_points": result.get("points", 0),
                "forward_cursor_advanced": result.get("cursor_advanced", False),
                "preexisting_rows_immutable": immutable,
                "table_deltas": changed,
                "audit_gates": gates,
                "http_real_requests": 0,
                "production_db_reads": 0,
                "production_db_writes": 0,
                "production_authorized": False,
                "historical_completeness": "NOT_VERIFIED",
            }
    except WriterLockBusy:
        return {
            "state": "BLOCKED_WRITER_BUSY",
            "lock_acquired": False,
            "lock_namespace": LOCK_NAMESPACE,
            "mock_fetch_calls": 0,
            "sqlite_connections_started": 0,
            "http_real_requests": 0,
            "production_db_reads": 0,
            "production_db_writes": 0,
            "production_authorized": False,
            "historical_completeness": "NOT_VERIFIED",
        }
