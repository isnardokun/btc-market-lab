#!/usr/bin/env python3
"""Archive BTC/Yahoo 1d closes ONLY for fully completed UTC calendar days.

The price archive previously contained partial bars written during the UTC
day in progress. Skipping them during a new import is insufficient: already
stored rows must also be purged, or snapshots compare against stale partials.
"""
import urllib.request
import json
import sqlite3
import datetime
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH
from ingestion.daily_cutoff import previous_completed_utc_day
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy

URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?interval=1d&range=2y"
GLOBAL_LOCK_NAMESPACE = "btc-research-global-writer-v1"


def _ingest_unlocked():
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    cutoff = previous_completed_utc_day(now)
    utc_today_start = int(datetime.datetime.combine(
        today, datetime.time.min, tzinfo=datetime.timezone.utc
    ).timestamp())

    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read())

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    inserted = 0
    skipped_open = 0
    with sqlite3.connect(DB_PATH) as db:
        # Remove records that prior releases stored while the day was
        # unfinished. Doing this in the same transaction avoids a mixed state.
        cur = db.execute(
            "DELETE FROM price_btc WHERE ts >= ?", (utc_today_start,)
        )
        purged = cur.rowcount
        for ts, price in zip(timestamps, closes):
            date_utc = datetime.datetime.fromtimestamp(
                ts, datetime.timezone.utc
            ).date()
            if date_utc > cutoff:
                skipped_open += 1
                continue
            if price is None:
                continue
            db.execute(
                "INSERT OR REPLACE INTO price_btc (ts, price) VALUES (?, ?)",
                (int(ts), price)
            )
            inserted += 1

    print(
        f"price_btc: {inserted} updated through {cutoff} UTC; "
        f"{skipped_open} open-day bars skipped; "
        f"{purged} pre-existing partial/future rows purged"
    )


def ingest(*, lock_directory=None):
    """Ingest BTC closes; optional guard MUST precede HTTP and SQL.

    Default behavior remains unchanged for daily.sh. Adding an opt-in
    DOES NOT protect other SQLite writers or enable production.
    """
    if lock_directory is None:
        return _ingest_unlocked()
    with hold_writer_lock(lock_directory, namespace=GLOBAL_LOCK_NAMESPACE):
        return _ingest_unlocked()


def main(argv=None):
    parser = argparse.ArgumentParser(description="BTC UTC daily Yahoo price ingestion")
    parser.add_argument("--global-lock-dir", type=Path, default=None,
                        help="Existing cooperative writer-lock directory; explicit opt-in")
    args = parser.parse_args(argv)
    try:
        return ingest(lock_directory=args.global_lock_dir) or 0
    except WriterLockBusy:
        print("BLOCKED_WRITER_BUSY: price ingest skipped before HTTP/SQL", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
