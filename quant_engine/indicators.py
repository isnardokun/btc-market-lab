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


def compute_scenarios(price, closes, highs, lows, supports, resistances,
                     rsi, atr_val, macd_hist, asset, macro_data=None):
    """
    Genera escenarios condicionales para BTC, SPY y GOLD.

    Cada escenario incluye:
      - Nivel de activación (precio que activa este escenario)
      - Objetivo condicional (si se activa, hacia dónde)
      - Catalizadores (qué eventos podrían provocar la activación)
      - Invalidez (cuándo este escenario deja de ser válido)
      - Horizonte: no derivado de ATR, sino basado en catalizadores concretos

    NO incluye:
      - Confianza "alta/media" (sin backtesting no es calibrable)
      - Horizontes estimados desde distancias ATR (no predictivo)

    Returns (bull_txt, base_txt, bear_txt).
    """
    md = macro_data or {}

    # ── Niveles de referencia ────────────────────────────────────────────
    s1 = supports[-1] if supports else None
    s2 = supports[-2] if len(supports) >= 2 else None
    r1 = resistances[-1] if resistances else None
    r2 = resistances[-2] if len(resistances) >= 2 else None

    # ── Clasificación RSI ────────────────────────────────────────────────
    rsi_zone = "sobreventa" if (rsi and rsi < 40) else \
               "sobrecompra" if (rsi and rsi > 65) else "neutral"
    macd_dir = "positivo" if (macd_hist is not None and macd_hist > 0) else "negativo"

    # ── Helper: objetivo en % ───────────────────────────────────────────
    def pct(target):
        if not target:
            return "?"
        return f"{((target / price) - 1) * 100:+.1f}%"

    if asset == "BTC":
        r1 = r1 or round(price * 1.05, 0)
        r2 = r2 or round(price * 1.10, 0)
        s1 = s1 or round(price * 0.93, 0)
        s2 = s2 or round(price * 0.85, 0)

        bull_txt = (
            f"Escenario alcista.\n"
            f"  Activación: BTC supera ${r1:,.0f} con volumen y cierre diario encima. "
            f"Indicadores: RSI {rsi_zone}, MACD {macd_dir}.\n"
            f"  Objetivo: ${r2:,.0f} ({pct(r2)} desde hoy) si momentum se confirma.\n"
            f"  Catalizadores: flujo ETF neto positivo sostenido, hash ribbon bullish crossover, "
            f"pivot dovish de la Fed, o datos macro fríos.\n"
            f"  Invalidez: cierre diario debajo de ${s1:,.0f} invalida este escenario. "
            f"Resolución esperada: depende de catalizador — días a varias semanas."
        )
        base_txt = (
            f"Escenario base.\n"
            f"  Precio en rango ${s1:,.0f}-${r1:,.0f}. "
            f"RSI {rsi_zone} ({rsi:.0f}), MACD {macd_dir}.\n"
            f"  Resistente a {r1:,.0f} ({pct(r1)}) — soporte en {s1:,.0f} ({pct(s1)}).\n"
            f"  Catalizador necesario para romper rango: señal macro clara, flujo ETF, "
            f"o dato de inflación/decisión Fed.\n"
            f"  Invalidez: ruptura confirmada debajo de ${s2:,.0f} cambia a escenario bajista."
        )
        bear_txt = (
            f"Escenario bajista.\n"
            f"  Activación: BTC pierde ${s1:,.0f} con volumen y cierra debajo. "
            f"RSI {rsi_zone}, MACD {macd_dir}.\n"
            f"  Objetivo: ${s2:,.0f} ({pct(s2)}) si soporte no recupera.\n"
            f"  Catalizadores: outflows ETF sostenidos, regulación hostil, riesgo geopolítico, "
            f"fortaleza inesperada del DXY, o corrección de hashrate.\n"
            f"  Invalidez: cierre diario encima de ${r1:,.0f} invalida este escenario y favorece alza."
        )

    elif asset in ("SPY", "SPX"):
        r1 = r1 or round(price * 1.02, 2)
        r2 = r2 or round(price * 1.05, 2)
        s1 = s1 or round(price * 0.97, 2)
        s2 = s2 or round(price * 0.93, 2)

        bull_txt = (
            f"Escenario alcista.\n"
            f"  Activación: SPY supera ${r1:,.2f} con breadth mejorando (>60% stocks sobre SMA50). "
            f"RSI {rsi_zone}, MACD {macd_dir}.\n"
            f"  Objetivo: ${r2:,.2f} ({pct(r2)}) si rompe resistencia y sostiene arriba.\n"
            f"  Catalizadores: NFP fuerte, ventas minoristas calientes, Fed dovish, "
            f"earnings encima de expectativas.\n"
            f"  Invalidez: cierre debajo de ${s1:,.2f} invalida. Horizonte: días a semanas."
        )
        base_txt = (
            f"Escenario base.\n"
            f"  SPY en rango ${s1:,.2f}-${r1:,.2f}. "
            f"RSI {rsi_zone} ({rsi:.0f}), MACD {macd_dir}.\n"
            f"  VIX {md.get('vix', 'N/A')} — {'eleva riesgo' if md.get('vix', 0) > 25 else 'condiciones normales'}.\n"
            f"  Catalizador para dirección: decisión Fed, dato inflación, o sorpresa earnings.\n"
            f"  Invalidez: ruptura debajo de ${s2:,.2f} cambia a escenario bajista."
        )
        bear_txt = (
            f"Escenario bajista.\n"
            f"  Activación: SPY pierde ${s1:,.2f} con volumen. "
            f"RSI {rsi_zone}, MACD {macd_dir}.\n"
            f"  Objetivo: ${s2:,.2f} ({pct(s2)}) si soporte no recupera.\n"
            f"  Catalizadores: sorpresa negativa en empleo, inflación caliente, inversión curva yield, "
            f"earnings decepcionantes, o riesgo geopolítico.\n"
            f"  Invalidez: recuperación encima de ${r1:,.2f} neutraliza. Horizonte: días a semanas."
        )

    else:  # GOLD
        r1 = r1 or round(price * 1.03, 0)
        r2 = r2 or round(price * 1.07, 0)
        s1 = s1 or round(price * 0.96, 0)
        s2 = s2 or round(price * 0.90, 0)

        bull_txt = (
            f"Escenario alcista.\n"
            f"  Activación: oro supera ${r1:,.0f} con demanda de refugio. "
            f"RSI {rsi_zone}, MACD {macd_dir}.\n"
            f"  Objetivo: ${r2:,.0f} ({pct(r2)}) si rompe y sostiene.\n"
            f"  Catalizadores: debilidad DXY, bancos centrales comprando, riesgo geopolítico, "
            f"inflación superior a expectativas.\n"
            f"  Invalidez: pérdida de ${s1:,.0f} invalida. Horizonte: semanas a meses."
        )
        base_txt = (
            f"Escenario base.\n"
            f"  Oro en rango ${s1:,.0f}-${r1:,.0f}. "
            f"RSI {rsi_zone} ({rsi:.0f}), MACD {macd_dir}.\n"
            f"  DXY {md.get('dxy', 'N/A')}, VIX {md.get('vix', 'N/A')}.\n"
            f"  Resistente a ${r1:,.0f} — soporte ${s1:,.0f}.\n"
            f"  Invalidez: ruptura debajo de ${s2:,.0f} activa escenario bajista."
        )
        bear_txt = (
            f"Escenario bajista.\n"
            f"  Activación: oro pierde ${s1:,.0f} con fortaleza del DXY. "
            f"RSI {rsi_zone}, MACD {macd_dir}.\n"
            f"  Objetivo: ${s2:,.0f} ({pct(s2)}) si soporte cede.\n"
            f"  Catalizadores: fortaleza DXY sostenida, profits en commodities, "
            f"mejora riesgo-on, o demanda física débil.\n"
            f"  Invalidez: recuperación encima de ${r1:,.0f} neutraliza."
        )

    return bull_txt, base_txt, bear_txt
