#!/usr/bin/env python3
"""
news_pipeline.py — Ingesta, validación y normalización de noticias.
Separado de la generación del reporte para cumplir arquitectura modular.

Responsabilidades:
1. Ingesta: consulta Exa (mcporter)
2. Validación: filtra errores de API, URLs vacías, contenido insuficiente
3. Normalización: extrae título/URL/fecha/texto de cada resultado
4. Análisis: bias (keyword-based), impacto, clasificador de hechos

NO inventa datos. Si Exa falla, retorna lista vacía con logging.
"""
import os, sys, re, subprocess, datetime, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from ingestion.specialized_news import collect_specialized_news
from pathlib import Path

# ─── Configuración ─────────────────────────────────────────────────────────
VENV_PATH = "/home/ignotus/.agent-reach-venv"
DB_PATH   = Path(__file__).parent.parent / "db" / "news.db"
EXA_ERROR_LOG = Path(__file__).parent.parent / "logs" / "news_errors.log"
MAX_HIGHLIGHT = 400
MIN_TITLE_LEN = 20
MIN_HIGHLIGHT_LEN = 50
MAX_RESULTS = 5

# Asegurar directorio de logs
EXA_ERROR_LOG.parent.mkdir(exist_ok=True)

# ─── Keyword maps ─────────────────────────────────────────────────────────
BIAS_KEYWORDS = {
    "Bullish":  ["bullish", "buy", "long", "surge", "recovery", "rebound",
                 "all-time high", " ath", "breakout", "strong buy", "upgrade",
                 "new record", "rally", "soar", "jump"],
    "Bearish":  ["bearish", "sell", "short", "plunge", "crash", "breakdown",
                 "rejection", "liquidat", "selloff", "plunge", "tumble",
                 "worst", "collapse", "fear"],
    "Neutral":  ["hold", "maintain", "unchanged", "stable", "steady"],
}

IMPACT_KEYWORDS = {
    "Tasas e inflación":  ["rate hike", "rate cut", "inflation", "fed", "treasury",
                           "yield", "recession", "cpi", "pce", "unemployment", "jobs"],
    "Flujos ETF/Instit":  ["etf", "institutional", "flow", "fund", "blackrock",
                           "fidelity", "spot", "accumulation", "distribution"],
    "Liquidez y apalanc":["liquidation", "short", "long", "leverage", "margin",
                           "funding rate", "open interest", "清算"],
    "Estructura técnica": ["breakout", "resistance", "support", "technical",
                           "moving average", "rsi", "macd", "chart"],
    "Geopolítica":        ["geopolitic", "oil", "supply", "opec", "middle east",
                           "china", "europe", "fed"],
    "On-chain":           ["hashrate", "difficulty", "utxo", "exchange flow",
                           "wallet", "miner", "whale"],
}

