#!/usr/bin/env python3
"""
backfill_onchain_metrics.py — Backfill historical BTC on-chain metrics
from bitview daily table into daily_metrics.
Uses correct series IDs and date matching via Python datetime.
"""
import sqlite3, datetime
DB_PATH = "/home/ignotus/btc-research/db/btc_research.db"

# series_id → (metric_name, unit)
# Scale functions based on verification vs known values:
# market_cap/realized_cap: divide by 1e12 to get T USD ✓
# hash_rate: /1e18 to get EH/s ✓
# difficulty: >1e12: /1e12 to get T; <1e12: /1e9 to get T (compact units)

def scale_cap(raw):
    return raw / 1e12  # USD -> T USD

def scale_diff(raw):
    if raw > 1e12:
        return raw / 1e12  # T
    else:
        return raw / 1e9   # compact units -> T

SERIES_MAP = {
    1978: ("realized_cap",  "USD_T", scale_cap),
    1330: ("market_cap",    "USD_T", scale_cap),
    7:    ("active_cap",    "USD_T", scale_cap),
    1097: ("hash_rate",    "EH/s",  lambda v: v / 1e18),
    550:  ("difficulty",    "T",      scale_diff),
    5:    ("active_addrs", "count",  None),
    2375: ("utxo_count",   "M",      None),
    2806: ("rhodl_ratio",  "ratio",  None),
    2807: ("nupl",         "ratio",  None),
    1357: ("mvrv",         "ratio",  None),
}

def date_from_ts(ts):
    """Convert Unix timestamp (seconds) to YYYY-MM-DD string."""
    return datetime.date.fromtimestamp(ts).strftime("%Y-%m-%d")

def backfill_onchain():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    # Build set of dates that have price_btc data
    cur.execute("SELECT ts FROM price_btc")
    price_dates = {date_from_ts(row[0]) for row in cur.fetchall()}
    print(f"Price data: {len(price_dates)} days from {min(price_dates)} to {max(price_dates)}")

    total_inserted = 0

    for series_id, (metric, unit, scale_fn) in SERIES_MAP.items():
        cur.execute("SELECT ts, value FROM daily WHERE series_id=? ORDER BY ts", (series_id,))
        rows = cur.fetchall()
        if not rows:
            print(f"  {metric}: NO DATA")
            continue

        inserted = 0
        for ts, raw_value in rows:
            date_str = date_from_ts(ts)
            if date_str not in price_dates:
                continue
            value = scale_fn(raw_value) if scale_fn else raw_value
            cur.execute("""
                INSERT OR IGNORE INTO daily_metrics
                (report_date, asset, metric, value, unit, source)
                VALUES (?, 'BTC', ?, ?, ?, 'bitview')
            """, (date_str, metric, value, unit))
            if cur.rowcount > 0:
                inserted += 1
        print(f"  {metric}: {inserted} new rows (from {date_from_ts(rows[0][0])} to {date_from_ts(rows[-1][0])})")
        total_inserted += inserted

    # Compute MVRV ratio where direct MVRV series is missing
    print("\n  Computing MVRV ratio from market_cap/realized_cap...")
    cur.execute("""
        SELECT a.report_date, a.value / NULLIF(b.value, 0) as mvrv
        FROM daily_metrics a
        JOIN daily_metrics b ON a.report_date = b.report_date
            AND b.asset = 'BTC' AND b.metric = 'realized_cap'
        WHERE a.asset = 'BTC' AND a.metric = 'market_cap'
          AND a.report_date NOT IN (
              SELECT report_date FROM daily_metrics
              WHERE asset = 'BTC' AND metric = 'mvrv' AND value IS NOT NULL
          )
    """)
    mvrv_rows = cur.fetchall()
    mvrv_inserted = 0
    for date_str, mvrv in mvrv_rows:
        if mvrv and 0 < mvrv < 100:
            cur.execute("""
                INSERT OR IGNORE INTO daily_metrics
                (report_date, asset, metric, value, unit, source)
                VALUES (?, 'BTC', 'mvrv', ?, 'ratio', 'computed')
            """, (date_str, mvrv))
            if cur.rowcount > 0:
                mvrv_inserted += 1
    print(f"  MVRV computed: {mvrv_inserted} new rows")
    total_inserted += mvrv_inserted

    # Compute aSOPR: SOPR averaged over 7 days (rolling)
    print("\n  Computing aSOPR from SOPR rolling average...")
    cur.execute("""
        SELECT a.report_date, AVG(b.value) as asopr
        FROM daily_metrics a
        JOIN daily_metrics b ON b.asset = 'BTC' AND b.metric = 'sopr'
            AND date(b.report_date) BETWEEN date(a.report_date, '-6 days') AND a.report_date
        WHERE a.asset = 'BTC' AND a.metric = 'sopr'
          AND a.report_date NOT IN (
              SELECT report_date FROM daily_metrics WHERE asset='BTC' AND metric='asopr_1w'
          )
        GROUP BY a.report_date
    """)
    asopr_rows = cur.fetchall()
    asopr_inserted = 0
    for date_str, asopr in asopr_rows:
        if asopr and 0.1 < asopr < 10:
            cur.execute("""
                INSERT OR IGNORE INTO daily_metrics
                (report_date, asset, metric, value, unit, source)
                VALUES (?, 'BTC', 'asopr_1w', ?, 'ratio', 'computed')
            """, (date_str, asopr))
            if cur.rowcount > 0:
                asopr_inserted += 1
    print(f"  aSOPR computed: {asopr_inserted} new rows")
    total_inserted += asopr_inserted

    db.commit()

    # Summary
    for metric in ['mvrv', 'nupl', 'hash_rate', 'realized_cap', 'market_cap', 'active_addrs', 'difficulty']:
        cur.execute("SELECT COUNT(*) FROM daily_metrics WHERE asset='BTC' AND metric=?", (metric,))
        cnt = cur.fetchone()[0]
        cur.execute("SELECT MIN(report_date), MAX(report_date) FROM daily_metrics WHERE asset='BTC' AND metric=?", (metric,))
        range_ = cur.fetchone()
        print(f"  {metric}: {cnt} rows ({range_[0]} to {range_[1]})")

    cur.execute("SELECT COUNT(*) FROM daily_metrics WHERE asset='BTC'")
    print(f"\nTotal BTC metric rows: {cur.fetchone()[0]}")
    print(f"Total inserted this run: {total_inserted}")
    db.close()

if __name__ == "__main__":
    backfill_onchain()
