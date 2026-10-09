#!/usr/bin/env python3
"""Non-destructive historical daily OHLCV archive for catalogued Yahoo symbols.

Plans offline by default. --apply downloads Yahoo range=max (1d only),
stores all completed market sessions under the ORIGINAL symbol, and never
changes existing BTC price_btc / daily_metrics / report snapshots.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_ohlc_history (
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    fetched_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider, symbol, ts)
);
CREATE INDEX IF NOT EXISTS idx_market_ohlc_symbol_ts
ON market_ohlc_history(symbol, ts);
"""


def symbols_in_catalog(db):
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        try:
            return sorted({row[0].strip() for row in conn.execute(
                "SELECT DISTINCT yahoo_symbol FROM ingest_instruments "
                "WHERE yahoo_symbol IS NOT NULL AND TRIM(yahoo_symbol) <> '' "
                "AND LOWER(provider) LIKE '%yahoo%'"
            ) if row[0].strip()})
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise


def extract_daily(result, *, now=None):
    """Keep only completed UTC dates, finite numbers, and no future candle."""
    today = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).date()
    item = result["chart"]["result"][0]
    raw = item["indicators"]["quote"][0]
    collected = []
    for i, timestamp in enumerate(item.get("timestamp", [])):
        day = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).date()
        if day >= today:
            continue
        values = [raw.get(key, [None] * (i + 1))[i] for key in
                  ("open", "high", "low", "close", "volume")]
        if values[3] is None or any(
            val is not None and (not isinstance(val, (float, int)) or
                                 not math.isfinite(val)) for val in values
        ):
            continue
        collected.append((int(timestamp), *values))
    return collected


def fetch_max(symbol, *, opener=urlopen):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + quote(symbol, safe="") + "?interval=1d&range=max")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener(req, timeout=45) as response:
        raw = response.read(12_000_001)
        if len(raw) > 12_000_000:
            raise ValueError("Yahoo histórica: payload excede límite")
    return json.loads(raw)


def archive(db, symbol, rows):
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO market_ohlc_history "
            "(provider,symbol,ts,open,high,low,close,volume,fetched_at_utc) "
            "VALUES ('Yahoo Finance', ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider,symbol,ts) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,"
            "close=excluded.close,volume=excluded.volume,"
            "fetched_at_utc=excluded.fetched_at_utc",
            [(symbol, *row, stamp) for row in rows],
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path(DB_PATH))
    p.add_argument("--symbol", action="append",
                   help="Solo instrumentos permitidos en ingest_instruments")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=4)
    p.add_argument("--apply", action="store_true",
                   help="Permitir API + ingesta no destructiva; por defecto solo PLAN")
    args = p.parse_args()
    if not 1 <= args.limit <= 20 or args.offset < 0 or not args.db.is_file():
        p.error("limit 1..20, offset >=0, SQLite existente obligatorio")
    available = symbols_in_catalog(args.db)
    if args.symbol and any(x not in available for x in args.symbol):
        p.error("No se permite un símbolo ajeno al catálogo local")
    chosen = sorted(set(args.symbol)) if args.symbol else available[args.offset:args.offset + args.limit]
    summary = {"total_symbols": len(available), "selected": chosen,
               "mode": "APPLY" if args.apply else "PLAN", "results": []}
    for symbol in chosen:
        if not args.apply:
            continue
        try:
            rows = extract_daily(fetch_max(symbol))
            if not rows:
                raise ValueError("Sin cierres completos disponibles")
            archive(args.db, symbol, rows)
            summary["results"].append({
                "symbol": symbol, "observations": len(rows),
                "first_utc": dt.datetime.fromtimestamp(rows[0][0], dt.timezone.utc).date().isoformat(),
                "last_utc": dt.datetime.fromtimestamp(rows[-1][0], dt.timezone.utc).date().isoformat(),
            })
        except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            summary["results"].append({"symbol": symbol, "error_type": type(exc).__name__})
            break
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if any("error_type" in x for x in summary["results"]) else 0


if __name__ == "__main__":
    sys.exit(main())
