#!/usr/bin/env python3
"""Build a verified, offline-readable, single-file daily report for Hermes.

The original approved report stays untouched. External links to news are kept
as optional navigation; no CSS, JS, images or fonts are loaded over the network.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REMOTE_FONT_LINK = re.compile(
    r"<link\b(?=[^>]*\brel\s*=\s*['\"]?stylesheet\b)[^>]*>",
    re.IGNORECASE,
)
URL_IN_CSS = re.compile(r"url\s*\(\s*['\"]?(?!data:)", re.IGNORECASE)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OfflineDependencyChecker(HTMLParser):
    """Inspect resource-bearing tags, but allow ordinary hyperlinks."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.errors = []

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if tag in {"script", "iframe", "object", "embed", "base"}:
            self.errors.append(f"Elemento no portátil: <{tag}>")
        if tag == "link":
            self.errors.append("Dependencia <link> sin integrar")
        for key in ("src", "srcset", "poster", "data"):
            value = props.get(key)
            if value and not value.lstrip().lower().startswith("data:"):
                self.errors.append(f"Recurso externo o local: <{tag}> {key}={value[:80]}")
        if tag in {"video", "audio"}:
            self.errors.append(f"Medio no integrado: <{tag}>")


def make_portable(html: str) -> str:
    if not re.search(r"<html\b", html, re.I) or not re.search(r"</html\s*>", html, re.I):
        raise ValueError("El HTML original está incompleto")
    if not re.search(r"<style\b", html, re.I):
        raise ValueError("No hay CSS integrado; el informe perdería el diseño")

    # Daily Report uses Google Fonts; use its existing CSS font fallbacks offline.
    cleaned = REMOTE_FONT_LINK.sub(
        lambda match: "" if "fonts.googleapis.com" in match.group(0).lower()
        else match.group(0),
        html,
    )
    css_urls = re.findall(r"url\s*\(\s*([^)]+)\)", cleaned, re.I)
    if re.search(r"@import\b", cleaned, re.I) or any(
        not ref.strip(" \\t\\n\\r\\\"'").lower().startswith("data:")
        for ref in css_urls
    ):
        raise ValueError("Hay imports/URLs CSS no integrados")
    validator = OfflineDependencyChecker()
    validator.feed(cleaned)
    if validator.errors:
        raise ValueError("; ".join(validator.errors))
    return cleaned


def verify_gate(report: Path, gate: Path, data: bytes) -> dict:
    if not gate.is_file():
        raise ValueError(f"Falta aprobación: {gate}")
    metadata = json.loads(gate.read_text(encoding="utf-8"))
    date_match = re.fullmatch(r"daily_report_(\d{4}-\d{2}-\d{2})\.html", report.name)
    if not date_match:
        raise ValueError("El nombre del reporte no sigue el formato diario esperado")
    if metadata.get("date") != date_match.group(1):
        raise ValueError("La fecha de aprobación no coincide con el reporte")
    if metadata.get("pass") is not True or metadata.get("critical_count", 0) != 0:
        raise ValueError("El publication gate rechazó el informe")
    if float(metadata.get("score_total", 0)) < 95:
        raise ValueError("La puntuación aprobada es inferior a 95")
    if Path(metadata.get("report", "")).resolve() != report.resolve():
        raise ValueError("La aprobación no corresponde al archivo solicitado")
    expected = metadata.get("report_sha256")
    if not expected or expected != sha256_bytes(data):
        raise ValueError("El hash del informe cambió o el gate no registra SHA256")
    return metadata


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".portable-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def export(report: Path, gate: Path, output: Path) -> Path:
    report = report.resolve()
    gate = gate.resolve()
    output = output.resolve()
    if not report.is_file():
        raise FileNotFoundError(report)
    if report == output:
        raise ValueError("No sobrescribir el informe original")
    raw = report.read_bytes()
    metadata = verify_gate(report, gate, raw)
    cleaned = make_portable(raw.decode("utf-8")).encode("utf-8")
    atomic_write(output, cleaned)
    manifest = {
        "report_date": metadata["date"],
        "source_file": str(report),
        "source_sha256": sha256_bytes(raw),
        "gate_file": str(gate),
        "gate_sha256": sha256_bytes(gate.read_bytes()),
        "portable_sha256": sha256_bytes(cleaned),
        "offline": True,
        "approved_score": metadata["score_total"],
    }
    atomic_write(output.with_name(output.name + ".manifest.json"),
                 json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return output


def main():
    parser = argparse.ArgumentParser(description="Exportar informe HTML autónomo validado")
    parser.add_argument("--input", required=True, type=Path, help="HTML aprobado original")
    parser.add_argument("--gate", required=True, type=Path, help="JSON del publication gate")
    parser.add_argument("--output", required=True, type=Path, help="HTML portátil")
    args = parser.parse_args()
    try:
        result = export(args.input, args.gate, args.output)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"ERROR portable_report: {exc}\n")
    print(f"HTML PORTÁTIL APROBADO: {result}")


if __name__ == "__main__":
    main()
