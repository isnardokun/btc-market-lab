"""Package A first integrated synthetic ingestion slice.

ONLY explicit TemporaryDirectory fixtures marked by the test harness can be
modified. Uses existing E11 POSIX flock BEFORE touching the synthetic DB.
No HTTP client, network, external API, cron, production path or CLI.
"""
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from ingestion.package_a_contracts import SeriesSpec, utc
from ingestion.package_a_legacy_bridge import reconciled_fixture_points
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy
from storage import package_a_ledger as ledger
from scripts.package_a_provenance_audit import audit_receipt_reconstruction

MARKER = ".package-a-offline-fixture"
MARKER_VALUE = "PACKAGE A OFFLINE SQLITE TEST FIXTURE\n"
LOCK_NAMESPACE = "btc-research-global-writer-v1"


@dataclass(frozen=True)
class FixtureResponse:
    status_code: int | None
    payload: object
    raw_bytes: bytes | None = None

    def __post_init__(self):
        if self.status_code is not None and (type(self.status_code) is not int or
                self.status_code not in (200, 429, 500, 503)):
            raise ValueError("Only known synthetic HTTP outcomes are permitted")
        if self.raw_bytes is not None and type(self.raw_bytes) is not bytes:
            raise ValueError("Explicit frozen HTTP RAW bytes must be bytes")


def _fixture_guard(path, lock_directory):
    if not isinstance(path, (str, Path)) or not isinstance(lock_directory, (str, Path)):
        raise ValueError("Explicit synthetic SQLite and lock paths required")
    dbfile = Path(path)
    parent = dbfile.parent
    locks = Path(lock_directory)
    if (dbfile.name != "package_a_synthetic.db"
            or not parent.name.startswith("package-a-fixture-")
            or dbfile.is_symlink() or parent.is_symlink()
            or locks.is_symlink() or not dbfile.is_file() or not locks.is_dir()
            or locks.resolve() != parent.resolve() / "locks"):
        raise ValueError("Only an explicitly created Package A fixture is accepted")
    flag = parent / MARKER
    if (not flag.is_file() or flag.is_symlink()
            or flag.read_text(encoding="utf-8") != MARKER_VALUE):
        raise ValueError("Synthetic fixture marker missing or corrupted")
    if ((parent / (dbfile.name + "-wal")).exists()
            or (parent / (dbfile.name + "-shm")).exists()):
        raise ValueError("Active synthetic WAL not supported by this fixture runner")
    return dbfile


