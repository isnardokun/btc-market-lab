#!/usr/bin/env python3
"""
archive_metrics.py — Archiva todos los indicadores del dia en daily_metrics.
Se ejecuta DESPUES de daily_report_v2.py en el pipeline daily.sh.
Guarda: BTC (precio + on-chain + tecnicos), SPY, GOLD, OIL, SILVER, MACRO.
"""
import os, sys, datetime, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingestion.daily_cutoff import closed_daily_bars
from analysis.daily_report import (
    yahoo_ohlc, compute_rsi, compute_ma, ema_python,
    compute_macd, stoch, williams_r, cci, atr,
    find_supp_res, get_btc_onchain, get_btc_price_data,
    get_macro_fred
)

DB_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/db/btc_research.db"
TODAY   = datetime.datetime.now(datetime.timezone.utc).date()
TODAY_STR = TODAY.strftime("%Y-%m-%d")

def _save(db, cur, date, asset, metric, value, unit, source):
    cur.execute("""
        INSERT OR REPLACE INTO daily_metrics (report_date, asset, metric, value, unit, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date, asset, metric, value, unit, source))

def _completed_yahoo(symbol, days):
    """Archive only completed UTC days, identical to the HTML report."""
    rows = closed_daily_bars(yahoo_ohlc(symbol, days))
    if len(rows) < 2:
        raise RuntimeError(f"{symbol}: fewer than two completed UTC candles")
    return rows


def archive_all():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    # ── BTC ────────────────────────────────────────────────────────────────
    btc_ohlc = _completed_yahoo("BTC-USD", 252)   # 252 needed for SMA200
    ohlc_f = [r for r in btc_ohlc
               if r["close"] is not None and r["high"] is not None and r["low"] is not None]
    btc_c = [r["close"] for r in ohlc_f]
    btc_h = [r["high"]  for r in ohlc_f]
    btc_l = [r["low"]   for r in ohlc_f]

    btc_cur   = btc_c[-1] if btc_c else None
    btc_prev  = btc_c[-2] if len(btc_c) >= 2 else None
    btc_rsi   = compute_rsi(btc_c)
    _, _, btc_hist = compute_macd(btc_c)
    btc_atr   = atr(btc_h, btc_l, btc_c)
    btc_sup, btc_res = find_supp_res(btc_c, btc_h, btc_l)
    btc_sma20  = compute_ma(btc_c, 20)
    btc_sma50  = compute_ma(btc_c, 50)
    btc_sma100 = compute_ma(btc_c, 100)
    btc_sma200 = compute_ma(btc_c, 200)
    btc_stoch_k, _ = stoch(btc_h, btc_l, btc_c)
    btc_willr  = williams_r(btc_h, btc_l, btc_c)
    btc_cci    = cci(btc_h, btc_l, btc_c)
    btc_ema20  = ema_python(btc_c, 20)
    btc_ema50  = ema_python(btc_c, 50)

    oc      = get_btc_onchain()
    btc_p   = get_btc_price_data()

    chg = (btc_cur - btc_prev) / btc_prev * 100 if btc_cur and btc_prev else None

    # Price + change
    _save(db, cur, TODAY_STR, "BTC", "price",        btc_cur,            "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "close_ts", btc_ohlc[-1]["ts"], "unix_s", "yahoo")
    if chg is not None:
        _save(db, cur, TODAY_STR, "BTC", "chg_24h",     chg,                "%",      "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "rsi_14",        btc_rsi,            "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "macd_hist",      btc_hist,           "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "atr_14",         btc_atr,            "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "stoch_k",        btc_stoch_k,        "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "williams_r",     btc_willr,          "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "cci_20",         btc_cci,            "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "sma_20",         btc_sma20,          "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "sma_50",         btc_sma50,          "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "sma_100",        btc_sma100,         "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "sma_200",        btc_sma200,         "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "ema_20",         btc_ema20,          "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "BTC", "ema_50",         btc_ema50,          "USD",    "yahoo")
    if btc_sup:
        _save(db, cur, TODAY_STR, "BTC", "sup_1",       btc_sup[-1],        "USD",    "computed")
    if len(btc_sup) > 1:
        _save(db, cur, TODAY_STR, "BTC", "sup_2",       btc_sup[-2],        "USD",    "computed")
    if btc_res:
        _save(db, cur, TODAY_STR, "BTC", "res_1",       btc_res[-1],        "USD",    "computed")
    if len(btc_res) > 1:
        _save(db, cur, TODAY_STR, "BTC", "res_2",       btc_res[-2],        "USD",    "computed")
    # On-chain
    _save(db, cur, TODAY_STR, "BTC", "mvrv",           oc.get("mvrv"),      "ratio",  "bitview")
    _save(db, cur, TODAY_STR, "BTC", "asopr_1w",       oc.get("asopr"),     "ratio",  "bitview")
    _save(db, cur, TODAY_STR, "BTC", "sopr_1w",         oc.get("sopr"),      "ratio",  "bitview")
    _save(db, cur, TODAY_STR, "BTC", "nupl",           oc.get("nupl"),      "ratio",  "bitview")
    _save(db, cur, TODAY_STR, "BTC", "rhodl",          oc.get("rhodl"),     "ratio",  "bitview")
    # On-chain — values already in target units from get_btc_onchain()
    _save(db, cur, TODAY_STR, "BTC", "hash_rate",
          oc.get("hr"), "EH/s", "bitview")
    _save(db, cur, TODAY_STR, "BTC", "difficulty",
          oc.get("diff"), "T", "bitview")
    _save(db, cur, TODAY_STR, "BTC", "active_addrs",   oc.get("addrs"),     "count",  "bitview")
    _save(db, cur, TODAY_STR, "BTC", "utxo_count",     oc.get("utxos"),     "M",      "bitview")
    _save(db, cur, TODAY_STR, "BTC", "market_cap",    oc.get("mcap"),      "USD_T",  "bitview")
    _save(db, cur, TODAY_STR, "BTC", "realized_cap",   oc.get("rcap"),      "USD_T",  "bitview")
    _save(db, cur, TODAY_STR, "BTC", "sma_200d_onchain", oc.get("sma200"), "USD",    "bitview")
    _save(db, cur, TODAY_STR, "BTC", "ath",            btc_p["ath"],        "USD",    "sql")
    _save(db, cur, TODAY_STR, "BTC", "ath_date",       None,                 "date",   "sql")
    _save(db, cur, TODAY_STR, "BTC", "high_52w",       btc_p["high52"],     "USD",    "sql")
    _save(db, cur, TODAY_STR, "BTC", "low_52w",        btc_p["low52"],      "USD",    "sql")
    _save(db, cur, TODAY_STR, "BTC", "from_ath",       btc_p["from_ath"],   "%",      "sql")

    # ── SPY ──────────────────────────────────────────────────────────────
    spy_ohlc  = _completed_yahoo("SPY",  252)   # 252 needed for SMA200
    spy_f = [r for r in spy_ohlc if r["close"] is not None]
    spy_c = [r["close"] for r in spy_f]
    spy_h = [r["high"]  for r in spy_f]
    spy_l = [r["low"]   for r in spy_f]
    spy_cur   = spy_c[-1]
    spy_prev  = spy_c[-2] if len(spy_c) >= 2 else None
    spy_rsi   = compute_rsi(spy_c)
    spy_sup, spy_res = find_supp_res(spy_c, spy_h, spy_l)
    spy_sma20  = compute_ma(spy_c, 20)
    spy_sma50  = compute_ma(spy_c, 50)
    spy_sma100 = compute_ma(spy_c, 100)
    spy_sma200 = compute_ma(spy_c, 200)
    spy_stoch_k, _ = stoch(spy_h, spy_l, spy_c)
    spy_willr  = williams_r(spy_h, spy_l, spy_c)
    spy_cci    = cci(spy_h, spy_l, spy_c)
    spy_atr    = atr(spy_h, spy_l, spy_c)
    spy_chg    = (spy_cur - spy_prev) / spy_prev * 100 if spy_cur and spy_prev else None

    _save(db, cur, TODAY_STR, "SPY", "price",        spy_cur,             "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "close_ts", spy_ohlc[-1]["ts"], "unix_s", "yahoo")
    if spy_chg is not None:
        _save(db, cur, TODAY_STR, "SPY", "chg_24h",    spy_chg,             "%",      "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "rsi_14",        spy_rsi,             "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "atr_14",         spy_atr,             "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "stoch_k",        spy_stoch_k,         "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "williams_r",     spy_willr,           "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "cci_20",         spy_cci,             "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "sma_20",         spy_sma20,           "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "sma_50",         spy_sma50,           "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "sma_100",        spy_sma100,          "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "SPY", "sma_200",        spy_sma200,          "USD",    "yahoo")
    if spy_sup:
        _save(db, cur, TODAY_STR, "SPY", "sup_1",      spy_sup[-1],         "USD",    "computed")
    if spy_res:
        _save(db, cur, TODAY_STR, "SPY", "res_1",      spy_res[-1],         "USD",    "computed")

    # ── GOLD ─────────────────────────────────────────────────────────────
    gold_ohlc = _completed_yahoo("GC=F", 252)   # 252 needed for SMA200
    gold_f = [r for r in gold_ohlc if r["close"] is not None]
    gold_c = [r["close"] for r in gold_f]
    gold_h = [r["high"]  for r in gold_f]
    gold_l = [r["low"]   for r in gold_f]
    gold_cur   = gold_c[-1]
    gold_prev  = gold_c[-2] if len(gold_c) >= 2 else None
    gold_rsi   = compute_rsi(gold_c)
    _, _, gold_hist = compute_macd(gold_c)
    gold_sup, gold_res = find_supp_res(gold_c, gold_h, gold_l)
    gold_sma20  = compute_ma(gold_c, 20)
    gold_sma50  = compute_ma(gold_c, 50)
    gold_sma100 = compute_ma(gold_c, 100)
    gold_sma200 = compute_ma(gold_c, 200)
    gold_stoch_k, _ = stoch(gold_h, gold_l, gold_c)
    gold_willr  = williams_r(gold_h, gold_l, gold_c)
    gold_cci    = cci(gold_h, gold_l, gold_c)
    gold_atr    = atr(gold_h, gold_l, gold_c)
    gold_chg    = (gold_cur - gold_prev) / gold_prev * 100 if gold_cur and gold_prev else None

    _save(db, cur, TODAY_STR, "GOLD", "price",        gold_cur,             "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "close_ts", gold_ohlc[-1]["ts"], "unix_s", "yahoo")
    if gold_chg is not None:
        _save(db, cur, TODAY_STR, "GOLD", "chg_24h",   gold_chg,             "%",      "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "rsi_14",        gold_rsi,             "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "macd_hist",     gold_hist,            "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "atr_14",        gold_atr,             "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "stoch_k",       gold_stoch_k,         "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "williams_r",    gold_willr,           "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "cci_20",        gold_cci,             "ratio",  "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "sma_20",        gold_sma20,           "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "sma_50",        gold_sma50,           "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "sma_100",       gold_sma100,          "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "GOLD", "sma_200",       gold_sma200,          "USD",    "yahoo")
    if gold_sup:
        _save(db, cur, TODAY_STR, "GOLD", "sup_1",    gold_sup[-1],         "USD",    "computed")
    if gold_res:
        _save(db, cur, TODAY_STR, "GOLD", "res_1",    gold_res[-1],         "USD",    "computed")

    # ── SILVER ────────────────────────────────────────────────────────────
    slvr_ohlc = _completed_yahoo("SI=F", 30)
    slvr_cur = slvr_ohlc[-1]["close"] if slvr_ohlc else None
    slvr_prev = slvr_ohlc[-2]["close"] if len(slvr_ohlc) >= 2 else None
    slvr_chg = (slvr_cur - slvr_prev) / slvr_prev * 100 if slvr_cur and slvr_prev else None
    _save(db, cur, TODAY_STR, "SILVER", "price",    slvr_cur,             "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "SILVER", "close_ts", slvr_ohlc[-1]["ts"], "unix_s", "yahoo")
    if slvr_chg is not None:
        _save(db, cur, TODAY_STR, "SILVER", "chg_24h", slvr_chg,             "%",      "yahoo")

    # ── OIL ────────────────────────────────────────────────────────────────
    oil_ohlc = _completed_yahoo("CL=F", 30)
    oil_cur = oil_ohlc[-1]["close"] if oil_ohlc else None
    oil_prev = oil_ohlc[-2]["close"] if len(oil_ohlc) >= 2 else None
    oil_chg = (oil_cur - oil_prev) / oil_prev * 100 if oil_cur and oil_prev else None
    _save(db, cur, TODAY_STR, "OIL", "price",         oil_cur,             "USD",    "yahoo")
    _save(db, cur, TODAY_STR, "OIL", "close_ts", oil_ohlc[-1]["ts"], "unix_s", "yahoo")
    if oil_chg is not None:
        _save(db, cur, TODAY_STR, "OIL", "chg_24h",    oil_chg,             "%",      "yahoo")

    # ── MACRO (FRED) ──────────────────────────────────────────────────────
    macro = get_macro_fred()
    fred_map = {
        # Key: (metric_name_in_db, macro_key, value, unit, source)
        # NFP = month-over-month CHANGE (NFP_CHANGE series); NFP level via nfp_level
        "yield_10y":   ("yield_10y",  macro.get("10y",        (None,None))[1], "%",     "fred"),
        "yield_2y":    ("yield_2y",   macro.get("2y",         (None,None))[1], "%",     "fred"),
        "dxy_index":   ("dxy",        macro.get("dxy",         (None,None))[1], "index", "fred"),
        "vix":         ("vix",        macro.get("vix",         (None,None))[1], "index", "fred"),
        "cpi_yoy":     ("cpi_yoy",    macro.get("cpi_yoy",    (None,None))[1], "%",     "fred"),
        "ppi_yoy":     ("ppi_yoy",    macro.get("ppi_yoy",    (None,None))[1], "%",     "fred"),
        "unemployment":("unemployment",macro.get("unemp",      (None,None))[1], "%",     "fred"),
        "nfp_change":   ("nfp",        macro.get("nfp",         (None,None))[1], "K",     "fred"),
        "nfp_level":   ("nfp_level",  macro.get("nfp_level",  (None,None))[1], "K",     "fred"),
        "nfci":        ("nfci",       macro.get("nfci",        (None,None))[1], "index", "fred"),
        "btc_fred":    ("btc_fred",   macro.get("btc_fred",    (None,None))[1], "USD",   "fred"),
        "retail_sales":("retail_sales",macro.get("retail_sales",(None,None))[1],"USD_M","fred"),
    }
    for metric, (key, value, unit, source) in fred_map.items():
        if value is not None:
            _save(db, cur, TODAY_STR, "MACRO", key, value, unit, source)

    db.commit()

    # Print summary
    cur.execute("SELECT COUNT(*) FROM daily_metrics WHERE report_date=?", (TODAY_STR,))
    count = cur.fetchone()[0]
    print(f"[{TODAY_STR}] Archived {count} metrics for {TODAY_STR}")
    print("\nBTC snapshot:")
    for m, u in [("price","USD"),("rsi_14","ratio"),("mvrv","ratio"),
                  ("asopr_1w","ratio"),("nupl","ratio"),("hash_rate","EH/s"),
                  ("market_cap","USD_T"),("realized_cap","USD_T")]:
        cur.execute("SELECT value FROM daily_metrics WHERE report_date=? AND asset='BTC' AND metric=?",
                    (TODAY_STR, m))
        r = cur.fetchone()
        print(f"  {m}: {r[0] if r else '---'} {u}")
    print("\nMACRO snapshot:")
    for m in ["yield_10y","dxy","vix","cpi_yoy","unemployment","nfp"]:
        cur.execute("SELECT value FROM daily_metrics WHERE report_date=? AND asset='MACRO' AND metric=?",
                    (TODAY_STR, m))
        r = cur.fetchone()
        print(f"  {m}: {r[0] if r else '---'}")
    db.close()

if __name__ == "__main__":
    archive_all()
