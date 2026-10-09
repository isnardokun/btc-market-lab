"""Versioned, additive archive metadata for the existing btc_research.db.

Do not replace/migrate the millions of already-stored daily observations.
All new objects are namespaced archive_* and only created on explicit apply
or when a historical ingestion runs. Data and history from existing tables
are never deleted. The retained backup is the reversible recovery path.
"""
import datetime as dt
import sqlite3

SCHEMA_VERSION = 1
DDL = """
CREATE TABLE IF NOT EXISTS archive_schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at_utc TEXT NOT NULL,
  description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_sources (
  source_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  plan_tier INTEGER,
  entitlement_verified INTEGER NOT NULL DEFAULT 0 CHECK(entitlement_verified IN (0,1)),
  time_reference TEXT NOT NULL DEFAULT 'UTC',
  created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_datasets (
  source_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  frequency TEXT NOT NULL,
  raw_unit TEXT,
  raw_scale TEXT,
  first_requested_utc TEXT,
  availability_note TEXT,
  registered_at_utc TEXT NOT NULL,
  PRIMARY KEY(source_id, metric),
  FOREIGN KEY(source_id) REFERENCES archive_sources(source_id)
);
CREATE TABLE IF NOT EXISTS archive_ingest_runs (
  run_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  status TEXT NOT NULL CHECK(status IN ('running','completed','partial','failed')),
  requests INTEGER NOT NULL DEFAULT 0,
  data_points INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(source_id) REFERENCES archive_sources(source_id)
);
CREATE TABLE IF NOT EXISTS archive_ingest_windows (
  source_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  start_utc TEXT NOT NULL,
  end_exclusive_utc TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ok','empty','partial','failed')),
  data_points INTEGER NOT NULL DEFAULT 0,
  fetched_at_utc TEXT NOT NULL,
  last_run_id TEXT,
  PRIMARY KEY(source_id, metric, start_utc, end_exclusive_utc),
  FOREIGN KEY(source_id) REFERENCES archive_sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_archive_windows_source_status
ON archive_ingest_windows(source_id,metric,status,end_exclusive_utc);
CREATE INDEX IF NOT EXISTS idx_archive_runs_source_started
ON archive_ingest_runs(source_id,started_at_utc);
CREATE TABLE IF NOT EXISTS archive_observation_revisions (
  revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  source_table TEXT NOT NULL,
  old_value REAL,
  new_value REAL,
  old_unit TEXT,
  new_unit TEXT,
  revision_recorded_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_archive_revisions_lookup
ON archive_observation_revisions(source_id,metric,observed_at_utc,revision_id);
"""

TRIGGERS = {
    "onchain_external_observations": """
      CREATE TRIGGER IF NOT EXISTS trg_archive_rbn_revisions
      BEFORE UPDATE OF value,unit ON onchain_external_observations
      WHEN OLD.value IS NOT NEW.value OR OLD.unit IS NOT NEW.unit
      BEGIN
        INSERT INTO archive_observation_revisions (
          source_id,metric,observed_at_utc,source_table,
          old_value,new_value,old_unit,new_unit,revision_recorded_at_utc)
        VALUES (OLD.provider,OLD.metric,OLD.observed_at_utc,
          'onchain_external_observations',OLD.value,NEW.value,OLD.unit,
          NEW.unit,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
      END;
    """,
    "macro_fred": """
      CREATE TRIGGER IF NOT EXISTS trg_archive_fred_revisions
      BEFORE UPDATE OF value ON macro_fred
      WHEN OLD.value IS NOT NEW.value
      BEGIN
        INSERT INTO archive_observation_revisions (
          source_id,metric,observed_at_utc,source_table,
          old_value,new_value,old_unit,new_unit,revision_recorded_at_utc)
        VALUES ('fred',OLD.series_id,OLD.date,'macro_fred',
          OLD.value,NEW.value,NULL,NULL,
          strftime('%Y-%m-%dT%H:%M:%fZ','now'));
      END;
    """,
    "daily": """
      CREATE TRIGGER IF NOT EXISTS trg_archive_bitview_revisions
      BEFORE UPDATE OF value ON daily
      WHEN OLD.value IS NOT NEW.value
      BEGIN
        INSERT INTO archive_observation_revisions (
          source_id,metric,observed_at_utc,source_table,
          old_value,new_value,old_unit,new_unit,revision_recorded_at_utc)
        SELECT 'bitview',s.name,strftime('%Y-%m-%dT%H:%M:%SZ',
          OLD.ts,'unixepoch'),'daily',OLD.value,NEW.value,NULL,NULL,
          strftime('%Y-%m-%dT%H:%M:%fZ','now')
        FROM series s WHERE s.id=OLD.series_id;
      END;
    """,
    "market_ohlc_history": """
      CREATE TRIGGER IF NOT EXISTS trg_archive_yahoo_close_revisions
      BEFORE UPDATE OF close ON market_ohlc_history
      WHEN OLD.close IS NOT NEW.close
      BEGIN
        INSERT INTO archive_observation_revisions (
          source_id,metric,observed_at_utc,source_table,
          old_value,new_value,old_unit,new_unit,revision_recorded_at_utc)
        VALUES ('yahoo',OLD.symbol,
          strftime('%Y-%m-%dT%H:%M:%SZ',OLD.ts,'unixepoch'),
          'market_ohlc_history',OLD.close,NEW.close,'USD','USD',
          strftime('%Y-%m-%dT%H:%M:%fZ','now'));
      END;
    """,
}

