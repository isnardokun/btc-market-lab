"""Config for BTC Research System — bitview.space API"""
import os

BASE_DIR = os.environ.get("BTC_RESEARCH_HOME", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH  = f"{BASE_DIR}/db/btc_research.db"
SCRATCH  = "/home/ignotus/.hermes/cache/scratch"
REPORTS  = f"{BASE_DIR}/reports"
CHARTS   = f"{BASE_DIR}/charts"
API_BASE = "https://bitview.space/api"

# Aliases for scripts that expect these names
CHARTS_DIR  = CHARTS
REPORTS_DIR = REPORTS

# ─── FRED API ────────────────────────────────────────────────────────────────
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")  # Set in Hermes environment; rotate leaked keys
FRED_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES  = [
    ("DGS10",           "Yield 10Y Treasury",  "daily",   "Treasury yields — driver macro #1. BTC correlation negativa."),
    ("DGS2",            "Yield 2Y Treasury",  "daily",   "Expectativas Fed corto plazo."),
    ("DTWEXBGS",        "Indice DXY Broad",   "daily",   "Dolar — impacto directo en BTC, oro, commodities."),
    ("VIXCLS",          "VIX CBOE",            "daily",   "Fear index — correlacion crypto."),
    ("CPIAUCSL",        "CPI-U All Items (SA), índice", "monthly", "Calcular YoY contra mismo mes del año anterior."),
    ("CPALTT01USM661S", "OECD CPI índice (serie heredada)", "monthly", "No usar como CPI YoY sin transformar."),
    ("PPIACO",           "PPI Commodities",     "monthly", "Inflacion intermedia."),
    ("UNRATE",           "Tasa Desempleo",       "monthly", "Mercado laboral."),
    ("PAYEMS",           "Nonfarm Payrolls",     "monthly", "Volatilidad en dias de publicacion."),
    ("CBBTCUSD",         "Bitcoin (FRED)",       "daily",   "Cross-reference precio BTC."),
    ("NFCI",             "Chicago Fed NFCI",      "daily",   "Stress financiero."),
    ("RSXFS",           "Retail Sales",         "monthly", "Consumo — salud economica."),
]

# ─── Series catalog ────────────────────────────────────────────────────────────
# Format: short_name → (api_name, index, description)
# index: '1d' = date-indexed, 'height' = block-height-indexed
SERIES = {

    # ── Price ──────────────────────────────────────────────────────────────
    "price":          ("price",          "1d", "BTC/USD daily close"),
    "price_ath":      ("price_ath",      "1d", "All-time high close"),
    "price_open":     ("price_open",     "1d", "Daily open"),
    "price_high":     ("price_high",     "1d", "Daily high"),
    "price_low":      ("price_low",      "1d", "Daily low"),

    # ── Moving averages ────────────────────────────────────────────────────
    "price_sma_200d": ("price_sma_200d", "1d", "SMA 200 days"),
    "price_ema_200d": ("price_ema_200d", "1d", "EMA 200 days"),

    # ── MVRV ──────────────────────────────────────────────────────────────
    "mvrv":           ("mvrv",           "1d", "MVRV ratio"),
    "lth_mvrv":       ("lth_mvrv",       "1d", "LTH MVRV"),
    "sth_mvrv":       ("sth_mvrv",       "1d", "STH MVRV"),

    # ── Realized ──────────────────────────────────────────────────────────
    "realized_price": ("realized_price", "1d", "Realized price (cost basis)"),

    # ── SOPR ──────────────────────────────────────────────────────────────
    "sopr_1w":        ("sopr_1w",        "1d", "SOPR weekly resample"),
    "sopr_1m":        ("sopr_1m",        "1d", "SOPR monthly resample"),

    # ── Returns (computed locally, no API) ────────────────────────────────
    # ── RHODL ─────────────────────────────────────────────────────────────
    "rhodl_ratio":    ("rhodl_ratio",    "1d", "RHODL Ratio"),

    # ── NUPL ──────────────────────────────────────────────────────────────
    "nupl":           ("nupl",           "1d", "Net Unrealized Profit/Loss"),
    "nupl_ppm":       ("nupl_ppm",       "1d", "NUPL in parts per million"),

    # ── Reserve Risk ──────────────────────────────────────────────────────
    "reserve_risk":   ("reserve_risk",   "1d", "Reserve Risk"),

    # ── Cap / Market ───────────────────────────────────────────────────────
    "market_cap":     ("market_cap",     "1d", "Market cap (USD)"),

    # ── Miner ─────────────────────────────────────────────────────────────
    "coinbase_sum_24h":("coinbase_sum_24h","1d","Daily coinbase / miner revenue (BTC)"),

    # ── Difficulty ─────────────────────────────────────────────────────────
    "difficulty":     ("difficulty",     "1d", "Network difficulty"),

    # ── UTXO ──────────────────────────────────────────────────────────────
    "utxo_count":     ("utxo_count",     "1d", "UTXO set count"),
    "lth_utxo_count": ("lth_utxo_count","1d", "LTH UTXO count"),
    "sth_utxo_count": ("sth_utxo_count","1d", "STH UTXO count"),
}
