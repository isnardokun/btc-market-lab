"""Research Studio v2: source-grounded, deterministic editorial synthesis."""
import datetime as dt
import html
import math

def valid(n):
    return isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n)

def money(n, precision=0):
    return "US$" + format(n, f",.{precision}f") if valid(n) else "—"

def change(n):
    return f"{n:+.2f}%" if valid(n) else "—"

def rsi_zone(value):
    if not valid(value):
        return "Sin dato"
    if value < 30:
        return "Sobreventa (RSI < 30)"
    if value > 70:
        return "Sobrecompra (RSI > 70)"
    return "Neutral (RSI 30–70)"

def render_executive_brief(*, btc, btc_change, btc_rsi, btc_macd_hist,
                           btc_sma20, btc_asof, spy, spy_change, gold,
                           gold_change, yield10, yield10_date, rbn_available):
    """Observed numbers / conditional interpretation / explicit data gaps."""
    btc_fact = (f"BTC {money(btc)} ({change(btc_change)}) al cierre "
                f"{btc_asof} UTC; RSI(14) "
                + (f"{btc_rsi:.1f} ({rsi_zone(btc_rsi)})" if valid(btc_rsi)
                   else "no disponible") + ".")
    facts = (btc_fact + f" SPY {money(spy, 2)} ({change(spy_change)}); "
             f"oro futuro GC=F {money(gold)} ({change(gold_change)}).")
    if valid(btc) and valid(btc_sma20):
        position = "por debajo" if btc < btc_sma20 else "por encima"
        reading = f"BTC {position} de SMA 20 ({money(btc_sma20)}); "
    else:
        reading = "SMA 20 no comparable; "
    reading += ("MACD histograma negativo." if valid(btc_macd_hist) and btc_macd_hist < 0
                else "MACD histograma positivo." if valid(btc_macd_hist) and btc_macd_hist > 0
                else "MACD sin señal direccional confirmada.")
    if valid(yield10):
        reading += (f" Treasury 10Y {yield10:.3f}% ({yield10_date}); "
                    "esta coexistencia no prueba causalidad.")
    risks = ("Faltan flujos ETF, demanda spot, funding y open interest verificados "
             "para un diagnóstico integrado. No inferir pronósticos ni causalidad. "
             + ("ResearchBitcoin local disponible con metodología independiente de Bitview."
                if rbn_available else "Sin complemento ResearchBitcoin local válido."))
    cards = "".join(
        '<article class="brief-panel"><h3>' + html.escape(label) + '</h3>'
        '<p>' + html.escape(body) + '</p></article>'
        for label, body in (("Hechos observados", facts),
                            ("Lectura condicional", reading),
                            ("Riesgos y datos pendientes", risks)))
    return ('<section id="executive-brief" class="research-brief" '
            'data-studio-version="v2"><div class="kicker">Research desk · Síntesis ejecutiva</div>'
            '<h2 class="section-title">Lo que cambió y por qué importa</h2>'
            '<p class="brief-caption">Cortes observados y escenarios condicionales. '
            'No son probabilidades calibradas ni recomendaciones.</p>'
            '<div class="brief-grid">' + cards + '</div></section>')

def rbn_diagnostic(items, *, btc_price, cutoff):
    """Only compare same-UTC-day values; preserve all provider observations."""
    if isinstance(cutoff, str):
        cutoff = dt.date.fromisoformat(cutoff)
    by_slug = {metric.slug: value for metric, day, value in items
               if day == cutoff and valid(value)}
    if not by_slug:
        return ""
    notes = []
    if valid(btc_price) and btc_price > 0:
        for slug, title in (("realized_price_sth", "Costo base STH"),
                            ("true_market_meanprice", "True Market Mean"),
                            ("realized_price_lth", "Costo base LTH")):
            basis = by_slug.get(slug)
            if valid(basis) and basis > 0:
                pct = (btc_price / basis - 1) * 100
                notes.append(f"{title}: {money(basis)}; precio BTC {pct:+.1f}% "
                             "respecto a esa referencia (misma fecha UTC).")
    sopr = by_slug.get("sopr_sth")
    if valid(sopr):
        notes.append(f"SOPR STH {sopr:.3f}: outputs gastados "
                     + ("bajo el costo" if sopr < 1 else
                        "sobre el costo" if sopr > 1 else "al costo")
                     + " según el proveedor; no pronostica flujos.")
    profit = by_slug.get("realized_profit_sth")
    loss = by_slug.get("realized_loss_sth")
    net = by_slug.get("net_realized_profit_loss_sth")
    if all(valid(x) for x in (profit, loss, net)):
        diff = (profit - loss) - net
        if abs(diff) > max(1.0, abs(net) * 0.001):
            notes.append(f"Conciliación pendiente: profit - loss = "
                         f"{money(profit - loss)}; neto API {money(net)}; "
                         f"diferencia absoluta {money(abs(diff))}. "
                         "Se desconoce si las tres series comparten definición, "
                         "filtros y denominador: no reescribir los datos.")
    if not notes:
        return ""
    lis = "".join("<li>" + html.escape(s) + "</li>" for s in notes)
    return ('<div class="expert-box rbn-diagnostic" data-rbn-cutoff="'
            + cutoff.isoformat() + '"><h3>Lectura cruzada ResearchBitcoin</h3>'
            '<p class="brief-caption">Solo observaciones del mismo día UTC; '
            'cálculos descriptivos, no señales de trading.</p><ul>'
            + lis + '</ul></div>')
