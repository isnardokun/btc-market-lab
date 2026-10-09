#!/usr/bin/env python3
"""
backfill_metrics.py — Compute and store historical metrics from existing data.
Uses price_btc + daily (bitview) + macro_fred to backfill daily_metrics.
Only backfills past dates; today's snapshot done separately by archive_metrics.py.
"""
import sqlite3, datetime, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/db/btc_research.db"

def compute_rsi_wilder(prices, period=14):
    """Wilder smoothed RSI."""
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_val = 100
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100 - (100 / (1 + rs))
    return rsi_val

def compute_ma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def compute_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period

def _save(cur, date, asset, metric, value, unit, source):
    cur.execute("""
        INSERT OR IGNORE INTO daily_metrics (report_date, asset, metric, value, unit, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date, asset, metric, value, unit, source))

def backfill():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    # Get all dates we have BTC price data
    cur.execute("SELECT ts, price FROM price_btc ORDER BY ts")
    rows = cur.fetchall()
    print(f"Backfilling from {len(rows)} price rows")

    # We need closes list for RSI/MA computation
    dates_prices = [(datetime.date.fromtimestamp(r[0]), r[1]) for r in rows]

    # Only backfill last 365 days to avoid massive compute
    dates_prices = dates_prices[-365:]
    print(f"Backfilling last {len(dates_prices)} days")

    for i, (date, price) in enumerate(dates_prices):
        date_str = date.isoformat()

        # Skip if already has metrics for this date
        cur.execute("SELECT COUNT(*) FROM daily_metrics WHERE report_date=? AND asset='BTC' AND metric='price'", (date_str,))
        if cur.fetchone()[0] > 0:
            continue

        # Build close list up to this point
        closes = [rows[j][1] for j in range(i+1)]
        if len(closes) < 30:
            continue

        # Get highs/lows for ATR (approximate using closes as high/low with noise)
        highs = [c * 1.002 for c in closes]
        lows  = [c * 0.998 for c in closes]

        rsi  = compute_rsi_wilder(closes)
        sma20 = compute_ma(closes, 20)
        sma50 = compute_ma(closes, 50)
        sma200 = compute_ma(closes, 200)
        atr14 = compute_atr(highs, lows, closes)

        prev = closes[-2] if len(closes) >= 2 else None
        chg = (price - prev) / prev * 100 if prev else None

        _save(cur, date_str, "BTC", "price", price, "USD", "yahoo")
        if chg is not None:
            _save(cur, date_str, "BTC", "chg_24h", chg, "%", "yahoo")
        if rsi is not None:
            _save(cur, date_str, "BTC", "rsi_14", rsi, "ratio", "yahoo")
        if sma20 is not None:
            _save(cur, date_str, "BTC", "sma_20", sma20, "USD", "yahoo")
        if sma50 is not None:
            _save(cur, date_str, "BTC", "sma_50", sma50, "USD", "yahoo")
        if sma200 is not None:
            _save(cur, date_str, "BTC", "sma_200", sma200, "USD", "yahoo")
        if atr14 is not None:
            _save(cur, date_str, "BTC", "atr_14", atr14, "USD", "yahoo")

        if i % 100 == 0 and i > 0:
            print(f"  Progress: {i}/{len(dates_prices)}")
            db.commit()

    db.commit()
    cur.execute("SELECT COUNT(*) FROM daily_metrics")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT report_date) FROM daily_metrics WHERE asset='BTC'")
    days = cur.fetchone()[0]
    print(f"\nBackfill complete. Total metrics rows: {total}, BTC days covered: {days}")
    db.close()

if __name__ == "__main__":
    backfill()