def run_fixture_page(db_path, lock_directory, spec, start, end, response, *,
                     now, fault=None, _parent_lock_held=False,
                     _frozen_request=None, _batch_run=None):
    """Commit one provider fixture page under the global writer lock.

    The payload is an in-memory synthetic JSON object, never downloaded.
    Callers cannot pass a fetch callback or a production API credential.
    """
    if not isinstance(spec, SeriesSpec) or not isinstance(response, FixtureResponse):
        raise ValueError("Provider spec and synthetic response required")
    begin, stop = utc(start), utc(end)
    if not begin < stop:
        raise ValueError("Invalid half-open time range")
    utc(now)
    path = _fixture_guard(db_path, lock_directory)
    try:
        # _parent_lock_held is strictly internal to the OFFLINE fake-HTTP
        # bridge and does not permit production paths (fixture guard above).
        # Its parent owns the same flock for the entire mock request + SQL.
        if type(_parent_lock_held) is not bool:
            raise ValueError("Invalid synthetic parent-lock marker")
        lock_context = (nullcontext() if _parent_lock_held else
                        hold_writer_lock(lock_directory, namespace=LOCK_NAMESPACE))
        with lock_context:
            raw = (response.raw_bytes if response.raw_bytes is not None else
                   json.dumps(response.payload, sort_keys=True,
                              separators=(",",":"), allow_nan=False).encode("utf-8")
                   if response.payload is not None else None)
            if response.raw_bytes is not None and response.status_code == 200:
                if json.loads(response.raw_bytes) != response.payload:
                    raise ValueError("Frozen HTTP payload differs from actual RAW bytes")
            # Error pages can be opaque bytes (HTML/plaintext 429/5xx).
            # Preserve actual RAW+SHA without trying to interpret a failure
            # response as a successful provider payload.
            # Parsing occurs before opening the DB, but after acquiring the
            # cooperative writer lock. No write transaction spans HTTP.
            error = None
            points = ()
            if response.status_code == 200:
                try:
                    points = reconciled_fixture_points(spec, response.payload,
                                                      start=start, end=end, now=now)
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    error = type(exc).__name__
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                # Keep the additive, self-contained SCHEMA migration outside
                # the page transaction: an aborted FIRST page must preserve
                # installed tables so crash/replay contracts remain valid.
                ledger.initialize(db, now=now)
                # A real OUTER transaction encloses ALL page writes and the
                # precommit integrity gate. SAVEPOINT release cannot make a
                # partial receipt/cursor durable before quality verification.
                # All synthetic fetch/parse work happened before BEGIN.
                db.execute("BEGIN IMMEDIATE")
                if response.status_code != 200 or error:
                    category = "failed" if response.status_code != 200 else "rejected"
                    receipt = ledger.record_failure(
                        db, spec, start, end, status=category,
                        raw=raw, http_status=response.status_code, now=now)
                    result = {"state": "PAGE_NOT_COMMITTED",
                              "receipt_id": receipt, "status": category,
                              "failure_class": error or ("SYNTHETIC_TRANSPORT_TIMEOUT"
                                  if response.status_code is None else
                                  "SYNTHETIC_HTTP_" + str(response.status_code)),
                              "new_rows": 0, "cursor_advanced": False}
                elif not points:
                    receipt = ledger.record_failure(
                        db, spec, start, end, status="empty",
                        raw=raw, http_status=200, now=now)
                    result = {"state": "PAGE_EMPTY_UNVERIFIED",
                              "receipt_id": receipt, "status": "empty",
                              "new_rows": 0, "cursor_advanced": False}
                else:
                    # All DB writes in one transaction; simulated crash in
                    # ledger rolls back the savepoint, then the outer with db.
                    recorded = ledger.store_ok_page(
                        db, spec, start, end, raw=raw, points=points, now=now, fault=fault)
                    result = {"state": "PAGE_COMMITTED_OFFLINE", "status": "ok", **recorded}
                # A page is not accepted until independent RAW -> SQL lineage
                # reconstruction passes inside the SAME uncommitted transaction.
                # A failed gate automatically rolls back this page/attempt;
                # legacy observations remain untouched rather than "fixed".
                if _frozen_request is not None:
                    if (not isinstance(_frozen_request, tuple) or
                            len(_frozen_request) != 2):
                        raise ValueError("Invalid frozen request provenance")
                    endpoint, params = _frozen_request
                    ledger.record_frozen_request_provenance(
                        db, result["receipt_id"], endpoint, params, now=now)
                if _batch_run is not None:
                    if type(_batch_run) is not tuple or len(_batch_run) != 3:
                        raise ValueError("Invalid synthetic batch run")
                    run_id,ordinal,planned = _batch_run
                    ledger.record_batch_run_page(
                        db,run_id=run_id,ordinal=ordinal,planned_pages=planned,
                        receipt_id=result["receipt_id"],receipt_status=result["status"],
                        now=now)
                proof = audit_receipt_reconstruction(db)
                request_proof = ledger.audit_frozen_request_provenance(db)
                run_proof = ledger.audit_batch_source_runs(db)
                if (proof["state"] != "RAW_SQL_LINEAGE_PASS"
                        or request_proof["state"] != "FROZEN_REQUEST_METADATA_PASS"
                        or run_proof["state"] != "BATCH_SOURCE_RUN_LINEAGE_PASS"
                        or db.execute("PRAGMA foreign_key_check").fetchone()
                        or db.execute("PRAGMA quick_check").fetchone()[0] != "ok"):
                    raise ValueError("BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE")
            return {
                **result, "provider": spec.provider, "metric": spec.metric,
                "page_provenance_gate": "RAW_SQL_LINEAGE_PASS",
                "http_real_requests": 0, "sqlite_productive_writes": 0,
                "production_authorized": False,
                "global_writer_exclusion_operationally_verified": False,
                "provider_availability": "NOT_CHECKED",
                "entitlement": "NOT_VERIFIED",
                "historical_completeness": "NOT_VERIFIED",
            }
    except WriterLockBusy:
        return {
            "state": "BLOCKED_WRITER_BUSY", "status": "blocked",
            "lock_acquired": False, "sqlite_connections_started": 0,
            "fake_responses_processed": 0, "http_real_requests": 0,
            "production_authorized": False,
            "historical_completeness": "NOT_VERIFIED",
        }


