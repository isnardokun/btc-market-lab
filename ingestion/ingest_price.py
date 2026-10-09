#!/usr/bin/env python3
"""Ingest BTC price from Yahoo Finance into price_btc table."""
import urllib.request, json, sqlite3, datetime, sys

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
    for ts, price in zip(timestamps, closes):
        if price is None:
            continue
        n = db.execute("INSERT OR REPLACE INTO price_btc (ts, price) VALUES (?, ?)",
                       (int(ts), price)).rowcount
        inserted += n

    db.commit()
    db.close()
    print(f"price_btc: {inserted} updated")

if __name__ == "__main__":
    ingest()
