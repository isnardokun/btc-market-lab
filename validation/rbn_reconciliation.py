"""Critical HTML ↔ SQLite reconciliation for optional ResearchBitcoin metrics.

Only the RBN card actually present (or expected due to fresh local data) is
checked. Missing ResearchBitcoin table/provider never blocks the base report.
This module reads SQLite as read-only; no API access, no data writes.
"""
import datetime as dt
from html.parser import HTMLParser

from rendering.onchain_complement import format_metric, read_complement


class _ResearchBitcoinCardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.card_depth = None
        self.card_count = 0
        self.metric_depth = None
        self.value_depth = None
        self.current = None
        self.value_parts = []
        self.entries = []

    def handle_starttag(self, tag, attrs):
        if tag == "a" and self.current is not None:
            self.current["links"].append(dict(attrs).get("href"))
        if tag != "div":
            return
        self.div_depth += 1
        data = dict(attrs)
        classes = set(data.get("class", "").split())
        if data.get("id") == "onchain-complement":
            self.card_count += 1
            if self.card_depth is None:
                self.card_depth = self.div_depth
            return
        if self.card_depth is None:
            return
        if "stat-item" in classes:
            if self.current is not None:
                self.current["malformed"] = True
                self.entries.append(self.current)
            self.metric_depth = self.div_depth
            self.current = {
                "provider": data.get("data-provider"),
                "slug": data.get("data-metric"),
                "date": data.get("data-asof-utc"),
                "raw": data.get("data-api-raw"),
                "links": [],
                "display": None,
                "visible_text": "",
                "malformed": False,
            }
        elif self.current is not None and "sval" in classes:
            self.value_depth = self.div_depth
            self.value_parts = []

    def handle_data(self, data):
        if self.current is not None:
            self.current["visible_text"] += data
            if self.value_depth is not None:
                self.value_parts.append(data)

    def handle_endtag(self, tag):
        if tag != "div" or self.div_depth <= 0:
            return
        if self.value_depth == self.div_depth and self.current is not None:
            self.current["display"] = "".join(self.value_parts).strip()
            self.value_depth = None
            self.value_parts = []
        if self.metric_depth == self.div_depth and self.current is not None:
            self.entries.append(self.current)
            self.current = None
            self.metric_depth = None
        if self.card_depth == self.div_depth:
            self.card_depth = None
        self.div_depth -= 1


def audit_rbn_against_html(html_text, db_path, report_date):
    """Return critical discrepancies without modifying any original data."""
    problems = []
    parser = _ResearchBitcoinCardParser()
    parser.feed(html_text)
    parser.close()
    try:
        if isinstance(report_date, str):
            report_date = dt.date.fromisoformat(report_date)
        if not isinstance(report_date, dt.date) or isinstance(report_date, dt.datetime):
            raise ValueError("Fecha del reporte inválida")
        # The daily report is always produced after the last completed UTC day.
        completed_day = report_date - dt.timedelta(days=1)
        expected = read_complement(db_path, completed_day)
    except (OSError, ValueError, TypeError) as exc:
        return ["RBN: no se pudo auditar procedencia temporal: " + type(exc).__name__]
    except Exception as exc:
        # SQLite errors must stop publication, not become a gate 100/100.
        return ["RBN: no se pudo reconciliar la tabla externa: " + type(exc).__name__]

    if parser.card_count > 1:
        problems.append("RBN: bloques onchain-complement duplicados")
    if not expected and parser.card_count == 0:
        return problems
    if expected and parser.card_count == 0:
        problems.append("RBN: faltan métricas válidas de SQLite en el HTML")
        return problems
    if parser.card_count and not expected:
        problems.append("RBN: HTML muestra métricas sin observaciones SQLite válidas")
        return problems

    expected_by_slug = {metric.slug: (metric, day, value)
                        for metric, day, value in expected}
    actual_slugs = set()
    for entry in parser.entries:
        slug = entry["slug"]
        if entry["malformed"] or entry["provider"] != "researchbitcoin":
            problems.append("RBN: tarjeta con estructura/proveedor inválido")
            continue
        if slug not in expected_by_slug:
            problems.append("RBN: métrica inesperada en HTML: " + str(slug)[:80])
            continue
        if slug in actual_slugs:
            problems.append("RBN: métrica duplicada en HTML: " + slug)
            continue
        actual_slugs.add(slug)
        metric, date, value = expected_by_slug[slug]
        if entry["date"] != date.isoformat():
            problems.append("RBN: fecha HTML/SQLite no coincide: " + slug)
        if entry["display"] != format_metric(metric, value):
            problems.append("RBN: valor HTML/SQLite no coincide: " + slug)
        if entry["links"] != [metric.docs]:
            problems.append("RBN: falta fuente metodológica correcta: " + slug)
        if metric.raw_scale == "fraction_0_1":
            if entry["raw"] != f"{value:.10g}":
                problems.append("RBN: dato API raw no coincide: " + slug)
            if ("Normalización provisional" not in entry["visible_text"]
                    or "× 100" not in entry["visible_text"]
                    or "denominador" not in entry["visible_text"]):
                problems.append("RBN: falta advertencia de escala provisional: " + slug)
        elif entry["raw"] is not None:
            problems.append("RBN: atributo raw inesperado: " + slug)
    for slug in expected_by_slug:
        if slug not in actual_slugs:
            problems.append("RBN: tarjeta SQLite válida ausente del HTML: " + slug)
    if parser.card_count and not parser.entries:
        problems.append("RBN: bloque sin tarjetas trazables")
    return problems
