"""Package A additive SQLite schema and page/coverage provenance.

ONLY methods on an explicit caller-owned SQLite connection. No implicit DB
paths, HTTP, cron, auto-application or claim of provider availability.
"""
import datetime as dt
import hashlib
import json
import sqlite3
import uuid
from urllib.parse import urlsplit

from ingestion.package_a_contracts import SeriesSpec, Point, utc

class SimulatedCrash(RuntimeError):
    """Fixture-only crash-injection signal; rollback the full page."""


MIGRATION_VERSION = 3
DDL = (
    """CREATE TABLE IF NOT EXISTS package_a_schema_migrations (
       version INTEGER PRIMARY KEY, applied_utc TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS package_a_receipts (
       receipt_id TEXT PRIMARY KEY,
       provider TEXT NOT NULL, metric TEXT NOT NULL, symbol TEXT NOT NULL,
       frequency TEXT NOT NULL,
       start_utc TEXT NOT NULL, end_exclusive_utc TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('ok','empty','failed','rejected')),
       http_status INTEGER,
       raw_payload BLOB, raw_sha256 TEXT,
       parsed_count INTEGER NOT NULL DEFAULT 0,
       captured_utc TEXT NOT NULL,
       CHECK ((raw_payload IS NULL AND raw_sha256 IS NULL)
              OR (raw_payload IS NOT NULL AND length(raw_sha256)=64)),
       CHECK (parsed_count >= 0)
       )""",
    """CREATE INDEX IF NOT EXISTS idx_package_a_receipts_window
       ON package_a_receipts(provider,metric,symbol,frequency,start_utc)""",
    """CREATE TABLE IF NOT EXISTS package_a_observations (
       provider TEXT NOT NULL, metric TEXT NOT NULL, symbol TEXT NOT NULL,
       frequency TEXT NOT NULL, observed_utc TEXT NOT NULL,
       dimension_key TEXT NOT NULL DEFAULT '',
       value REAL NOT NULL, unit TEXT NOT NULL, raw_scale TEXT NOT NULL,
       row_sha256 TEXT NOT NULL, receipt_id TEXT NOT NULL,
       PRIMARY KEY(provider,metric,symbol,frequency,observed_utc,dimension_key),
       FOREIGN KEY(receipt_id) REFERENCES package_a_receipts(receipt_id)
       )""",
    """CREATE INDEX IF NOT EXISTS idx_package_a_observations_dates
       ON package_a_observations(provider,metric,symbol,observed_utc)""",
    """CREATE TABLE IF NOT EXISTS package_a_cursors (
       provider TEXT NOT NULL, metric TEXT NOT NULL,
       symbol TEXT NOT NULL, frequency TEXT NOT NULL,
       end_exclusive_utc TEXT NOT NULL, advanced_by_receipt TEXT NOT NULL,
       PRIMARY KEY(provider,metric,symbol,frequency),
       FOREIGN KEY(advanced_by_receipt) REFERENCES package_a_receipts(receipt_id)
       )""",
    """CREATE TABLE IF NOT EXISTS package_a_coverage (
       provider TEXT NOT NULL, metric TEXT NOT NULL, symbol TEXT NOT NULL,
       frequency TEXT NOT NULL,
       first_archived_utc TEXT NOT NULL, last_archived_utc TEXT NOT NULL,
       archived_rows INTEGER NOT NULL CHECK(archived_rows >= 1),
       provider_window_status TEXT NOT NULL DEFAULT 'NOT_CHECKED'
          CHECK(provider_window_status='NOT_CHECKED'),
       entitlement_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED'
          CHECK(entitlement_status='NOT_VERIFIED'),
       historical_completeness TEXT NOT NULL DEFAULT 'NOT_VERIFIED'
          CHECK(historical_completeness='NOT_VERIFIED'),
       PRIMARY KEY(provider,metric,symbol,frequency)
       )""",
    """CREATE TABLE IF NOT EXISTS package_a_request_provenance (
       receipt_id TEXT PRIMARY KEY,
       endpoint_origin TEXT NOT NULL,
       endpoint_path TEXT NOT NULL,
       request_params_json TEXT NOT NULL,
       request_sha256 TEXT NOT NULL,
       transport TEXT NOT NULL CHECK(transport='FROZEN_OFFLINE'),
       method TEXT NOT NULL CHECK(method='GET'),
       captured_utc TEXT NOT NULL,
       FOREIGN KEY(receipt_id) REFERENCES package_a_receipts(receipt_id)
       )""",
    """CREATE TABLE IF NOT EXISTS package_a_source_runs (
       run_id TEXT PRIMARY KEY,
       planned_pages INTEGER NOT NULL CHECK(planned_pages BETWEEN 1 AND 32),
       created_utc TEXT NOT NULL,
       updated_utc TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('running','complete','blocked')),
       mode TEXT NOT NULL CHECK(mode='OFFLINE_SYNTHETIC_BATCH')
       )""",
    """CREATE TABLE IF NOT EXISTS package_a_run_pages (
       run_id TEXT NOT NULL,
       ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 31),
       receipt_id TEXT NOT NULL UNIQUE,
       receipt_status TEXT NOT NULL
          CHECK(receipt_status IN ('ok','failed','rejected','empty')),
       PRIMARY KEY(run_id,ordinal),
       FOREIGN KEY(run_id) REFERENCES package_a_source_runs(run_id),
       FOREIGN KEY(receipt_id) REFERENCES package_a_receipts(receipt_id)
       )""",
)
REQUIRED = {
    "package_a_receipts": {"receipt_id","provider","metric","symbol","frequency",
                           "start_utc","end_exclusive_utc","status","raw_payload","raw_sha256"},
    "package_a_observations": {"provider","metric","symbol","frequency",
                               "observed_utc","dimension_key","row_sha256","receipt_id"},
    "package_a_cursors": {"provider","metric","symbol","frequency",
                          "end_exclusive_utc","advanced_by_receipt"},
    "package_a_coverage": {"provider","metric","symbol","frequency",
                            "first_archived_utc","last_archived_utc","archived_rows"},
    "package_a_request_provenance": {"receipt_id","endpoint_origin","endpoint_path",
                                     "request_params_json","request_sha256","transport",
                                     "method","captured_utc"},
    "package_a_source_runs": {"run_id","planned_pages","created_utc","updated_utc",
                              "status","mode"},
    "package_a_run_pages": {"run_id","ordinal","receipt_id","receipt_status"},
}


