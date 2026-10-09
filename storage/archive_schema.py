"""Additive research archive schema, independent of pre-existing analytical tables.

No DROP, no destructive migration, no unit conversion. SQL migrations are
idempotent and all changes happen on the local SQLite machine only.
"""
import sqlite3
import datetime as dt
import hashlib
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS archive_sources (
  provider TEXT PRIMARY KEY,
  access_tier TEXT,
  cadence TEXT,
  updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_series (
  provider TEXT NOT NULL,
  metric TEXT NOT NULL,
  unit TEXT,
  raw_scale TEXT,
  endpoint TEXT,
  first_accessible_utc TEXT,
  last_accessible_utc TEXT,
  PRIMARY KEY(provider,metric),
  FOREIGN KEY(provider) REFERENCES archive_sources(provider)
);
CREATE TABLE IF NOT EXISTS archive_fetch_runs (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  metric TEXT NOT NULL,
  requested_from_utc TEXT NOT NULL,
  requested_to_utc TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('planned','completed','empty','failed','partial')),
  rows_received INTEGER NOT NULL DEFAULT 0 CHECK(rows_received >= 0),
  rows_saved INTEGER NOT NULL DEFAULT 0 CHECK(rows_saved >= 0),
  fetched_at_utc TEXT NOT NULL,
  error_code TEXT,
  FOREIGN KEY(provider,metric) REFERENCES archive_series(provider,metric)
);
CREATE INDEX IF NOT EXISTS idx_archive_runs_series
 ON archive_fetch_runs(provider,metric,fetched_at_utc);
CREATE TABLE IF NOT EXISTS archive_observation_revisions (
  provider TEXT NOT NULL,
  metric TEXT NOT NULL,
  observed_utc TEXT NOT NULL,
  observed_value REAL NOT NULL,
  value_unit TEXT,
  fetched_at_utc TEXT NOT NULL,
  raw_hash_sha256 TEXT NOT NULL,
  PRIMARY KEY(provider,metric,observed_utc,raw_hash_sha256),
  FOREIGN KEY(provider,metric) REFERENCES archive_series(provider,metric)
);
CREATE INDEX IF NOT EXISTS idx_archive_revisions_asof
 ON archive_observation_revisions(provider,metric,observed_utc,fetched_at_utc);
CREATE TABLE IF NOT EXISTS archive_coverage (
  provider TEXT NOT NULL,
  metric TEXT NOT NULL,
  first_observed_utc TEXT,
  last_observed_utc TEXT,
  observation_count INTEGER NOT NULL DEFAULT 0 CHECK(observation_count>=0),
  verified_at_utc TEXT NOT NULL,
  completeness TEXT NOT NULL CHECK(completeness IN
    ('unknown','partial','provider_limited','complete_verified')),
  PRIMARY KEY(provider,metric),
  FOREIGN KEY(provider,metric) REFERENCES archive_series(provider,metric)
);
"""


def migrate(db_path):
    if not Path(db_path).is_file():
        raise FileNotFoundError("No se crea una nueva BD por accidente")
    with sqlite3.connect(db_path, timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(DDL)


def register_source(connection, provider, tier, now):
    connection.execute(
        "INSERT INTO archive_sources(provider,access_tier,cadence,updated_at_utc)"
        " VALUES (?,?,?,?) ON CONFLICT(provider) DO UPDATE SET "
        "access_tier=excluded.access_tier,updated_at_utc=excluded.updated_at_utc",
        (provider, tier, "daily", now))


def register_series(connection, provider, metric, unit, raw_scale, endpoint):
    connection.execute(
        "INSERT INTO archive_series(provider,metric,unit,raw_scale,endpoint)"
        " VALUES(?,?,?,?,?) ON CONFLICT(provider,metric) DO UPDATE SET "
        "unit=excluded.unit,raw_scale=excluded.raw_scale,endpoint=excluded.endpoint",
        (provider,metric,unit,raw_scale,endpoint))


def record_batch(db_path, provider, slug, metric, first, end, observed, tier):
    """One ACID transaction for fetch log, raw versions, and coverage."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        register_source(conn, provider, tier, now)
        register_series(conn, provider, slug, metric.unit, metric.raw_scale,
                        metric.endpoint + "/" + slug)
        for _, (observed_at, value) in observed.items():
            digest = hashlib.sha256(
                f"{slug}|{observed_at}|{value!r}".encode("utf-8")
            ).hexdigest()
            conn.execute(
                "INSERT OR IGNORE INTO archive_observation_revisions "
                "(provider,metric,observed_utc,observed_value,value_unit,"
                "fetched_at_utc,raw_hash_sha256) VALUES (?,?,?,?,?,?,?)",
                (provider, slug, observed_at, value, metric.unit, now, digest)
            )
        conn.execute(
            "INSERT INTO archive_fetch_runs "
            "(provider,metric,requested_from_utc,requested_to_utc,status,"
            "rows_received,rows_saved,fetched_at_utc) VALUES (?,?,?,?,?,?,?,?)",
            (provider, slug, first, end, "completed" if observed else "empty",
             len(observed), len(observed), now)
        )
        summary = conn.execute(
            "SELECT MIN(observed_utc),MAX(observed_utc),COUNT(DISTINCT observed_utc) "
            "FROM archive_observation_revisions WHERE provider=? AND metric=?",
            (provider, slug)
        ).fetchone()
        conn.execute(
            "INSERT INTO archive_coverage "
            "(provider,metric,first_observed_utc,last_observed_utc,"
            "observation_count,verified_at_utc,completeness) "
            "VALUES (?,?,?,?,?,?,'partial') ON CONFLICT(provider,metric)"
            " DO UPDATE SET first_observed_utc=excluded.first_observed_utc,"
            " last_observed_utc=excluded.last_observed_utc,"
            " observation_count=excluded.observation_count,"
            " verified_at_utc=excluded.verified_at_utc",
            (provider, slug, summary[0], summary[1], summary[2], now)
        )
