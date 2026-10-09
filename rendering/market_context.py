"""Read-only research context from SQLite; no live HTTP or invented values."""
import datetime as dt
from html import escape
from pathlib import Path
import sqlite3


def _utc(s):
    return dt.datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(dt.timezone.utc)

def render_market_context(db_path, now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:raise ValueError("UTC-aware now required")
    now=now.astimezone(dt.timezone.utc)
    if not Path(db_path).is_file():return ""
    blocks=[]
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        existing={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "market_context_migrations" not in existing:
            return ""
        # Only show source observations with a meaningful, explicit cutoff.
        for source in ("binance","bybit"):
            for metric in ("open_interest","funding_settled"):
                row=db.execute(
                    "SELECT raw_value,raw_unit,observed_utc,quote_usd FROM market_derivatives "
                    "WHERE provider=? AND metric=? AND symbol='BTCUSDT' "
                    "ORDER BY observed_utc DESC LIMIT 1",(source,metric)).fetchone()
                if not row:continue
                value,unit,stamp,usd=row
                try:age=(now-_utc(stamp)).total_seconds()/3600
                except ValueError:continue
                if not 0<=age<=36:continue
                title=source.title()+" · "+("OI" if metric=="open_interest" else "Funding liquidado")
                number=(f"{value*100:+.4f}%" if metric=="funding_settled"
                        else f"{value:,.2f} {unit}")
                note=("USD equivalente: "+f"US$ {usd:,.0f}" if usd is not None and metric=="open_interest"
                      else "sin conversión USD verificada" if metric=="open_interest"
                      else "fracción ×100; liquidación pasada, no estimación futura")
                blocks.append(
                    '<div class="stat-item" data-provider="'+escape(source)+'" data-metric="'+metric+
                    '" data-asof-utc="'+escape(stamp)+'"><div class="slbl">'+escape(title)+
                    '</div><div class="sval">'+escape(number)+
                    '</div><div class="ssub">'+escape(note)+" · "+escape(stamp)+" UTC</div></div>")
        row=db.execute("SELECT MAX(trade_date) FROM market_etf_flows WHERE provider='farside'").fetchone()
        if row and row[0]:
            day=dt.date.fromisoformat(row[0])
            age=(now.date()-day).days
            if 0<=age<=7:
                records=db.execute(
                    "SELECT ticker,net_flow_usd_m FROM market_etf_flows "
                    "WHERE provider='farside' AND trade_date=? ORDER BY ticker",(day.isoformat(),)).fetchall()
                reported_total=next((value for ticker,value in records if ticker=="TOTAL"),None)
                component_values=[value for ticker,value in records if ticker!="TOTAL"]
                total=reported_total if reported_total is not None else sum(component_values)
                total_label=("Total publicado por Farside" if reported_total is not None
                             else "Suma parcial, total oficial ausente")
                blocks.append(
                    '<div class="stat-item" data-provider="farside" data-metric="etf_flows" data-asof-utc="'+
                    day.isoformat()+'"><div class="slbl">ETF spot BTC · Farside</div>'+
                    '<div class="sval">US$ '+f"{total:+,.1f}M"+
                    '</div><div class="ssub">'+escape(total_label)+'; '+str(len(component_values))+
                    ' fondos con cifra reportada · '+day.isoformat()+
                    ' · provisional, sujeto a revisiones</div></div>')
        future=db.execute(
            "SELECT provider,title,scheduled_utc,time_precision,event_date "
            "FROM market_calendar_events WHERE state='scheduled' AND event_date BETWEEN ? AND ? "
            "ORDER BY event_date,scheduled_utc LIMIT 8",
            (now.date().isoformat(),(now.date()+dt.timedelta(days=14)).isoformat())).fetchall()
        if future:
            lines=[]
            for provider,title,stamp,precision,day in future:
                when=stamp if precision=="utc" and stamp else day+" (hora UTC no confirmada)"
                lines.append("<li>"+escape(when)+" · "+escape(title)+" ("+escape(provider.upper())+")</li>")
            blocks.append('<div class="stat-item"><div class="slbl">Próximos eventos macroeconómicos</div>'+
                          '<ul>'+''.join(lines)+'</ul><div class="ssub">BLS/FRED: calendario, no consenso ni resultado actual</div></div>')
    if not blocks:return ""
    return ('<div class="card" id="market-context"><h3>ETF · Derivados · Calendario macro</h3>'
            '<p class="ssub">Fuentes archivadas en SQLite; fechas UTC explícitas. '
            'Datos parciales, cobertura por proveedor; no son señales automáticas.</p>'
            '<div class="stats-bar">'+''.join(blocks)+'</div></div>')
