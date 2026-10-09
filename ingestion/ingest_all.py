#!/usr/bin/env python3
"""
BTC Research — Full ingestion of ALL bitview.space series.
One-time backfill + daily incremental updates.

Usage:
    python3 ingest_all.py --backfill      # full historical (first run)
    python3 ingest_all.py                 # daily incremental (90 days)
    python3 ingest_all.py --dry-run       # test without writing
"""
import sys, os, json, time, argparse, sqlite3, threading, concurrent.futures
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

API       = config.API_BASE
DB_PATH   = config.DB_PATH
VALID_TXT = "/home/ignotus/btc-research/all_series_valid.txt"
LOG_FILE  = "/home/ignotus/btc-research/ingest_all.log"
WORKERS   = 20          # concurrent API workers
BATCH_MS  = 60          # min ms between batches of 20
CHUNK     = 20           # series per concurrent batch

BITVIEW_EPOCH_SEC = 1230768000  # 2009-01-01 00:00:00 UTC

# ─── Schema ────────────────────────────────────────────────────────────────

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    return db

def init_db(db):
    """Never drop populated history: schema initialization must be additive."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            idx TEXT,
            dtype TEXT,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS daily (
            id INTEGER PRIMARY KEY,
            series_id INTEGER NOT NULL REFERENCES series(id),
            ts INTEGER NOT NULL,
            block_height INTEGER,
            value REAL,
            UNIQUE(series_id, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_series_ts ON daily(series_id, ts);
        CREATE INDEX IF NOT EXISTS idx_daily_ts ON daily(ts);
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    db.commit()

# ─── API fetch ─────────────────────────────────────────────────────────────

def fetch_json(path):
    url = f"{API}{path}"
    req = urllib_request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    })
    with urllib_request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

import urllib.request as urllib_request

# ─── Timestamp conversion ──────────────────────────────────────────────────

def day1_to_ts(start_days, i, period=86400):
    """Convert bitview day1 index to Unix timestamp."""
    return BITVIEW_EPOCH_SEC + (start_days + i) * period

def height_to_ts_approx(block_height):
    """Approximate timestamp from block height (4 years = 210,000 blocks, 10 min/block)."""
    return 1230940800 + block_height * 600  # rough approximation

# ─── DB helpers ────────────────────────────────────────────────────────────

def get_series_id(db, name, idx, dtype):
    row = db.execute("SELECT id FROM series WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    db.execute("INSERT INTO series (name, idx, dtype) VALUES (?,?,?)", (name, idx, dtype))
    db.commit()
    return db.execute("SELECT id FROM series WHERE name=?", (name,)).fetchone()[0]

def get_last_ts(db, series_id):
    row = db.execute(
        "SELECT ts FROM daily WHERE series_id=? ORDER BY ts DESC LIMIT 1", (series_id,)
    ).fetchone()
    return row[0] if row else None

def save_rows(db, series_id, rows):
    """rows: list of (ts, block_height, value)"""
    db.executemany(
        "INSERT OR REPLACE INTO daily (series_id, ts, block_height, value) VALUES (?,?,?,?)",
        [(series_id, ts, bh, v) for ts, bh, v in rows]
    )

# ─── Ingest one series ─────────────────────────────────────────────────────

def ingest_one(name, idx, dtype, force_start=None, force_end=None, dry_run=False):
    """Ingest a single series. Returns (name, count, status)."""
    db = get_db()
    series_id = get_series_id(db, name, idx, dtype)

    # Determine date range
    last_ts = None if force_start else get_last_ts(db, series_id)
    if force_start and force_end:
        start_str, end_str = force_start, force_end
    elif last_ts:
        start_str = datetime.utcfromtimestamp(last_ts + 86400).strftime("%Y-%m-%d")
        end_str   = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        end_str   = datetime.utcnow().strftime("%Y-%m-%d")
        start_str = "2010-01-01"  # all available history for new series

    try:
        if idx == "height":
            # Block-indexed — use block range
            tip = int(fetch_json("/blocks/tip/height"))
            # Full historical backfill — all available blocks
            start_bh = max(0, tip - 1000000)  # ~1M blocks covers BTC entire history
            r = fetch_json(f"/series/{name}/height?start={start_bh}&end={tip}")
        else:
            r = fetch_json(f"/series/{name}/{idx}?start={start_str}&end={end_str}")

        data = r.get("data", [])
        if not data:
            return (name, 0, "no_data")

        # Period in seconds
        period = 86400
        if idx in ("1w", "week1"):
            period = 86400 * 7
        elif idx in ("1mo", "month1"):
            period = 86400 * 30

        rows = []
        if idx == "height":
            # data is [[v1, v2, ...], ...] or nested
            start_bh = int(r.get("start", 0))
            if isinstance(data[0], list):
                # Multi-value per block: flatten to one row per sub-value
                for bi, block_data in enumerate(data):
                    bh = start_bh + bi
                    ts_approx = height_to_ts_approx(bh)
                    for vi, v in enumerate(block_data):
                        rows.append((ts_approx, bh, v))
            else:
                for bi, v in enumerate(data):
                    bh = start_bh + bi
                    rows.append((height_to_ts_approx(bh), bh, v))
        else:
            start_days = int(r.get("start", 0))
            for i, v in enumerate(data):
                if v is None:
                    continue
                ts = day1_to_ts(start_days, i, period)
                rows.append((ts, None, v))

        if not dry_run:
            save_rows(db, series_id, rows)
            db.commit()

        return (name, len(rows), "ok")
    except Exception as e:
        return (name, 0, str(e)[:80])
    finally:
        db.close()

# ─── Concurrent batch runner ────────────────────────────────────────────────

def run_all(backfill=False, dry_run=False, limit=None):
    # Load valid series
    with open(VALID_TXT) as f:
        lines = [l.strip().split("\t") for l in f if l.strip()]
    series_list = [(l[0], l[1], l[2] if len(l)>2 else "") for l in lines]
    if limit:
        series_list = series_list[:limit]

    total = len(series_list)
    print(f"\nIngesting {total} series  (backfill={backfill}, workers={WORKERS})")

    lock = threading.Lock()
    done_count = [0]
    ok_count    = [0]
    err_count   = [0]
    total_rows  = [0]
    logf = open(LOG_FILE, "w")

    def worker(item):
        name, idx, dtype = item
        result = ingest_one(name, idx, dtype, dry_run=dry_run)
        with lock:
            done_count[0] += 1
            sname, cnt, status = result
            if status == "ok":
                ok_count[0] += 1
                total_rows[0] += cnt
            else:
                err_count[0] += 1
            logf.write(f"{status}\t{sname}\t{cnt}\n")
            logf.flush()
            if done_count[0] % 100 == 0:
                print(f"  {done_count[0]}/{total}  ok={ok_count[0]} err={err_count[0]} rows={total_rows[0]:,}")

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(worker, item) for item in series_list]
        concurrent.futures.wait(futs)

    elapsed = time.time() - t0
    logf.close()

    print(f"\n{'='*50}")
    print(f"Done in {elapsed/60:.1f} min")
    print(f"OK: {ok_count[0]} series, {total_rows[0]:,} rows")
    print(f"Err: {err_count[0]} series")
    print(f"Log: {LOG_FILE}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--limit",    type=int, default=None)
    args = parser.parse_args()

    db = get_db()
    init_db(db)
    print(f"DB: {DB_PATH}")

    run_all(backfill=args.backfill, dry_run=args.dry_run, limit=args.limit)

if __name__ == "__main__":
    main()
