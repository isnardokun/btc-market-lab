#!/usr/bin/env python3
"""
tests/unit/test_indicators.py
============================
Unit tests para quant_engine/indicators.py
"""
import sys, os
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _BASE)
from quant_engine.indicators import (
    compute_rsi, compute_ma, ema_python,
    compute_macd, stoch, williams_r, cci, atr, find_supp_res
)

def test_compute_rsi_wilder():
    """RSI de Wilder: misma implementación que TradingView."""
    # Precios estables (sin cambios) → RSI = 100 (no hay pérdidas)
    closes = [100.0] * 20
    rsi = compute_rsi(closes, 14)
    assert rsi == 100.0, f"RSI estable debe ser 100, got {rsi}"

    # Subida sostenida → RSI > 50
    up_closes = [100 + i for i in range(30)]
    rsi_up = compute_rsi(up_closes, 14)
    assert rsi_up > 50, f"RSI en subida debe ser >50, got {rsi_up}"

    # Bajada sostenida → RSI < 50
    down_closes = [100 - i for i in range(30)]
    rsi_down = compute_rsi(down_closes, 14)
    assert rsi_down < 50, f"RSI en bajada debe ser <50, got {rsi_down}"

    # Datos insuficientes → None
    assert compute_rsi([100, 101, 102], 14) is None

    print("  ✅ compute_rsi_wilder OK")


def test_compute_ma():
    """SMA simple."""
    closes = list(range(1, 11))  # 1..10
    assert compute_ma(closes, 5) == 8.0   # avg of [6,7,8,9,10] = 40/5 = 8
    assert compute_ma(closes, 10) == 5.5  # avg of 1..10 = 55/10 = 5.5
    assert compute_ma(closes, 20) is None  # insufficient data
    print("  ✅ compute_ma OK")


def test_compute_macd():
    """MACD(12,26,9) retorna (macd_line, signal_line, histogram)."""
    closes = [100 + i + (i % 3) * 0.5 for i in range(50)]
    macd_line, signal, hist = compute_macd(closes)
    assert macd_line is not None
    assert signal is not None
    assert hist is not None
    assert abs(macd_line - signal) < 10  # shouldn't be wildly divergent
    print("  ✅ compute_macd OK")


def test_stoch():
    """Estocástico lento (14,3,3)."""
    highs = [105, 106, 107, 108, 109]
    lows = [95, 96, 97, 98, 99]
    closes = [100, 101, 102, 103, 104]
    k, d = stoch(highs, lows, closes, 3)
    assert 0 <= k <= 100, f"Estocastico K debe estar 0-100, got {k}"
    assert 0 <= d <= 100, f"Estocastico D debe estar 0-100, got {d}"
    print("  ✅ stoch OK")


def test_williams_r():
    """Williams %R."""
    highs = [105]*5
    lows = [95]*5
    closes = [100]*5
    wr = williams_r(highs, lows, closes, 5)
    assert wr is not None
    assert -100 <= wr <= 0, f"Williams %R debe estar entre -100 y 0, got {wr}"
    print("  ✅ williams_r OK")


def test_atr():
    """Average True Range."""
    # Necesitamos al menos `period` filas de datos
    import random
    random.seed(42)
    highs = [105 + random.gauss(0, 2) for _ in range(20)]
    lows = [95 + random.gauss(0, 2) for _ in range(20)]
    closes = [(h + l) / 2 + random.gauss(0, 1) for h, l in zip(highs, lows)]
    atr_val = atr(highs, lows, closes, 14)
    assert atr_val is not None, f"ATR no deve ser None con 20 datos"
    assert atr_val > 0, f"ATR debe ser positivo, got {atr_val}"
    print(f"  ✅ atr OK — ATR={atr_val:.2f}")


def test_find_supp_res():
    """Soportes y resistencias locales."""
    # Precio en sube y baja con claro máximo y mínimo local
    closes = [100, 102, 105, 103, 101, 98, 96, 99, 102, 105]
    highs = [101, 103, 106, 104, 102, 99, 97, 100, 103, 106]
    lows = [99, 101, 104, 102, 100, 97, 95, 98, 101, 104]
    supp, res = find_supp_res(closes, highs, lows, lookback=2)
    assert isinstance(supp, list)
    assert isinstance(res, list)
    print(f"  ✅ find_supp_res OK — supp={len(supp)}, res={len(res)}")


def test_compute_ma_returns_float():
    """compute_ma retorna float, NO lista — bug crítico."""
    closes = list(range(1, 21))
    result = compute_ma(closes, 10)
    assert isinstance(result, float), f"compute_ma debe retornar float, got {type(result)}"
    assert not isinstance(result, list), "compute_ma NO debe retornar lista"
    print("  ✅ compute_ma returns float (not list) OK")


def test_rsi_range():
    """RSI siempre entre 0 y 100."""
    import random
    random.seed(42)
    for _ in range(5):
        closes = [100 + random.gauss(0, 5) for _ in range(40)]
        rsi = compute_rsi(closes, 14)
        if rsi is not None:
            assert 0 <= rsi <= 100, f"RSI debe estar 0-100, got {rsi}"
    print("  ✅ RSI range 0-100 OK")


if __name__ == "__main__":
    print("Running unit tests for indicators...")
    test_compute_rsi_wilder()
    test_compute_ma()
    test_compute_macd()
    test_stoch()
    test_williams_r()
    test_atr()
    test_find_supp_res()
    test_compute_ma_returns_float()
    test_rsi_range()
    print("\n✅ All indicators unit tests passed")
