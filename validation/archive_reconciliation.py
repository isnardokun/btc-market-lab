"""Non-compensable publication audit of rendered prices vs archived UTC closes.

Compare report_day in daily_metrics, never the latest arbitrary historic entry.
"""
import datetime as dt
import math
import re

TICKERS = (
    ("BTC", "BTC/USD", 0.51),
    ("SPY", "SPDR S&P 500", 0.011),
    ("GOLD", "ORO FUT. GC=F", 0.51),
    ("SILVER", "PLATA FUT. SI=F", 0.011),
    ("OIL", "WTI (CL=F)", 0.011),
)
TICKER_RE = re.compile(
    r'<div class="ticker-item">\s*<div class="tk-pair">([^<]+)</div>'
    r'\s*<div class="tk-price">\s*\$?([\d,]+(?:\.\d+)?)\s*</div>',
    re.I,
)
BTC_RSI_RE = re.compile(
    r'<td>\s*RSI\(14\)\s*</td>\s*<td[^>]*>\s*([-+]?\d+(?:\.\d+)?)\s*</td>',
    re.I,
)
BTC_MACD_RE = re.compile(
    r'<td>\s*MACD\s*\(12,26,9\)\s*</td>\s*<td[^>]*>\s*([-+]?\d+(?:\.\d+)?)\s*</td>',
    re.I,
)


def audit_archive_against_html(html, connection, report_day):
    """Fail closed for missing, inconsistent, or incomplete daily-bar data."""
    errors = []
    rows = connection.execute(
        "SELECT asset, metric, value FROM daily_metrics WHERE report_date=? "
        "AND asset IN ('BTC','SPY','GOLD','SILVER','OIL') "
        "AND metric IN ('price','close_ts','rsi_14','macd_hist')",
        (report_day,),
    ).fetchall()
    archived = {(asset, metric): value for asset, metric, value in rows}
    tickers = {}
    for match in TICKER_RE.finditer(html):
        try:
            tickers[match.group(1).strip()] = float(match.group(2).replace(",", ""))
        except ValueError:
            pass

    def check_number(asset, metric, shown, tolerance):
        stored = archived.get((asset, metric))
        if stored is None or not isinstance(stored, (int, float)) or not math.isfinite(stored):
            errors.append(f"{asset}: daily_metrics.{metric} ausente o inválido para {report_day}")
        elif not math.isfinite(shown) or abs(shown - stored) > tolerance:
            errors.append(
                f"{asset}: HTML {metric}={shown:g} difiere de "
                f"daily_metrics={stored:g} para {report_day}"
            )

    report_date = dt.date.fromisoformat(report_day)
    for asset, label, tolerance in TICKERS:
        if label not in tickers:
            errors.append(f"{asset}: ticker '{label}' ausente; no puede reconciliarse")
        else:
            check_number(asset, "price", tickers[label], tolerance)
        ts = archived.get((asset, "close_ts"))
        if ts is None or not isinstance(ts, (int, float)) or not math.isfinite(ts):
            errors.append(f"{asset}: close_ts de vela completada no archivado")
        else:
            observed_day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
            if observed_day >= report_date:
                errors.append(
                    f"{asset}: close_ts={observed_day} es vela del día de publicación o futura"
                )

    # BTC is the first report section; do not use SPY/GOLD indicator values.
    section = html.partition("<section>")[2].partition("</section>")[0]
    if "Bitcoin (BTC)" not in section:
        errors.append("BTC: sección de indicadores no encontrada")
    else:
        for metric, pattern, tolerance in (
            ("rsi_14", BTC_RSI_RE, 0.055),
            ("macd_hist", BTC_MACD_RE, 0.015),
        ):
            match = pattern.search(section)
            if match is None:
                errors.append(f"BTC: {metric} no aparece en la tabla técnica")
            else:
                check_number("BTC", metric, float(match.group(1)), tolerance)

        asof = re.search(r'data-btc-asof-utc="(\d{4}-\d{2}-\d{2} \d{2}:\d{2})"', section)
        if asof is None:
            errors.append("BTC: sin fecha UTC de la observación en el HTML")
        else:
            html_ts = dt.datetime.strptime(asof.group(1), "%Y-%m-%d %H:%M").replace(
                tzinfo=dt.timezone.utc
            ).timestamp()
            check_number("BTC", "close_ts", html_ts, 0.5)

    return errors
