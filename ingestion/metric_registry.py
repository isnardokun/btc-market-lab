"""
ingestion/metric_registry.py
===========================
Registro explícito de conversiones para series on-chain de bitview.

VERIFICADO contra datos reales (Oct 2026):
  - hash_rate (id=1097): raw=1.042e21 H/s → 1042 EH/s (÷1e18) ✅
  - difficulty (id=550):  raw=1.327e14 → 132.7 T (÷1e12) ✅
  - market_cap (id=1330): raw=1.671T USD → $1.67T (YA en USD, dtype=Dollars)
  - realized_cap (id=1978): raw=1.080T USD → $1.08T (YA en USD)
  - active_cap (id=7): raw=684.6B USD → $684.6B (YA en USD)
  - mvrv (id=1357): 1.546563 (ratio, sin conversión)
  - sopr_1w (id=2052): 1.0066737 (ratio, sin conversión)
  - asopr_1w (id=104): 1.016459 (ratio, sin conversión)
  - nupl (id=2807): 0.353405 (ratio, sin conversión)
  - rhodl_ratio (id=2806): 0.109904 (ratio, sin conversión)
  - utxo_count (id=2375): 165,048,999 → 165.05M (÷1e6) ✅

CONVERSIÓN CRÍTICA: NO usar umbral 1e15 para market_cap/realized_cap/active_cap.
  Las series con dtype=Dollars YA están en USD.
  Solo hash_rate, difficulty y utxo_count requieren conversión explícita.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# SerieID → Factor de conversión (display = raw × factor)
# Clave: series_id (int)
# ─────────────────────────────────────────────────────────────────────────────

METRIC_REGISTRY: dict[int, dict] = {
    # HASH RATE: raw en H/s → EH/s
    1097: {
        "name": "hash_rate",
        "source_unit": "H/s",
        "display_unit": "EH/s",
        "factor": 1e-18,
        "display_name": "Hash Rate",
        "provider": "bitview",
        "method": "Network hashrate (EH/s = H/s ÷ 10¹⁸)",
        "verified": True,
    },
    # DIFFICULTY: raw compact → T (trillones)
    550: {
        "name": "difficulty",
        "source_unit": "difficulty_compact",
        "display_unit": "T",
        "factor": 1e-12,
        "display_name": "Difficulty",
        "provider": "bitview",
        "method": "Mining difficulty (compact format → T = ÷10¹²)",
        "verified": True,
    },
    # MARKET CAP: dtype=Dollars → YA está en USD
    1330: {
        "name": "market_cap",
        "source_unit": "USD",
        "display_unit": "USD_T",
        "factor": 1e-12,  # 1 USD = 1e-12 T$, para mostrar en T$
        "display_name": "Market Cap",
        "provider": "bitview",
        "method": "Market Cap = price × supply in USD (dtype=Dollars)",
        "verified": True,
    },
    # REALIZED CAP: dtype=Dollars → YA está en USD
    1978: {
        "name": "realized_cap",
        "source_unit": "USD",
        "display_unit": "USD_T",
        "factor": 1e-12,
        "display_name": "Realized Cap",
        "provider": "bitview",
        "method": "Realized Cap in USD (dtype=Dollars)",
        "verified": True,
    },
    # ACTIVE CAP: dtype=Dollars → YA está en USD
    7: {
        "name": "active_cap",
        "source_unit": "USD",
        "display_unit": "USD_T",
        "factor": 1e-12,
        "display_name": "Active Cap",
        "provider": "bitview",
        "method": "Active Cap in USD (dtype=Dollars)",
        "verified": True,
    },
    # MVRV: ratio puro — sin conversión
    1357: {
        "name": "mvrv",
        "source_unit": "ratio",
        "display_unit": "ratio",
        "factor": 1.0,
        "display_name": "MVRV Ratio",
        "provider": "bitview",
        "method": "Market Cap / Realized Cap",
        "verified": True,
    },
    # SOPR 1W: ratio — sin conversión
    2052: {
        "name": "sopr_1w",
        "source_unit": "ratio",
        "display_unit": "ratio",
        "factor": 1.0,
        "display_name": "SOPR 1-week",
        "provider": "bitview",
        "method": "Spent Output Profit/Loss Ratio (1-week)",
        "verified": True,
    },
    # aSOPR 1W: ratio — sin conversión
    104: {
        "name": "asopr_1w",
        "source_unit": "ratio",
        "display_unit": "ratio",
        "factor": 1.0,
        "display_name": "aSOPR 1-week",
        "provider": "bitview",
        "method": "Adjusted SOPR (removes 1-output UTXOs)",
        "verified": True,
    },
    # NUPL: ratio — sin conversión
    2807: {
        "name": "nupl",
        "source_unit": "ratio",
        "display_unit": "ratio",
        "factor": 1.0,
        "display_name": "NUPL",
        "provider": "bitview",
        "method": "Net Unrealized Profit/Loss = (Market Cap - Realized Cap) / Market Cap",
        "verified": True,
    },
    # RHODL RATIO: ratio — sin conversión
    2806: {
        "name": "rhodl_ratio",
        "source_unit": "ratio",
        "display_unit": "ratio",
        "factor": 1.0,
        "display_name": "RHODL Ratio",
        "provider": "bitview",
        "method": "Realized HODL Wave",
        "verified": True,
    },
    # UTXO COUNT: raw count → millones
    2375: {
        "name": "utxo_count",
        "source_unit": "count",
        "display_unit": "M",
        "factor": 1e-6,
        "display_name": "UTXO Set Size",
        "provider": "bitview",
        "method": "Total unspent transaction outputs (÷10⁶ = M)",
        "verified": True,
    },
    # ACTIVE ADDRESSES: raw count — sin conversión
    5: {
        "name": "active_addrs",
        "source_unit": "count",
        "display_unit": "count",
        "factor": 1.0,
        "display_name": "Active Addresses (24h)",
        "provider": "bitview",
        "method": "Unique addresses with sent or received tx per day",
        "verified": True,
    },
}


def convert_metric(series_id: int, raw_value: float) -> tuple[float, str, bool]:
    """
    Convierte un valor raw de bitview a su representación de display
    usando el metric_registry.

    Returns (display_value, display_unit, verified)
    """
    if raw_value is None:
        return None, None, False

    entry = METRIC_REGISTRY.get(series_id)
    if entry is None:
        return raw_value, "unknown", False

    factor = entry.get("factor", 1.0)
    return raw_value * factor, entry["display_unit"], entry.get("verified", False)


def get_verified_series() -> list[int]:
    return [sid for sid, e in METRIC_REGISTRY.items() if e.get("verified")]


def get_series_info(series_id: int) -> dict | None:
    return METRIC_REGISTRY.get(series_id)
