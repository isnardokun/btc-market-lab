"""Package A additive SQLite schema and page/coverage provenance.

ONLY methods on an explicit caller-owned SQLite connection. No implicit DB
paths, HTTP, cron, auto-application or claim of provider availability.
"""
import datetime as dt
import hashlib
import json
import sqlite3
import uuid

from ingestion.package_a_contracts import SeriesSpec, Point, utc

MIGRATION_VERSION = 1
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
}


def initialize(conn, *, now):
    """Explicit, additive fixture migration; caller controls COMMIT/rollback."""
    if not isinstance(conn, sqlite3.Connection):
        raise ValueError("Explicit SQLite connection required")
    stamp = utc(now).isoformat().replace("+00:00", "Z")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError("Foreign keys must be enabled before migration")
    for statement in DDL:
        conn.execute(statement)
    for table, needed in REQUIRED.items():
        cols = {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")}
        if not needed.issubset(cols):
            raise ValueError("Incompatible existing Package A schema " + table)
    conn.execute("INSERT OR IGNORE INTO package_a_schema_migrations(version,applied_utc) "
                 "VALUES (?,?)", (MIGRATION_VERSION, stamp))
    installed = {v for (v,) in conn.execute("SELECT version FROM package_a_schema_migrations")}
    if installed != {MIGRATION_VERSION}:
        raise ValueError("Unsupported Package A schema revision")


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


def store_ok_page(conn, spec, start, end, *, raw, points, now):
    """Atomically persist request RAW + append-only observations + cursor.

    Exact replays are accepted without mutating old observations/cursor.
    Conflicting revisions or skipped cursor windows are rejected entirely.
    """
    if not isinstance(spec, SeriesSpec) or not isinstance(raw, bytes) or not raw:
        raise ValueError("Typed source and real raw fixture bytes required")
    if not isinstance(points, tuple) or not points or not all(isinstance(x, Point) for x in points):
        raise ValueError("Nonempty parsed fixture points required")
    begin, stop = _bounds(start, end)
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
        if not prior or prior[0] == begin:
            conn.execute(
                "INSERT INTO package_a_cursors VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(provider,metric,symbol,frequency) DO UPDATE SET "
                "end_exclusive_utc=excluded.end_exclusive_utc,"
                "advanced_by_receipt=excluded.advanced_by_receipt",
                (*_key(spec), stop, receipt))
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