SCHEMA_PRIMARY_KEYS = {
    "package_a_schema_migrations": ("version",),
    "package_a_receipts": ("receipt_id",),
    "package_a_observations": ("provider", "metric", "symbol", "frequency",
                               "observed_utc", "dimension_key"),
    "package_a_cursors": ("provider", "metric", "symbol", "frequency"),
    "package_a_coverage": ("provider", "metric", "symbol", "frequency"),
    "package_a_request_provenance": ("receipt_id",),
    "package_a_source_runs": ("run_id",),
    "package_a_run_pages": ("run_id","ordinal"),
}
SCHEMA_FOREIGN_KEYS = {
    "package_a_observations": {("receipt_id", "package_a_receipts", "receipt_id")},
    "package_a_cursors": {("advanced_by_receipt", "package_a_receipts", "receipt_id")},
    "package_a_request_provenance": {("receipt_id", "package_a_receipts", "receipt_id")},
    "package_a_run_pages": {("run_id","package_a_source_runs","run_id"),
                            ("receipt_id","package_a_receipts","receipt_id")},
}


def validate_schema(conn):
    """Require actual PK/FK/index shape, not merely a v1 marker/column names."""
    for table, expected_pk in SCHEMA_PRIMARY_KEYS.items():
        info = conn.execute("PRAGMA table_info(" + table + ")").fetchall()
        columns = {r[1] for r in info}
        if not REQUIRED.get(table, set()).issubset(columns):
            raise ValueError("Incompatible existing Package A schema " + table)
        pk = tuple(r[1] for r in sorted(info, key=lambda x:x[5]) if r[5] > 0)
        if pk != expected_pk:
            raise ValueError("Incompatible Package A primary key " + table)
        fks = {(r[3],r[2],r[4]) for r in
               conn.execute("PRAGMA foreign_key_list(" + table + ")")}
        if fks != SCHEMA_FOREIGN_KEYS.get(table, set()):
            raise ValueError("Incompatible Package A foreign key " + table)
    for index in ("idx_package_a_receipts_window", "idx_package_a_observations_dates"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                        (index,)).fetchone() is None:
            raise ValueError("Missing Package A index " + index)


