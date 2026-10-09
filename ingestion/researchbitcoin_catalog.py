"""Verified ResearchBitcoin V2 scalar metric catalog for an OPTIONAL BTC report complement.

Provider references: https://researchbitcoin.net/metrics/ and
https://api.researchbitcoin.net/faq/. Do not confuse RBN methodology
with bitview, nor insert these values into the existing bitview 'daily' table.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    slug: str
    endpoint: str
    title: str
    unit: str
    group: str
    priority: int = 2  # 1 = headline complementary metric; 2 = supporting metric
    # Representation of API raw numeric values, separate from display unit.
    # Only two observed Supply-in-Profit percentages currently use fractions.
    raw_scale: str = "native"

    @property
    def docs(self):
        return f"https://researchbitcoin.net/metrics/{self.slug}/"


# Each slug/endpoint verified in the ResearchBitcoin public metrics catalog.
# These are all scalar, d1, Tier-0 metrics as documented October 2026.
CATALOG = {
    m.slug: m for m in (
        Metric("realized_price_sth", "/v2/realizedprice", "Costo base STH", "USD", "cost_basis", 1),
        Metric("realized_price_lth", "/v2/realizedprice", "Costo base LTH", "USD", "cost_basis", 1),
        Metric("true_market_meanprice", "/v2/cointime_statistics", "True Market Mean", "USD", "cost_basis", 1),
        Metric("mvrv_sth", "/v2/market_value_to_realized_value", "MVRV STH", "ratio", "valuation", 1),
        Metric("mvrv_z_sth", "/v2/market_value_to_realized_value", "MVRV Z STH", "z_score", "valuation", 1),
        Metric("sopr_sth", "/v2/spent_output_profit_ratio", "SOPR STH", "ratio", "spending", 1),
        Metric("sopr_lth", "/v2/spent_output_profit_ratio", "SOPR LTH", "ratio", "spending"),
        Metric("realized_loss_sth", "/v2/realizedloss", "Pérdida realizada STH", "USD", "spending", 1),
        Metric("realized_profit_sth", "/v2/realizedprofit", "Ganancia realizada STH", "USD", "spending"),
        Metric("net_realized_profit_loss_sth", "/v2/net_realized_profit_loss", "P/L realizado neto STH", "USD", "spending"),
        Metric("supply_in_profit_sth_percent", "/v2/supply_in_profitloss", "Oferta STH en ganancias", "percent", "supply", 1, "fraction_0_1"),
        Metric("supply_in_profit_percent", "/v2/supply_in_profitloss", "Oferta total en ganancias", "percent", "supply", 1, "fraction_0_1"),
        Metric("liveliness", "/v2/cointime_statistics", "Liveliness", "ratio", "activity"),
    )
}

PROVIDER = "researchbitcoin"
BASE_URL = "https://api.researchbitcoin.net"
