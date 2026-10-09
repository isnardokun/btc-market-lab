#!/usr/bin/env python3
"""
BTC Research System — Daily Ingestion Script
Fetches on-chain series from bitview.space API and stores in SQLite.

Usage:
    python3 ingest.py              # normal daily run (last 30 days + latest block)
    python3 ingest.py --full        # full historical backfill (2010-presente)
    python3 ingest.py --series sopr # ingest only one series
    python3 ingest.py --dry-run     # test API without writing to DB
"""
import sys, os, json, time, argparse
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

API = config.API_BASE

# ─── Database schema ─────────────────────────────────────────────────────────

SCHEMA_SERIES = """
CREATE TABLE IF NOT EXISTS series (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT
);
"""

SCHEMA_DAILY = """
CREATE TABLE IF NOT EXISTS daily (
    id          INTEGER PRIMARY KEY,
    series_id   INTEGER NOT NULL REFERENCES series(id),
    ts          INTEGER NOT NULL,   -- UTC midnight unix timestamp
    block_height INTEGER,
    value       REAL,
    UNIQUE(series_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_daily_series_ts ON daily(series_id, ts);
CREATE INDEX IF NOT EXISTS idx_daily_ts       ON daily(ts);
"""

SCHEMA_BLOCKS = """
CREATE TABLE IF NOT EXISTS blocks (
    id          INTEGER PRIMARY KEY,
    height      INTEGER NOT NULL UNIQUE,
    ts          INTEGER NOT NULL,   -- block timestamp
    fetched_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height);
"""

SCHEMA_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# ─── Helpers ────────────────────────────────────────────────────────────────

