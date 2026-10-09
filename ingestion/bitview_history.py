#!/usr/bin/env python3
"""Resumable, non-destructive Bitview d1 historical windows.

The old ingest_all.py --backfill ignored backfill and historically deleted
tables. This tool explicitly reads existing series; saves only verifiable
daily values and records attempted windows, including no-data windows.
Run in bounded batches, not as a daily cron.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion import config
from ingestion.daily_cutoff import previous_completed_utc_day
from ingestion.ingest import fetch_series
from storage.archive_schema import archive_schema_installed, save_window

EPOCH = dt.date(2009, 1, 1)
LEDGER = """
CREATE TABLE IF NOT EXISTS historical_fetch_windows (
 provider TEXT NOT NULL, metric TEXT NOT NULL,
 start_day TEXT NOT NULL, end_day TEXT NOT NULL,
 status TEXT NOT NULL, count_values INTEGER NOT NULL,
 fetched_at_utc TEXT NOT NULL,
 PRIMARY KEY(provider,metric,start_day,end_day)
);
"""


def windows(first, last, width=180):
    if not 1 <= width <= 365:
        raise ValueError("window days fuera de 1..365")
    result = []
    while first <= last:
        end = min(last, first + dt.timedelta(days=width - 1))
        result.append((first, end))
        first = end + dt.timedelta(days=1)
    return result


def decode_day1_response(payload, first, last):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Bitview: formato d1 inesperado")
    if payload.get("index") not in ("1d", "day1", "date", "date1"):
        raise ValueError("Bitview: índice d1 no verificable")
    start_index = payload.get("start")
    if isinstance(start_index, bool) or not isinstance(start_index, int):
        raise ValueError("Bitview: inicio de índice inválido")
    # Bitview day1 starts 2009-01-01; zero is the first UTC day.
    output = []
    for i, value in enumerate(payload["data"]):
        day = EPOCH + dt.timedelta(days=start_index + i)
        if not first <= day <= last:
            continue
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Bitview: observación no escalar o no finita")
        ts = int(dt.datetime.combine(day, dt.time(), dt.timezone.utc).timestamp())
        output.append((ts, float(value)))
    return output


def catalog(conn, all_daily=False, slug=None):
    if slug:
        found = conn.execute("SELECT name FROM series WHERE name=?", (slug,)).fetchone()
        if not found and slug not in config.SERIES:
            raise ValueError("Serie no incluida en catálogo local o configuración")
        return [slug]
    if all_daily:
        rows = conn.execute("SELECT name FROM series WHERE idx IN ('1d','day1') "
                            "ORDER BY name").fetchall()
        if not rows:
            raise ValueError("Catálogo Bitview sin índices diarios etiquetados")
        return [x[0] for x in rows]
    # Known, verified daily series in the production configuration.
    return sorted({name for name, idx, desc in config.SERIES.values() if idx == "1d"})


def history_plan(db_path, *, all_daily=False, slug=None, first=None, cutoff=None,
                 chunk_days=180, max_requests=8):
    if not 1 <= max_requests <= 50:
        raise ValueError("max-requests 1..50")
    first = first or EPOCH
    cutoff = cutoff or previous_completed_utc_day()
    if first < EPOCH or first > cutoff:
        raise ValueError("Bitview: fecha fuera de historia válida")
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro",
                         uri=True) as db:
        names = catalog(db, all_daily=all_daily, slug=slug)
        try:
            previous = {(m, a, b) for m, a, b in db.execute(
                "SELECT metric,start_day,end_day FROM historical_fetch_windows "
                "WHERE provider='bitview'"
            )}
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            previous = set()
    pending = []
    for name in names:
        for start, end in windows(first, cutoff, chunk_days):
            if (name, start.isoformat(), end.isoformat()) not in previous:
                pending.append((name, start, end))
    return len(names), len(pending), pending[:max_requests]


def run(db_path, *, apply=False, all_daily=False, slug=None, first=None,
        cutoff=None, chunk_days=180, max_requests=8, client=fetch_series):
    if not Path(db_path).is_file():
        raise ValueError("SQLite existente es obligatorio")
    cutoff = cutoff or previous_completed_utc_day()
    count_series, total_pending, selected = history_plan(
        db_path, all_daily=all_daily, slug=slug, first=first,
        cutoff=cutoff, chunk_days=chunk_days, max_requests=max_requests,
    )
    report = {"provider":"bitview","selected_series":count_series,
              "remaining_windows_before":total_pending, "executed":bool(apply),
              "requests":[],"rows_saved":0}
    if not apply:
        report["requests"] = [{"metric":m, "start":a.isoformat(),
                              "end_inclusive":b.isoformat()} for m,a,b in selected]
        return report
    for name, start, end in selected:
        item = {"metric":name,"start":start.isoformat(),"end_inclusive":end.isoformat()}
        try:
            data = client(name, "1d", start=start.isoformat(), end=end.isoformat())
            values = decode_day1_response(data, start, end)
            with sqlite3.connect(db_path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript(LEDGER)
                row = db.execute("SELECT id FROM series WHERE name=?", (name,)).fetchone()
                if row:
                    sid = row[0]
                else:
                    db.execute("INSERT INTO series (name,description) VALUES (?,?)",
                               (name, "bitview d1 historical"))
                    sid = db.execute("SELECT id FROM series WHERE name=?", (name,)).fetchone()[0]
                db.executemany(
                    "INSERT INTO daily(series_id,ts,block_height,value) VALUES (?,?,NULL,?) "
                    "ON CONFLICT(series_id,ts) DO UPDATE SET value=excluded.value",
                    [(sid,ts,value) for ts,value in values],
                )
                db.execute("INSERT OR REPLACE INTO historical_fetch_windows "
                           "(provider,metric,start_day,end_day,status,count_values,fetched_at_utc) "
                           "VALUES ('bitview',?,?,?,?,?,?)",
                           (name,start.isoformat(),end.isoformat(),
                            "ok" if values else "empty",len(values),
                            dt.datetime.now(dt.timezone.utc).isoformat()))
                if archive_schema_installed(db):
                    save_window(db, "bitview", name, start, end+dt.timedelta(days=1),
                                "ok" if values else "empty", len(values))
            item["values"]=len(values)
            report["rows_saved"]+=len(values)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            item["error_type"]=type(exc).__name__
            report["requests"].append(item)
            break
        report["requests"].append(item)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path(config.DB_PATH))
    parser.add_argument("--all-daily",action="store_true",
                        help="Todas las series catalogadas con índice 1d (sin height)")
    parser.add_argument("--series",help="Una serie exacta")
    parser.add_argument("--from",dest="first",default="2009-01-01")
    parser.add_argument("--chunk-days",type=int,default=180)
    parser.add_argument("--max-requests",type=int,default=8)
    parser.add_argument("--apply",action="store_true",help="Autorizar HTTP y SQLite")
    args=parser.parse_args()
    try:
        result=run(args.db,apply=args.apply,all_daily=args.all_daily,
                   slug=args.series,first=dt.date.fromisoformat(args.first),
                   chunk_days=args.chunk_days,max_requests=args.max_requests)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if any("error_type" in r for r in result["requests"]) else 0
    except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:
        print("Bitview history STOP: "+type(exc).__name__,file=sys.stderr)
        return 2


if __name__=="__main__":
    sys.exit(main())
