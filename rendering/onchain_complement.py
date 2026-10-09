"""Optional BTC research on-chain addendum from separately archived RBN metrics.

Read-only; no outbound API calls or DB writes from report generation.
Never replace bitview's canonical MVRV/SOPR with RBN's distinct methodology.
"""
import datetime as dt
import html
import math
import sqlite3
from pathlib import Path

from ingestion.researchbitcoin_catalog import CATALOG, PROVIDER


def read_complement(db_path, cutoff, *, max_age_days=4):
    """Only verified, finite, sufficiently recent fully completed UTC observations."""
    if not Path(db_path).is_file():
        return []
    if isinstance(cutoff, str):
        cutoff = dt.date.fromisoformat(cutoff)
    result = []
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        try:
            rows = db.execute("""
                SELECT metric, observed_date, observed_at_utc, value, unit, source_endpoint
                FROM onchain_external_observations
                WHERE provider=? AND observed_date<=?
                ORDER BY observed_date DESC
            """, (PROVIDER, cutoff.isoformat())).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
    seen = set()
    for slug, observed_day, observed_at, value, unit, endpoint in rows:
        metric = CATALOG.get(slug)
        if not metric or slug in seen or metric.unit != unit:
            continue
        try:
            day = dt.date.fromisoformat(observed_day)
            stamp = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            age = (cutoff - day).days
        except (ValueError, TypeError):
            continue
        if (stamp.tzinfo is None or stamp.astimezone(dt.timezone.utc).date() != day
                or not 0 <= age <= max_age_days
                or not isinstance(value, (int, float)) or not math.isfinite(value)
                or endpoint != metric.endpoint + "/" + slug
                or (metric.raw_scale == "fraction_0_1" and not 0 <= value <= 1)):
            continue
        seen.add(slug)
        result.append((metric, day, value))
    return sorted(result, key=lambda r: (r[0].priority, r[0].group, r[0].slug))


def format_metric(metric, value):
    if metric.unit == "USD":
        return f"US$ {value:,.0f}"
    if metric.unit == "percent":
        if metric.raw_scale == "fraction_0_1":
            # Display-only normalization; SQLite retains exact original 0..1.
            return f"≈{100 * value:.1f}%*"
        return f"{value:.1f}%"
    if metric.unit == "z_score":
        return f"{value:.2f} z"
    return f"{value:.3f}"


def render_complement(db_path, cutoff):
    """Drop-in HTML card, but return nothing until local RBN data is validated and stored."""
    items = read_complement(db_path, cutoff)
    if not items:
        return ""
    blocks = []
    for metric, day, value in items:
        fraction = metric.raw_scale == "fraction_0_1"
        raw_attr = f' data-api-raw="{value:.10g}"' if fraction else ""
        caveat = ('<div class="ssub">*Normalización provisional: '
                  'dato bruto × 100 (API 0–1); confirmar escala y '
                  'denominador con el proveedor.</div>') if fraction else ""
        blocks.append(
            '<div class="stat-item" data-provider="researchbitcoin" '
            f'data-metric="{html.escape(metric.slug, quote=True)}" '
            f'data-asof-utc="{day.isoformat()}"{raw_attr}>'
            f'<div class="slbl">{html.escape(metric.title)}</div>'
            f'<div class="sval">{html.escape(format_metric(metric, value))}</div>'
            f'<div class="ssub">{day.isoformat()} UTC · '
            f'<a href="{html.escape(metric.docs, quote=True)}" '
            'rel="noopener noreferrer">Ficha metodológica</a></div>'
            f'{caveat}</div>'
        )
    return (
        '<div class="card" id="onchain-complement">'
        '<h3>Complemento on-chain — Bitcoin Lab / ResearchBitcoin</h3>'
        '<p style="font-size:11px;color:#52645a;margin:0 0 14px">'
        'Series adicionales con observación UTC confirmada. '
        'Proveedor y metodología independientes de bitview; los indicadores '
        'homónimos NO se sustituyen ni se promedian. '
        'Las observaciones pueden tener retraso; no son valores en tiempo real. '
        '<a href="https://researchbitcoin.net" rel="noopener noreferrer">'
        'Fuente: researchbitcoin.net</a>.</p>'
        '<div style="display:grid;grid-template-columns:'
        'repeat(auto-fit,minmax(165px,1fr));gap:10px">'
        + "".join(blocks) + '</div></div>'
    )
