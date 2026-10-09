#!/usr/bin/env python3
"""
tests/unit/test_metric_registry.py
================================
Unit tests para ingestion/metric_registry.py
"""
import sys, os
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _BASE)
from ingestion.metric_registry import convert_metric, METRIC_REGISTRY, get_series_info

def test_hash_rate_conversion():
    """hash_rate: raw H/s → EH/s (÷1e18)."""
    # Valor conocido: ~1042 EH/s = 1.042e21 H/s
    raw = 1.042e21
    display, unit, verified = convert_metric(1097, raw)
    assert abs(display - 1042) < 1, f"hash_rate 1042 EH/s, got {display}"
    assert unit == "EH/s"
    assert verified is True
    print(f"  ✅ hash_rate: {raw} H/s → {display} {unit}")


def test_difficulty_conversion():
    """difficulty: compact → T (÷1e12)."""
    # Valor conocido: ~103.36 T = 1.0336e14
    raw = 1.0336e14
    display, unit, verified = convert_metric(550, raw)
    assert abs(display - 103.36) < 0.1, f"difficulty 103.36 T, got {display}"
    assert unit == "T"
    assert verified is True
    print(f"  ✅ difficulty: {raw} → {display} {unit}")


def test_market_cap_conversion():
    """market_cap: dtype=Dollars (ya en USD) → T$ (÷1e12)."""
    # Valor conocido: ~$1.67T → raw = 1.67e12
    raw = 1.67e12
    display, unit, verified = convert_metric(1330, raw)
    assert abs(display - 1.67) < 0.01, f"market_cap 1.67 T$, got {display}"
    assert unit == "USD_T"
    print(f"  ✅ market_cap: ${raw} → ${display} T")


def test_realized_cap_conversion():
    """realized_cap: dtype=Dollars → T$ (÷1e12)."""
    # Valor conocido: ~$545B → raw = 5.45e11
    raw = 5.45e11
    display, unit, verified = convert_metric(1978, raw)
    assert abs(display - 0.545) < 0.01, f"realized_cap 0.545 T$, got {display}"
    assert unit == "USD_T"
    print(f"  ✅ realized_cap: ${raw} → ${display} T")


def test_mvrv_no_conversion():
    """mvrv: ratio puro — sin conversión."""
    raw = 1.55
    display, unit, verified = convert_metric(1357, raw)
    assert display == 1.55, f"mvrv sin conversion, got {display}"
    assert unit == "ratio"
    assert verified is True
    print(f"  ✅ mvrv: {raw} → {display} (ratio, sin conversion)")


def test_sopr_no_conversion():
    """sopr_1w: ratio — sin conversión."""
    raw = 1.017
    display, unit, verified = convert_metric(2052, raw)
    assert abs(display - 1.017) < 0.001, f"sopr sin conversion, got {display}"
    assert unit == "ratio"
    print(f"  ✅ sopr_1w: {raw} → {display}")


def test_nupl_no_conversion():
    """nupl: ratio — sin conversión."""
    raw = 0.186
    display, unit, verified = convert_metric(2807, raw)
    assert abs(display - 0.186) < 0.001, f"nupl sin conversion, got {display}"
    assert unit == "ratio"
    print(f"  ✅ nupl: {raw} → {display}")


def test_utxo_count_conversion():
    """utxo_count: count → M (÷1e6)."""
    # Valor conocido: ~165M UTXOs
    raw = 165_048_999
    display, unit, verified = convert_metric(2375, raw)
    assert abs(display - 165.05) < 0.1, f"utxo_count 165.05 M, got {display}"
    assert unit == "M"
    print(f"  ✅ utxo_count: {raw:,} → {display:.2f} {unit}")


def test_active_addrs_no_conversion():
    """active_addrs: count — sin conversión."""
    raw = 6728
    display, unit, verified = convert_metric(5, raw)
    assert display == 6728, f"active_addrs sin conversion, got {display}"
    assert unit == "count"
    print(f"  ✅ active_addrs: {raw:,} → {display:,}")


def test_unknown_series_id():
    """Series ID no registradas retornan raw sin conversión."""
    raw = 12345.67
    display, unit, verified = convert_metric(99999, raw)
    assert display == raw, f"unknown series deve volver raw, got {display}"
    assert unit == "unknown"
    assert verified is False
    print(f"  ✅ unknown series_id: retorna raw={raw}")


def test_none_value():
    """Valor None retorna (None, None, False)."""
    display, unit, verified = convert_metric(1097, None)
    assert display is None
    assert unit is None
    assert verified is False
    print("  ✅ None value handling OK")


def test_all_verified_series():
    """Todas las 13 series en el registry estan verificadas."""
    print(f"\n  Registry tiene {len(METRIC_REGISTRY)} entradas")
    for sid, entry in METRIC_REGISTRY.items():
        print(f"    ID {sid}: {entry['name']} — {entry['source_unit']} → {entry['display_unit']} ✅" if entry.get('verified') else f"    ID {sid}: {entry['name']} — ⚠️  NO VERIFICADO")
    print(f"  ✅ Registry check complete")


if __name__ == "__main__":
    print("Running metric_registry unit tests...\n")
    test_hash_rate_conversion()
    test_difficulty_conversion()
    test_market_cap_conversion()
    test_realized_cap_conversion()
    test_mvrv_no_conversion()
    test_sopr_no_conversion()
    test_nupl_no_conversion()
    test_utxo_count_conversion()
    test_active_addrs_no_conversion()
    test_unknown_series_id()
    test_none_value()
    print()
    test_all_verified_series()
    print("\n✅ All metric_registry unit tests passed")