def inspect_fixture(db_path, lock_directory):
    """Read-only in-fixture audit of raw receipts, row hashes and cursor FK."""
    path = _fixture_guard(db_path, lock_directory)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        # All RAW, receipts, observations and cursors must come from one
        # consistent SQLite snapshot, not independent per-query snapshots.
        db.execute("BEGIN")
        fk = db.execute("PRAGMA foreign_key_check").fetchall()
        bad_raw = 0
        for payload, digest in db.execute(
                "SELECT raw_payload,raw_sha256 FROM package_a_receipts "
                "WHERE raw_payload IS NOT NULL"):
            if hashlib.sha256(payload).hexdigest() != digest:
                bad_raw += 1
        bad_points = 0
        for source, metric, symbol, frequency, moment, dim, value, unit, scale, digest in db.execute(
            "SELECT provider,metric,symbol,frequency,observed_utc,dimension_key,"
            "value,unit,raw_scale,row_sha256 FROM package_a_observations"
        ):
            try:
                spec = SeriesSpec(source, metric, symbol, frequency, unit, scale)
                from ingestion.package_a_contracts import Point
                actual = ledger._point_hash(spec, Point(moment, dim, value))
                if actual != digest:
                    bad_points += 1
            except (ValueError, TypeError):
                bad_points += 1
        bad_links = db.execute(
            "SELECT COUNT(*) FROM package_a_observations o LEFT JOIN package_a_receipts r "
            "ON o.receipt_id=r.receipt_id "
            "WHERE r.receipt_id IS NULL OR r.status <> 'ok'").fetchone()[0]
        bad_cursors = db.execute(
            "SELECT COUNT(*) FROM package_a_cursors c LEFT JOIN package_a_receipts r "
            "ON c.advanced_by_receipt=r.receipt_id WHERE r.receipt_id IS NULL "
            "OR r.status <> 'ok'").fetchone()[0]
        bad_cursor_windows = db.execute(
            "SELECT COUNT(*) FROM package_a_cursors c JOIN package_a_receipts r "
            "ON c.advanced_by_receipt=r.receipt_id WHERE "
            "c.provider<>r.provider OR c.metric<>r.metric OR c.symbol<>r.symbol "
            "OR c.frequency<>r.frequency OR c.end_exclusive_utc<>r.end_exclusive_utc"
        ).fetchone()[0]
        bad_coverage = 0
        for provider,metric,symbol,frequency,left,right,count in db.execute(
            "SELECT provider,metric,symbol,frequency,first_archived_utc,"
            "last_archived_utc,archived_rows FROM package_a_coverage"
        ):
            observed = db.execute(
                "SELECT MIN(observed_utc),MAX(observed_utc),COUNT(*) "
                "FROM package_a_observations WHERE provider=? AND metric=? "
                "AND symbol=? AND frequency=?",(provider,metric,symbol,frequency)
            ).fetchone()
            if observed != (left,right,count):
                bad_coverage += 1
        observations = db.execute("SELECT COUNT(*) FROM package_a_observations").fetchone()[0]
        receipts = db.execute("SELECT COUNT(*) FROM package_a_receipts").fetchone()[0]
        rows = ledger.read_coverage(db)
        evidence = ledger.read_history_evidence(db)
        request_meta = ledger.audit_frozen_request_provenance(db)
        source_runs = ledger.audit_batch_source_runs(db)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        reconstruction = audit_receipt_reconstruction(db)
    accepted = not (fk or bad_raw or bad_points or bad_links or bad_cursors or bad_cursor_windows or bad_coverage) and integrity == "ok" and reconstruction["state"] == "RAW_SQL_LINEAGE_PASS" and request_meta["state"] == "FROZEN_REQUEST_METADATA_PASS" and source_runs["state"] == "BATCH_SOURCE_RUN_LINEAGE_PASS"
    return {
        "state": "LOCAL_FIXTURE_INTEGRITY_PASS" if accepted else "LOCAL_FIXTURE_INTEGRITY_FAIL",
        "rows": observations, "receipts": receipts, "source_coverage": rows,
        "historical_request_evidence": evidence,
        "receipt_reconstruction": reconstruction,
        "request_provenance": request_meta,
        "source_runs": source_runs,
        "raw_digest_errors": bad_raw, "normalized_digest_errors": bad_points,
        "coverage_errors": bad_coverage, "cursor_window_errors": bad_cursor_windows,
        "lineage_errors": bad_links,
        "cursor_linkage_errors": bad_cursors, "foreign_key_errors": len(fk),
        "sqlite_integrity": integrity,
        "real_provider_window_verified": False,
        "real_entitlement_verified": False,
        "historical_completeness": "NOT_VERIFIED",
        "production_authorized": False,
    }
