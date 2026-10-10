"""Additive SQLite-only persistence for ETF, derivatives and macro events.

All fetched source bodies and revised observations live INSIDE btc_research.db.
No JSON/CSV data cache. Schema installation is deliberately opt-in and WAL-
safe backed up by scripts/market_context_migrate.py.
"""
import datetime as dt
import hashlib
import json
import math
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS market_context_migrations(
 version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_source_cursors(
 provider TEXT NOT NULL, stream TEXT NOT NULL, cursor TEXT NOT NULL,
 updated_utc TEXT NOT NULL, PRIMARY KEY(provider,stream));
CREATE TABLE IF NOT EXISTS market_source_runs(
 run_id TEXT PRIMARY KEY, provider TEXT NOT NULL, started_utc TEXT NOT NULL,
 ended_utc TEXT, status TEXT NOT NULL CHECK(status IN ('running','success','empty','failed')),
 requests INTEGER NOT NULL DEFAULT 0, points INTEGER NOT NULL DEFAULT 0,
 error_code TEXT);
CREATE INDEX IF NOT EXISTS idx_market_runs_provider ON market_source_runs(provider,started_utc);
CREATE TABLE IF NOT EXISTS market_raw_payloads(
 sha256 TEXT PRIMARY KEY, provider TEXT NOT NULL, endpoint TEXT NOT NULL,
 fetched_utc TEXT NOT NULL, content_type TEXT NOT NULL, body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS market_etf_flows(
 provider TEXT NOT NULL, trade_date TEXT NOT NULL, ticker TEXT NOT NULL,
 net_flow_usd_m REAL NOT NULL, state TEXT NOT NULL CHECK(state IN ('reported','preliminary')),
 raw_sha256 TEXT NOT NULL, fetched_utc TEXT NOT NULL,
 PRIMARY KEY(provider,trade_date,ticker),
 FOREIGN KEY(raw_sha256) REFERENCES market_raw_payloads(sha256));
CREATE INDEX IF NOT EXISTS idx_market_etf_date ON market_etf_flows(trade_date,ticker);
CREATE TABLE IF NOT EXISTS market_derivatives(
 provider TEXT NOT NULL, symbol TEXT NOT NULL,
 metric TEXT NOT NULL CHECK(metric IN ('open_interest','funding_settled')),
 observed_utc TEXT NOT NULL, interval_label TEXT NOT NULL,
 raw_value REAL NOT NULL, raw_unit TEXT NOT NULL,
 quote_usd REAL, source_endpoint TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
 fetched_utc TEXT NOT NULL,
 PRIMARY KEY(provider,symbol,metric,observed_utc,interval_label),
 FOREIGN KEY(raw_sha256) REFERENCES market_raw_payloads(sha256));
CREATE INDEX IF NOT EXISTS idx_market_deriv_time
 ON market_derivatives(provider,metric,symbol,observed_utc);
CREATE TABLE IF NOT EXISTS market_calendar_events(
 provider TEXT NOT NULL, uid TEXT NOT NULL, title TEXT NOT NULL,
 scheduled_utc TEXT, time_precision TEXT NOT NULL
 CHECK(time_precision IN ('utc','date_only','unconfirmed')),
 event_date TEXT NOT NULL, source_url TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('scheduled','cancelled')),
 raw_sha256 TEXT NOT NULL, fetched_utc TEXT NOT NULL,
 PRIMARY KEY(provider,uid), FOREIGN KEY(raw_sha256) REFERENCES market_raw_payloads(sha256));
CREATE INDEX IF NOT EXISTS idx_market_calendar_time ON market_calendar_events(event_date,scheduled_utc);
CREATE TABLE IF NOT EXISTS market_context_revisions(
 revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
 table_name TEXT NOT NULL, key_json TEXT NOT NULL, old_json TEXT NOT NULL,
 new_json TEXT NOT NULL, recorded_utc TEXT NOT NULL);
"""

def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

# v3 DDL — used only for NEW installations (v1→v3 or fresh)
REQUEST_LINEAGE_DDL = """
CREATE TABLE IF NOT EXISTS market_request_lineage(
 request_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL UNIQUE REFERENCES market_source_runs(run_id),
 provider TEXT NOT NULL CHECK(provider IN ('binance','bybit')),
 stream TEXT NOT NULL CHECK(stream IN ('open_interest','funding_settled')),
 symbol TEXT NOT NULL CHECK(symbol='BTCUSDT'),
 interval_label TEXT NOT NULL,
 endpoint_path TEXT NOT NULL,
 requested_start_ms INTEGER,
 requested_end_ms INTEGER,
 requested_limit INTEGER NOT NULL CHECK(requested_limit BETWEEN 1 AND 1000),
 attempted_utc TEXT NOT NULL,
 ended_utc TEXT NOT NULL,
 http_attempts INTEGER NOT NULL CHECK(http_attempts BETWEEN 0 AND 1),
 status TEXT NOT NULL CHECK(status IN ('success','empty','failed')),
 error_class TEXT,
 raw_sha256 TEXT REFERENCES market_raw_payloads(sha256),
 returned_rows INTEGER NOT NULL CHECK(returned_rows BETWEEN 0 AND 1000),
 persisted_rows INTEGER NOT NULL CHECK(persisted_rows BETWEEN 0 AND 1000),
 first_observed_utc TEXT,
 last_observed_utc TEXT,
 direction TEXT NOT NULL CHECK(direction IN ('backward','forward')),
 CHECK(status='failed' OR (http_attempts=1 AND raw_sha256 IS NOT NULL)),
 CHECK(status!='failed' OR (raw_sha256 IS NULL AND persisted_rows=0))
);
"""

# One acquisition record PER REQUEST — not per SHA.
# request_id is NOT NULL and FK'd to lineage (each request = one acquisition).
# The same SHA+provider+endpoint can appear in multiple acquisitions (different requests).
# raw_sha256 is NOT NULL and FK'd to market_raw_payloads.
REQUEST_ACQUISITIONS_DDL = """
CREATE TABLE IF NOT EXISTS market_request_acquisitions(
 acquired_id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL REFERENCES market_request_lineage(request_id),
 provider TEXT NOT NULL,
 endpoint_path TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL REFERENCES market_raw_payloads(sha256),
 acquired_utc TEXT NOT NULL,
 UNIQUE(request_id)
);
"""

def request_lineage_installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='market_request_lineage'").fetchone() is not None

def install_request_lineage(db):
    """
    Additive upgrade: adds missing columns to EXISTING market_request_lineage table.
    
    Cases:
      - No lineage table: CREATE v3 (full schema with all columns)
      - v1 present: ALTER to add stream, symbol, interval_label
      - v2 present: ALTER to add direction, requested_start_ms
      - v3 present: idempotent no-op
    
    Caller controls the transaction and backup.
    """
    require(db)

    existing_cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_lineage)")}

    if not existing_cols:
        # Fresh install: create v3 schema (complete)
        db.execute(REQUEST_LINEAGE_DDL)
        db.execute(REQUEST_ACQUISITIONS_DDL)
        db.execute("INSERT OR IGNORE INTO market_context_migrations(version,applied_at_utc)"
                   " VALUES(2,?),(3,?)",(now_utc(),now_utc()))
        return

    # Upgrade existing table: add missing columns one by one
    # v1→v2: stream, symbol, interval_label (v2 introduced these)
    if "stream" not in existing_cols:
        db.execute("ALTER TABLE market_request_lineage ADD COLUMN stream TEXT NOT NULL "
                   "CHECK(stream IN ('open_interest','funding_settled'))")
    if "symbol" not in existing_cols:
        db.execute("ALTER TABLE market_request_lineage ADD COLUMN symbol TEXT NOT NULL "
                   "CHECK(symbol='BTCUSDT')")
    if "interval_label" not in existing_cols:
        db.execute("ALTER TABLE market_request_lineage ADD COLUMN interval_label TEXT NOT NULL")

    # v2→v3: direction, requested_start_ms
    if "direction" not in existing_cols:
        db.execute("ALTER TABLE market_request_lineage ADD COLUMN direction TEXT NOT NULL "
                   "DEFAULT 'backward' CHECK(direction IN ('backward','forward'))")
    if "requested_start_ms" not in existing_cols:
        db.execute("ALTER TABLE market_request_lineage ADD COLUMN requested_start_ms INTEGER")

    # Mark migrations as applied (idempotent — INSERT OR IGNORE handles re-runs)
    db.execute("INSERT OR IGNORE INTO market_context_migrations(version,applied_at_utc)"
               " VALUES(2,?),(3,?)",(now_utc(),now_utc()))

    # Create acquisitions table (always safe with IF NOT EXISTS)
    db.execute(REQUEST_ACQUISITIONS_DDL)

def install_request_lineage_v2_only(db):
    """
    Legacy v2 installer — used ONLY for tests that seed v1 schema and
    expect v2 migration. Creates v2-compatible table (without direction/requested_start_ms).
    """
    require(db)
    V2_DDL = """
    CREATE TABLE IF NOT EXISTS market_request_lineage(
     request_id TEXT PRIMARY KEY,
     run_id TEXT NOT NULL UNIQUE REFERENCES market_source_runs(run_id),
     provider TEXT NOT NULL CHECK(provider IN ('binance','bybit')),
     stream TEXT NOT NULL CHECK(stream IN ('open_interest','funding_settled')),
     symbol TEXT NOT NULL CHECK(symbol='BTCUSDT'),
     interval_label TEXT NOT NULL,
     endpoint_path TEXT NOT NULL,
     requested_end_ms INTEGER,
     requested_limit INTEGER NOT NULL CHECK(requested_limit BETWEEN 1 AND 1000),
     attempted_utc TEXT NOT NULL,
     ended_utc TEXT NOT NULL,
     http_attempts INTEGER NOT NULL CHECK(http_attempts BETWEEN 0 AND 1),
     status TEXT NOT NULL CHECK(status IN ('success','empty','failed')),
     error_class TEXT,
     raw_sha256 TEXT REFERENCES market_raw_payloads(sha256),
     returned_rows INTEGER NOT NULL CHECK(returned_rows BETWEEN 0 AND 1000),
     persisted_rows INTEGER NOT NULL CHECK(persisted_rows BETWEEN 0 AND 1000),
     first_observed_utc TEXT,
     last_observed_utc TEXT,
     CHECK(status='failed' OR (http_attempts=1 AND raw_sha256 IS NOT NULL)),
     CHECK(status!='failed' OR (raw_sha256 IS NULL AND persisted_rows=0))
    );
    """
    db.execute(V2_DDL)
    db.execute("INSERT OR IGNORE INTO market_context_migrations(version,applied_at_utc)"
               " VALUES(2,?)",(now_utc(),))

def write_request_lineage(db,*,request_id,run_id,provider,metric,
                          interval_label,endpoint_path,requested_end_ms,
                          requested_limit,attempted_utc,ended_utc,http_attempts,
                          status,error_class=None,raw_sha256=None,returned_rows=0,
                          persisted_rows=0,first_observed_utc=None,last_observed_utc=None,
                          direction="backward",requested_start_ms=None):
    require(db)
    if not request_lineage_installed(db):
        raise RuntimeError("Market request lineage v2 not migrated")
    if not endpoint_path.startswith("/") or "?" in endpoint_path:
        raise ValueError("Only sanitized provider endpoint paths are allowed")
    if provider not in ("binance","bybit") or metric not in ("open_interest","funding_settled"):
        raise ValueError("Bad provider/stream")
    if status not in ("success","empty","failed") or http_attempts not in (0,1):
        raise ValueError("Bad request receipt")
    if status=="failed" and (raw_sha256 is not None or persisted_rows):
        raise ValueError("Failed request cannot retain uncommitted data")
    if direction not in ("backward","forward"):
        raise ValueError("direction must be backward or forward")
    db.execute(
      "INSERT INTO market_request_lineage"
      "(request_id,run_id,provider,stream,symbol,interval_label,endpoint_path,"
      "requested_end_ms,requested_limit,attempted_utc,ended_utc,http_attempts,"
      "status,error_class,raw_sha256,returned_rows,persisted_rows,"
      "first_observed_utc,last_observed_utc,direction,requested_start_ms)"
      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (request_id,run_id,provider,metric,"BTCUSDT",interval_label,endpoint_path,
       requested_end_ms,requested_limit,attempted_utc,ended_utc,http_attempts,
       status,error_class,raw_sha256,returned_rows,persisted_rows,
       first_observed_utc,last_observed_utc,direction,requested_start_ms))


def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_context_migrations'").fetchone() is not None

def install(db):
    if not isinstance(db,sqlite3.Connection):
        raise TypeError("SQLite connection required")
    db.executescript(DDL)
    db.execute("INSERT OR IGNORE INTO market_context_migrations VALUES(1,?)",(now_utc(),))
    install_request_lineage(db)

def require(db):
    if not installed(db):
        raise RuntimeError("SQLite schema absent: first run python3 scripts/market_context_migrate.py --apply")

def raw_payload(db,provider,endpoint,body,kind):
    require(db)
    if not isinstance(body,bytes) or not 0 < len(body) <= 3_000_000:
        raise ValueError("Source body outside approved bounds")
    digest=hashlib.sha256(body).hexdigest()
    db.execute("INSERT OR IGNORE INTO market_raw_payloads VALUES(?,?,?,?,?,?)",
               (digest,provider,endpoint,now_utc(),kind,body))
    return digest

def _finite(value):
    if isinstance(value,bool):
        raise ValueError("Boolean is not a numeric observation")
    x=float(value)
    if not math.isfinite(x):
        raise ValueError("Nonfinite numerical observation")
    return x

def _date(value):
    return dt.date.fromisoformat(str(value)).isoformat()

def _timestamp(value):
    parsed=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp required")
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds")

def _upsert(db,table,key,fields):
    require(db)
    allowed={
      "market_etf_flows":("provider","trade_date","ticker"),
      "market_derivatives":("provider","symbol","metric","observed_utc","interval_label"),
      "market_calendar_events":("provider","uid")
    }
    if table not in allowed or tuple(key)!=allowed[table]:
        raise ValueError("Unapproved table or key")
    names=list(fields)
    if not set(key)<=set(names) or any(not k.replace("_","").isalnum() for k in names):
        raise ValueError("Bad column selection")
    where=" AND ".join(k+"=?" for k in key)
    old=db.execute("SELECT * FROM "+table+" WHERE "+where,[fields[k] for k in key]).fetchone()
    old_fields=[d[1] for d in db.execute("PRAGMA table_info("+table+")")]
    if old is not None:
        previous=dict(zip(old_fields,old))
        # fetched_utc intentionally does NOT create a revision on identical data
        meaningful={n:fields[n] for n in names if n not in ("fetched_utc","raw_sha256")}
        if all(previous[n]==v for n,v in meaningful.items()):
            return False
        revised=dict(previous)
        revised.update(fields)
        db.execute("INSERT INTO market_context_revisions"
                   "(table_name,key_json,old_json,new_json,recorded_utc) VALUES(?,?,?,?,?)",
                   (table,json.dumps({k:fields[k] for k in key},sort_keys=True),
                    json.dumps(previous,sort_keys=True),json.dumps(revised,sort_keys=True),
                    now_utc()))
    columns=",".join(names)
    q=",".join("?" for _ in names)
    update=",".join(n+"=excluded."+n for n in names if n not in key)
    db.execute("INSERT INTO "+table+"("+columns+") VALUES("+q+") "
               "ON CONFLICT("+",".join(key)+") DO UPDATE SET "+update,
               list(fields.values()))
    return True

def store_etf(db,*,provider,trade_date,ticker,amount_m,state,sha):
    if provider!="farside" or not ticker.isupper() or not ticker.isalnum() :
        raise ValueError("Invalid ETF provider/ticker")
    if state not in ("reported","preliminary"):
        raise ValueError("Invalid ETF state")
    return _upsert(db,"market_etf_flows",("provider","trade_date","ticker"),
       dict(provider=provider,trade_date=_date(trade_date),ticker=ticker,
            net_flow_usd_m=_finite(amount_m),state=state,
            raw_sha256=sha,fetched_utc=now_utc()))

def store_derivative(db,*,provider,symbol,metric,observed_utc,interval_label,
                     value,unit,endpoint,sha,quote_usd=None):
    if provider not in ("binance","bybit") or symbol!="BTCUSDT":
        raise ValueError("Unknown derivative instrument")
    if metric not in ("open_interest","funding_settled"):
        raise ValueError("Invalid derivative metric")
    if unit not in ("BTC","USD","fraction"):
        raise ValueError("Invalid raw unit")
    if (metric=="funding_settled" and unit!="fraction" or
        metric=="open_interest" and unit=="fraction"):
        raise ValueError("Incompatible derivative unit")
    x=_finite(value)
    if metric=="open_interest" and x<0:
        raise ValueError("OI must not be negative")
    if metric=="funding_settled" and abs(x)>0.2:
        raise ValueError("Funding rate suspicious; reject")
    return _upsert(db,"market_derivatives",
      ("provider","symbol","metric","observed_utc","interval_label"),
      dict(provider=provider,symbol=symbol,metric=metric,
           observed_utc=_timestamp(observed_utc),interval_label=interval_label,
           raw_value=x,raw_unit=unit,
           quote_usd=_finite(quote_usd) if quote_usd is not None else None,
           source_endpoint=endpoint,raw_sha256=sha,fetched_utc=now_utc()))

def store_event(db,*,provider,uid,title,scheduled_utc,event_date,precision,source_url,sha,state="scheduled"):
    if provider not in ("bls","fred","fomc") or not 1<=len(uid)<=200 or not 4<len(title)<300:
        raise ValueError("Bad macro event")
    if precision not in ("utc","date_only","unconfirmed") or state not in ("scheduled","cancelled"):
        raise ValueError("Invalid macro precision/status")
    stamp=_timestamp(scheduled_utc) if scheduled_utc else None
    if precision=="utc" and not stamp or precision!="utc" and stamp:
        raise ValueError("Timestamp precision mismatch")
    if stamp and stamp[:10]!=_date(event_date):
        raise ValueError("Calendar UTC day mismatch")
    if not source_url.startswith("https://"):
        raise ValueError("Unattributed calendar event")
    return _upsert(db,"market_calendar_events",("provider","uid"),
       dict(provider=provider,uid=uid,title=title,scheduled_utc=stamp,
            time_precision=precision,event_date=_date(event_date),source_url=source_url,
            state=state,raw_sha256=sha,fetched_utc=now_utc()))

def get_cursor(db,provider,stream):
    require(db)
    row=db.execute("SELECT cursor FROM market_source_cursors WHERE provider=? AND stream=?",
                   (provider,stream)).fetchone()
    return row[0] if row else None

def save_cursor(db,provider,stream,cursor):
    require(db)
    db.execute("INSERT INTO market_source_cursors VALUES(?,?,?,?) "
               "ON CONFLICT(provider,stream) DO UPDATE SET cursor=excluded.cursor,"
               "updated_utc=excluded.updated_utc",
               (provider,stream,str(cursor),now_utc()))