def initialize(conn, *, now):
    """Atomic additive fixture DDL with strict shape validation.

    SAVEPOINT ensures failed or forged schema does not persist partial DDL.
    A preexisting caller transaction remains owned by the caller.
    """
    if not isinstance(conn, sqlite3.Connection):
        raise ValueError("Explicit SQLite connection required")
    stamp = utc(now).isoformat().replace("+00:00", "Z")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError("Foreign keys must be enabled before migration")
    conn.execute("SAVEPOINT package_a_schema")
    try:
        for statement in DDL:
            conn.execute(statement)
        validate_schema(conn)
        # Upgrade exact older fixture v1/v2 markers in the same savepoint.
        # Existing receipts get NO fabricated request or source-run identity.
        installed = {v for (v,) in conn.execute(
            "SELECT version FROM package_a_schema_migrations")}
        if installed in ({1},{2}):
            conn.execute("DELETE FROM package_a_schema_migrations")
            installed = set()
        if not installed:
            conn.execute("INSERT INTO package_a_schema_migrations(version,applied_utc) "
                         "VALUES (?,?)", (MIGRATION_VERSION, stamp))
        elif installed != {MIGRATION_VERSION}:
            raise ValueError("Unsupported Package A schema revision")
        conn.execute("RELEASE SAVEPOINT package_a_schema")
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT package_a_schema")
        conn.execute("RELEASE SAVEPOINT package_a_schema")
        raise


def _key(spec):
    return (spec.provider, spec.metric, spec.symbol, spec.frequency)


def _point_hash(spec, point):
    source = json.dumps([*_key(spec), point.timestamp, point.dimension,
                         point.value, spec.unit, spec.raw_scale],
                        ensure_ascii=True, separators=(",",":"), allow_nan=False)
    return hashlib.sha256(source.encode()).hexdigest()


def _receipt(conn, spec, begin, stop, *, status, payload, http_status, count, now):
    receipt = uuid.uuid4().hex
    raw = None if payload is None else bytes(payload)
    digest = hashlib.sha256(raw).hexdigest() if raw is not None else None
    conn.execute(
        "INSERT INTO package_a_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (receipt, *_key(spec), begin, stop, status, http_status,
         raw, digest, count, now))
    return receipt


def record_failure(conn, spec, start, end, *, status, raw=None, http_status=None, now):
    """Record failed/empty/rejected ATTEMPT without changing cursor or points."""
    if status not in ("failed", "rejected", "empty") or not isinstance(spec, SeriesSpec):
        raise ValueError("Invalid failure receipt")
    begin, stop = _bounds(start, end)
    raw = raw if raw is None else bytes(raw)
    conn.execute("SAVEPOINT package_a_attempt")
    try:
        rid = _receipt(conn, spec, begin, stop, status=status, payload=raw,
                       http_status=http_status, count=0, now=utc(now).isoformat().replace("+00:00","Z"))
        conn.execute("RELEASE SAVEPOINT package_a_attempt")
        return rid
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT package_a_attempt")
        conn.execute("RELEASE SAVEPOINT package_a_attempt")
        raise