# ─── Logging de errores ───────────────────────────────────────────────────
def log_error(query, error_msg, raw_output=""):
    """Registra errores de Exa para auditoría."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(EXA_ERROR_LOG, "a") as f:
        f.write(f"\n[{ts}] QUERY: {query[:80]}\n")
        f.write(f"ERROR: {error_msg}\n")
        if raw_output[:200]:
            f.write(f"OUTPUT (first 200): {raw_output[:200]}\n")
        f.write("─" * 60 + "\n")

# ─── Ingesta: consulta Exa via mcporter ──────────────────────────────────
def exa_search(query, n=MAX_RESULTS):
    """
    Consulta Exa via mcporter. Retorna lista de resultados crudos.
    Si falla, retorna [] y loguea el error.
    """
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = VENV_PATH
    env["PATH"] = VENV_PATH + "/bin:" + env.get("PATH", "")

    try:
        result = subprocess.run(
            ["mcporter", "call", "exa.web_search_exa",
             f"query={query}", f"numResults={n}"],
            capture_output=True, text=True, timeout=30, env=env
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        rc = result.returncode

        # ── Validar respuesta ─────────────────────────────────────────────
        if rc != 0:
            log_error(query, f"mcporter exit {rc}: {stderr[:200]}", stdout[:200])
            return []

        if not stdout:
            log_error(query, "Empty stdout from mcporter", "")
            return []

        # Detectar errores de API en el output
        error_indicators = [
            "rate limit", "429", "401", "403", "500",
            "Error:", "error_code", "exceeded", "quota",
            "timeout", "connection refused"
        ]
        for err in error_indicators:
            if err.lower() in stdout.lower() and "Title:" not in stdout:
                log_error(query, f"Exa API error detected: {err}", stdout[:300])
                return []

        # Verificar que hay al menos un resultado con Title:
        if "Title:" not in stdout:
            log_error(query, "No 'Title:' found in Exa output", stdout[:300])
            return []

        return stdout

    except subprocess.TimeoutExpired:
        log_error(query, "mcporter timeout (30s)", "")
        return []
    except FileNotFoundError:
        print("ERROR: mcporter not found — check agent-reach installation", file=sys.stderr)
        return []
    except Exception as e:
        log_error(query, str(e), "")
        return []

# ─── Normalización: parsear output de mcporter ────────────────────────────
def parse_mcporter_raw(raw_output):
    """
    Parsea el output de texto plano de mcporter.
    Retorna lista de dicts con: title, url, published, highlight, date, raw
    """
    entries = []
    if not raw_output or "Title:" not in raw_output:
        return entries

    for block in raw_output.split("Title:"):
        if not block.strip():
            continue

        # Extraer título — todo hasta "URL:"
        title = block.split("URL:")[0].strip() if "URL:" in block else block.strip()

        # Extraer URL
        url = ""
        if "URL:" in block:
            url_part = block.split("URL:")[1]
            if "Published:" in url_part:
                url = url_part.split("Published:")[0].strip()
            else:
                url = url_part.split("Highlights:")[0].strip() if "Highlights:" in url_part else url_part.strip()

        # Extraer Published
        published = ""
        if "Published:" in block:
            pub_part = block.split("Published:")[1]
            published = pub_part.split("Highlights:")[0].strip() if "Highlights:" in pub_part else pub_part.strip()

        # Extraer Highlights
        highlight = ""
        if "Highlights:" in block:
            highlight = block.split("Highlights:")[1].strip()

        # Extraer fecha ISO del campo published
        date_str = ""
        if published:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", published)
            if m:
                date_str = m.group(1)

        entries.append({
            "title":     title,
            "url":       url,
            "published": published,
            "highlight": highlight[:MAX_HIGHLIGHT],
            "date":      date_str,
            "raw":       block[:200],  # para debugging
        })

    return entries

# ─── Validación: filtrar entradas inválidas ──────────────────────────────
def validate_entry(entry, index):
    """
    Validates a news entry. Returns (valid: bool, reason: str).
    """
    # URL check — must be non-empty and look like a real URL
    url = entry.get("url", "")
    if not url or len(url) < 10:
        return False, f"[{index}] URL vacía o muy corta: '{url[:50]}'"
    if not url.startswith(("http://", "https://")):
        return False, f"[{index}] URL sin protocolo: '{url[:50]}'"
    if url in ["http://", "https://", ""]:
        return False, f"[{index}] URL placeholder"

    # A source timestamp may not be in the future relative to UTC generation.
    published = str(entry.get("published", "") or "")
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:?\d{2})\b", published)
    if iso:
        value = iso.group(0).replace("Z", "+00:00")
        if re.search(r"[+-]\d{4}$", value):
            value = value[:-2] + ":" + value[-2:]
        try:
            event_dt = datetime.datetime.fromisoformat(value)
            if event_dt > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=3):
                return False, f"[{index}] Publicación futura frente a reloj UTC"
        except ValueError:
            return False, f"[{index}] Fecha publicada ISO inválida"

    # Title check — must be non-empty and long enough
    title = entry.get("title", "")
    if not title or len(title) < MIN_TITLE_LEN:
        return False, f"[{index}] Título muy corto: '{title[:50]}'"

    # Highlight check — must have some content
    highlight = entry.get("highlight", "")
    if not highlight or len(highlight) < MIN_HIGHLIGHT_LEN:
        return False, f"[{index}] Highlight insuficiente ({len(highlight)} chars)"

    # Title shouldn't be an error message
    # Match actual API failure messages, not substrings within legitimate headlines.
    if re.search(r"^(?:api\s+)?(?:error\s*[:\-]|http\s+[45]\d\d\b|rate\s+limit\b|request\s+failed\b|timeout\s*[:\-])",
                 title.strip(), flags=re.I):
        return False, f"[{index}] Título parece mensaje de API: '{title[:50]}'"

    return True, ""

# ─── Análisis: bias y impacto ─────────────────────────────────────────────
def classify_bias(text):
    """Clasifica bias por keywords. No usa LLM."""
    text_lower = text.lower()
    for bias, keywords in BIAS_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            return bias
    return "Neutral"

def classify_impact(text):
    """Clasifica impacto económico por keywords."""
    text_lower = text.lower()
    for category, keywords in IMPACT_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            return category
    return "General"

def classify_impact_text(text, bias):
    """Genera texto de impacto según categoría y bias."""
    text_lower = text.lower()
    category = classify_impact(text)

    templates = {
        "Tasas e inflación":  "Tasas e inflación: determina costo de capital y atractivo relativo de BTC vs renta fija.",
        "Flujos ETF/Instit":  "Flujos ETF/institucionales: impulsan demanda spot y pueden catalizar movimientos direccionales.",
        "Liquidez y apalanc": "Liquidez y apalancamiento: niveles de funding rate y liquidaciones definen puntos de inflexión.",
        "Estructura técnica": "Estructura técnica: ruptura de resistencias clave confirma o invalida el momentum.",
        "Geopolítica":        "Geopolítica y commodities: eventos internacionales alteran flujos hacia activos de refugio.",
        "On-chain":           "Métricas on-chain: whale activity, flujos de exchange y hashrate anticipan movimientos.",
        "General":            "Contexto general de mercado.",
    }
    return templates.get(category, templates["General"])

# ─── Pipeline completo ────────────────────────────────────────────────────
def _canonical_key(entry):
    """Dedupe tracking variants before comparing normalized headlines."""
    url = str(entry.get("url", "")).strip()
    parts = urlsplit(url)
    excluded = {"fbclid", "gclid", "mc_cid", "mc_eid"}
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parts.query)
                             if not key.lower().startswith("utm_") and key.lower() not in excluded))
    clean_url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                           parts.path.rstrip("/"), query, ""))
    title = re.sub(r"[^a-z0-9]+", " ", str(entry.get("title", "")).lower()).strip()
    return clean_url, title


def merge_validated_news(exa_entries, rss_entries, limit=7):
    """Keep original-source items first, then Exa; never duplicate URL/headline."""
    items = []
    seen_urls, seen_titles = set(), set()
    for item in list(rss_entries) + list(exa_entries):
        key_url, key_title = _canonical_key(item)
        if key_url in seen_urls or key_title in seen_titles:
            continue
        valid, _ = validate_entry(item, len(items))
        if not valid:
            continue
        seen_urls.add(key_url)
        seen_titles.add(key_title)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def run_news_pipeline(queries):
    """Exa discovery plus optional primary research RSS; fail-open per provider."""
    results = {}
    error_log = []
    specialized = collect_specialized_news(queries.keys())

    for asset, query in queries.items():
        raw = exa_search(query, n=MAX_RESULTS)
        entries = parse_mcporter_raw(raw)
        validated = []
        for i, entry in enumerate(entries):
            valid, reason = validate_entry(entry, i)
            if not valid:
                error_log.append(f"{asset}: {reason}")
                continue
            entry["source"] = urlsplit(entry["url"]).hostname or "Exa search"
            entry["source_type"] = "discovery"
            validated.append(entry)
        results[asset] = merge_validated_news(validated, specialized.get(asset, []))
        if not results[asset]:
            print(f"[news_pipeline] {asset}: 0 noticias válidas", file=sys.stderr)

    if error_log:
        print(f"[news_pipeline] {len(error_log)} entradas filtradas:", file=sys.stderr)
        for issue in error_log[:5]:
            print(f"  {issue}", file=sys.stderr)
    return results


# ─── Normalizar para el reporte ─────────────────────────────────────────
def clean_source_excerpt(title, highlight, max_chars=280):
    """Clean search-result boilerplate without inventing or translating facts.

    Do not append synthetic conclusions. Keep the exact underlying wording
    except for repeated leading titles/markdown headings and whitespace.
    Truncate at a word boundary, ending with an explicit ellipsis.
    """
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    excerpt = re.sub(r"\s+", " ", str(highlight or "")).strip()
    excerpt = re.sub(r"^(?:[.\s-]+)", "", excerpt)
    if not excerpt:
        return ""
    # Search highlights frequently begin with the same headline multiple times.
    for _ in range(4):
        leading = excerpt.lstrip(" #-:–")
        if title and leading.casefold().startswith(title.casefold()):
            following = leading[len(title):].lstrip(" #:-–")
            if following == leading or not following:
                break
            excerpt = following
        else:
            break
    excerpt = re.sub(r"^(?:#{1,4}\s+)", "", excerpt)
    if len(excerpt) <= max_chars:
        return excerpt
    head = excerpt[:max_chars].rstrip()
    at = head.rfind(" ")
    if at >= max_chars * 0.7:
        head = head[:at]
    return head.rstrip(" ,.;:") + "…"


def normalize_for_report(news_items):
    """Preserve original-language excerpts; use uncalibrated topic categories."""
    report_items = []
    for n in news_items:
        title = re.sub(r"\s+", " ", str(n.get("title", "") or "")).strip()
        summary = clean_source_excerpt(title, n.get("highlight", ""), max_chars=280)
        topic = classify_impact((title + " " + summary).lower())
        report_items.append({
            "date": n.get("date") or "Fecha no confirmada",
            "title": title,
            "url": n.get("url", ""),
            "summary": summary or "Extracto de fuente no disponible",
            "bias": "Sin evaluar",
            "impact": topic,
            "source": n.get("source") or urlsplit(n.get("url", "")).hostname or "fuente no identificada",
            "source_type": n.get("source_type") or "discovery",
        })
    return report_items


# ─── Query definitions ────────────────────────────────────────────────────
def default_news_queries(run_day=None):
    """News search terms follow the UTC report date; never hardcode a month."""
    day = run_day or datetime.datetime.now(datetime.timezone.utc).date()
    period = day.strftime("%B %Y")
    return {
        "BTC": f"Bitcoin BTC crypto price analysis market {period}",
        "SPY": f"S&P 500 stock market equities analysis {period}",
        "GOLD": f"gold oil commodities price analysis {period}",
        "MACRO": f"Federal Reserve interest rates inflation macro economic {period}",
    }


DEFAULT_QUERIES = default_news_queries()

# ─── CLI ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="News pipeline")
    parser.add_argument("--asset", default="BTC,SPY,GOLD,MACRO",
                        help="Assets to search (comma-separated)")
    parser.add_argument("--save", help="Save results to JSON file")
    args = parser.parse_args()

    assets = args.asset.split(",")
    queries = {a: DEFAULT_QUERIES.get(a, f"{a} analysis {datetime.datetime.now(datetime.timezone.utc).strftime('%B %Y')}") for a in assets}

    print(f"News pipeline — {datetime.date.today()}")
    raw_results = run_news_pipeline(queries)

    # Normalize and print summary
    total = 0
    for asset, items in raw_results.items():
        report_items = normalize_for_report(items)
        print(f"\n{asset}: {len(report_items)} noticias válidas")
        for item in report_items:
            print(f"  [{item['bias']}] {item['title'][:70]}")
            print(f"    URL: {item['url'][:80]}")
        total += len(report_items)

        if args.save and report_items:
            out = Path(args.save)
            out.parent.mkdir(exist_ok=True)
            existing = []
            if out.exists():
                existing = json.loads(out.read_text())
            existing.extend(report_items)
            out.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
            print(f"  Guardado en {out}")

    print(f"\nTotal: {total} noticias válidas")
