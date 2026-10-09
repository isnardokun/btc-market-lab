#!/usr/bin/env python3
"""Ingest BTC price from Yahoo Finance into price_btc table."""
import urllib.request, json, sqlite3, datetime, sys
from ingestion.daily_cutoff import previous_completed_utc_day

URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?interval=1d&range=2y"

def ingest():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    db = sqlite3.connect("/home/ignotus/btc-research/db/btc_research.db")

    inserted = 0
    skipped_open = 0
    cutoff = previous_completed_utc_day()
    for ts, price in zip(timestamps, closes):
        date_utc = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date()
        if date_utc > cutoff:
            skipped_open += 1
            continue
        if price is None:
            continue
        n = db.execute("INSERT OR REPLACE INTO price_btc (ts, price) VALUES (?, ?)",
                       (int(ts), price)).rowcount
        inserted += n

    db.commit()
    db.close()
    print(f"price_btc: {inserted} updated through {cutoff} UTC; {skipped_open} open-day bars skipped")

if __name__ == "__main__":
    ingest()
