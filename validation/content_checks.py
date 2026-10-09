#!/usr/bin/env python3
"""Non-compensable financial/editorial checks for the produced HTML (stdlib).

Pure function: can be tested against archived bad reports without SQLite.
"""
import datetime as dt
import html as htmllib
import math
import re

SPAN_PRICE = re.compile(r'<div class="price-row">\s*<span class="big-price">([^<]+)</span>', re.I)
SCENARIO = re.compile(r'<div class="scenario s-(bull|base|bear)">([\s\S]*?)</div>', re.I)
MONTHS = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dic": 12, "dec": 12,
}


def number(raw):
    try:
        value = float(raw.replace(",", "").replace("$", "").strip())
        return value if math.isfinite(value) and value > 0 else None
    except (AttributeError, ValueError):
        return None


def plain(markup):
    return htmllib.unescape(re.sub(r"<[^>]*>", " ", markup)).replace("\xa0", " ")


def parse_day(raw):
    parts = re.search(r"(\d{1,2})\s+([a-zA-Z]{3,})\s+(20\d{2})", raw or "")
    if not parts:
        return None
    month = MONTHS.get(parts.group(2).lower()[:3])
    if month is None:
        return None
    try:
        return dt.date(int(parts.group(3)), month, int(parts.group(1)))
    except ValueError:
        return None


def audit_report_html(source, *, report_day=None):
    """Return specific hard-block issues; NEVER automatically repair bad claims."""
    errors = []
    day = report_day or dt.date.today()

    if re.search(r"\{(?:usd|usd0|pct|[a-zA-Z_]+)\s*\([^{}]*\)\}", source):
        errors.append("Expresión de plantilla sin interpolar en HTML")
    if re.search(r"\d+(?:\.\d+)?%%|\d+(?:\.\d+)?xx\b", source):
        errors.append("Unidades monetarias/porcentuales duplicadas (%% o xx)")

    ticker = re.search(
        r'<div class="tk-pair">BTC/USD</div>\s*<div class="tk-price">([^<]+)</div>', source
    )
    if ticker is None or number(ticker.group(1)) is None:
        errors.append("Precio principal BTC/USD ausente o inválido")

    sections = re.findall(r"<section>([\s\S]*?)</section>", source)
    inspected = set()
    for section in sections:
        if "Bitcoin (BTC)" in section or "Bitcoin (BTC) —" in section:
            asset = "BTC"
        elif "SPDR S&P 500 ETF" in section and "section-title" in section:
            asset = "SPY"
        elif ("Oro Futuro" in section or "Oro" in section[:300]) and "section-title" in section:
            asset = "GOLD"
        else:
            continue
        inspected.add(asset)
        match = SPAN_PRICE.search(section)
        spot = number(match.group(1)) if match else None
        if spot is None:
            errors.append(f"{asset}: cotización principal ausente o inválida")
            continue

        for kind, inner in SCENARIO.findall(section):
            readable = plain(inner)
            activation = re.search(
                r"Activaci[oó]n:\s*[^$]{0,95}\$([\d,]+(?:\.\d+)?)", readable, re.I
            )
            objective = re.search(
                r"Objetivo(?:\s+condicional)?:\s*\$([\d,]+(?:\.\d+)?)", readable, re.I
            )
            if activation and objective:
                a = number(activation.group(1))
                o = number(objective.group(1))
                if kind == "bull" and (a is None or o is None or not spot < a < o):
                    errors.append(f"{asset}: escenario alcista incoherente (spot < activación < objetivo)")
                if kind == "bear" and (a is None or o is None or not spot > a > o):
                    errors.append(f"{asset}: escenario bajista incoherente (spot > activación > objetivo)")
            elif activation and not objective and "no estimable" not in readable.lower():
                errors.append(f"{asset}: escenario {kind} omite objetivo sin justificarlo")

            if kind == "base":
                old = re.search(
                    r"Precio en rango\s*\$([\d,.]+)\s*[-–]\s*\$([\d,.]+)", readable, re.I
                )
                new = re.search(
                    r"cotiza entre soporte\s*\$([\d,.]+)\s*y\s*resistencia\s*\$([\d,.]+)", readable, re.I
                )
                bounds = old or new
                if bounds:
                    lo, hi = number(bounds.group(1)), number(bounds.group(2))
                    if lo is None or hi is None or not lo < spot < hi:
                        errors.append(f"{asset}: rango base no contiene precio spot")

        # The local-extreme labels must always be relative to the latest close.
        for label, direction in [("Soportes", -1), ("Resistencias", 1)]:
            tag = "<h4>" + label + "</h4>"
            start_pos = section.find(tag)
            if start_pos < 0:
                continue
            end_pos = section.find("<h4>", start_pos + len(tag))
            # The next </div></div></div> ends the levels grid; avoid mixing
            # resistance prices into support prices if there is only one support.
            if end_pos < 0:
                end_pos = section.find('<div class="bias-box">', start_pos)
            if end_pos < 0:
                end_pos = len(section)
            level_markup = section[start_pos:end_pos]
            values = re.findall(r'<span class="lev-price">\$([\d,.]+)</span>', level_markup)
            for raw in values[:3]:
                n = number(raw)
                if n is not None and (n - spot) * direction <= 0:
                    errors.append(f"{asset}: {label.lower()} al lado equivocado del precio")
                    break

        if asset == "BTC":
            for label in ("Max 52s", "Min 52s"):
                hit = re.search(
                    re.escape(label) + r"</div>\s*<div[^>]*>\$[\d,.]+</div>\s*"
                    r"<div class=\"ssub\">([^<]+)</div>", section, re.I
                )
                if hit:
                    when = parse_day(hit.group(1))
                    if when is None or not (day - dt.timedelta(days=365) <= when <= day):
                        errors.append(f"BTC: fecha de {label} fuera de ventana de 52 semanas")

            # Catch the specific cross-value contradiction in the historical report.
            old_claim = re.search(
                r"BTC cayo\s*([-+]?\d+(?:\.\d+)?)%\s*desde el ATH de\s*\$([\d,.]+)"
                r"[\s\S]*?hasta el minimo de 52 semanas en\s*\$([\d,.]+)",
                plain(section), re.I
            )
            if old_claim:
                stated = float(old_claim.group(1))
                high = number(old_claim.group(2))
                low = number(old_claim.group(3))
                if high and low and abs(stated - ((low / high - 1) * 100)) > 0.3:
                    errors.append("BTC: % de caída ATH→mínimo 52 semanas no coincide con los precios")

    for a in ("BTC", "SPY", "GOLD"):
        if a not in inspected:
            errors.append(a + ": sección de precio no encontrada")

    return errors
