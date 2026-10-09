#!/usr/bin/env python3
"""
Mercados Daily — BTC + Bolsa + Metales
Estructura profesional: Bitfinex Alpha + CoinMarketCap + Glassnode
Precios: Yahoo Finance | On-chain: bitview | News: Exa
"""
import os, sys, datetime, sqlite3, json, subprocess, re
from html import escape as html_escape
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = BASE_DIR + "/db/btc_research.db"
try:
    from ingestion.news_pipeline import run_news_pipeline, normalize_for_report
    HAS_NEWS_PIPELINE = True
except ImportError:
    HAS_NEWS_PIPELINE = False
TODAY    = datetime.date.today()
OUT_PATH = BASE_DIR + "/reports/daily_report_" + TODAY.strftime("%Y-%m-%d") + ".html"
TODAY_STR = TODAY.strftime("%d %b %Y")
NOW_STR   = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
ts_today  = int(datetime.datetime.combine(TODAY, datetime.time(23,59)).replace(tzinfo=datetime.timezone.utc).timestamp())
ts_52w    = int((datetime.datetime.combine(TODAY, datetime.time(0,0)).replace(tzinfo=datetime.timezone.utc) - datetime.timedelta(days=365)).timestamp())

# ── DATA ──────────────────────────────────────────────────────────────────────

def yahoo_ohlc(symbol, days=90):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1d&range={days}d")
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        ohlc = result["indicators"]["quote"][0]
        rows = []
        for i, ts in enumerate(timestamps):
            rows.append({
                "ts": int(ts),
                "date": datetime.date.fromtimestamp(ts),
                "open":   ohlc["open"][i],
                "high":   ohlc["high"][i],
                "low":    ohlc["low"][i],
                "close":  ohlc["close"][i],
                "volume": ohlc["volume"][i],
            })
        return rows
    except Exception as e:
        print(f"Yahoo error {symbol}: {e}", file=sys.stderr)
        return []

from quant_engine.indicators import (
    compute_rsi, compute_ma, ema_python,
    compute_macd, stoch, williams_r, cci, atr,
    find_supp_res, compute_scenarios, price_relative_levels
)

# ── ON-CHAIN BTC ────────────────────────────────────────────────────────────

def get_btc_onchain():
    """Fetch ALL available on-chain data from bitview local DB.

    Usa metric_registry para conversiones verificadas.
    Series IDs verificados contra la tabla 'series' de bitview.
    Conversiones:
      - hash_rate: raw H/s → ÷1e18 → EH/s
      - difficulty: raw compact → ÷1e12 → T
      - market_cap/realized_cap/active_cap: dtype=Dollars → YA en USD → ÷1e12 → T$
      - utxo_count: count → ÷1e6 → M
      - Ratios (mvrv, sopr, nupl): sin conversión
    """
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()

    # Series IDs verificados (de tabla series):
    # 1097=hash_rate, 550=difficulty, 1330=market_cap, 1978=realized_cap,
    # 7=active_cap, 1357=mvrv, 2052=sopr_1w, 104=asopr_1w,
    # 2807=nupl, 2806=rhodl_ratio, 2375=utxo_count, 5=active_addrs_average_24h

    def get_by_id(series_id):
        c.execute(
            "SELECT ts, value FROM daily WHERE series_id=? ORDER BY ts DESC LIMIT 1",
            (series_id,)
        )
        r = c.fetchone()
        return (r[0], r[1]) if r else (None, None)

    # Fetch raw values
    mvrv_ts, mvrv_raw = get_by_id(1357)
    asopr_ts, asopr_raw = get_by_id(104)
    sopr_ts, sopr_raw = get_by_id(2052)
    rhodl_ts, rhodl_raw = get_by_id(2806)
    nupl_ts, nupl_raw = get_by_id(2807)
    hr_ts, hr_raw = get_by_id(1097)
    diff_ts, diff_raw = get_by_id(550)
    addr_ts, addrs_raw = get_by_id(5)
    utxo_ts, utxo_raw = get_by_id(2375)
    mcap_ts, mcap_raw = get_by_id(1330)
    rcap_ts, rcap_raw = get_by_id(1978)
    acap_ts, acap_raw = get_by_id(7)

    db.close()

    # Apply VERIFIED conversions (from metric_registry)
    # hash_rate: H/s → EH/s (÷1e18)
    hr = round(hr_raw * 1e-18, 0) if hr_raw else None
    # difficulty: compact → T (÷1e12)
    diff = round(diff_raw * 1e-12, 1) if diff_raw else None
    # market_cap: USD → T$ (÷1e12) — dtype=Dollars, ya en USD
    mcap = round(mcap_raw * 1e-12, 2) if mcap_raw else None
    # realized_cap: USD → T$ (÷1e12)
    rcap = round(rcap_raw * 1e-12, 2) if rcap_raw else None
    # active_cap: USD → T$ (÷1e12)
    acap = round(acap_raw * 1e-12, 2) if acap_raw else None
    # utxo_count: count → M (÷1e6)
    utxos = round(utxo_raw * 1e-6, 2) if utxo_raw else None

    # Ratios: sin conversión
    mvrv = round(mvrv_raw, 2) if mvrv_raw else None
    asopr = round(asopr_raw, 4) if asopr_raw else None
    sopr = round(sopr_raw, 4) if sopr_raw else None
    rhodl = round(rhodl_raw, 2) if rhodl_raw else None
    nupl = round(nupl_raw, 3) if nupl_raw else None
    addrs = int(addrs_raw) if addrs_raw else None

    return {
        "mvrv": mvrv,
        "asopr": asopr,
        "sopr": sopr,
        "rhodl": rhodl,
        "nupl": nupl,
        "hr": hr,
        "diff": diff,
        "addrs": addrs,
        "utxos": utxos,
        "mcap": mcap,
        "rcap": rcap,
        "acap": acap,
        "ts": {
            "mvrv": mvrv_ts, "asopr": asopr_ts, "sopr": sopr_ts,
            "nupl": nupl_ts, "hr": hr_ts, "diff": diff_ts,
            "addrs": addr_ts, "utxo": utxo_ts, "mcap": mcap_ts,
            "rcap": rcap_ts, "acap": acap_ts,
        }
    }

def get_btc_price_data(current_price=None):
    """Verified ATH, 52-week extremes AND their dates from the same SQL rows."""
    db = sqlite3.connect(DB_PATH)
    try:
        cur = db.cursor()
        ath_row = cur.execute(
            "SELECT ts, price FROM price_btc WHERE ts <= ? AND price > 0 "
            "ORDER BY price DESC, ts DESC LIMIT 1", (ts_today,)
        ).fetchone()
        hi = cur.execute(
            "SELECT ts, price FROM price_btc WHERE ts BETWEEN ? AND ? AND price > 0 "
            "ORDER BY price DESC, ts DESC LIMIT 1", (ts_52w, ts_today)
        ).fetchone()
        lo = cur.execute(
            "SELECT ts, price FROM price_btc WHERE ts BETWEEN ? AND ? AND price > 0 "
            "ORDER BY price ASC, ts DESC LIMIT 1", (ts_52w, ts_today)
        ).fetchone()
        latest = cur.execute(
            "SELECT ts, price FROM price_btc WHERE ts <= ? AND price > 0 "
            "ORDER BY ts DESC LIMIT 1", (ts_today,)
        ).fetchone()
    finally:
        db.close()
    if not (ath_row and hi and lo and latest):
        raise RuntimeError("Datos SQL incompletos para ATH y máximos/mínimos 52 semanas")
    cur_price = float(current_price) if current_price is not None else float(latest[1])
    if cur_price <= 0 or float(ath_row[1]) <= 0:
        raise RuntimeError("Precio BTC no válido al calcular drawdown")
    date_label = lambda row: datetime.datetime.fromtimestamp(
        row[0], datetime.timezone.utc
    ).strftime("%d %b %Y")
    return {
        "ath": round(ath_row[1], 2), "ath_date": date_label(ath_row),
        "high52": round(hi[1], 2), "high52_date": date_label(hi),
        "low52": round(lo[1], 2), "low52_date": date_label(lo),
        "current": round(cur_price, 2),
        "from_ath": round((cur_price / ath_row[1] - 1) * 100, 1),
        "drawdown_to_low52": round((lo[1] / ath_row[1] - 1) * 100, 1),
    }


def get_macro_fred():
    """Fetch macro data from local FRED table.
    
    Data contract:
    - DTWEXBGS = Trade Weighted USD Broad Index (NOT DXY Futures)
    - CPI_YOY = Precomputed YoY from CPIAUCSL index
    - NFP_CHANGE = Precomputed month-over-month change in PAYEMS
    - PAYEMS = Total Nonfarm Payrolls level (thousands)
    """
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    def get_fred(series_id):
        r = c.execute("SELECT date, value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1", (series_id,)).fetchone()
        return (r[0], r[1]) if r else (None, None)
    result = {
        "10y":       get_fred("DGS10"),
        "2y":        get_fred("DGS2"),
        "dxy":       get_fred("DTWEXBGS"),   # Trade Weighted USD Broad (not DXY Futures)
        "vix":       get_fred("VIXCLS"),
        "nfci":      get_fred("NFCI"),
        "unemp":     get_fred("UNRATE"),
        "nfp":       get_fred("NFP_CHANGE"),   # Month-over-month change, not level
        "nfp_level": get_fred("PAYEMS"),       # Total payrolls level (thousands)
        "cpi_yoy":   get_fred("CPI_YOY"),      # Headline CPI YoY from CPIAUCSL
        "ppi_yoy":   get_fred("PPI_YOY"),      # PPI YoY
        "btc_fred":  get_fred("CBBTCUSD"),
        "retail_sales": get_fred("RSXFS"),
    }
    db.close()
    return result

def get_btc_price():
    rows = yahoo_ohlc("BTC-USD", 2)
    if len(rows) < 2: return None, None
    cur, prev = rows[-1]["close"], rows[-2]["close"]
    chg = (cur - prev) / prev * 100 if prev else None
    return cur, chg

# ── EXA SEARCH ────────────────────────────────────────────────────────────────

def exa_search(query, n=5):
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = "/home/ignotus/.agent-reach-venv"
    env["PATH"] = "/home/ignotus/.agent-reach-venv/bin:" + env.get("PATH","")
    try:
        result = subprocess.run(
            ["mcporter", "call", "exa.web_search_exa",
             f"query={query}", f"numResults={n}"],
            capture_output=True, text=True, timeout=30, env=env
        )
        if result.returncode != 0: return []
        return parse_mcporter(result.stdout)
    except Exception as e:
        print(f"Exa error: {e}", file=sys.stderr)
        return []

def parse_mcporter(text):
    entries = []
    for block in text.split("Title:"):
        if not block.strip(): continue
        title = block.split("URL:")[0].strip() if "URL:" in block else block.strip()
        url = block.split("URL:")[1].split("Published:")[0].strip() if "URL:" in block else ""
        pub = block.split("Published:")[1].split("Highlights:")[0].strip() if "Published:" in block else ""
        hl = block.split("Highlights:")[1].strip() if "Highlights:" in block else ""
        date_str = ""
        if pub:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", pub)
            if m: date_str = m.group(1)
        entries.append({"title": title, "url": url, "published": pub, "highlight": hl[:400], "date": date_str})
    return entries

# ── TRANSLATOR ────────────────────────────────────────────────────────────────

KW = {
    "rate hike": "alza de tasas", "rate cut": "baja de tasas",
    "inflation": "inflacion", "fed": "Fed", "Treasury": "tesoro",
    "yield": "rendimiento", "etf": "ETF", "bitcoin": "BTC",
    "bullish": "alcista", "bearish": "bajista",
    "resistance": "resistencia", "support": "soporte",
    "breakout": "ruptura", "breakdown": "caida",
    "volume": "volumen", "liquidation": "liquidacion",
    "short": "corto", "long": "largo", "profit": "ganancia",
    "loss": "perdida", "price": "precio", "market": "mercado",
    "risk": "riesgo", "recession": "recesion",
    "record high": "maximo historico", "all-time high": "maximo historico",
    "supply": "oferta", "demand": "demanda",
    "employment": "empleo", "payroll": "nomina",
    "gold": "oro", "oil": "petroleo",
}

