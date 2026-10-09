#!/usr/bin/env python3
"""Local read-only historical research by provider/metric/UTC date interval.

No data smoothing, cross-provider coercion, false interpolation, or API calls.
Return bounded JSON rows with source attribution. Keep SQLite private.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH


def query_history(db_path, provider, metric, first, last, *, limit=1000):
    if provider not in ("bitview","researchbitcoin","fred","fred-vintage","yahoo","news","rbn-distribution"):
        raise ValueError("Proveedor histórico no soportado")
    if not metric or len(metric)>150:
        raise ValueError("Métrica o símbolo obligatorio (<=150 caracteres)")
    if isinstance(first,str):
        first=dt.date.fromisoformat(first)
    if isinstance(last,str):
        last=dt.date.fromisoformat(last)
    if first>last or not 1<=limit<=5000:
        raise ValueError("Fechas UTC o límite inválidos")
    first_ts=int(dt.datetime.combine(first,dt.time(),dt.timezone.utc).timestamp())
    end_ts=int(dt.datetime.combine(last+dt.timedelta(days=1),
                                   dt.time(),dt.timezone.utc).timestamp())
    selections={
        "bitview":(
            "SELECT d.ts,d.value FROM daily d JOIN series s ON s.id=d.series_id "
            "WHERE s.name=? AND d.ts>=? AND d.ts<? ORDER BY d.ts LIMIT ?",
            (metric,first_ts,end_ts,limit),"tsvalue"),
        "researchbitcoin":(
            "SELECT observed_date,value,unit FROM onchain_external_observations "
            "WHERE provider='researchbitcoin' AND metric=? AND observed_date>=? "
            "AND observed_date<=? ORDER BY observed_date LIMIT ?",
            (metric,first.isoformat(),last.isoformat(),limit),"dateunit"),
        "fred":(
            "SELECT date,value FROM macro_fred WHERE series_id=? AND date>=? "
            "AND date<=? ORDER BY date LIMIT ?",
            (metric,first.isoformat(),last.isoformat(),limit),"datevalue"),
        "fred-vintage":(
            "SELECT observed_date,value,realtime_start,captured_at_utc "
            "FROM archive_fred_vintages WHERE series_id=? AND observed_date>=? "
            "AND observed_date<=? ORDER BY observed_date,realtime_start LIMIT ?",
            (metric,first.isoformat(),last.isoformat(),limit),"vintage"),
        "rbn-distribution":(
            "SELECT observed_at_utc,dimensions_key,value,unit "
            "FROM archive_multidimensional_observations "
            "WHERE source_id='researchbitcoin' AND metric=? "
            "AND observed_at_utc>=? AND observed_at_utc<? "
            "ORDER BY observed_at_utc,dimensions_key LIMIT ?",
            (metric,dt.datetime.combine(first,dt.time(),dt.timezone.utc).isoformat(),
             dt.datetime.combine(last+dt.timedelta(days=1),
                                 dt.time(),dt.timezone.utc).isoformat(),limit),
            "distribution"),
        "yahoo":(
            "SELECT ts,open,high,low,close,volume FROM market_ohlc_history "
            "WHERE symbol=? AND ts>=? AND ts<? ORDER BY ts LIMIT ?",
            (metric,first_ts,end_ts,limit),"ohlcv"),
        "news":(
            "SELECT report_date,title,url,source,source_type,published_label "
            "FROM research_news_archive WHERE asset=? AND report_date>=? "
            "AND report_date<=? ORDER BY report_date LIMIT ?",
            (metric,first.isoformat(),last.isoformat(),limit),"news"),
    }
    sql,params,kind=selections[provider]
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        rows=db.execute(sql,params).fetchall()
    def dated(ts):
        return dt.datetime.fromtimestamp(ts,dt.timezone.utc).date().isoformat()
    mapped=[]
    for row in rows:
        if kind=="tsvalue":
            mapped.append({"date_utc":dated(row[0]),"value":row[1]})
        elif kind=="dateunit":
            mapped.append({"date_utc":row[0],"value":row[1],"unit":row[2],
                           "note":"raw ResearchBitcoin, no escala implícita"})
        elif kind=="datevalue":
            mapped.append({"date_utc":row[0],"value":row[1]})
        elif kind=="vintage":
            mapped.append({"date_utc":row[0],"value":row[1],
                           "realtime_start":row[2],"captured_at_utc":row[3],
                           "note":"Solo vintages realmente archivados"})
        elif kind=="distribution":
            mapped.append({"observed_at_utc":row[0],"dimensions_key":row[1],
                           "value":row[2],"unit":row[3],
                           "note":"Distribución cruda; no agregar bins automáticamente"})
        elif kind=="ohlcv":
            mapped.append({"date_utc":dated(row[0]),"open":row[1],"high":row[2],
                           "low":row[3],"close":row[4],"volume":row[5]})
        else:
            mapped.append({"date_report_utc":row[0],"title":row[1],"url":row[2],
                           "source":row[3],"source_type":row[4],
                           "published_label":row[5]})
    return {"provider":provider,"metric":metric,"from":first.isoformat(),
            "to":last.isoformat(),"count":len(mapped),"truncated":len(mapped)>=limit,
            "rows":mapped}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    p.add_argument("--provider",choices=["bitview","researchbitcoin","fred","fred-vintage","yahoo","news","rbn-distribution"],
                   required=True)
    p.add_argument("--metric",required=True)
    p.add_argument("--from",dest="first",required=True)
    p.add_argument("--to",dest="last",required=True)
    p.add_argument("--limit",type=int,default=1000)
    args=p.parse_args()
    try:
        result=query_history(args.db,args.provider,args.metric,args.first,args.last,
                             limit=args.limit)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,sqlite3.Error) as exc:
        print("History query STOP: "+type(exc).__name__,file=sys.stderr)
        return 1


if __name__=="__main__":
    sys.exit(main())
