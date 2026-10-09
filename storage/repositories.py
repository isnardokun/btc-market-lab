"""
storage/repositories.py
=======================
Capa de acceso a datos — SEPARADA de lógica de presentación.

Responsabilidades:
- Queries SQL a price_btc, daily, macro_fred, daily_metrics
- Conversión de unidades (del metric_registry)
- Frescura de datos y timestamps
- NO hace cálculos de indicadores (eso es quant_engine)
- NO genera HTML (eso es analysis/rendering)

Uso: analysis/daily_report.py importa desde aquí.
"""
import sqlite3, datetime, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingestion.metric_registry import METRIC_REGISTRY

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db", "btc_research.db"
)

TODAY = datetime.date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
ts_52w = int((
    datetime.datetime.combine(TODAY, datetime.time(0, 0)).replace(tzinfo=datetime.timezone.utc)
    - datetime.timedelta(days=365)
).timestamp())


def _get_db():
    return sqlite3.connect(DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# BTC PRICE (Yahoo Finance → price_btc)
# ─────────────────────────────────────────────────────────────────────────────

def get_btc_ohlc(days=252):
    """OHLCV diario de BTC-USD desde Yahoo Finance (price_btc)."""
    db = _get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT ts, open, high, low, close, volume FROM price_btc "
        "ORDER BY ts DESC LIMIT ?",
        (days,)
    )
    rows = cur.fetchall()
    db.close()
    return list(reversed(rows))


def get_btc_latest_price():
    """Precio de cierre mas reciente del BTC."""
    db = _get_db()
    cur = db.cursor()
    cur.execute("SELECT ts, close FROM price_btc ORDER BY ts DESC LIMIT 1")
    r = cur.fetchone()
    db.close()
    return (r[0], r[1]) if r else (None, None)


def get_btc_ath():
    """ATH y fecha del ATH desde price_btc."""
    db = _get_db()
    cur = db.cursor()
    cur.execute("SELECT ts, price FROM price_btc ORDER BY price DESC LIMIT 1")
    r = cur.fetchone()
    db.close()
    if r:
        return r[0], r[1]  # ts, price
    return None, None


def get_btc_52w_high_low():
    """52-week high y low."""
    db = _get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT MAX(price), MIN(price) FROM price_btc WHERE ts >= ?",
        (ts_52w,)
    )
    r = cur.fetchone()
    db.close()
    return r[0], r[1]  # high52, low52


def get_sma(closes, period):
    """SMA simple de una lista de cierres."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def get_closes(days=252):
    """Lista de precios de cierre BTC (para cálculos de indicadores)."""
    ohlc = get_btc_ohlc(days)
    return [row[4] for row in ohlc if row[4] is not None]


# ─────────────────────────────────────────────────────────────────────────────
# BITVIEW ON-CHAIN (tabla daily)
# ─────────────────────────────────────────────────────────────────────────────

def get_btc_onchain_latest():
    """
    Fetch latest on-chain data para BTC.
    Aplica conversiones verificadas desde metric_registry.
    """
    db = _get_db()
    cur = db.cursor()

    def get_by_id(series_id):
        cur.execute(
            "SELECT ts, value FROM daily WHERE series_id=? ORDER BY ts DESC LIMIT 1",
            (series_id,)
        )
        r = cur.fetchone()
        return (r[0], r[1]) if r else (None, None)

    # Fetch all with correct series IDs
    data = {}
    for sid in METRIC_REGISTRY:
        name = METRIC_REGISTRY[sid]["name"]
        ts, raw = get_by_id(sid)
        if raw is not None:
            factor = METRIC_REGISTRY[sid].get("factor", 1.0)
            data[name] = round(raw * factor, 4)
            data[f"{name}_ts"] = ts
        else:
            data[name] = None
            data[f"{name}_ts"] = None

    db.close()
    return data


# ─────────────────────────────────────────────────────────────────────────────
# MACRO FRED (tabla macro_fred)
# ─────────────────────────────────────────────────────────────────────────────

def get_macro_latest(series_ids: list[str]) -> dict:
    """
    Fetch latest values para una lista de series_id FRED.
    Retorna {series_id: (date, value)}
    """
    db = _get_db()
    cur = db.cursor()
    result = {}
    for sid in series_ids:
        cur.execute(
            "SELECT date, value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1",
            (sid,)
        )
        r = cur.fetchone()
        result[sid] = (r[0], r[1]) if r else (None, None)
    db.close()
    return result


def get_macro_range(series_id: str, days: int = 90) -> list:
    """Últimos N días de una serie FRED."""
    db = _get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT date, value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT ?",
        (series_id, days)
    )
    rows = cur.fetchall()
    db.close()
    return list(reversed(rows))


def get_macro_for_date(date_str: str) -> dict:
    """Todos los valores macro para una fecha específica."""
    db = _get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT series_id, value FROM macro_fred WHERE date=?",
        (date_str,)
    )
    rows = cur.fetchall()
    db.close()
    return {r[0]: r[1] for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# DAILY METRICS (tabla daily_metrics — archivado histórico)
# ─────────────────────────────────────────────────────────────────────────────

def get_archived_metric(asset: str, metric: str, days: int = 30) -> list:
    """Métricas archivadas para un activo."""
    db = _get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT report_date, value FROM daily_metrics "
        "WHERE asset=? AND metric=? "
        "ORDER BY report_date DESC LIMIT ?",
        (asset, metric, days)
    )
    rows = cur.fetchall()
    db.close()
    return list(reversed(rows))


def get_latest_metric(asset: str, metric: str):
    """Valor más reciente archivado de una métrica."""
    db = _get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT report_date, value FROM daily_metrics "
        "WHERE asset=? AND metric=? "
        "ORDER BY report_date DESC LIMIT 1",
        (asset, metric)
    )
    r = cur.fetchone()
    db.close()
    return (r[0], r[1]) if r else (None, None)


# ─────────────────────────────────────────────────────────────────────────────
# FRESHNESS —是什么时候 datos fueron actualizados?
# ─────────────────────────────────────────────────────────────────────────────

def get_data_freshness() -> dict:
    """Retorna timestamp de última actualización por fuente."""
    db = _get_db()
    cur = db.cursor()
    freshness = {}

    # BTC price
    cur.execute("SELECT MAX(ts) FROM price_btc")
    r = cur.fetchone()
    freshness["price_btc"] = r[0] if r else None

    # On-chain (bitview)
    cur.execute("SELECT MAX(ts) FROM daily")
    r = cur.fetchone()
    freshness["daily"] = r[0] if r else None

    # Macro FRED
    cur.execute("SELECT MAX(date) FROM macro_fred")
    r = cur.fetchone()
    freshness["macro_fred"] = r[0] if r else None

    db.close()
    return freshness


def ts_to_date(ts: int) -> str:
    """Convierte Unix timestamp a string YYYY-MM-DD."""
    if ts is None:
        return "unknown"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