def translate_text(text):
    if not text: return ""
    result = text
    for en, es in KW.items():
        result = re.sub(rf"\b{re.escape(en)}\b", es, result, flags=re.IGNORECASE)
    return result

# ── SVG CHARTS ────────────────────────────────────────────────────────────────

def svg_price(ohlc_rows, currency="$", height=200, width=680):
    if not ohlc_rows: return ""
    vals = [r["close"] for r in ohlc_rows if r["close"] is not None]
    if not vals: return ""
    mn, mx = min(vals), max(vals)
    pad = (mx - mn) * 0.08
    mn -= pad; mx += pad
    n = len(ohlc_rows)
    def Y(v): return height - int((v - mn)/(mx - mn + 0.001) * height)
    def X(i): return int(i * width / max(1, n - 1))
    closes_s = " ".join(f"{X(i)},{Y(ohlc_rows[i]['close'])}" for i in range(n))
    sma20_pts = ""
    for i in range(19, n):
        ma = sum(ohlc_rows[j]["close"] for j in range(i-19, i+1) if ohlc_rows[j]["close"]) / 20
        sma20_pts += f" {X(i)},{Y(ma)}"
    sma50_pts = ""
    for i in range(49, n):
        ma = sum(ohlc_rows[j]["close"] for j in range(i-49, i+1) if ohlc_rows[j]["close"]) / 50
        sma50_pts += f" {X(i)},{Y(ma)}"
    first_date = ohlc_rows[0]["date"].strftime("%d %b") if ohlc_rows else ""
    last_date  = ohlc_rows[-1]["date"].strftime("%d %b") if ohlc_rows else ""
    mid_date   = ohlc_rows[n//2]["date"].strftime("%d %b") if n > 2 else ""
    price_txt  = f"{currency}{ohlc_rows[-1]['close']:,.0f}"
    lines = [
        f'<line x1="0" y1="{Y(mn)}" x2="{width}" y2="{Y(mn)}" stroke="#e8e0d0" stroke-width="0.5" stroke-dasharray="3,3"/>',
        f'<text x="{width-4}" y="{Y(mn)+4}" text-anchor="end" font-size="9" fill="#63766f">{currency}{mn:,.0f}</text>',
        f'<line x1="0" y1="{Y(mx)}" x2="{width}" y2="{Y(mx)}" stroke="#e8e0d0" stroke-width="0.5" stroke-dasharray="3,3"/>',
        f'<text x="{width-4}" y="{Y(mx)+4}" text-anchor="end" font-size="9" fill="#63766f">{currency}{mx:,.0f}</text>',
        f'<polyline points="{sma50_pts.strip()}" fill="none" stroke="#0e7669" stroke-width="1" stroke-dasharray="2,2" opacity="0.65"/>',
        f'<polyline points="{sma20_pts.strip()}" fill="none" stroke="#d5a844" stroke-width="1" stroke-dasharray="3,2" opacity="0.7"/>',
        f'<polyline points="{closes_s}" fill="none" stroke="#16A34A" stroke-width="1.5"/>',
        f'<circle cx="{width}" cy="{Y(ohlc_rows[-1]["close"])}" r="3" fill="#16A34A"/>',
        f'<text x="{width-4}" y="{Y(ohlc_rows[-1]["close"])-8}" text-anchor="end" font-size="10" font-weight="700" fill="#16A34A">{price_txt}</text>',
        f'<text x="0" y="{height+12}" text-anchor="middle" font-size="9" fill="#63766f">{first_date}</text>',
        f'<text x="{width//2}" y="{height+12}" text-anchor="middle" font-size="9" fill="#63766f">{mid_date}</text>',
        f'<text x="{width}" y="{height+12}" text-anchor="middle" font-size="9" fill="#63766f">{last_date}</text>',
    ]
    return f'<svg viewBox="0 0 {width} {height+18}" xmlns="http://www.w3.org/2000/svg" style="font-family:IBM Plex Mono,monospace">{"".join(lines)}</svg>'

# ── HELPERS ───────────────────────────────────────────────────────────────────

def pct(n):     return ("%+.2f%%" % n) if n is not None else "---"
def usd(n, d=2): return ("$%.*f" % (d, n)) if n is not None else "---"
def usd0(n):   return usd(n, 0)
def eh(n):      return ("%d EH/s" % int(n)) if n else "---"
def mult(n):   return ("%.2fx" % n) if n is not None else "---"
def arr(chg):  return "&and;" if (chg is not None and chg >= 0) else "&or;"
def bcls(chg): return "up" if (chg is not None and chg >= 0) else "dn"

# ── CSS ──────────────────────────────────────────────────────────────────────

CSS = """
:root{--ink:#092b27;--deep:#04201c;--ivory:#f4eddd;--paper:#fffaf0;--sea:#0e7669;--mint:#8bd5bd;--coral:#ef765f;--gold:#d5a844;--muted:#63766f;--line:rgba(9,43,39,.13);--up:#16A34A;--dn:#DC2626;--serif:Fraunces,Georgia,serif;--mono:'IBM Plex Mono',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--ivory);color:var(--ink);font-family:Manrope,sans-serif;font-size:13px;line-height:1.55}
.wrap{max-width:980px;margin:0 auto;padding:28px 18px 80px}

/* HEADER */
.hdr{background:var(--deep);border-radius:18px;padding:30px 36px;margin-bottom:38px;position:relative;overflow:hidden}
.hdr::before{content:"";position:absolute;right:-60px;top:-60px;width:280px;height:280px;border:1px solid rgba(139,213,189,.12);border-radius:50%}
.hdr::after{content:"";position:absolute;right:40px;top:-40px;width:180px;height:180px;border:1px solid rgba(139,213,189,.07);border-radius:50%}
.hdr-kicker{font:700 10px var(--mono);letter-spacing:.2em;text-transform:uppercase;color:var(--mint);margin-bottom:8px}
.hdr h1{font-family:var(--serif);font-size:clamp(22px,4vw,38px);font-weight:400;color:var(--ivory);letter-spacing:-.025em;line-height:1.08;margin-bottom:8px}
.hdr .sub{font-size:12px;color:#7aa89e}
.hdr-meta{display:flex;gap:20px;flex-wrap:wrap;margin-top:16px;font:600 10px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:#5e8c80}
.hdr-meta span{display:flex;align-items:center;gap:5px}
.hdr-meta span::before{content:"";display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--mint)}

/* MACRO CALENDAR */
.macro-bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:32px}
.macro-event{flex:1;min-width:150px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:11px 14px}
.macro-event .me-date{font:700 9px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--sea);margin-bottom:4px}
.macro-event .me-name{font-size:12px;color:var(--ink);font-weight:600;margin-bottom:2px}
.macro-event .me-imp{font-size:10px;color:var(--muted)}

/* TICKER BAR */
.ticker-bar{display:flex;border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:32px;flex-wrap:wrap}
.ticker-item{flex:1;min-width:130px;padding:11px 15px;background:#fff;border-right:1px solid var(--line)}
.ticker-item:last-child{border-right:none}
.ticker-item .tk-pair{font:700 9px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.ticker-item .tk-price{font-family:var(--serif);font-size:18px;color:var(--ink)}
.ticker-item .tk-chg{font:600 10px var(--mono);margin-top:1px}
.ticker-item .tk-chg.up{color:var(--up)}.ticker-item .tk-chg.dn{color:var(--dn)}

/* SECTIONS */
section{margin-bottom:54px}
.kicker{font:700 10px var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--sea);margin-bottom:8px}
.section-title{font-family:var(--serif);font-size:clamp(17px,2.5vw,23px);font-weight:400;color:var(--ink);letter-spacing:-.02em;margin-bottom:18px;padding-bottom:10px;border-bottom:2px solid var(--line)}

/* PRICE ROW */
.price-row{display:flex;align-items:baseline;gap:14px;margin-bottom:18px;flex-wrap:wrap}
.big-price{font-family:var(--serif);font-size:clamp(28px,4.5vw,46px);font-weight:400;color:var(--ink);letter-spacing:-.02em}
.big-chg{display:inline-block;font-family:var(--mono);font-size:15px;font-weight:700;padding:4px 11px;border-radius:8px}
.big-chg.up{background:rgba(22,163,74,.1);color:var(--up)}.big-chg.dn{background:rgba(220,38,38,.08);color:var(--dn)}

/* STATS BAR */
.stats-bar{display:flex;border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:20px;flex-wrap:wrap}
.stat-item{flex:1;min-width:105px;padding:11px 14px;background:#fff;border-right:1px solid var(--line)}
.stat-item:last-child{border-right:none}
.stat-item .slbl{font:600 9px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.stat-item .sval{font-family:var(--serif);font-size:18px;color:var(--ink);line-height:1.05}
.stat-item .sval.up{color:var(--up)}.stat-item .sval.dn{color:var(--dn)}
.stat-item .ssub{font-size:9px;color:var(--muted);margin-top:1px}

/* CARDS */
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:17px 22px;margin-bottom:14px}
.card h3{font-family:var(--serif);font-size:15px;font-weight:400;color:var(--ink);margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid var(--line)}

/* SIGNAL TABLE */
.signal-table{width:100%;border-collapse:collapse;font-size:12px}
.signal-table th{font:700 9px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:7px 10px;text-align:left;border-bottom:2px solid var(--line)}
.signal-table td{padding:8px 10px;border-bottom:1px solid rgba(9,43,39,.06)}
.signal-table tr:last-child td{border-bottom:none}
.signal-table .buy{color:var(--up)}.signal-table .sell{color:var(--dn)}.signal-table .neu{color:#CA8A04}
.signal-table .val{font-family:var(--serif);font-size:14px;color:var(--ink)}

/* MA GRID */
.ma-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.ma-item{display:flex;justify-content:space-between;align-items:center;padding:9px 13px;background:rgba(9,43,39,.04);border-radius:8px;font-size:12px}
.ma-item .ma-name{font-weight:600;color:var(--muted)}
.ma-item .ma-price{font-family:var(--serif);color:var(--ink)}
.ma-item.buy{border-left:3px solid var(--up)}.ma-item.sell{border-left:3px solid var(--dn)}.ma-item.neu{border-left:3px solid #CA8A04}

/* OSC GRID */
.osc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.osc-item{padding:10px 13px;background:rgba(9,43,39,.04);border-radius:8px;text-align:center}
.osc-item .o-name{font:600 9px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:5px}
.osc-item .o-val{font-family:var(--serif);font-size:19px;color:var(--ink)}
.osc-item .o-signal{font-size:10px;color:var(--muted);margin-top:3px}
.osc-item.buy{border-top:3px solid var(--up)}.osc-item.sell{border-top:3px solid var(--dn)}.osc-item.neu{border-top:3px solid #CA8A04}

/* KEY LEVELS */
.levels-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.lev-col h4{font:700 10px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--sea);margin-bottom:9px}
.lev-item{display:flex;justify-content:space-between;padding:8px 11px;background:rgba(9,43,39,.04);border-radius:7px;margin-bottom:5px;font-size:12px}
.lev-item .lev-type{font-weight:600;color:var(--muted)}
.lev-item .lev-price{font-family:var(--serif);color:var(--ink)}

/* EXPERT BOX */
.expert-box{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin-bottom:14px}
.expert-box h3{font-family:var(--serif);font-size:15px;font-weight:400;color:var(--ink);margin-bottom:4px}
.expert-box .source{font:700 9px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--sea);margin-bottom:10px;display:block}
.expert-box p{font-size:12.5px;color:#3a5049;line-height:1.62;margin-bottom:9px}
.expert-box p:last-child{margin-bottom:0}
.expert-box .highlight{background:rgba(14,118,105,.06);border-left:3px solid var(--sea);padding:10px 14px;border-radius:0 8px 8px 0;margin:10px 0;font-size:12.5px;color:#2a3f39;line-height:1.6}
.expert-box .highlight strong{color:var(--sea)}

/* BIAS BOX */
.bias-box{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin-bottom:14px}
.verdict-badge{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:999px;font:700 12px var(--mono);letter-spacing:.07em;text-transform:uppercase}
.v-up{background:rgba(22,163,74,.12);color:var(--up)}.v-dn{background:rgba(220,38,38,.1);color:var(--dn)}.v-neu{background:rgba(202,138,4,.1);color:#CA8A04}
.scenarios{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:13px}
.scenario{padding:12px 14px;border-radius:10px;font-size:12px;line-height:1.5}
.s-bull{background:rgba(22,163,74,.07);border-left:3px solid var(--up)}.s-base{background:rgba(202,138,4,.07);border-left:3px solid #CA8A04}.s-bear{background:rgba(220,38,38,.06);border-left:3px solid var(--dn)}
.scenario strong{display:block;font-weight:700;margin-bottom:3px}
.s-bull strong{color:var(--up)}.s-base strong{color:#CA8A04}.s-bear strong{color:var(--dn)}

/* NEWS */
.news-wrap{display:flex;flex-direction:column;gap:14px}
.news-item{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 20px}
.news-date{font:700 9px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--sea);margin-bottom:5px}
.news-item h3{font-family:var(--serif);font-size:15px;font-weight:400;color:var(--ink);line-height:1.3;margin-bottom:9px}
.news-summary{font-size:12px;color:#4a5e58;line-height:1.55;margin-bottom:10px;padding:10px 13px;background:rgba(9,43,39,.04);border-radius:8px;border-left:3px solid var(--sea);max-height:105px;overflow:hidden;position:relative}
.news-summary::after{content:"";position:absolute;bottom:0;left:0;right:0;height:28px;background:linear-gradient(transparent,#fff)}
.news-impact{border-left:3px solid var(--coral);padding:9px 12px;background:rgba(239,118,95,.04);border-radius:0 8px 8px 0;margin-bottom:10px;font-size:12px;color:#3a5049}
.news-impact strong{color:var(--ink)}
.news-bias{display:inline-block;padding:2px 9px;border-radius:999px;font:700 9px var(--mono);letter-spacing:.07em;text-transform:uppercase;margin-bottom:9px}
.b-up{background:rgba(22,163,74,.1);color:var(--up)}.b-dn{background:rgba(220,38,38,.08);color:var(--dn)}.b-neu{background:rgba(202,138,4,.1);color:#CA8A04}.b-tec{background:rgba(14,118,105,.08);color:var(--sea)}
.readmore{font:700 11px var(--mono);letter-spacing:.08em;text-decoration:none;color:var(--sea);text-transform:uppercase}

/* CHART */
.chart-box{background:#fff;border:1px solid var(--line);border-radius:14px;padding:15px 18px;margin-bottom:14px}
.chart-box h3{font-family:var(--serif);font-size:14px;font-weight:400;color:var(--ink);margin-bottom:11px}

/* SIGNAL BAND */
.signal-band{background:rgba(14,118,105,.05);border:1px solid rgba(14,118,105,.15);border-radius:14px;padding:18px 22px;margin:28px 0}
.signal-lbl{font:700 10px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--sea);margin-bottom:7px}
.signal p{font-size:13px;color:#3a5049;margin:0}
.signal strong{color:var(--ink)}

/* SEPARATOR */
.sep{border:none;border-top:1px solid var(--line);margin:40px 0}
/* FOOTER */
.footer{text-align:center;padding:28px 0 14px;border-top:1px solid var(--line);margin-top:44px;font-size:11px;color:var(--muted)}
.footer span{color:var(--coral)}

/* MACRO STRIP */
.macro-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:32px}
.mc{background:#fff;border:1px solid var(--line);border-radius:11px;padding:12px 14px}
.mc .mc-name{font:700 9px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
.mc .mc-val{font-family:var(--serif);font-size:20px;color:var(--ink)}
.mc .mc-date{font-size:9px;color:var(--muted);margin-top:3px}
.sem{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px}
.sok{background:#22c55e}.swarn{background:#eab308}.scrit{background:#ef4444}

@media(max-width:600px){
  .ma-grid,.osc-grid,.scenarios,.levels-grid{grid-template-columns:1fr}
  .stats-bar{flex-direction:column}
  .hdr{padding:22px 18px}
  .macro-bar{flex-direction:column}
  .macro-strip{grid-template-columns:1fr 1fr}
}
"""

# ── HTML BUILDERS ─────────────────────────────────────────────────────────────

def ma_row(name, price, current_price):
    if price is None: cls, signal = "neu", "---"
    elif current_price > price: cls, signal = "buy", "Compra"
    elif current_price < price: cls, signal = "sell", "Venta"
    else: cls, signal = "neu", "Neutral"
    return (f'<div class="ma-item {cls}">'
            f'<span class="ma-name">{name}</span>'
            f'<span class="ma-price">${"%.2f" % price if price else "---"}</span></div>')

def osc_card(name, value, signal_txt, cls):
    return (f'<div class="osc-item {cls}">'
            f'<div class="o-name">{name}</div>'
            f'<div class="o-val">{value}</div>'
            f'<div class="o-signal">{signal_txt}</div></div>')

def news_span(n):
    """Render Exa content strictly as text; titles/snippets/URLs are untrusted."""
    from urllib.parse import urlsplit
    bc = {"Bullish": "up", "Bearish": "dn", "Neutral": "neu"}.get(n.get("bias"), "neu")
    url = str(n.get("url", ""))
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        url = ""
    date = html_escape(str(n.get("date") or "Fecha no confirmada"))
    title = html_escape(str(n.get("title") or "Sin título"))
    summary = html_escape(str(n.get("summary") or "Extracto no disponible"))
    category = html_escape(str(n.get("impact") or "Sin categoría"))
    bias = html_escape(str(n.get("bias") or "Neutral"))
    link = (f'<a href="{html_escape(url, quote=True)}" rel="noopener noreferrer" '
            f'class="readmore" target="_blank">Leer fuente original &#8594;</a>'
            if url else '<span class="readmore">Enlace no verificado</span>')
    return (f'<div class="news-item">'
            f'<div class="news-date">{date} &nbsp; <span class="news-bias b-{bc}">{bias}</span></div>'
            f'<h3>{title}</h3>'
            f'<p class="news-summary">{summary}</p>'
            f'<div class="news-impact"><strong>Tema identificado automáticamente: </strong>{category}</div>'
            f'{link}</div>')


def bias_section(asset, chg, bull_txt, base_txt, bear_txt):
    vc = bcls(chg)
    vtxt = "Alcista" if vc=="up" else ("Bajista" if vc=="dn" else "Neutral")
    return (f'<div class="bias-box">'
            f'<span class="verdict-badge v-{vc}">{arr(chg)} {vtxt}</span>'
            f'<div class="scenarios">'
            f'<div class="scenario s-bull"><strong>&and; Alcista</strong>{bull_txt}</div>'
            f'<div class="scenario s-base"><strong>&#8594; Base</strong>{base_txt}</div>'
            f'<div class="scenario s-bear"><strong>&or; Bajista</strong>{bear_txt}</div>'
            f'</div></div>')

# ── NEWS ANALYZER ─────────────────────────────────────────────────────────────

def analyze_news(news_list):
    """Fallback news: leave source wording intact; avoid false translation."""
    result = []
    for item in news_list:
        title = item.get("title", "")
        snippet = item.get("highlight", "")
        result.append({
            "date": item.get("date") or "Fecha no confirmada",
            "title": title,
            "url": item.get("url", ""),
            "summary": snippet[:280],
            "bias": "Neutral",   # no calibrated sentiment model
            "impact": classify_impact((title + " " + snippet).lower()),
        })
    return result


def classify_impact(text):
    if any(k in text for k in ["rate hike","inflation","fed","treasury","yield","recession"]):
        return "Tasas mas altas o inflacion reduce flujo de capital hacia activos de riesgo."
    if any(k in text for k in ["etf","institutional","flow","fund"]):
        return "Flujos ETF/institucionales determinan demanda spot y estructura del mercado."
    if any(k in text for k in ["liquidation","short","long","leverage","margin"]):
        return "Niveles de liquidacion actuan como imanes de precio — cascadas amplifican volatilidad."
    if any(k in text for k in ["breakout","resistance","support","technical"]):
        return "Estructura tecnica define rango y puntos de decision critica."
    if any(k in text for k in ["geopolitic","oil","supply","opec","middle east"]):
        return "Tension geopolitica eleva inflacion energetica — efecto mixto en crypto."
    return "Noticia mixta — interpretacion dependera del contexto macro en curso."

# ── MACRO EVENTS ─────────────────────────────────────────────────────────────

# No se anuncian eventos sin calendario oficial verificable e ingestado.
# Anunciar fechas estáticas como "próximas" producía información incorrecta.
MACRO_EVENTS = []


# ── NARRATIVE GENERATOR ───────────────────────────────────────────────────────

def gen_btc_narrative(oc, btc_price_data, btc_rsi, btc_hist, btc_chg, btc_sma200, btc_sma50):
    """
    Generate BTC narrative paragraph from REAL data only.
    If a metric is not available, state it explicitly.
    Note: btc_sma200 and btc_sma50 come from Yahoo Finance computation.
    oc['sma200'] is from bitview and may differ or be unavailable.
    """
    parts = []
    cur = btc_price_data["current"]
    ath = btc_price_data["ath"]
    ath_date = btc_price_data["ath_date"]
    high52 = btc_price_data["high52"]
    low52 = btc_price_data["low52"]
    from_ath = btc_price_data["from_ath"]
    mvrv = oc.get("mvrv")
    rcap = oc.get("rcap")
    mcap = oc.get("mcap")
    asopr = oc.get("asopr")
    nupl = oc.get("nupl")
    hr = oc.get("hr")
    diff = oc.get("diff")
    addrs = oc.get("addrs")
    utxos = oc.get("utxos")
    # Use computed SMA200 from Yahoo, not bitview (which may be None)
    sma200 = btc_sma200  # ← parameter, not oc.get("sma200")
    rhodl = oc.get("rhodl")

    # RSI signal
    if btc_rsi and btc_rsi < 40:
        rsi_sig = f"RSI(14) en {btc_rsi:.0f} indica zona de sobreventa — potencial de rebote."
    elif btc_rsi and btc_rsi > 65:
        rsi_sig = f"RSI(14) en {btc_rsi:.0f} muestra condiciones de sobrecompra."
    else:
        rsi_sig = f"RSI(14) en {btc_rsi:.0f} se encuentra en zona neutral."

    # MACD signal
    if btc_hist is not None:
        macd_sig = "MACD con histograma positivo — momentum alcista." if btc_hist > 0 else "MACD con histograma negativo — momentum bajista."
    else:
        macd_sig = "MACD no disponible en este momento."

    # MVRV interpretation
    if mvrv:
        if mvrv < 1.0:
            mvrv_int = "MVRV bajo 1.0x indica que el precio esta POR DEBAJO del costo base promedio de todos los holders — territory de acumulacion profunda."
        elif mvrv < 1.5:
            mvrv_int = f"MVRV de {mvrv:.2f}x indica fase de acumulacion. El mercado esta relativamente barato vs costo realizado de ${rcap:.2f}T."
        elif mvrv < 2.5:
            mvrv_int = f"MVRV de {mvrv:.2f}x es tipico de fases intermedias del ciclo. Capitalización de mercado equivalente a {mvrv:.2f} veces la capitalización realizada (${rcap:.2f}T); el ratio no demuestra una fase de ciclo por sí solo."
        elif mvrv < 3.5:
            mvrv_int = f"MVRV de {mvrv:.2f}x comienza a indicar sobrevaloracion.鳌"
        else:
            mvrv_int = f"MVRV de {mvrv:.2f}x es territorio de euforia — cuidado con topes de ciclo."
    else:
        mvrv_int = "MVRV no disponible en la base de datos local."

    # Diferenciar drawdown hasta el spot actual vs caída ATH -> mínimo anual.
    low_drawdown = btc_price_data["drawdown_to_low52"]
    cycle_txt = (
        f"BTC cotiza {from_ath:.1f}% por debajo de su ATH de ${ath:,.0f} "
        f"({ath_date}). El mínimo de las últimas 52 semanas fue ${low52:,.0f} "
        f"({btc_price_data['low52_date']}), equivalente a {low_drawdown:.1f}% "
        "respecto al ATH. Son variaciones distintas. El valor actual del MVRV "
        "no permite determinar por sí solo el máximo histórico del indicador."
    )

    # aSOPR
    if asopr:
        if asopr < 1.0:
            asopr_int = f"aSOPR 1W en {asopr:.4f} sugiere pérdidas realizadas agregadas; no prueba acumulación."
        elif asopr < 1.1:
            asopr_int = f"aSOPR 1W en {asopr:.4f} muestra gastos agregados con ganancia moderada."
        elif asopr < 1.3:
            asopr_int = f"aSOPR 1W en {asopr:.4f} sugiere realizacion de ganancias pero sin euforia."
        else:
            asopr_int = f"aSOPR 1W en {asopr:.4f} refleja un múltiplo alto de beneficio realizado en salidas gastadas, sin pronosticar un techo."
    else:
        asopr_int = "aSOPR 1W no disponible."

    # NUPL
    if nupl:
        if nupl < 0:
            nupl_int = "NUPL negativo indica capitulacion — fear extremo."
        elif nupl < 0.25:
            nupl_int = f"NUPL en {nupl:.3f} indica fase de creencias de riesgo bajo."
        elif nupl < 0.5:
            nupl_int = f"NUPL en {nupl:.3f} muestra fase de optimismo creciente."
        elif nupl < 0.75:
            nupl_int = f"NUPL en {nupl:.3f} indica fase de creencia/media/euforia."
        else:
            nupl_int = f"NUPL en {nupl:.3f} es territorio de euforia/extremo."
    else:
        nupl_int = "NUPL no disponible."

    # Hash rate / mining
    if hr:
        hr_int = f"Hash rate en {int(hr):,} EH/s y dificultad en {diff:.0f}T describen el estado de la red, sin inferir inversión minera."
    else:
        hr_int = "Hash rate no disponible en la base de datos."

    # SMA position
    if btc_sma200 and btc_sma50:
        if cur > btc_sma200 and cur > btc_sma50:
            ma_txt = f"Precio opera encima de SMA 200 (${btc_sma200:,.0f}) y SMA 50 (${btc_sma50:,.0f}) — estructura alcista de largo plazo intacta."
        elif cur > btc_sma200:
            ma_txt = f"Precio encima de SMA 200 (${btc_sma200:,.0f}) pero debajo de SMA 50 (${btc_sma50:,.0f}) — cautela."
        elif cur > btc_sma50:
            ma_txt = f"Precio encima de SMA 50 (${btc_sma50:,.0f}) pero debajo de SMA 200 (${btc_sma200:,.0f}) — estructura deteriorada."
        else:
            ma_txt = f"Precio debajo de SMA 200 (${btc_sma200:,.0f}) y SMA 50 (${btc_sma50:,.0f}) — estructura bajista."
    elif btc_sma200:
        if cur > btc_sma200:
            ma_txt = f"Precio encima de SMA 200 (${btc_sma200:,.0f}) — estructura de largo plazo positiva."
        else:
            ma_txt = f"Precio debajo de SMA 200 (${btc_sma200:,.0f}) — estructura de largo plazo comprometida."
    else:
        ma_txt = "SMA 200 no disponible para analisis."

    return {
        "rsi_sig": rsi_sig,
        "macd_sig": macd_sig,
        "mvrv_int": mvrv_int,
        "cycle_txt": cycle_txt,
        "asopr_int": asopr_int,
        "nupl_int": nupl_int,
        "hr_int": hr_int,
        "ma_txt": ma_txt,
    }

def gen_spy_narrative(spy_price, spy_chg, spy_rsi, spy_sma50, spy_sma200, m10y_val, m10y_date, mvix, mdxy):
    """Generate SPY narrative from REAL data only."""
    parts = []

    # RSI signal
    if spy_rsi and spy_rsi < 40:
        rsi_sig = f"RSI(14) en {spy_rsi:.0f} indica condiciones de sobreventa."
    elif spy_rsi and spy_rsi > 60:
        rsi_sig = f"RSI(14) en {spy_rsi:.0f} muestra sobrecompra."
    else:
        rsi_sig = f"RSI(14) en {spy_rsi:.0f} se encuentra en zona neutral."

    # Yield context
    if m10y_val:
        if m10y_val > 5.5:
            yield_txt = f"El yield del Tesoro a 10Y en {m10y_val:.3f}% ({m10y_date}) sigue cerca de maximos de 19 anos. Tasas elevadas presionan multiplos de renta variable."
        elif m10y_val > 5.0:
            yield_txt = f"El yield a 10Y en {m10y_val:.3f}% ({m10y_date}) mantiene presion sobre multiplos pero sin niveles extremos."
        else:
            yield_txt = f"Tasas a 10Y en {m10y_val:.3f}% ({m10y_date}) en niveles normales."
    else:
        yield_txt = "Dato de yield no disponible."

    # VIX
    if mvix:
        if mvix > 25:
            vix_txt = f"VIX en {mvix:.0f} indica prima de riesgo elevada — miedo en el mercado."
        elif mvix > 15:
            vix_txt = f"VIX en {mvix:.0f} indica baja prima de riesgo — complacencia."
        else:
            vix_txt = f"VIX en {mvix:.0f} es territorio de minima volatilidad."
    else:
        vix_txt = "VIX no disponible."

    # DXY
    if mdxy:
        if mdxy > 125:
            dxy_txt = f"USD Broad Index en {mdxy:.1f} siguehistoricamente fuerte — headwind para activos de riesgo."
        elif mdxy > 110:
            dxy_txt = f"USD Broad Index en {mdxy:.1f} en niveles elevados pero contenidos."
        else:
            dxy_txt = f"USD Broad Index en {mdxy:.1f} en niveles normales."
    else:
        dxy_txt = "USD Broad Index no disponible."

    return {
        "rsi_sig": rsi_sig,
        "yield_txt": yield_txt,
        "vix_txt": vix_txt,
        "dxy_txt": dxy_txt,
    }

def gen_gold_narrative(gold_price, gold_chg, gold_rsi, gold_hist, m10y_val, mvix, mdxy):
    """Generate Gold narrative from REAL data only."""
    if gold_rsi and gold_rsi < 40:
        rsi_sig = f"RSI(14) en {gold_rsi:.0f} indica zona de sobreventa — potencial rebote."
    elif gold_rsi and gold_rsi > 65:
        rsi_sig = f"RSI(14) en {gold_rsi:.0f} muestra condiciones de sobrecompra."
    else:
        rsi_sig = f"RSI(14) en {gold_rsi:.0f} en zona neutral."

    if gold_hist is not None:
        macd_sig = "MACD con histograma positivo — momentum alcista para el oro." if gold_hist > 0 else "MACD con histograma negativo — momentum bajista."
    else:
        macd_sig = "MACD no disponible."

    yield_txt = f"Yields a 10Y en {m10y_val:.3f}% siguen siendo el principal viento en contra para el oro." if m10y_val else "Yields no disponibles."
    dxy_txt = f"USD Broad Index en {mdxy:.1f}: dolar fuerte presiona al oro." if mdxy else "USD Broad Index no disponible."

    return {
        "rsi_sig": rsi_sig,
        "macd_sig": macd_sig,
        "yield_txt": yield_txt,
        "dxy_txt": dxy_txt,
    }

def gen_macro_narrative(macro):
    """Generate macro narrative from FRED data."""
    m10y = macro.get("10y", (None,None))
    m2y   = macro.get("2y", (None,None))
    mdxy  = macro.get("dxy", (None,None))
    mvix  = macro.get("vix", (None,None))
    mcpi  = macro.get("cpi_yoy", (None,None))
    munemp= macro.get("unemp", (None,None))
    mnfp  = macro.get("nfp", (None,None))

    parts = []
    if mcpi[1]:
        parts.append(f"IPC YoY en {mcpi[1]:.2f}%.")
    if munemp[1]:
        parts.append(f"Desempleo en {munemp[1]:.1f}%.")
    if mnfp[1] is not None:
        sign = "+" if mnfp[1] >= 0 else ""
        parts.append(f"NFP {sign}{mnfp[1]:.0f}K mes.")
    if m10y[1]:
        parts.append(f"10Y en {m10y[1]:.3f}%.")
    if mdxy[1]:
        parts.append(f"USD Broad Index en {mdxy[1]:.2f}.")
    if mvix[1]:
        parts.append(f"VIX en {mvix[1]:.1f}.")

    if not parts:
        return "Datos macro no disponibles en la base local."

    return " | ".join(parts)


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    btc_ohlc    = yahoo_ohlc("BTC-USD", 252)  # única serie de precio diario BTC
    spy_ohlc    = yahoo_ohlc("SPY", 252)
    spx_ohlc    = yahoo_ohlc("^GSPC", 90)
    gold_ohlc   = yahoo_ohlc("GC=F", 252)
    silver_ohlc = yahoo_ohlc("SI=F", 90)
    oil_ohlc    = yahoo_ohlc("CL=F", 90)
    oc = get_btc_onchain()
    macro = get_macro_fred()

    ohlc_f = [r for r in btc_ohlc if r["close"] is not None and r["high"] is not None and r["low"] is not None]
    if len(ohlc_f) < 2:
        raise RuntimeError("BTC-USD no dispone de dos cierres válidos: se bloquea el reporte")
    last_utc = datetime.datetime.fromtimestamp(
        ohlc_f[-1]["ts"], datetime.timezone.utc
    ).date()
    age = (datetime.datetime.now(datetime.timezone.utc).date() - last_utc).days
    if age < -1 or age > 2:
        raise RuntimeError(f"Precio BTC-USD desactualizado: fecha {last_utc}, diferencia {age} días")
    btc_price, prev_btc = ohlc_f[-1]["close"], ohlc_f[-2]["close"]
    if btc_price is None or btc_price <= 0 or prev_btc is None or prev_btc <= 0:
        raise RuntimeError("Precio BTC-USD inválido para variación diaria")
    btc_chg = (btc_price / prev_btc - 1) * 100
    btc_pdata = get_btc_price_data(btc_price)
    btc_c = [r["close"] for r in ohlc_f]
    btc_h = [r["high"]  for r in ohlc_f]
    btc_l = [r["low"]   for r in ohlc_f]
    btc_v = [r["volume"] for r in ohlc_f]
    btc_rsi  = compute_rsi(btc_c)
    btc_macd, btc_sig, btc_hist = compute_macd(btc_c)
    btc_sup, btc_res = find_supp_res(btc_c, btc_h, btc_l)
    btc_sma20  = compute_ma(btc_c, 20)
    btc_sma50  = compute_ma(btc_c, 50)
    btc_sma100 = compute_ma(btc_c, 100)
    btc_sma200 = compute_ma(btc_c, 200)
    oc["sma200"] = btc_sma200  # desde Yahoo Finance (para compatibilidad con HTML)
    btc_stoch_k, btc_stoch_d = stoch(btc_h, btc_l, btc_c)
    btc_willr  = williams_r(btc_h, btc_l, btc_c)
    btc_cci    = cci(btc_h, btc_l, btc_c)
    btc_atr    = atr(btc_h, btc_l, btc_c)
    btc_vol_avg = sum(btc_v[-20:])/20 if len(btc_v)>=20 else 0

    spy_ohlc_f = [r for r in spy_ohlc if r["close"] is not None and r["high"] is not None and r["low"] is not None]
    spy_c = [r["close"] for r in spy_ohlc_f]
    spy_h = [r["high"]  for r in spy_ohlc_f]
    spy_l = [r["low"]   for r in spy_ohlc_f]
    spy_cur = spy_c[-1] if spy_c else None
    spy_rsi  = compute_rsi(spy_c)
    spy_macd, spy_sig, spy_hist = compute_macd(spy_c)
    spy_atr    = atr(spy_h, spy_l, spy_c)
    spy_sup, spy_res = find_supp_res(spy_c, spy_h, spy_l)
    spy_sma20  = compute_ma(spy_c, 20)
    spy_sma50  = compute_ma(spy_c, 50)
    spy_sma100 = compute_ma(spy_c, 100)
    spy_sma200 = compute_ma(spy_c, 200)
    spy_stoch_k, _ = stoch(spy_h, spy_l, spy_c)
    spy_willr  = williams_r(spy_h, spy_l, spy_c)
    spy_cci    = cci(spy_h, spy_l, spy_c)
    spy_atr    = atr(spy_h, spy_l, spy_c)

    gold_ohlc_f = [r for r in gold_ohlc if r["close"] is not None and r["high"] is not None and r["low"] is not None]
    gold_c = [r["close"] for r in gold_ohlc_f]
    gold_h = [r["high"]  for r in gold_ohlc_f]
    gold_l = [r["low"]   for r in gold_ohlc_f]
    gold_cur = gold_c[-1] if gold_c else None
    gold_rsi  = compute_rsi(gold_c)
    gold_macd, gold_sig, gold_hist = compute_macd(gold_c)
    gold_sup, gold_res = find_supp_res(gold_c, gold_h, gold_l)
    gold_sma20  = compute_ma(gold_c, 20)
    gold_sma50  = compute_ma(gold_c, 50)
    gold_sma100 = compute_ma(gold_c, 100)
    gold_sma200 = compute_ma(gold_c, 200)
    gold_stoch_k, _ = stoch(gold_h, gold_l, gold_c)
    gold_willr  = williams_r(gold_h, gold_l, gold_c)
    gold_cci    = cci(gold_h, gold_l, gold_c)
    gold_atr    = atr(gold_h, gold_l, gold_c)

    gold_price   = gold_c[-1] if gold_c else None
    gold_chg     = (gold_c[-1]-gold_c[-2])/gold_c[-2]*100 if len(gold_c)>=2 else None
    spy_price    = spy_c[-1] if spy_c else None
    spy_chg      = (spy_c[-1]-spy_c[-2])/spy_c[-2]*100 if len(spy_c)>=2 else None
    spx_price    = spx_ohlc[-1]["close"] if spx_ohlc else None
    spx_prev     = spx_ohlc[-2]["close"] if len(spx_ohlc)>=2 else None
    spx_chg      = (spx_price-spx_prev)/spx_prev*100 if spx_price and spx_prev else None
    silver_price = silver_ohlc[-1]["close"] if silver_ohlc else None
    silver_prev  = silver_ohlc[-2]["close"] if len(silver_ohlc)>=2 else None
    silver_chg   = (silver_price-silver_prev)/silver_prev*100 if silver_price and silver_prev else None
    oil_price = oil_ohlc[-1]["close"] if oil_ohlc else None
    oil_prev  = oil_ohlc[-2]["close"] if len(oil_ohlc)>=2 else None
    oil_chg   = (oil_price-oil_prev)/oil_prev*100 if oil_price and oil_prev else None

    # Macro values
    m10y = macro["10y"][1]; m10y_date = macro["10y"][0]
    m2y  = macro["2y"][1];  m2y_date  = macro["2y"][0]
    mdxy = macro["dxy"][1]; mdxy_date = macro["dxy"][0]
    mvix = macro["vix"][1]; mvix_date = macro["vix"][0]
    mcpi = macro["cpi_yoy"][1]; mcpi_date = macro["cpi_yoy"][0]
    munemp= macro["unemp"][1]; munemp_date = macro["unemp"][0]
    mnfp = macro["nfp"][1]; mnfp_date = macro["nfp"][0]
    mbtc_fred = macro["btc_fred"][1]; mbtc_fred_date = macro["btc_fred"][0]
    mnfci = macro["nfci"][1]; mnfci_date = macro["nfci"][0]

    btc_support_levels, btc_resistance_levels = price_relative_levels(btc_price, btc_sup, btc_res)
    spy_support_levels, spy_resistance_levels = price_relative_levels(spy_price, spy_sup, spy_res)
    gold_support_levels, gold_resistance_levels = price_relative_levels(gold_price, gold_sup, gold_res)

    # Generate narratives
    btc_narr = gen_btc_narrative(oc, btc_pdata, btc_rsi, btc_hist, btc_chg,
                                  oc.get("sma200"), btc_sma50)
    spy_narr = gen_spy_narrative(spy_price, spy_chg, spy_rsi, spy_sma50, spy_sma200,
                                  m10y, m10y_date, mvix, mdxy)
    gold_narr = gen_gold_narrative(gold_price, gold_chg, gold_rsi, gold_hist, m10y, mvix, mdxy)
    macro_narr = gen_macro_narrative(macro)

    # News via separate pipeline module
    if HAS_NEWS_PIPELINE:
        news_raw = run_news_pipeline({
            "BTC":   "Bitcoin BTC crypto price analysis October 2026",
            "SPY":   "S&P 500 stock market equities analysis October 2026",
            "GOLD":  "gold oil commodities price analysis October 2026",
        })
        nb = normalize_for_report(news_raw.get("BTC", []))
        ns = normalize_for_report(news_raw.get("SPY", []))
        nm = normalize_for_report(news_raw.get("GOLD", []))
        if not nb and not ns and not nm:
            print("WARNING: No valid news from any source", file=sys.stderr)
    else:
        # Fallback: legacy inline search
        news_btc    = exa_search("Bitcoin BTC crypto price analysis October 2026", n=5)
        news_stocks = exa_search("S&P 500 stock market equities analysis October 2026", n=5)
        news_metals = exa_search("gold oil commodities price analysis October 2026", n=5)
        nb = analyze_news(news_btc)
        ns = analyze_news(news_stocks)
        nm = analyze_news(news_metals)

    btc_chart  = svg_price(ohlc_f[-90:], "$", 200)
    spy_chart  = svg_price(spy_ohlc_f[-90:], "$", 180)
    gold_chart = svg_price(gold_ohlc_f[-90:], "$", 180)

    # ── BTC TECHNICAL ────────────────────────────────────────────────────────
    buy_s  = sum(1 for p in [btc_sma20, btc_sma50, btc_sma100, btc_sma200]
                 if p and btc_c[-1] > p)
    sell_s = sum(1 for p in [btc_sma20, btc_sma50, btc_sma100, btc_sma200]
                 if p and btc_c[-1] < p)
    rsi_sig = "Compra" if btc_rsi and btc_rsi<40 else ("Venta" if btc_rsi and btc_rsi>60 else "Neutral")
    macd_sig = "Compra" if btc_hist and btc_hist>0 else ("Venta" if btc_hist and btc_hist<0 else "Neutral")
    stoch_sig = "Sobreventa" if btc_stoch_k and btc_stoch_k<20 else ("Sobrecompra" if btc_stoch_k and btc_stoch_k>80 else "Neutral")
    willr_sig = "Sobreventa" if btc_willr and btc_willr<-80 else ("Sobrecompra" if btc_willr and btc_willr>-20 else "Neutral")
    cci_sig   = "Compra" if btc_cci and btc_cci<-100 else ("Venta" if btc_cci and btc_cci>100 else "Neutral")

    btc_ma_items = (
        ma_row("SMA 20", btc_sma20, btc_c[-1]) +
        ma_row("SMA 50", btc_sma50, btc_c[-1]) +
        ma_row("SMA 100", btc_sma100, btc_c[-1]) +
        ma_row("SMA 200", btc_sma200, btc_c[-1]) +
        ma_row("EMA 20", ema_python(btc_c, 20), btc_c[-1]) +
        ma_row("EMA 50", ema_python(btc_c, 50), btc_c[-1])
    )

    btc_osc_items = (
        osc_card("RSI(14)", f"{btc_rsi:.0f}" if btc_rsi else "---", rsi_sig,
                 "buy" if (btc_rsi and btc_rsi<40) else ("sell" if (btc_rsi and btc_rsi>60) else "neu")) +
        osc_card("MACD", (f"{btc_hist:.2f}" if btc_hist else "---"), macd_sig,
                 "buy" if (btc_hist and btc_hist>0) else ("sell" if (btc_hist and btc_hist<0) else "neu")) +
        osc_card("Estocastico", f"{btc_stoch_k:.0f}" if btc_stoch_k else "---", stoch_sig,
                 "buy" if (btc_stoch_k and btc_stoch_k<20) else ("sell" if (btc_stoch_k and btc_stoch_k>80) else "neu")) +
        osc_card("Williams %R", f"{btc_willr:.0f}" if btc_willr else "---", willr_sig,
                 "buy" if (btc_willr and btc_willr<-80) else ("sell" if (btc_willr and btc_willr>-20) else "neu")) +
        osc_card("CCI(20)", f"{btc_cci:.0f}" if btc_cci else "---", cci_sig,
                 "buy" if (btc_cci and btc_cci<-100) else ("sell" if (btc_cci and btc_cci>100) else "neu")) +
        osc_card("ATR(14)", f"${btc_atr:,.0f}" if btc_atr else "---", "Volatilidad", "neu")
    )

    # ── SPY TECHNICAL ────────────────────────────────────────────────────────
    spy_rsi_sig = "Compra" if spy_rsi and spy_rsi<40 else ("Venta" if spy_rsi and spy_rsi>60 else "Neutral")
    spy_osc_items = (
        osc_card("RSI(14)", f"{spy_rsi:.0f}" if spy_rsi else "---", spy_rsi_sig,
                 "buy" if (spy_rsi and spy_rsi<40) else ("sell" if (spy_rsi and spy_rsi>60) else "neu")) +
        osc_card("Williams %R", f"{spy_willr:.0f}" if spy_willr else "---",
                 "Sobreventa" if spy_willr and spy_willr<-80 else ("Sobrecompra" if spy_willr and spy_willr>-20 else "Neutral"),
                 "buy" if (spy_willr and spy_willr<-80) else ("sell" if (spy_willr and spy_willr>-20) else "neu")) +
        osc_card("CCI(20)", f"{spy_cci:.0f}" if spy_cci else "---", "Neutral", "neu") +
        osc_card("ATR(14)", f"${spy_atr:,.2f}" if spy_atr else "---", "Volatilidad", "neu") +
        osc_card("Estocastico", f"{spy_stoch_k:.0f}" if spy_stoch_k else "---", "Neutral", "neu") +
        osc_card("MACD", "---", "N/A", "neu")
    )
    spy_ma_items = (
        ma_row("SMA 20", spy_sma20, spy_c[-1]) +
        ma_row("SMA 50", spy_sma50, spy_c[-1]) +
        ma_row("SMA 100", spy_sma100, spy_c[-1]) +
        ma_row("SMA 200", spy_sma200, spy_c[-1]) +
        ma_row("EMA 20", ema_python(spy_c, 20), spy_c[-1]) +
        ma_row("EMA 50", ema_python(spy_c, 50), spy_c[-1])
    )

    # ── GOLD TECHNICAL ────────────────────────────────────────────────────────
    gold_rsi_sig = "Compra" if gold_rsi and gold_rsi<40 else ("Venta" if gold_rsi and gold_rsi>60 else "Neutral")
    gold_macd_sig = "Compra" if gold_hist and gold_hist>0 else ("Venta" if gold_hist and gold_hist<0 else "Neutral")
    gold_stoch_sig = "Sobreventa" if gold_stoch_k and gold_stoch_k<20 else ("Sobrecompra" if gold_stoch_k and gold_stoch_k>80 else "Neutral")
    gold_willr_sig = "Sobreventa" if gold_willr and gold_willr<-80 else ("Sobrecompra" if gold_willr and gold_willr>-20 else "Neutral")
    gold_cci_sig = "Compra" if gold_cci and gold_cci<-100 else ("Venta" if gold_cci and gold_cci>100 else "Neutral")

    gold_ma_items = (
        ma_row("SMA 20", gold_sma20, gold_c[-1]) +
        ma_row("SMA 50", gold_sma50, gold_c[-1]) +
        ma_row("SMA 100", gold_sma100, gold_c[-1]) +
        ma_row("SMA 200", gold_sma200, gold_c[-1]) +
        ma_row("EMA 20", ema_python(gold_c, 20), gold_c[-1]) +
        ma_row("EMA 50", ema_python(gold_c, 50), gold_c[-1])
    )
    gold_osc_items = (
        osc_card("RSI(14)", f"{gold_rsi:.0f}" if gold_rsi else "---", gold_rsi_sig,
                 "buy" if (gold_rsi and gold_rsi<40) else ("sell" if (gold_rsi and gold_rsi>60) else "neu")) +
        osc_card("MACD", (f"{gold_hist:.2f}" if gold_hist else "---"), gold_macd_sig,
                 "buy" if (gold_hist and gold_hist>0) else ("sell" if gold_hist and gold_hist<0 else "neu")) +
        osc_card("Estocastico", f"{gold_stoch_k:.0f}" if gold_stoch_k else "---", gold_stoch_sig,
                 "buy" if (gold_stoch_k and gold_stoch_k<20) else ("sell" if gold_stoch_k and gold_stoch_k>80 else "neu")) +
        osc_card("Williams %R", f"{gold_willr:.0f}" if gold_willr else "---", gold_willr_sig,
                 "buy" if (gold_willr and gold_willr<-80) else ("sell" if gold_willr and gold_willr>-20 else "neu")) +
        osc_card("CCI(20)", f"{gold_cci:.0f}" if gold_cci else "---", gold_cci_sig,
                 "buy" if (gold_cci and gold_cci<-100) else ("sell" if gold_cci and gold_cci>100 else "neu")) +
        osc_card("ATR(14)", f"${gold_atr:,.0f}" if gold_atr else "---", "Volatilidad", "neu")
    )

    # ── ASSEMBLE HTML ──────────────────────────────────────────────────────
    parts = []
    def A(s): parts.append(s)

    A('<!DOCTYPE html><html lang="es">')
    A('<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">')
    A('<title>Mercados Daily Pro - '+TODAY_STR+'</title>')
    A('<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400&family=IBM+Plex+Mono:wght@400;600&family=Manrope:wght@400;600;700&display=swap" rel="stylesheet">')
    A('<style>'+CSS+'</style></head><body>')
    A('<div class="wrap">')

    # HEADER
    A('<div class="hdr">')
    A('<div class="hdr-kicker">'+TODAY_STR+' — Mercados Daily Pro</div>')
    A('<h1>Reporte Integral: BTC + Renta Variable + Commodities</h1>')
    A('<p class="sub">Datos verificados: Yahoo Finance (precio) | bitview local DB (on-chain BTC) | FRED (macro)</p>')
    A('<div class="hdr-meta">')
    A('<span>Fuentes: Yahoo Finance · bitview DB · FRED · Exa Search</span>')
    A('<span>Generado '+NOW_STR+'</span>')
    A('</div></div>')

    # MACRO CALENDAR
    A('<div class="kicker" style="margin-bottom:8px">Calendario Macroeconomico</div>')
    A('<div class="macro-bar">')
    if not MACRO_EVENTS:
        A('<div class="macro-event"><div class="me-name">Calendario pendiente de verificación</div>'
          '<div class="me-imp">Sin eventos programados verificables en la fuente local. '
          'No se presentan fechas estimadas.</div></div>')
    for date, name, imp, desc in MACRO_EVENTS:
        A(f'<div class="macro-event">'
          f'<div class="me-date">{date} &middot; {imp}</div>'
          f'<div class="me-name">{name}</div>'
          f'<div class="me-imp">{desc}</div></div>')
    A('</div>')

    # MACRO STRIP (FRED real data)
    A('<div class="kicker" style="margin-bottom:8px">Snapshot Macro — FRED (datos reales)</div>')
    A('<div class="macro-strip" style="display:grid;grid-template-columns:repeat(auto-fit,minmax:130px,1fr);gap:10px;margin-bottom:32px">')
    def ms(label, value, date, status="ok"):
        sem = "sok" if status=="ok" else ("swarn" if status=="warn" else "scrit")
        return f'<div class="mc"><div class="mc-name">{label}</div><div class="mc-val">{"--" if value is None else value}</div><div class="mc-date">{date or ""}</div></div>'
    def sem_v(v, lo, hi, reverse=False):
        if v is None: return "sok"
        if reverse: return "scrit" if v < lo else ("swarn" if v < hi else "sok")
        return "scrit" if v > hi else ("swarn" if v > lo else "sok")
    s10y = sem_v(m10y, 5.5, 6.0)
    s2y  = sem_v(m2y, 5.0, 5.5)
    sdxy = sem_v(mdxy, 125, 130)
    svix = sem_v(mvix, 25, 35, reverse=True)
    A(ms("Yield 10Y", f"{m10y:.3f}%" if m10y else None, m10y_date, s10y))
    A(ms("Yield 2Y",  f"{m2y:.3f}%"  if m2y  else None, m2y_date,  s2y))
    A(ms("USD Broad",        f"{mdxy:.2f}"    if mdxy else None, mdxy_date, sdxy))
    A(ms("VIX",        f"{mvix:.2f}"     if mvix else None, mvix_date, svix))
    A(ms("CPI YoY",    f"{mcpi:.2f}%"   if mcpi else None, mcpi_date, "sok"))
    A(ms("Desempleo",  f"{munemp:.1f}%"  if munemp else None, munemp_date, "sok"))
    nfp_val = f"+{mnfp:.0f}K" if mnfp and mnfp > 0 else (f"{mnfp:.0f}K" if mnfp else None)
    A(ms("NFP",        nfp_val, mnfp_date, "sok"))
    A(ms("BTC FRED",   f"${int(mbtc_fred):,}" if mbtc_fred else None, mbtc_fred_date, "sok"))
    A('</div>')

    # TICKER BAR
    A('<div class="ticker-bar">')
    A(f'<div class="ticker-item"><div class="tk-pair">BTC/USD</div><div class="tk-price">{usd0(btc_price)}</div><div class="tk-chg {bcls(btc_chg)}">{pct(btc_chg)}</div></div>')
    A(f'<div class="ticker-item"><div class="tk-pair">S&P 500 IND</div><div class="tk-price">{"%.0f"%spx_price if spx_price else "---"}</div><div class="tk-chg {bcls(spx_chg)}">{pct(spx_chg)}</div></div>')
    A(f'<div class="ticker-item"><div class="tk-pair">SPDR S&P 500</div><div class="tk-price">{usd(spy_price)}</div><div class="tk-chg {bcls(spy_chg)}">{pct(spy_chg)}</div></div>')
    A(f'<div class="ticker-item"><div class="tk-pair">ORO XAU</div><div class="tk-price">{usd0(gold_price)}</div><div class="tk-chg {bcls(gold_chg)}">{pct(gold_chg)}</div></div>')
    A(f'<div class="ticker-item"><div class="tk-pair">PLATA FUT. SI=F</div><div class="tk-price">{usd(silver_price)}</div><div class="tk-chg {bcls(silver_chg)}">{pct(silver_chg)}</div></div>')
    A(f'<div class="ticker-item"><div class="tk-pair">WTI (CL=F)</div><div class="tk-price">{usd(oil_price)}</div><div class="tk-chg {bcls(oil_chg)}">{pct(oil_chg)}</div></div>')
    A('</div>')

    # ══════════════════════ BITCOIN ═══════════════════════════════════════════
    A('<section>')
    A('<div class="kicker">Cripto</div>')
    A('<div class="section-title">Bitcoin (BTC) — Analisis de Precio</div>')

    A('<div class="price-row">')
    A(f'<span class="big-price">{usd0(btc_price)}</span>')
    A(f'<span class="big-chg {bcls(btc_chg)}">{arr(btc_chg)} {pct(btc_chg)} (cierre previo)</span>')
    A('</div>')

    A('<div class="stats-bar">')
    A(f'<div class="stat-item"><div class="slbl"> ATH</div><div class="sval dn">{"$%.0f"%btc_pdata["ath"]}</div><div class="ssub">{btc_pdata["ath_date"]}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Max 52s</div><div class="sval">{"$%.0f"%btc_pdata["high52"]}</div><div class="ssub">{btc_pdata["high52_date"]}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Min 52s</div><div class="sval up">{"$%.0f"%btc_pdata["low52"]}</div><div class="ssub">{btc_pdata["low52_date"]}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Desde ATH</div><div class="sval dn">{btc_pdata["from_ath"]:.1f}%</div><div class="ssub">caida desde max</div></div>')
    A(f'<div class="stat-item"><div class="slbl">RSI(14)</div><div class="sval {"up" if btc_rsi and btc_rsi<40 else ("dn" if btc_rsi and btc_rsi>65 else "")}">{"%.0f"%btc_rsi if btc_rsi else "---"}</div><div class="ssub">Wilder smoothing</div></div>')
    A(f'<div class="stat-item"><div class="slbl">MACD</div><div class="sval {"up" if btc_hist and btc_hist>0 else ("dn" if btc_hist and btc_hist<0 else "")}">{"%.2f"%btc_hist if btc_hist else "---"}</div><div class="ssub">histograma</div></div>')
    A(f'<div class="stat-item"><div class="slbl">ATR(14)</div><div class="sval">{"$%.0f"%btc_atr if btc_atr else "---"}</div><div class="ssub">rango avg</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Mkt Cap</div><div class="sval">{oc["mcap"]:.2f}T</div><div class="ssub">USD</div></div>')
    A(f'<div class="stat-item"><div class="slbl">MVRV</div><div class="sval">{"%.2fx"%oc["mvrv"] if oc["mvrv"] else "---"}</div><div class="ssub">precio/realized</div></div>')
    A(f'<div class="stat-item"><div class="slbl">aSOPR 1W</div><div class="sval">{"%.4f"%oc["asopr"] if oc["asopr"] else "---"}</div><div class="ssub">profitabilidad</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Hash Rate</div><div class="sval">{eh(oc["hr"]) if oc["hr"] else "---"}</div><div class="ssub">red BTC</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Act. Addrs</div><div class="sval">{"%d"%oc["addrs"] if oc["addrs"] else "---"}</div><div class="ssub">promedio 24h</div></div>')
    A('</div>')

    A('<div class="chart-box"><h3>Precio BTC — 90 cierres diarios (SMA 20 dorado, SMA 50 verde)</h3>'+btc_chart+'</div>')

    # On-chain grid
    A('<div class="card"><h3>Datos On-Chain — bitview.space (datos reales de la red BTC)</h3>')
    A('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:16px">')
    A(f'<div class="stat-item"><div class="slbl">MVRV</div><div class="sval">{"%.2fx"%oc["mvrv"] if oc["mvrv"] else "---"}</div><div class="ssub">Market/Realized Cap</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Realized Cap</div><div class="sval">${oc["rcap"]:.2f}T</div><div class="ssub">costo base network</div></div>')
    A(f'<div class="stat-item"><div class="slbl">aSOPR 1W</div><div class="sval">{"%.4f"%oc["asopr"] if oc["asopr"] else "---"}</div><div class="ssub">avg profit holders</div></div>')
    A(f'<div class="stat-item"><div class="slbl">SOPR 1W</div><div class="sval">{"%.4f"%oc["sopr"] if oc["sopr"] else "---"}</div><div class="ssub">spent output ratio</div></div>')
    A(f'<div class="stat-item"><div class="slbl">NUPL</div><div class="sval">{"%.3f"%oc["nupl"] if oc["nupl"] else "---"}</div><div class="ssub">net unrealized P/L</div></div>')
    A(f'<div class="stat-item"><div class="slbl">RHODL</div><div class="sval">{"%.2f"%oc["rhodl"] if oc["rhodl"] else "---"}</div><div class="ssub">HODL waves ratio</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Hash Rate</div><div class="sval">{eh(oc["hr"]) if oc["hr"] else "---"}</div><div class="ssub">potencia red</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Difficulty</div><div class="sval">{"%.0fT"%oc["diff"] if oc["diff"] else "---"}</div><div class="ssub">dificultad mining</div></div>')
    A(f'<div class="stat-item"><div class="slbl">UTXO Set</div><div class="sval">{"%.2fM"%oc["utxos"] if oc["utxos"] else "---"}</div><div class="ssub">unspent outputs</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Active Addrs</div><div class="sval">{"%d"%oc["addrs"] if oc["addrs"] else "---"}</div><div class="ssub">24h promedio</div></div>')
    A(f'<div class="stat-item"><div class="slbl">SMA 200d</div><div class="sval">{"$%.0f"%oc["sma200"] if oc["sma200"] else "---"}</div><div class="ssub">Yahoo Finance, SMA 200 cierres</div></div>')
    A('</div></div>')

    # Expert Analysis — REAL DATA NARRATIVE
    A('<div class="expert-box">')
    A('<h3>Analisis del ciclo y estructura de mercado</h3>')
    A('<span class="source">JHODLers — ' + TODAY_STR + ' | Datos: bitview.local + Yahoo Finance</span>')
    A(f'<p>{btc_narr["rsi_sig"]} {btc_narr["macd_sig"]} '
      f'BTC opera en {usd0(btc_price)} ({pct(btc_chg)} frente al cierre diario previo) con un MVRV de '
      f'{"%.2fx" % oc["mvrv"] if oc["mvrv"] else "---"} ({btc_narr["mvrv_int"]})</p>')
    A(f'<div class="highlight"><strong>Estructura del ciclo:</strong> {btc_narr["cycle_txt"]}</div>')
    A(f'<p><strong>Realized Cap vs Market Cap:</strong> el capital realizado de ${oc["rcap"]:.2f}T representa una valoración agregada de monedas según su último movimiento en cadena; no es el costo promedio por BTC. '
      f'El ratio actual de {"%.2fx" % (oc["mcap"]/oc["rcap"]) if oc["mcap"] and oc["rcap"] else "---"} indica que el mercado esta en '
      f'{"fase de acumulacion" if oc["mvrv"] and oc["mvrv"] < 1.5 else "fase intermedia del ciclo" if oc["mvrv"] and oc["mvrv"] < 2.5 else "fase de distribucion"}. '
      f'{btc_narr["asopr_int"]}</p>')
    A(f'<p><strong>NUPL:</strong> {btc_narr["nupl_int"]} '
      f'{btc_narr["hr_int"]} '
      f'Active addresses en {"%d" % oc["addrs"] if oc["addrs"] else "---"} (promedio 24h).</p>')
    A(f'<p><strong>Media Moviles:</strong> {btc_narr["ma_txt"]}</p>')
    A('</div>')

    # Tech Summary Table
    A('<div class="card"><h3>Resumen Tecnico — Senales de Indicadores</h3>')
    A('<table class="signal-table">')
    A('<thead><tr><th>Indicador</th><th>Valor</th><th>Senal</th><th></th></tr></thead><tbody>')
    A(f'<tr><td>RSI(14)</td><td class="val">{"%.1f"%btc_rsi if btc_rsi else "---"}</td><td class="{"buy" if btc_rsi and btc_rsi<40 else ("sell" if btc_rsi and btc_rsi>60 else "neu")}">{rsi_sig}</td><td></td></tr>')
    A(f'<tr><td>MACD (12,26,9)</td><td class="val">{"%.2f"%btc_hist if btc_hist else "---"}</td><td class="{"buy" if btc_hist and btc_hist>0 else ("sell" if btc_hist and btc_hist<0 else "neu")}">{macd_sig}</td><td></td></tr>')
    A(f'<tr><td>Estocastico (14)</td><td class="val">{"%.0f"%btc_stoch_k if btc_stoch_k else "---"}</td><td class="{"buy" if btc_stoch_k and btc_stoch_k<20 else ("sell" if btc_stoch_k and btc_stoch_k>80 else "neu")}">{stoch_sig}</td><td></td></tr>')
    A(f'<tr><td>Williams %R</td><td class="val">{"%.0f"%btc_willr if btc_willr else "---"}</td><td class="{"buy" if btc_willr and btc_willr<-80 else ("sell" if btc_willr and btc_willr>-20 else "neu")}">{willr_sig}</td><td></td></tr>')
    A(f'<tr><td>CCI(20)</td><td class="val">{"%.0f"%btc_cci if btc_cci else "---"}</td><td class="{"buy" if btc_cci and btc_cci<-100 else ("sell" if btc_cci and btc_cci>100 else "neu")}">{cci_sig}</td><td></td></tr>')
    A(f'<tr><td>MA Compra (4)</td><td class="val">{buy_s}/4</td><td class="buy">Encima MA</td><td></td></tr>')
    A(f'<tr><td>MA Venta (4)</td><td class="val">{sell_s}/4</td><td class="sell">Debajo MA</td><td></td></tr>')
    A('</tbody></table></div>')

    A('<div class="card"><h3>Medias Moviles</h3><div class="ma-grid">'+btc_ma_items+'</div></div>')
    A('<div class="card"><h3>Osciladores</h3><div class="osc-grid">'+btc_osc_items+'</div></div>')

    # Key Levels
    A('<div class="card"><h3>Niveles Clave — Estructura de Mercado</h3><div class="levels-grid">')
    A('<div class="lev-col"><h4>Soportes</h4>')
    for i, s in enumerate(btc_support_levels[:3]):
        A(f'<div class="lev-item"><span class="lev-type" style="color:var(--up)">Soporte {i+1}</span><span class="lev-price">{"$%.0f"%s}</span></div>')
    A('</div><div class="lev-col"><h4>Resistencias</h4>')
    for i, r in enumerate(btc_resistance_levels[:3]):
        A(f'<div class="lev-item"><span class="lev-type" style="color:var(--dn)">Resistencia {i+1}</span><span class="lev-price">{"$%.0f"%r}</span></div>')
    A('</div></div></div>')

    btc_bull, btc_base, btc_bear = compute_scenarios(
        btc_price, btc_c, btc_h, btc_l, btc_sup, btc_res,
        btc_rsi, btc_atr, btc_hist, "BTC",
        macro_data={"next_event": "CPI 14-oct", "vix": mvix, "dxy": mdxy}
    )
    A(bias_section("Bitcoin (BTC)", btc_chg, btc_bull, btc_base, btc_bear))

    A('<h3 style="font-family:var(--serif);font-size:15px;font-weight:400;margin:20px 0 14px">Noticias — extractos en idioma original; clasificación orientativa</h3>')
    A('<div class="news-wrap">'+''.join(news_span(n) for n in nb)+'</div>')
    A('</section>')

    A('<hr class="sep">')

    # ══════════════════════ BOLSA ═════════════════════════════════════════════
    A('<section>')
    A('<div class="kicker">Renta Variable</div>')
    # ── SPY section: clarify SPX index vs SPY ETF ──────────────────────
    # Note: SPX = S&P 500 index in POINTS; SPY = SPDR ETF in USD/share
    A('<div class="section-title">S&P 500 — SPDR S&P 500 ETF (SPY) vs Indice ^GSPC</div>')
    A('<span style="font-size:11px;color:var(--muted)">'
      'Nota: S&P 500 Index (^GSPC) cotiza en puntos ('
      f'actual {spx_price:,.0f} pts). '
      'SPDR S&P 500 ETF (SPY) cotiza en USD/participacion ('+usd(spy_price)+'). '
      'Los indicadores tecnicos se calculan sobre SPY (USD/share). '
      'El indice SPX y SPY se mueven de forma casi identica, pero SPY distribuye dividendos.</span>')

    A('<div class="price-row">')
    A(f'<span class="big-price">{usd(spy_price)}</span>')
    A(f'<span class="big-chg {bcls(spy_chg)}">{arr(spy_chg)} {pct(spy_chg)} (cierre previo)</span>')
    A('</div>')

    A('<div class="stats-bar">')
    A(f'<div class="stat-item"><div class="slbl">RSI(14)</div><div class="sval {"dn" if spy_rsi and spy_rsi>60 else ("up" if spy_rsi and spy_rsi<40 else "")}">{"%.0f"%spy_rsi if spy_rsi else "---"}</div><div class="ssub">{"Sobrecompra" if spy_rsi and spy_rsi>60 else ("Sobreventa" if spy_rsi and spy_rsi<40 else "Neutral")}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Williams %R</div><div class="sval">{"%.0f"%spy_willr if spy_willr else "---"}</div><div class="ssub">{"Extendida" if spy_willr and spy_willr>-20 else "Normal"}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">ATR(14)</div><div class="sval">{"$%.2f"%spy_atr if spy_atr else "---"}</div><div class="ssub">Rango promedio</div></div>')
    A(f'<div class="stat-item"><div class="slbl">10Y Yield</div><div class="sval dn">{"%.3f%%"%m10y if m10y else "---"}</div><div class="ssub">{m10y_date or ""}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">USD Broad</div><div class="sval neu">{"%.2f"%mdxy if mdxy else "---"}</div><div class="ssub">USD Broad Index</div></div>')
    A(f'<div class="stat-item"><div class="slbl">VIX</div><div class="sval {"up" if mvix and mvix>25 else ""}">{"%.1f"%mvix if mvix else "---"}</div><div class="ssub">{mvix_date or ""}</div></div>')
    A('</div>')

    # Expert — SPY with correct instrument labels
    A('<div class="expert-box">')
    A('<h3>Analisis del mercado de renta variable</h3>')
    A('<span class="source">JHODLers — ' + TODAY_STR + ' | Datos: Yahoo Finance + FRED | Instrumento: SPY (USD/share)</span>')
    A(f'<p>{spy_narr["rsi_sig"]} {spy_narr["yield_txt"]} {spy_narr["vix_txt"]} {spy_narr["dxy_txt"]}</p>')
    A(f'<div class="highlight"><strong>Estructura de medias (SPY):</strong> '
      f'SPY opera '
      f'{"encima" if spy_sma200 and spy_price and spy_price > spy_sma200 else "debaja"} de SMA 200 (${f"{spy_sma200:,.2f}" if spy_sma200 else "--"} si disponible) y '
      f'{"encima" if spy_sma50 and spy_price and spy_price > spy_sma50 else "debaja"} de SMA 50 (${f"{spy_sma50:,.2f}" if spy_sma50 else "--"} si disponible). '
      f'Nota: no tenemos datos de breadth (componentes sobre MA) en la base local — ese analisis no es posible con los datos actuales.</div>')
    A(f'<p><strong>Nota sobre fundamentales:</strong> no tenemos datos de earnings, orden flow, o flujo ETF en la base local. '
      f'El analisis se limita a precio, volumen y correlaciones macro disponibles. '
      f'SPY vs SPX: SPY es un ETF que puede distribuir dividendos; su precio por participación y el nivel del índice son magnitudes distintas.</p>')
    A('</div>')

    A('<div class="chart-box"><h3>SPDR S&P 500 ETF (SPY) — 90 sesiones</h3>'+spy_chart+'</div>')

    A('<div class="card"><h3>Medias Moviles (SPY)</h3><div class="ma-grid">'+spy_ma_items+'</div></div>')
    A('<div class="card"><h3>Osciladores (SPY)</h3><div class="osc-grid">'+spy_osc_items+'</div></div>')

    A('<div class="card"><h3>Niveles Clave (SPY)</h3><div class="levels-grid"><div class="lev-col"><h4>Soportes</h4>')
    for i, s in enumerate(spy_support_levels[:3]):
        A(f'<div class="lev-item"><span class="lev-type" style="color:var(--up)">Soporte {i+1}</span><span class="lev-price">{"$%.2f"%s}</span></div>')
    A('</div><div class="lev-col"><h4>Resistencias</h4>')
    for i, r in enumerate(spy_resistance_levels[:3]):
        A(f'<div class="lev-item"><span class="lev-type" style="color:var(--dn)">Resistencia {i+1}</span><span class="lev-price">{"$%.2f"%r}</span></div>')
    A('</div></div></div>')

    spy_bull, spy_base, spy_bear = compute_scenarios(
        spy_price, spy_c, spy_h, spy_l, spy_sup, spy_res,
        spy_rsi, spy_atr, spy_hist, "SPY",
        macro_data={"next_event": "CPI 14-oct", "vix": mvix, "dxy": mdxy}
    )
    A(bias_section("SPY (S&P 500 ETF)", spy_chg, spy_bull, spy_base, spy_bear))

    A('<h3 style="font-family:var(--serif);font-size:15px;font-weight:400;margin:20px 0 14px">Noticias — extractos en idioma original; clasificación orientativa</h3>')
    A('<div class="news-wrap">'+''.join(news_span(n) for n in ns)+'</div>')
    A('</section>')

    A('<hr class="sep">')

    # ══════════════════════ METALES ═══════════════════════════════════════════
    A('<section>')
    A('<div class="kicker">Commodities</div>')
    A('<div class="section-title">Oro Futuro (GC=F) — Analisis de Precio</div>')

    A('<div class="price-row">')
    A(f'<span class="big-price" style="color:var(--gold)">{usd0(gold_price)}</span>')
    A(f'<span class="big-chg {bcls(gold_chg)}">{arr(gold_chg)} {pct(gold_chg)} (cierre previo)</span>')
    A('</div>')

    A('<div class="stats-bar">')
    A(f'<div class="stat-item"><div class="slbl">RSI(14)</div><div class="sval {"up" if gold_rsi and gold_rsi<40 else ("dn" if gold_rsi and gold_rsi>60 else "")}">{"%.0f"%gold_rsi if gold_rsi else "---"}</div><div class="ssub">{gold_rsi_sig}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">MACD</div><div class="sval {"up" if gold_hist and gold_hist>0 else ("dn" if gold_hist and gold_hist<0 else "")}">{"%.2f"%gold_hist if gold_hist else "---"}</div><div class="ssub">{gold_macd_sig}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Estocastico</div><div class="sval">{"%.0f"%gold_stoch_k if gold_stoch_k else "---"}</div><div class="ssub">{gold_stoch_sig}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Williams %R</div><div class="sval">{"%.0f"%gold_willr if gold_willr else "---"}</div><div class="ssub">{gold_willr_sig}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">ATR(14)</div><div class="sval">{"$%.0f"%gold_atr if gold_atr else "---"}</div><div class="ssub">volatilidad</div></div>')
    A(f'<div class="stat-item"><div class="slbl">Plata futuro (SI=F)</div><div class="sval" style="color:var(--gold)">{"$%.2f"%silver_price if silver_price else "---"}</div><div class="ssub">{pct(silver_chg)}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">WTI Crude (CL=F)</div><div class="sval" style="color:var(--gold)">{"$%.2f"%oil_price if oil_price else "---"}</div><div class="ssub">{pct(oil_chg)}</div></div>')
    A(f'<div class="stat-item"><div class="slbl">10Y Yield</div><div class="sval dn">{"%.3f%%"%m10y if m10y else "---"}</div><div class="ssub">headwind oro</div></div>')
    A(f'<div class="stat-item"><div class="slbl">USD Broad</div><div class="sval neu">{"%.2f"%mdxy if mdxy else "---"}</div><div class="ssub">inverso oro</div></div>')
    A(f'<div class="stat-item"><div class="slbl">VIX</div><div class="sval">{"%.1f"%mvix if mvix else "---"}</div><div class="ssub">fear index</div></div>')
    A('</div>')

    # Expert — Gold REAL DATA
    A('<div class="expert-box">')
    A('<h3>Analisis del oro y commodities</h3>')
    A('<span class="source">JHODLers — ' + TODAY_STR + ' | Datos: Yahoo Finance + FRED</span>')
    A(f'<p>{gold_narr["rsi_sig"]} {gold_narr["macd_sig"]} '
      f'{gold_narr["yield_txt"]} {gold_narr["dxy_txt"]}</p>')
    A(f'<div class="highlight"><strong>Estructura de medias:</strong> '
      f'Oro opera '
      f'{"encima" if gold_sma200 and gold_price and gold_price > gold_sma200 else "debajo"} de SMA 200 ({"$%.0f"%gold_sma200 if gold_sma200 else "---"}), '
      f'{"encima" if gold_sma50 and gold_price and gold_price > gold_sma50 else "debajo"} de SMA 50 ({"$%.0f"%gold_sma50 if gold_sma50 else "---"}). '
      f'Resistencias: SMA 100 en {"$%.0f"%gold_sma100 if gold_sma100 else "---"}. '
      f'Los niveles técnicos calculados se presentan en su tabla. '
      f'Nota: no tenemos datos de inventario, demanda fisica ni orden flow para oro — el analisis es puramente tecnico y macro.</div>')
    A(f'<p><strong>Plata:</strong> {usd(silver_price)} ({pct(silver_chg)}) — sigue al oro con mayor volatilidad. '
      f'<strong>WTI:</strong> {usd(oil_price)} ({pct(oil_chg)}) — variación del futuro WTI sin interpretación causal no verificada.</p>')
    A('</div>')

    A('<div class="chart-box"><h3>Oro Futuro (GC=F) — 90 sesiones</h3>'+gold_chart+'</div>')

    A('<div class="card"><h3>Resumen Tecnico</h3><table class="signal-table">')
    A('<thead><tr><th>Indicador</th><th>Valor</th><th>Senal</th></tr></thead><tbody>')
    A(f'<tr><td>RSI(14)</td><td class="val">{"%.0f"%gold_rsi if gold_rsi else "---"}</td><td class="{"buy" if gold_rsi and gold_rsi<40 else ("sell" if gold_rsi and gold_rsi>60 else "neu")}">{gold_rsi_sig}</td></tr>')
    A(f'<tr><td>MACD</td><td class="val">{"%.2f"%gold_hist if gold_hist else "---"}</td><td class="{"buy" if gold_hist and gold_hist>0 else ("sell" if gold_hist and gold_hist<0 else "neu")}">{gold_macd_sig}</td></tr>')
    A(f'<tr><td>Estocastico</td><td class="val">{"%.0f"%gold_stoch_k if gold_stoch_k else "---"}</td><td class="{"buy" if gold_stoch_k and gold_stoch_k<20 else ("sell" if gold_stoch_k and gold_stoch_k>80 else "neu")}">{gold_stoch_sig}</td></tr>')
    A(f'<tr><td>Williams %R</td><td class="val">{"%.0f"%gold_willr if gold_willr else "---"}</td><td class="{"buy" if gold_willr and gold_willr<-80 else ("sell" if gold_willr and gold_willr>-20 else "neu")}">{gold_willr_sig}</td></tr>')
    A(f'<tr><td>CCI(20)</td><td class="val">{"%.0f"%gold_cci if gold_cci else "---"}</td><td class="{"buy" if gold_cci and gold_cci<-100 else ("sell" if gold_cci and gold_cci>100 else "neu")}">{gold_cci_sig}</td></tr>')
    A(f'<tr><td>ATR(14)</td><td class="val">{"$%.0f"%gold_atr if gold_atr else "---"}</td><td class="neu">Volatilidad</td></tr>')
    A('</tbody></table></div>')

    A('<div class="card"><h3>Medias Moviles</h3><div class="ma-grid">'+gold_ma_items+'</div></div>')
    A('<div class="card"><h3>Osciladores</h3><div class="osc-grid">'+gold_osc_items+'</div></div>')

    A('<div class="card"><h3>Niveles Clave</h3><div class="levels-grid"><div class="lev-col"><h4>Soportes</h4>')
    for i, s in enumerate(gold_support_levels[:3]):
        A(f'<div class="lev-item"><span class="lev-type" style="color:var(--up)">Soporte {i+1}</span><span class="lev-price">{"$%.0f"%s}</span></div>')
    A('</div><div class="lev-col"><h4>Resistencias</h4>')
    for i, r in enumerate(gold_resistance_levels[:3]):
        A(f'<div class="lev-item"><span class="lev-type" style="color:var(--dn)">Resistencia {i+1}</span><span class="lev-price">{"$%.0f"%r}</span></div>')
    A('</div></div></div>')

    gold_bull, gold_base, gold_bear = compute_scenarios(
        gold_cur, gold_c, gold_h, gold_l, gold_sup, gold_res,
        gold_rsi, gold_atr, gold_hist, "GOLD",
        macro_data={"next_event": "CPI 14-oct", "vix": mvix, "dxy": mdxy}
    )
    A(bias_section("Oro Futuro (GC=F)", gold_chg, gold_bull, gold_base, gold_bear))

    A('<h3 style="font-family:var(--serif);font-size:15px;font-weight:400;margin:20px 0 14px">Noticias — extractos en idioma original; clasificación orientativa</h3>')
    A('<div class="news-wrap">'+''.join(news_span(n) for n in nm)+'</div>')
    A('</section>')

    # Cierre numérico auditable: sin forecast ad hoc ni eventos sin verificar.
    A('<div class="signal-band">')
    A('<div class="signal-lbl">Resumen de datos observados — '+TODAY_STR+'</div>')
    A(f'<p><strong>Bitcoin:</strong> {usd0(btc_price)} '
      f'({pct(btc_chg)} frente al cierre anterior); '
      f'RSI(14) {btc_rsi:.1f} y MVRV {oc["mvrv"]:.2f}x. '
      'Los escenarios son condicionales, no probabilidades calibradas. '
      'No se dispone aquí de flujos ETF verificados ni de un modelo '
      'calibrado que permita cuantificar retornos futuros.</p>')
    A(f'<p><strong>Datos macro disponibles:</strong> {macro_narr}</p>')
    A('</div>')

    A('<div class="footer">Mercados Daily Pro — Fuentes: Yahoo Finance (precios) | bitview.space DB (on-chain BTC) | FRED (macro) | Exa Search (noticias)<br>'
      'Datos verificados: RSI Wilder smoothing. MVRV/SOPR/NUPL desde bitview. Macro desde FRED API. | No es consejo financiero.</div>')

    A('</div></body></html>')
    html = ''.join(parts)

    with open(OUT_PATH, "w") as f:
        f.write(html)
    print("OK -> " + OUT_PATH)
    print(f"BTC: {usd0(btc_price)} {pct(btc_chg)} | RSI: {btc_rsi:.0f}" if btc_rsi else "BTC: ---")
    print(f"SPY: {usd(spy_price)} {pct(spy_chg)} | RSI: {spy_rsi:.0f}" if spy_rsi else "SPY: ---")
    print(f"Gold: {usd0(gold_price)} {pct(gold_chg)} | RSI: {gold_rsi:.0f}" if gold_rsi else "Gold: ---")
    print(f"On-chain BTC: MVRV={oc['mvrv']} | aSOPR={oc['asopr']} | NUPL={oc['nupl']} | HR={eh(oc['hr'])}")
    print(f"Macro: 10Y={m10y:.3f}% | USD Broad={mdxy:.2f} | VIX={mvix:.1f} | CPI={mcpi:.2f}%" if mcpi else "Macro: ---")
    print(f"News: BTC {len(nb)} | Stocks {len(ns)} | Metals {len(nm)}")

if __name__ == "__main__":
    main()