PROVIDERS = (
    ("researchbitcoin", "ResearchBitcoin V2", 2),
    ("bitview", "Bitview on-chain", None),
    ("fred", "FRED / ALFRED macro", None),
    ("yahoo", "Yahoo Finance market OHLCV", None),
    ("news", "Specialized RSS / Exa", None),
)


def ensure_archive_schema(conn):
    """Idempotent migration; accepts caller-owned SQLite connection.

    No PRAGMA user_version reset, no ALTER of production rows, and no DROP.
    Called by ingestion before database writes to enable audit triggers.
    """
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("Se requiere sqlite3.Connection")
    # executescript itself may commit a pre-existing caller transaction.
    # This function must be called before ingestion transactions begin.
    conn.executescript(DDL)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO archive_sources "
        "(source_id,display_name,plan_tier,entitlement_verified,created_at_utc) "
        "VALUES (?,?,?,0,?)",
        [(provider,label,tier,now) for provider,label,tier in PROVIDERS],
    )
    # Configured Tier 2 is user-declared, not proof a token has Tier 2 access.
    conn.execute(
        "INSERT OR IGNORE INTO archive_schema_migrations "
        "(version,applied_at_utc,description) VALUES (?,?,?)",
        (SCHEMA_VERSION,now,"Archive metadata and append-only change audit v1"),
    )
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    for table,sql in TRIGGERS.items():
        if table in existing and (table != "daily" or "series" in existing):
            conn.execute(sql)
    # The outer caller owns commit to allow app integration to be atomic.
    return SCHEMA_VERSION


def save_window(conn, source_id, metric, start, end, status, points, *, run_id=None):
    if source_id not in {p for p,_,_ in PROVIDERS}:
        raise ValueError("Fuente de archivo no reconocida")
    if status not in {"ok","empty","partial","failed"}:
        raise ValueError("Estado de ventana no reconocido")
    if not isinstance(points,int) or points < 0:
        raise ValueError("Data points inválidos")
    first = dt.date.fromisoformat(str(start))
    exclusive = dt.date.fromisoformat(str(end))
    if exclusive <= first:
        raise ValueError("Ventana vacía/invertida")
    conn.execute(
        "INSERT INTO archive_ingest_windows "
        "(source_id,metric,start_utc,end_exclusive_utc,status,data_points,"
        "fetched_at_utc,last_run_id) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id,metric,start_utc,end_exclusive_utc) "
        "DO UPDATE SET status=excluded.status,data_points=excluded.data_points,"
        "fetched_at_utc=excluded.fetched_at_utc,last_run_id=excluded.last_run_id",
        (source_id,metric,first.isoformat(),exclusive.isoformat(),status,points,
         dt.datetime.now(dt.timezone.utc).isoformat(),run_id),
    )


def archive_schema_installed(conn):
    """Read-only schema status, safe to call from ordinary daily ingests."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='archive_schema_migrations'"
    ).fetchone() is not None


def require_archive_schema(conn):
    """History apply is opt-in only after WAL-safe backup + migration."""
    if not archive_schema_installed(conn):
        raise RuntimeError(
            "Instala primero la migración segura: "
            "python3 scripts/archive_db_migrate.py --apply"
        )
    version = conn.execute(
        "SELECT MAX(version) FROM archive_schema_migrations"
    ).fetchone()[0]
    if version != SCHEMA_VERSION:
        raise RuntimeError("Esquema histórico incompatible, detener ingesta")
