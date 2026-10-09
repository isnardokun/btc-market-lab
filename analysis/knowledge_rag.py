"""Small local, citation-first methodology RAG for the BTC daily report.

Retrieval: SQLite FTS5 over curated, versioned knowledge cards (offline).
Augmentation: numeric observations from the already-validated daily snapshot.
Generation: deterministic, conditional Spanish text with source and caveat.

This is deliberately NOT an LLM making unconstrained market predictions. An
optional local LLM may later consume these evidence cards but must never
replace numeric observations, timestamps or source attribution.
"""
import datetime as dt
import html
import math
import re
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Knowledge:
    metric: str
    title: str
    keywords: str
    method: str
    caveat: str
    url: str


# Original-language summaries independently drafted from public methodology.
CARDS = (
    Knowledge("mvrv", "MVRV y valoración relativa", "mvrv market realized cap valuation btc",
              "Relación entre capitalización de mercado y capitalización realizada.",
              "No constituye una señal de suelo, techo ni probabilidad de retorno.",
              "https://researchbitcoin.net/metrics/mvrv/"),
    Knowledge("asopr", "SOPR y beneficio realizado", "asopr sopr spending profit loss utxo",
              "Compara el valor al gastar monedas con su valor de adquisición. "
              "Los filtros y la frecuencia pueden diferir entre proveedores.",
              "No equivale a flujos hacia exchanges ni a presión vendedora futura.",
              "https://researchbitcoin.net/metrics/sopr/"),
    Knowledge("nupl", "Beneficios y pérdidas no realizados", "nupl unrealized profit loss capital",
              "Una medición agregada del beneficio o pérdida no realizados.",
              "La agregación no demuestra que una cohorte concreta esté en ganancias.",
              "https://docs.glassnode.com/basic-api/endpoints/indicators"),
    Knowledge("active_addrs", "Direcciones activas no son usuarios", "active addresses wallets users activity",
              "Las direcciones de una red son identificadores, no personas únicas.",
              "No comparar series de diferentes definiciones, ventanas o proveedores.",
              "https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/addresses/active-addresses"),
    Knowledge("sth_sopr", "SOPR de tenedores de corto plazo", "sth sopr realized spending cohort losses",
              "SOPR STH restringe el conjunto observado a monedas jóvenes.",
              "Es diferente de aSOPR semanal, y exige verificar umbral de edad y limpieza.",
              "https://researchbitcoin.net/metrics/sopr_sth/"),
    Knowledge("sth_cost", "Costo base de corto plazo", "sth realized price holder cost basis",
              "Realized Price STH sintetiza el valor realizado de monedas de corto plazo.",
              "El nivel no es una zona de soporte garantizada.",
              "https://researchbitcoin.net/metrics/realized_price_sth/"),
)


def retrieve(query, limit=3):
    """Real FTS5 retrieval, backed by a deterministic lexical fallback."""
    tokens = re.findall(r"[a-z0-9_]+", str(query).lower())[:8]
    if not tokens or limit < 1:
        return []
    db = sqlite3.connect(":memory:")
    try:
        try:
            db.execute("CREATE VIRTUAL TABLE knowledge USING fts5(metric, title, keywords, method, caveat)")
            db.executemany("INSERT INTO knowledge VALUES (?,?,?,?,?)", [
                (c.metric, c.title, c.keywords, c.method, c.caveat) for c in CARDS
            ])
            results = db.execute(
                "SELECT metric FROM knowledge WHERE knowledge MATCH ? ORDER BY bm25(knowledge) LIMIT ?",
                (" OR ".join('"' + t + '"' for t in tokens), limit),
            ).fetchall()
            indices = [row[0] for row in results]
        except sqlite3.OperationalError:
            # SQLite without FTS5 still supports well-defined offline retrieval.
            def score(card):
                haystack = (card.metric + " " + card.keywords + " " + card.title).lower()
                return sum(int(t in haystack) for t in tokens)
            indices = [c.metric for c in sorted(CARDS, key=score, reverse=True)
                       if score(c) > 0][:limit]
        return [next(c for c in CARDS if c.metric == key) for key in indices]
    finally:
        db.close()


def numeric(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def explain_snapshot(snapshot, observation_dates=None):
    """Generate only supported observations and linked methodological cautions.

    Snapshot keys: mvrv, asopr, nupl, price, sma200 (all already resolved and
    source-checked in the existing report pipeline). Missing values -> no claim.
    """
    observation_dates = observation_dates or {}
    facts = []
    for key in ("mvrv", "asopr", "nupl"):
        value = snapshot.get(key)
        if not numeric(value):
            continue
        source = next((c for c in retrieve(key, 5) if c.metric == key), None)
        if source is None:
            continue
        if key == "mvrv":
            interpretation = (
                "capitalización de mercado superior a la realizada" if value > 1
                else "capitalización de mercado inferior a la realizada" if value < 1
                else "capitalización de mercado igual a la realizada"
            )
            sentence = f"MVRV {value:.3f}: {interpretation}."
        elif key == "asopr":
            interpretation = ("gastos agregados por encima del costo de adquisición"
                              if value > 1 else
                              "gastos agregados por debajo del costo de adquisición"
                              if value < 1 else "gastos agregados al costo de adquisición")
            sentence = f"aSOPR 1W {value:.4f}: {interpretation}."
        else:
            interpretation = ("beneficio no realizado neto agregado"
                              if value > 0 else
                              "pérdida no realizada neta agregada" if value < 0
                              else "beneficio y pérdida no realizados equilibrados")
            sentence = f"NUPL {value:.3f}: {interpretation}."
        day = observation_dates.get(key)
        if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: [0-2]\d:[0-5]\d(?: UTC)?)?", day):
            day = None
        facts.append({
            "metric": key, "observation": sentence,
            "asof": day, "method": source.method,
            "caveat": source.caveat, "methodology_url": source.url,
        })
    return facts


def render_research_note(snapshot, observation_dates=None):
    """Source-backed HTML section; omit if no observed metrics are available."""
    evidence = explain_snapshot(snapshot, observation_dates)
    if not evidence:
        return ""
    pieces = [
        '<div class="card" id="research-methodology">'
        '<h3>Análisis contrastado — contexto metodológico (RAG local)</h3>'
        '<p style="font-size:11px;color:#52645a">Interpretaciones condicionales '
        'sobre datos medidos. Recuperación local de fichas metodológicas; '
        'no se trata de pronósticos ni de conclusiones de un modelo calibrado.</p>'
    ]
    for card in evidence:
        date_text = f' · dato: {html.escape(card["asof"])}' if card["asof"] else ''
        pieces.append(
            '<div class="stat-item" style="margin:9px 0">'
            f'<p><strong>{html.escape(card["observation"])}</strong>{date_text}</p>'
            f'<p>{html.escape(card["method"])} '
            f'<em>{html.escape(card["caveat"])}</em></p>'
            f'<p><a rel="noopener noreferrer" href="{html.escape(card["methodology_url"], quote=True)}">'
            'Consultar metodología</a></p></div>'
        )
    pieces.append('</div>')
    return "".join(pieces)
