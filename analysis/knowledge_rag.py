"""Versioned offline methodology RAG: evidence cards, not price prediction.

The corpus contains originally authored summaries and attribution links. It
never downloads webpages, accepts outside prompt instructions or inserts market
values. Numerical prose remains gated by snapshot observations at report time.
"""
import datetime as dt
import hashlib
import html
import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


CORPUS_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "corpus_v2.json"
ALLOWED_DOMAINS = frozenset((
    "researchbitcoin.net", "docs.glassnode.com",
    "gitbook-docs.coinmetrics.io", "www.bls.gov",
    "fred.stlouisfed.org", "www.cftc.gov",
    "www.sec.gov", "bitcoinops.org", "www.binance.com",
))
ALLOWED_FAMILIES = frozenset((
    "onchain", "macro", "derivatives", "protocol", "quality",
))
ALLOWED_SOURCE_KINDS = frozenset((
    "primary_methodology", "provider_methodology",
    "protocol_reference", "editorial_rule",
))


@dataclass(frozen=True)
class Knowledge:
    metric: str
    title: str
    keywords: str
    method: str
    caveat: str
    url: str
    family: str = "onchain"
    source_kind: str = "provider_methodology"
    corpus_version: str = "2.0.0"
    reviewed_utc: str = "2026-10-09"


def load_corpus(path=CORPUS_PATH):
    """Fail closed on malformed or unaudited material; never fetch remotely."""
    raw = Path(path).read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if (payload.get("schema_version") != 2 or
            not re.fullmatch(r"\d+\.\d+\.\d+", payload.get("corpus_version", ""))):
        raise ValueError("Invalid corpus schema/version")
    reviewed = payload.get("reviewed_utc", "")
    try:
        dt.date.fromisoformat(reviewed)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid review date") from exc
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("Empty or malformed knowledge corpus")
    seen = set()
    cards = []
    required = ("metric", "family", "title", "keywords", "method",
                "caveat", "url", "source_kind")
    for row in records:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str)
                                             or not row[k].strip() for k in required):
            raise ValueError("Incomplete or malformed corpus record")
        metric = row["metric"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", metric) or metric in seen:
            raise ValueError("Duplicate or invalid metric ID: " + metric)
        if row["family"] not in ALLOWED_FAMILIES:
            raise ValueError("Invalid knowledge family: " + metric)
        if row["source_kind"] not in ALLOWED_SOURCE_KINDS:
            raise ValueError("Invalid source category: " + metric)
        url = urlsplit(row["url"])
        if (url.scheme != "https" or url.hostname not in ALLOWED_DOMAINS
                or url.username or url.password or url.port
                or not url.path.startswith("/") or url.fragment):
            raise ValueError("Unapproved methodology reference: " + metric)
        if (len(row["method"]) < 24 or len(row["caveat"]) < 20
                or len(row["keywords"]) > 500):
            raise ValueError("Incomplete methodological rationale: " + metric)
        seen.add(metric)
        cards.append(Knowledge(
            metric=metric, title=row["title"], keywords=row["keywords"],
            method=row["method"], caveat=row["caveat"], url=row["url"],
            family=row["family"], source_kind=row["source_kind"],
            corpus_version=payload["corpus_version"], reviewed_utc=reviewed,
        ))
    return tuple(cards)


CARDS = load_corpus()
CARD_BY_ID = {c.metric: c for c in CARDS}


def corpus_manifest(path=CORPUS_PATH):
    """Reproducible fingerprint of the indexed editorial data (not API data)."""
    raw = Path(path).read_bytes()
    records = load_corpus(path)
    return {
        "schema_version": 2,
        "version": records[0].corpus_version,
        "reviewed_utc": records[0].reviewed_utc,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "count": len(records),
        "families": {family: sum(c.family == family for c in records)
                     for family in sorted(ALLOWED_FAMILIES)},
    }


def _tokens(query):
    # Unicode-safe exact tokens, drop zero-length and cap SQLite query size.
    value = unicodedata.normalize("NFKC", str(query)).lower()
    return [s for s in re.findall(r"[^\W_]+(?:_[^\W_]+)*", value)
            if len(s) > 1][:10]


def retrieve(query, limit=3, families=None):
    """FTS5 rank weighted by exact metric ID; deterministic lexical fallback.

    No embeddings or external model. No retrieval of outside document bodies.
    """
    tokens = _tokens(query)
    if not tokens or not isinstance(limit, int) or limit < 1:
        return []
    if families is not None:
        family_filter = set(families)
        if not family_filter <= ALLOWED_FAMILIES:
            raise ValueError("Unknown knowledge family")
    else:
        family_filter = ALLOWED_FAMILIES
    eligible = [c for c in CARDS if c.family in family_filter]
    if not eligible:
        return []
    # Explicit single metric lookup prevents ambiguous BM25 cross-card matches.
    if len(tokens) == 1 and tokens[0] in CARD_BY_ID:
        exact = CARD_BY_ID[tokens[0]]
        if exact.family in family_filter:
            return [exact]
    try:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE VIRTUAL TABLE knowledge USING fts5("
                       "metric, title, keywords, method, caveat, "
                       "tokenize='unicode61 remove_diacritics 2')")
            db.executemany("INSERT INTO knowledge VALUES (?,?,?,?,?)",
                           [(c.metric, c.title, c.keywords, c.method, c.caveat)
                            for c in eligible])
            # SQLite FTS MATCH accepts only our quoted literal terms.
            match = " OR ".join('"' + t.replace('"', '') + '"' for t in tokens)
            rows = db.execute(
                "SELECT metric FROM knowledge WHERE knowledge MATCH ? "
                "ORDER BY bm25(knowledge, 14.0, 4.0, 3.0, 1.2, 0.4), metric "
                "LIMIT ?", (match, min(limit, 30))
            ).fetchall()
            matches = [CARD_BY_ID[metric] for metric, in rows]
    except sqlite3.OperationalError:
        # FTS5 not compiled: lexical match, not empty/fabricated results.
        folded = lambda s: ''.join(
            x for x in unicodedata.normalize("NFKD", s.lower())
            if not unicodedata.combining(x))
        def score(card):
            return (10 * sum(t == folded(card.metric) for t in tokens)
                    + 3 * sum(t in folded(card.title) for t in tokens)
                    + 2 * sum(t in folded(card.keywords) for t in tokens))
        matches = sorted((c for c in eligible if score(c) > 0),
                         key=lambda c: (-score(c), c.metric))[:limit]
    return matches[:limit]


def evidence_bundle(query, limit=5, families=None):
    """Structured evidence only: metadata, source and interpretation limits."""
    cards = retrieve(query, limit=limit, families=families)
    return {
        "corpus": corpus_manifest(),
        "query": str(query)[:200],
        "results": [{
            "metric": c.metric, "family": c.family, "title": c.title,
            "method": c.method, "caveat": c.caveat,
            "source_kind": c.source_kind, "methodology_url": c.url,
            "reviewed_utc": c.reviewed_utc,
        } for c in cards],
        "disclaimer": "Fichas metodológicas, no observaciones de mercado ni señales.",
    }


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
    manifest = corpus_manifest()
    pieces = [
        '<div class="card" id="research-methodology" '
        f'data-corpus-version="{html.escape(manifest["version"], quote=True)}" '
        f'data-corpus-sha256="{manifest["sha256"]}">'
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