def _bounds(start, end):
    left, right = utc(start), utc(end)
    if right <= left:
        raise ValueError("Invalid request window")
    return (left.isoformat().replace("+00:00", "Z"),
            right.isoformat().replace("+00:00", "Z"))


def store_ok_page(conn, spec, start, end, *, raw, points, now, fault=None):
    """Atomically persist request RAW + append-only observations + cursor.

    Exact replays are accepted without mutating old observations/cursor.
    Conflicting revisions or skipped cursor windows are rejected entirely.
    """
    if not isinstance(spec, SeriesSpec) or not isinstance(raw, bytes) or not raw:
        raise ValueError("Typed source and real raw fixture bytes required")
    if not isinstance(points, tuple) or not points or not all(isinstance(x, Point) for x in points):
        raise ValueError("Nonempty parsed fixture points required")
    begin, stop = _bounds(start, end)
    if fault not in (None, "after_receipt", "after_rows", "after_cursor"):
        raise ValueError("Unknown crash injection")
    if any(not begin <= p.timestamp < stop for p in points):
        raise ValueError("Parsed observation outside receipt window")
    timestamp = utc(now).isoformat().replace("+00:00","Z")
    conn.execute("SAVEPOINT package_a_page")
    try:
        prior = conn.execute(
            "SELECT end_exclusive_utc FROM package_a_cursors "
            "WHERE provider=? AND metric=? AND symbol=? AND frequency=?", _key(spec)
        ).fetchone()
        if prior and prior[0] != begin:
            # Historical replay is allowed only when the entire requested
            # window is at or before the currently committed cursor.
            if not (begin < prior[0] and stop <= prior[0]):
                raise ValueError("Discontinuous or overlapping forward cursor")

        receipt = _receipt(conn, spec, begin, stop, status="ok", payload=raw,
                           http_status=200, count=len(points), now=timestamp)
        if fault == "after_receipt":
            raise SimulatedCrash("after_receipt")
        inserted = 0
        for p in points:
            digest = _point_hash(spec, p)
            key = (*_key(spec), p.timestamp, p.dimension)
            previous = conn.execute(
                "SELECT row_sha256 FROM package_a_observations "
                "WHERE provider=? AND metric=? AND symbol=? AND frequency=? "
                "AND observed_utc=? AND dimension_key=?", key).fetchone()
            if previous is not None:
                if previous[0] != digest:
                    raise ValueError("Conflicting historical observation; never overwrite")
                continue
            conn.execute(
                "INSERT INTO package_a_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (*key, p.value, spec.unit, spec.raw_scale, digest, receipt))
            inserted += 1
        if fault == "after_rows":
            raise SimulatedCrash("after_rows")
        if not prior or prior[0] == begin:
            conn.execute(
                "INSERT INTO package_a_cursors VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(provider,metric,symbol,frequency) DO UPDATE SET "
                "end_exclusive_utc=excluded.end_exclusive_utc,"
                "advanced_by_receipt=excluded.advanced_by_receipt",
                (*_key(spec), stop, receipt))
        if fault == "after_cursor":
            raise SimulatedCrash("after_cursor")
        aggregate = conn.execute(
            "SELECT MIN(observed_utc),MAX(observed_utc),COUNT(*) "
            "FROM package_a_observations WHERE provider=? AND metric=? AND symbol=? AND frequency=?",
            _key(spec)).fetchone()
        conn.execute(
            "INSERT INTO package_a_coverage "
            "(provider,metric,symbol,frequency,first_archived_utc,last_archived_utc,archived_rows) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(provider,metric,symbol,frequency) DO UPDATE SET "
            "first_archived_utc=excluded.first_archived_utc,"
            "last_archived_utc=excluded.last_archived_utc,archived_rows=excluded.archived_rows",
            (*_key(spec), *aggregate))
        conn.execute("RELEASE SAVEPOINT package_a_page")
        return {"receipt_id": receipt, "parsed_rows": len(points),
                "new_rows": inserted, "cursor_advanced": (not prior or prior[0] == begin),
                "raw_sha256": hashlib.sha256(raw).hexdigest()}
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT package_a_page")
        conn.execute("RELEASE SAVEPOINT package_a_page")
        raise


def read_coverage(conn):
    """Observed-only archive state; no external history/completeness claims."""
    result = []
    for row in conn.execute(
        "SELECT provider,metric,symbol,frequency,first_archived_utc,last_archived_utc,"
        "archived_rows,provider_window_status,entitlement_status,historical_completeness "
        "FROM package_a_coverage ORDER BY provider,metric,symbol,frequency"
    ):
        result.append(dict(zip((
            "provider","metric","symbol","frequency","first_archived_utc",
            "last_archived_utc","archived_rows","provider_window_status",
            "entitlement_status","historical_completeness"), row)))
    return result



def read_history_evidence(conn):
    """Reconcile attempted versus archived windows in ONE caller-owned snapshot.

    Streams with only failed or empty responses must remain visible. A local
    success is NOT proof of the provider's earliest obtainable observation.
    """
    counts=("ok","empty","failed","rejected")
    streams={}
    def row_for(key):
        if key not in streams:
            provider,metric,symbol,frequency=key
            streams[key]={
                "provider":provider,"metric":metric,"symbol":symbol,"frequency":frequency,
                "requested_first_utc":None,"requested_end_exclusive_utc":None,
                "attempted_requests":0,
                "attempt_status_counts":{status:0 for status in counts},
                "successful_requests_with_raw_receipt":0,
                "successful_requests_missing_raw_receipt":0,
                "reported_rows_by_success_receipts":0,
                "archived_first_utc":None,"archived_last_utc":None,"archived_rows":0,
                "provider_window_status":"NOT_CHECKED",
                "entitlement_status":"NOT_VERIFIED",
                "historical_completeness":"NOT_VERIFIED"
            }
        return streams[key]
    for provider,metric,symbol,frequency,start,end,status,parsed,digest in conn.execute(
        "SELECT provider,metric,symbol,frequency,start_utc,end_exclusive_utc,"
        "status,parsed_count,raw_sha256 FROM package_a_receipts ORDER BY rowid"
    ):
        row=row_for((provider,metric,symbol,frequency))
        row["attempted_requests"]+=1
        row["attempt_status_counts"][status]+=1
        row["requested_first_utc"]=(
            min(start,row["requested_first_utc"]) if row["requested_first_utc"] else start)
        row["requested_end_exclusive_utc"]=(
            max(end,row["requested_end_exclusive_utc"]) if row["requested_end_exclusive_utc"] else end)
        if status=="ok":
            row["reported_rows_by_success_receipts"]+=parsed
            row["successful_requests_with_raw_receipt" if digest
                else "successful_requests_missing_raw_receipt"]+=1
    for item in read_coverage(conn):
        row=row_for((item["provider"],item["metric"],item["symbol"],item["frequency"]))
        row["archived_first_utc"]=item["first_archived_utc"]
        row["archived_last_utc"]=item["last_archived_utc"]
        row["archived_rows"]=item["archived_rows"]
    for row in streams.values():
        status=row["attempt_status_counts"]
        row["local_request_history_state"]=(
            "ATTEMPTS_WITH_FAILURE_OR_EMPTY"
            if status["failed"] or status["rejected"] or status["empty"]
            else "LOCAL_SUCCESS_RECEIPTS_ONLY"
            if status["ok"] else "MISSING_REQUEST_RECEIPTS"
        )
        row["local_receipt_completeness"]=(
            "MISSING_NEW_RECEIPT" if row["successful_requests_missing_raw_receipt"]
            else "LOCAL_RAW_RECEIPTS_PRESENT" if row["successful_requests_with_raw_receipt"]
            else "NO_SUCCESS_RECEIPTS"
        )
    return [streams[key] for key in sorted(streams)]


def record_frozen_request_provenance(conn, receipt_id, endpoint, params, *, now):
    """Write sanitized one-shot request evidence inside the caller's transaction.

    No secrets: only method/origin/path and REDACTED query parameters are
    retained. This is mock-transport lineage, never verification of upstream.
    """
    parsed = urlsplit(endpoint)
    allowed_hosts = {"api.researchbitcoin.net", "bitview.space",
                     "query1.finance.yahoo.com", "api.stlouisfed.org",
                     "fapi.binance.com", "api.bybit.com"}
    if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or not isinstance(params, dict)):
        raise ValueError("Invalid frozen provenance endpoint")
    clean = {}
    for key, value in params.items():
        if type(key) is not str or type(value) is not str:
            raise ValueError("Invalid frozen provenance parameter")
        clean[key] = "[REDACTED]" if any(
            flag in key.lower() for flag in ("key","token","secret","password")) else value
    params_json = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    canonical = json.dumps(
        [parsed.netloc, parsed.path, "GET", params_json, "FROZEN_OFFLINE"],
        separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    conn.execute(
        "INSERT INTO package_a_request_provenance "
        "(receipt_id,endpoint_origin,endpoint_path,request_params_json,"
        "request_sha256,transport,method,captured_utc) "
        "VALUES (?,?,?,?,?,'FROZEN_OFFLINE','GET',?)",
        (receipt_id, parsed.netloc, parsed.path, params_json, digest,
         utc(now).isoformat().replace("+00:00","Z")))
    return digest


def audit_frozen_request_provenance(conn):
    """Read-only evidence integrity; missing legacy metadata is NOT forged."""
    valid = 0
    errors = 0
    for receipt, origin, path, params_json, sha, transport, method in conn.execute(
        "SELECT receipt_id,endpoint_origin,endpoint_path,request_params_json,"
        "request_sha256,transport,method FROM package_a_request_provenance"
    ):
        try:
            params = json.loads(params_json)
            if (not isinstance(params, dict) or
                    any(type(k) is not str or type(v) is not str or
                        (any(secret in k.lower() for secret in
                             ("key","token","secret","password")) and
                         v != "[REDACTED]")
                        for k, v in params.items())):
                raise ValueError("Unredacted or invalid provenance")
            canonical = json.dumps(
                [origin, path, method, params_json, transport],
                separators=(",", ":"))
            if (transport != "FROZEN_OFFLINE" or method != "GET"
                    or hashlib.sha256(canonical.encode()).hexdigest() != sha):
                raise ValueError("Frozen request provenance digest mismatch")
            valid += 1
        except (ValueError, TypeError):
            errors += 1
    missing = conn.execute(
        "SELECT COUNT(*) FROM package_a_receipts r "
        "LEFT JOIN package_a_request_provenance p ON p.receipt_id=r.receipt_id "
        "WHERE p.receipt_id IS NULL").fetchone()[0]
    return {
        "state": "FROZEN_REQUEST_METADATA_PASS" if not errors else
                 "FROZEN_REQUEST_METADATA_FAIL",
        "frozen_requests_with_metadata": valid,
        "metadata_digest_errors": errors,
        "receipts_without_frozen_request_metadata": missing,
        "legacy_metadata_retroactively_fabricated": False,
        "upstream_requests_verified": False,
    }


def record_batch_run_page(conn, *, run_id, ordinal, planned_pages,
                          receipt_id, receipt_status, now):
    """Link ONE persisted receipt to its owning synthetic batch atomically.

    Never link failed/rolled-back pages, never backfill missing legacy IDs.
    Creation, FK page link and terminal state transition share the exact
    source page SQLite transaction; crashes cannot commit phantom runs.
    """
    if (type(run_id) is not str or len(run_id) != 32 or
            any(c not in "0123456789abcdef" for c in run_id) or
            type(ordinal) is not int or type(planned_pages) is not int or
            not 1 <= planned_pages <= 32 or not 0 <= ordinal < planned_pages or
            receipt_status not in ("ok","failed","rejected","empty")):
        raise ValueError("Invalid synthetic batch source run identity")
    stamp=utc(now).isoformat().replace("+00:00","Z")
    existing=conn.execute(
        "SELECT planned_pages,status FROM package_a_source_runs WHERE run_id=?",
        (run_id,)).fetchone()
    if existing is None:
        if ordinal != 0:
            raise ValueError("Cannot create source run at nonzero page")
        conn.execute(
            "INSERT INTO package_a_source_runs "
            "(run_id,planned_pages,created_utc,updated_utc,status,mode) "
            "VALUES (?,?,?,?,'running','OFFLINE_SYNTHETIC_BATCH')",
            (run_id,planned_pages,stamp,stamp))
    elif existing != (planned_pages,"running"):
        raise ValueError("Source run closed or planned window changed")
    count=conn.execute(
        "SELECT COUNT(*) FROM package_a_run_pages WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    if count != ordinal:
        raise ValueError("Skipped/duplicate source run page ordinal")
    conn.execute(
        "INSERT INTO package_a_run_pages "
        "(run_id,ordinal,receipt_id,receipt_status) VALUES (?,?,?,?)",
        (run_id,ordinal,receipt_id,receipt_status))
    status = ("blocked" if receipt_status != "ok" else
              "complete" if ordinal + 1 == planned_pages else "running")
    conn.execute(
        "UPDATE package_a_source_runs SET status=?,updated_utc=? WHERE run_id=?",
        (status,stamp,run_id))
    return status


def audit_batch_source_runs(conn):
    """Read-only independent run→ordinal→receipt reconciliation.

    Incomplete running runs are NOT incorrectly asserted complete; they are
    valid recoverable artifacts after crash between successful pages.
    """
    audited=complete=blocked=interrupted=errors=0
    for run_id,total,status,mode in conn.execute(
        "SELECT run_id,planned_pages,status,mode FROM package_a_source_runs"
    ):
        audited+=1
        pages=list(conn.execute(
            "SELECT p.ordinal,p.receipt_status,r.status FROM package_a_run_pages p "
            "LEFT JOIN package_a_receipts r ON r.receipt_id=p.receipt_id "
            "WHERE p.run_id=? ORDER BY p.ordinal",(run_id,)))
        valid=(mode=="OFFLINE_SYNTHETIC_BATCH" and 1 <= total <= 32 and
               0 < len(pages) <= total and
               [p[0] for p in pages] == list(range(len(pages))) and
               all(p[1] == p[2] for p in pages) and
               all(p[1] == "ok" for p in pages[:-1]))
        expected=("blocked" if pages and pages[-1][1] != "ok" else
                  "complete" if len(pages) == total else "running")
        if not valid or expected != status:
            errors+=1
        elif status == "complete":
            complete+=1
        elif status == "blocked":
            blocked+=1
        else:
            interrupted+=1
    unlinked=conn.execute(
        "SELECT COUNT(*) FROM package_a_receipts r "
        "LEFT JOIN package_a_run_pages p ON r.receipt_id=p.receipt_id "
        "WHERE p.receipt_id IS NULL").fetchone()[0]
    return {
        "state":"BATCH_SOURCE_RUN_LINEAGE_PASS" if not errors else
                "BATCH_SOURCE_RUN_LINEAGE_FAIL",
        "runs":audited, "complete_runs":complete,
        "blocked_runs":blocked, "interrupted_or_running_runs":interrupted,
        "run_integrity_errors":errors, "receipts_without_batch_run":unlinked,
        "retrospective_run_ids_fabricated":False,
        "external_history_verified":False,
    }
