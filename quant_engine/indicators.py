"""
quant_engine/indicators.py
Funciones de cálculo cuantitativo puro — sin side effects, sin HTML.
Cada función es determinista: misma entrada → misma salida.

Contratos:
  compute_rsi(closes, period=14)     → float | None
  compute_ma(closes, period)        → float | None (SMA)
  ema_python(data, n)               → float | None
  compute_macd(closes, fast=12, slow=26, signal=9) → (macd, signal, hist) | (None, None, None)
  stoch(highs, lows, closes, period=14) → (k, d) | (None, None)
  williams_r(highs, lows, closes, period=14) → float | None
  atr(highs, lows, closes, period=14) → float | None
  find_supp_res(closes, highs, lows, lookback=20) → ([supports], [resistances])
"""
from __future__ import annotations


def compute_rsi(closes, period=14):
    """RSI de Wilder (smoothed). Requiere al menos `period` valores."""
    vals = [c for c in closes[-period-1:] if c is not None]
    if len(vals) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(vals)):
        delta = vals[i] - vals[i-1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    if not gains:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Wilder smoothing for subsequent values
    for i in range(len(gains), len(closes)):
        delta = closes[i] - closes[i-1] if closes[i-1] is not None else 0
        g = max(delta, 0)
        l = max(-delta, 0)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_ma(closes, period):
    """SMA simple. Requiere exactamente `period` valores."""
    vals = [c for c in closes[-period:] if c is not None]
    return sum(vals) / len(vals) if len(vals) == period else None


def ema_python(data, n):
    """EMA con k = 2/(n+1). Requiere al menos n valores."""
    if len(data) < n:
        return None
    k = 2 / (n + 1)
    ema = sum(data[:n]) / n
    for price in data[n:]:
        if price is None:
            continue
        ema = price * k + ema * (1 - k)
    return ema


def compute_macd(closes, fast=12, slow=26, signal=9):
    """MACD (12,26,9) estándar. Retorna (macd_line, signal_line, histogram)."""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = ema_python(closes, fast)
    ema_slow = ema_python(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_vals = []
    for i in range(slow, len(closes)):
        ef = ema_python(closes[:i+1], fast)
        es = ema_python(closes[:i+1], slow)
        if ef and es:
            macd_vals.append(ef - es)
    if len(macd_vals) < signal:
        return None, None, None
    # Signal = EMA of MACD
    sig = sum(macd_vals[:signal]) / signal
    for v in macd_vals[signal:]:
        sig = v * (2/(signal+1)) + sig * (1 - 2/(signal+1))
    hist = macd_vals[-1] - sig if macd_vals else None
    return macd_vals[-1], sig, hist


def stoch(highs, lows, closes, period=14):
    """Estocástico lento (14,3,3). Retorna (%K, %D)."""
    n = len(closes)
    if n < period:
        return None, None
    fast_k = []
    for i in range(period-1, n):
        window_h = highs[i-period+1:i+1]
        window_l = lows[i-period+1:i+1]
        c = closes[i]
        if None in window_h + window_l + [c]:
            continue
        h = max(window_h)
        l = min(window_l)
        if h == l:
            continue
        fast_k.append(100 * (c - l) / (h - l))
    if len(fast_k) < 3:
        return None, None
    # %K = avg of fast_k last 3, %D = SMA of %K
    k = sum(fast_k[-3:]) / 3
    d = sum(fast_k[-3:]) / 3  # simplified: %D = %K for slow stoch
    return k, d


def williams_r(highs, lows, closes, period=14):
    """Williams %R. Requiere al menos `period` valores."""
    n = len(closes)
    if n < period:
        return None
    window_h = highs[n-period:]
    window_l = lows[n-period:]
    c = closes[-1]
    if None in window_h + window_l + [c]:
        return None
    h = max(window_h)
    l = min(window_l)
    if h == l:
        return None
    return -100 * (h - c) / (h - l)


def cci(highs, lows, closes, period=20):
    """Commodity Channel Index. Requiere al menos `period` valores."""
    n = len(closes)
    if n < period:
        return None
    tp = [(h + l + c) / 3 for h, l, c in zip(highs[n-period:], lows[n-period:], closes[n-period:])]
    if None in tp:
        return None
    sma = sum(tp) / len(tp)
    mad = sum(abs(tp_i - sma) for tp_i in tp) / len(tp)
    if mad == 0:
        return None
    cci_val = (tp[-1] - sma) / (0.015 * mad)
    return cci_val


def atr(highs, lows, closes, period=14):
    """Average True Range. Requiere al menos `period` valores."""
    n = len(closes)
    if n < period + 1:
        return None
    trs = []
    for i in range(1, n):
        h, l, c_prev = highs[i], lows[i], closes[i-1]
        if None in (h, l, c_prev):
            continue
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def find_supp_res(closes, highs, lows, lookback=20):
    """
    Encuentra soportes y resistencias usando min/max locales en lookback.
    Retorna ([soportes_ordenados], [resistencias_ordenadas]).
    """
    if len(closes) < lookback:
        return [], []
    n = len(closes)
    supports = []
    resistances = []
    for i in range(lookback, n - lookback):
        c = closes[i]
        h = highs[i]
        l = lows[i]
        if c is None or h is None or l is None:
            continue
        is_support = True
        is_resistance = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i or j < 0 or j >= n:
                continue
            if closes[j] is None:
                continue
            if closes[j] < c - 0.001:
                is_support = False
            if closes[j] > c + 0.001:
                is_resistance = False
        if is_support:
            supports.append(c)
        if is_resistance:
            resistances.append(c)
    supports = sorted(set(supports))
    resistances = sorted(set(resistances))
    return supports, resistances


def price_relative_levels(price, supports, resistances):
    """Return price-relative historical pivots, without invented target levels."""
    import math
    if price is None or not math.isfinite(price) or price <= 0:
        raise ValueError("Precio actual no válido para clasificar niveles")
    def finite_values(values):
        return {float(v) for v in (values or [])
                if isinstance(v, (float, int)) and math.isfinite(v) and v > 0}
    below = sorted((v for v in finite_values(supports) if v < price), reverse=True)
    above = sorted(v for v in finite_values(resistances) if v > price)
    return below, above


def compute_scenarios(price, closes, highs, lows, supports, resistances,
                      rsi, atr_val, macd_hist, asset, macro_data=None):
    """Directional scenarios with valid triggers and no invented objectives."""
    below, above = price_relative_levels(price, supports, resistances)
    s1, s2 = (below + [None, None])[:2]
    r1, r2 = (above + [None, None])[:2]
    rsi_zone = ("sobreventa" if rsi is not None and rsi < 30 else
                "sobrecompra" if rsi is not None and rsi > 70 else "neutral")
    macd_dir = ("positivo" if macd_hist is not None and macd_hist > 0 else
                "negativo" if macd_hist is not None else "no disponible")
    precision = 2 if asset in ("SPY", "SPX") else 0
    fmt = lambda n: f"$" + f"{n:,.{precision}f}"
    pct = lambda target: f"{(target / price - 1) * 100:+.1f}%"
    market = {"BTC": "BTC", "SPY": "SPY", "SPX": "SPY", "GOLD": "oro"}.get(asset, asset)
    if r1 is not None:
        bull = (f"Escenario alcista. Activación: {market} supera {fmt(r1)} con cierre "
                f"confirmado y volumen. RSI {rsi_zone}; MACD {macd_dir}. ")
        if r2 is not None:
            bull += f"Objetivo condicional: {fmt(r2)} ({pct(r2)} respecto al precio actual). "
        else:
            bull += "Objetivo no estimable: no hay otra resistencia superior validada. "
        bull += ("Catalizadores a vigilar: demanda spot, flujos y datos macro. "
                 "No se asume que ya se hayan producido. ")
        if s1 is not None:
            bull += f"Invalidez: cierre sostenido bajo {fmt(s1)}."
    else:
        bull = (f"Escenario alcista para {market} sin nivel de activación "
                "cuantificable: no hay resistencia histórica validada por encima "
                "del precio actual. No se publica objetivo inventado.")
    if s1 is not None and r1 is not None:
        base = (f"Escenario base. {market} cotiza entre soporte {fmt(s1)} y "
                f"resistencia {fmt(r1)} (spot {fmt(price)}). "
                f"RSI {rsi_zone}; MACD {macd_dir}. "
                "Una ruptura confirmada fuera del rango invalida el escenario.")
    else:
        base = (f"Escenario base de {market} sin rango completo verificable "
                f"(spot {fmt(price)}). No se presenta rango ajeno al precio.")
    if s1 is not None:
        bear = (f"Escenario bajista. Activación: {market} pierde {fmt(s1)} "
                f"con cierre confirmado y volumen. RSI {rsi_zone}; MACD {macd_dir}. ")
        if s2 is not None:
            bear += f"Objetivo condicional: {fmt(s2)} ({pct(s2)} respecto al precio actual). "
        else:
            bear += "Objetivo no estimable: no hay otro soporte inferior validado. "
        bear += "Catalizadores a vigilar: flujos, riesgo macro y demanda. "
        if r1 is not None:
            bear += f"Invalidez: recuperación sostenida sobre {fmt(r1)}."
    else:
        bear = (f"Escenario bajista para {market} sin soporte de activación "
                "cuantificable bajo el precio actual. No se publica objetivo inventado.")
    return bull, base, bear