def get_db():
    import sqlite3
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    db = sqlite3.connect(config.DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db(db):
    db.executescript(SCHEMA_SERIES)
    db.executescript(SCHEMA_DAILY)
    db.executescript(SCHEMA_BLOCKS)
    db.executescript(SCHEMA_META)
    db.commit()

def api_fetch(path: str, retries=3, delay=1.0):
    url = f"{API}{path}"
    for attempt in range(retries):
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
                return data
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise RuntimeError(f"API failed after {retries} attempts: {e}\nURL: {url}")

def series_name_to_api_name(name: str, index: str) -> str:
    """Map our short names to bitview series names."""
    return name  # already aligned with config

def fetch_tip_height() -> int:
    """Get current blockchain tip height."""
    return int(api_fetch("/blocks/tip/height"))

def fetch_series(series_name: str, index: str, start: str = None, end: str = None, block_start: int = None, block_end: int = None):
    """Fetch a series from bitview API. Returns list of [ts_or_height, value] pairs."""
    params = []
    if start:
        params.append(f"start={start}")
    if end:
        params.append(f"end={end}")
    if block_start is not None:
        params.append(f"start={block_start}")
    if block_end is not None:
        params.append(f"end={block_end}")
    query = "&".join(params)
    path = f"/series/{series_name}/{index}"
    if query:
        path += f"?{query}"

    data = api_fetch(path)
    return data  # dict with 'data' key

def get_series_id(db, name: str, description: str = "") -> int:
    """Get or create series id."""
    cur = db.execute("SELECT id FROM series WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    db.execute("INSERT INTO series (name, description) VALUES (?, ?)", (name, description))
    db.commit()
    return db.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()[0]

def save_daily(db, series_id: int, ts: int, block_height: int, value: float):
    """Save one data point, upsert on conflict."""
    db.execute("""
        INSERT INTO daily (series_id, ts, block_height, value)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(series_id, ts) DO UPDATE SET
            block_height=excluded.block_height,
            value=excluded.value
    """, (series_id, ts, block_height, value))

def get_last_ts(db, series_name: str) -> int | None:
    """Get last ingested timestamp for a series."""
    sid = get_series_id(db, series_name)
    row = db.execute(
        "SELECT ts FROM daily WHERE series_id=? ORDER BY ts DESC LIMIT 1", (sid,)
    ).fetchone()
    return row[0] if row else None

def ts_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def date_to_ts(d: str) -> int:
    """Calendar dates in bitview.daily are UTC, regardless of host timezone."""
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def complete_utc_window(last_ts: int | None, now_utc: datetime | None = None):
    """Incremental inclusive dates ending on last *completed* UTC day.

    A daily series should never request data for the still-open calendar day.
    The provider can publish late: completed-day gaps are still retried and
    logged, while truly up-to-date series skip unnecessary HTTP requests.
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    cutoff = now.astimezone(timezone.utc).date() - timedelta(days=1)
    if last_ts is not None:
        start = datetime.fromtimestamp(last_ts, timezone.utc).date() + timedelta(days=1)
    else:
        start = cutoff - timedelta(days=90)
    if start > cutoff:
        return None
    return start.isoformat(), cutoff.isoformat()

# ─── Ingestion per series ────────────────────────────────────────────────────

def ingest_series(db, short_name: str, series_name: str, index: str,
                  force_start: str = None, force_end: str = None,
                  dry_run: bool = False):
    """Ingest one series. Returns count of rows saved."""

    # Determine start/end without requesting the incomplete current UTC date.
    if force_start is not None or force_end is not None:
        if force_start is None or force_end is None:
            raise ValueError("force_start and force_end must be provided together")
        start_str, end_str = force_start, force_end
    else:
        window = complete_utc_window(get_last_ts(db, short_name))
        if window is None:
            print(f"  [CURRENT] {short_name}: all completed UTC days already stored")
            return 0
        start_str, end_str = window

    series_desc = config.SERIES[short_name][2] if short_name in config.SERIES else ""
    series_id = get_series_id(db, short_name, series_desc)

    # Fetch
    path = f"/series/{series_name}/{index}?start={start_str}&end={end_str}"
    result = api_fetch(path)

    data = result.get("data", [])
    idx_type = result.get("index", "")

    count = 0
    if not data:
        print(f"  [WARN] {short_name}: provider missing completed UTC observations "
              f"({start_str} → {end_str}); will retry next run")
        return 0

    # result["start"] = days since 2009-01-01
    # ts (Unix) = 1230768000 + (start_days + i) * period_seconds
    BITVIEW_EPOCH_SEC = 1230768000  # Unix epoch for 2009-01-01 00:00:00 UTC
    idx_type = result.get("index", "")
    period = 86400  # daily
    if "week" in idx_type or "7" in idx_type:
        period = 86400 * 7
    elif "month" in idx_type:
        period = 86400 * 30

    for i, val in enumerate(data):
        if val is None:
            continue
        ts = BITVIEW_EPOCH_SEC + (result["start"] + i) * period
        save_daily(db, series_id, ts, None, float(val))
        count += 1

    if not dry_run:
        db.commit()

    print(f"  [{'OK' if count else 'SKIP'}] {short_name}: {count} rows ({start_str} → {end_str})")
    return count

# ─── Full backfill ──────────────────────────────────────────────────────────

def backfill_full(db, short_name: str, series_name: str, index: str):
    """Backfill entire history for a series in yearly chunks."""
    print(f"  Full backfill: {short_name}")

    # Known data start dates per series type
    PRICE_START = "2010-07-18"
    ONCHAIN_START = "2011-01-01"
    MINER_START = "2011-01-01"

    # For block-height series, use block ranges
    if index == "height":
        tip = fetch_tip_height()
        # Go back in chunks of 100k blocks
        chunk_size = 100_000
        start = max(0, tip - chunk_size)
        while start >= 0:
            end = min(start + chunk_size, tip)
            try:
                ingest_series(db, short_name, series_name, index,
                              force_start=str(start), force_end=str(end))
            except Exception as e:
                print(f"  [ERR] block range {start}-{end}: {e}")
            start -= chunk_size
    else:
        # For date-indexed series
        end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        # Go back 5 years in 1-year chunks, then everything before that in one shot
        for years_back in [1, 2, 3, 4, 5]:
            start = (datetime.now(timezone.utc) - timedelta(days=365*years_back)).strftime("%Y-%m-%d")
            try:
                ingest_series(db, short_name, series_name, index,
                              force_start=start, force_end=end)
            except Exception as e:
                print(f"  [ERR] range {start}-{end}: {e}")
            end = start
        # Final backfill pre-2021
        try:
            ingest_series(db, short_name, series_name, index,
                          force_start="2010-01-01", force_end=end)
        except Exception as e:
            print(f"  [ERR] pre-2021: {e}")

# ─── Update block table ─────────────────────────────────────────────────────

def update_tip_height(db):
    """Update the meta table with current tip height."""
    h = fetch_tip_height()
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('tip_height', ?)", (str(h),))
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_ingest_ts', ?)",
                (str(int(time.time())),))
    db.commit()
    return h

# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BTC Research — Daily Ingestion")
    parser.add_argument("--full", action="store_true", help="Full historical backfill")
    parser.add_argument("--dry-run", action="store_true", help="Test API without writing")
    parser.add_argument("--series", type=str, help="Ingest only one series by short name")
    args = parser.parse_args()

    db = get_db()
    init_db(db)

    print(f"\n{'='*60}")
    print(f"BTC Research Ingestion  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"DB: {config.DB_PATH}")
    print(f"{'='*60}\n")

    # Update tip
    tip = update_tip_height(db)
    print(f"Tip height: {tip:,}\n")

    if args.series:
        if args.series not in config.SERIES_CATALOG:
            print(f"Unknown series: {args.series}")
            sys.exit(1)
        s_name, s_idx, s_desc = config.SERIES_CATALOG[args.series]
        if args.full:
            backfill_full(db, args.series, s_name, s_idx)
        else:
            ingest_series(db, args.series, s_name, s_idx, dry_run=args.dry_run)
    else:
        total = 0
        for short, (s_name, s_idx, s_desc) in config.SERIES.items():
            print(f"\nIngesting: {short} ({s_name}/{s_idx})")
            try:
                if args.full:
                    backfill_full(db, short, s_name, s_idx)
                else:
                    n = ingest_series(db, short, s_name, s_idx, dry_run=args.dry_run)
                    total += n
            except Exception as e:
                print(f"  [FATAL] {short}: {e}")

        print(f"\n{'='*60}")
        print(f"Done. Total rows this run: ~{total}")
        print(f"{'='*60}")

if __name__ == "__main__":
    main()
