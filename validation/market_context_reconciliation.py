"""Non-compensable HTML ↔ SQLite checks for optional market-context cards."""
import datetime as dt
from html import unescape
from pathlib import Path
import re
import sqlite3

CARD=re.compile(r'<div class="stat-item" data-provider="(binance|bybit|farside)"([^>]*?)>',re.I)
ATTR=re.compile(r'([a-z][a-z0-9-]*)="([^"]*)"')
VALUE=re.compile(r'<div class="sval">([^<]*)</div>')
SHA=re.compile(r'^[a-f0-9]{64}$')

def audit_market_context_against_html(markup,db_path):
    """Treat all displayed market-context cards as untrusted until reconciled."""
    content=re.search(r'<div class="card" id="market-context">([\s\S]*?)</div>\s*(?:<div|<hr|<section|$)',markup)
    # Cards are nested; inspect the report from its stable market-context anchor.
    if 'id="market-context"' not in markup:
        return []
    errors=[]
    cards=list(CARD.finditer(markup))
    if not cards:
        return ["Market context: section present without source-backed cards"]
    if not Path(db_path).is_file():
        return ["Market context: SQLite unavailable"]
    try:
        db=sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True)
        with db:
            needed={"market_derivatives","market_etf_flows","market_raw_payloads"}
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not needed<=names:
                return ["Market context: SQLite schema missing"]
            seen=set()
            for card in cards:
                provider=card.group(1)
                meta=dict(ATTR.findall(card.group(2)))
                metric=meta.get("data-metric")
                stamp=meta.get("data-asof-utc")
                raw=meta.get("data-raw-value")
                unit=meta.get("data-raw-unit")
                digest=meta.get("data-source-sha256")
                if not all((metric,stamp,raw,unit,digest)) or not SHA.fullmatch(digest):
                    errors.append("Market context: missing value, date, unit or payload SHA")
                    continue
                key=(provider,metric,stamp)
                if key in seen:
                    errors.append("Market context: duplicated display card")
                    continue
                seen.add(key)
                try: value=float(raw)
                except ValueError:
                    errors.append("Market context: nonnumeric raw attribute")
                    continue
                if provider=="farside":
                    if metric!="etf_flows" or unit!="USD_m":
                        errors.append("Market context: ETF unit or metric mismatch");continue
                    amounts=db.execute(
                        "SELECT ticker,net_flow_usd_m,raw_sha256 FROM market_etf_flows "
                        "WHERE provider='farside' AND trade_date=?",(stamp,)).fetchall()
                    reported=next((r for r in amounts if r[0]=="TOTAL"),None)
                    expected=reported[1] if reported else sum(r[1] for r in amounts)
                    expected_sha=reported[2] if reported else {r[2] for r in amounts}
                    valid_source=(digest==expected_sha if reported else
                                  digest in expected_sha)
                    display="US$ "+f"{expected:+,.1f}M"
                    if not amounts or not valid_source:
                        errors.append("Market context: ETF display has no matching source")
                        continue
                else:
                    if metric not in ("funding_settled","open_interest"):
                        errors.append("Market context: unrecognized derivative metric");continue
                    result=db.execute(
                        "SELECT raw_value,raw_unit,raw_sha256 FROM market_derivatives "
                        "WHERE provider=? AND symbol='BTCUSDT' AND metric=? "
                        "AND observed_utc=? AND interval_label=?",
                        (provider,metric,stamp,
                         "settlement" if metric=="funding_settled" else "5m")
                    ).fetchone()
                    if result is None:
                        errors.append("Market context: derivative display has no matching source")
                        continue
                    expected,stored_unit,expected_sha=result
                    if unit!=stored_unit or digest!=expected_sha:
                        errors.append("Market context: derivative unit or payload SHA mismatch")
                        continue
                    display=(f"{expected*100:+.4f}%" if metric=="funding_settled"
                             else f"{expected:,.2f} {unit}")
                if abs(value-expected)>max(1e-10,abs(expected)*1e-10):
                    errors.append("Market context: HTML raw value differs from SQLite")
                rendered=VALUE.search(markup[card.end():card.end()+500])
                if not rendered or unescape(rendered.group(1))!=display:
                    errors.append("Market context: displayed financial number differs from SQLite")
                payload=db.execute("SELECT 1 FROM market_raw_payloads WHERE sha256=?",(digest,)).fetchone()
                if payload is None:
                    errors.append("Market context: source payload SHA missing")
    except (sqlite3.Error, OSError, ValueError) as exc:
        return ["Market context: audit not available ("+type(exc).__name__+")"]
    finally:
        if 'db' in locals():db.close()
    return errors
