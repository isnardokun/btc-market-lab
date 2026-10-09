#!/usr/bin/env python3
"""Optional ResearchBitcoin V2 adapter. No network/write unless explicitly requested.

The existing bitview 'series'/'daily' data remain authoritative and unmodified.
RBN metrics are stored independently with original units and UTC provenance.
Auth: RESEARCHBITCOIN_API_TOKEN via X-API-Token; NEVER query-string tokens.
"""
import argparse
import datetime as dt
import json
import math
import os
import sqlite3
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.researchbitcoin_catalog import BASE_URL, CATALOG, PROVIDER

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "db" / "btc_research.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS onchain_external_observations (
    provider TEXT NOT NULL,
    metric TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source_endpoint TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider, metric, observed_date)
);
CREATE INDEX IF NOT EXISTS idx_onchain_external_asof
ON onchain_external_observations(provider, observed_date);
"""


def last_completed_day(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Se requiere fecha timezone-aware")
    return now.astimezone(dt.timezone.utc).date() - dt.timedelta(days=1)


def query_params(today=None, days=7):
    """RBN 'to_time' is exclusive; omit today's partial candle."""
    if not 1 <= days <= 14:
        raise ValueError("days debe estar entre 1 y 14 para limitar cuota")
    cutoff = last_completed_day(today)
    start = cutoff - dt.timedelta(days=days - 1)
    end_exclusive = cutoff + dt.timedelta(days=1)
    return {
        "from_time": start.isoformat(),
        "to_time": end_exclusive.isoformat(),
        "resolution": "d1",
        "output_format": "json",
    }


def fetch(slug, *, days=7, token=None, opener=urlopen):
    """One bounded request; no automated retries/quota spending."""
    if slug not in CATALOG:
        raise ValueError("Métrica fuera del catálogo aprobado")
    secret = token if token is not None else os.getenv("RESEARCHBITCOIN_API_TOKEN", "")
    if not secret:
        raise RuntimeError("Falta RESEARCHBITCOIN_API_TOKEN en el entorno local")
    item = CATALOG[slug]
    url = BASE_URL + item.endpoint + "/" + item.slug + "?" + urlencode(query_params(days=days))
    req = Request(url, headers={
        "X-API-Token": secret,
        "Accept": "application/json",
        "User-Agent": "BTC-Market-Lab/1.0",
    })
    try:
        with opener(req, timeout=20) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Respuesta demasiado grande")
            return json.loads(raw)
    except HTTPError as exc:
        # Never echo headers, request objects, response bodies or auth tokens.
        raise RuntimeError(f"ResearchBitcoin HTTP {exc.code} para {slug}") from None
    except URLError:
        raise RuntimeError(f"ResearchBitcoin conexión fallida para {slug}") from None


def describe_shape(response):
    """Only field names/types (never the returned observations)."""
    if isinstance(response, dict):
        sample = response.get("data")
        shape = {
            "top_level_keys": sorted(str(x) for x in response.keys())[:30],
            "data_type": type(sample).__name__,
        }
        if isinstance(sample, list):
            shape["data_length"] = len(sample)
            if sample and isinstance(sample[0], dict):
                shape["row_fields"] = sorted(str(x) for x in sample[0].keys())[:30]
            elif sample:
                shape["row_type"] = type(sample[0]).__name__
        elif isinstance(sample, dict):
            shape["data_fields"] = sorted(str(x) for x in sample.keys())[:30]
        return shape
    return {"root_type": type(response).__name__}


def parse_timestamp(value):
    if isinstance(value, bool):
        raise ValueError("Timestamp inválido")
    if isinstance(value, (int, float)):
        if not 946684800 <= value < 4102444800:
            raise ValueError("Timestamp numérico fuera de rango UNIX segundos")
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    if not isinstance(value, str):
        raise ValueError("Timestamp no reconocido")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Timestamp ISO inválido") from None
    if parsed.tzinfo is None:
        # The documented provider time-zone is UTC; date-only = 00:00 UTC.
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def parse_scalar_rows(payload, slug, now=None):
    """STRICT parsing of a common JSON scalar representation.

    Until Hermes confirms the live response schema, unknown shapes fail
    closed and are NOT written to SQLite. No interpolation or fabricated data.
    """
    if slug not in CATALOG:
        raise ValueError("Métrica no aprobada")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Formato JSON sin lista data verificable; usar --sample")
    cutoff = last_completed_day(now)
    item = CATALOG[slug]
    found = {}
    for row in payload["data"]:
        if not isinstance(row, dict):
            raise ValueError("Fila no estructurada; revisar esquema --sample")
        time_key = next((k for k in ("time", "timestamp", "datetime", "date") if k in row), None)
        if time_key is None:
            raise ValueError("Fila sin campo de fecha/time; revisar --sample")
        # ResearchBitcoin API usa el slug como key de valor (ej. {"realized_price_sth": 45000, "time": ...})
        # También acepta formato genérico {"value": ..., "time": ...}
        # Only the requested metric key, or explicitly generic "value",
        # is acceptable. Another catalog slug is not a value for this metric.
        if slug in row:
            value_key = slug
        elif "value" in row:
            value_key = "value"
        else:
            raise ValueError(f"Fila sin valor para {slug}; revisar --sample")
        if value_key == "value" and any(other in row for other in CATALOG if other != slug):
            raise ValueError(f"Respuesta contiene otra métrica distinta de {slug}")
        stamp = parse_timestamp(row[time_key])
        day = stamp.date()
        if day > cutoff:
            continue  # Explicitly reject uncompleted UTC day.
        value = row[value_key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Valor no escalar finito")
        if item.raw_scale == "fraction_0_1":
            # Live observed provider values are raw fractions; avoid silently
            # accepting a 0..100 number as if it were a fraction.
            if not 0 <= value <= 1:
                raise ValueError("Escala fraccional 0..1 no confirmada para esta observación")
        elif item.unit == "percent" and not (0 <= value <= 100):
            raise ValueError("Porcentaje incompatible con escala 0..100; verificar metodología")
        if item.unit == "USD" and value < 0 and slug not in {"net_realized_profit_loss_sth"}:
            raise ValueError("USD negativo en métrica no firmada")
        found[day.isoformat()] = (stamp.isoformat(), float(value))
    if not found:
        raise ValueError("Sin observaciones diarias completas válidas")
    return found


def initialize_database(path):
    # Use a separate namespaced table; never alter bitview's large daily table.
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)


def store_rows(path, slug, rows, *, fetched_at=None):
    if slug not in CATALOG:
        raise ValueError("Slug no aprobado")
    fetched_at = fetched_at or dt.datetime.now(dt.timezone.utc).isoformat()
    metric = CATALOG[slug]
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        for day, (asof, value) in rows.items():
            if dt.date.fromisoformat(day) != parse_timestamp(asof).date():
                raise ValueError("Fecha no coincide con timestamp")
            db.execute("""
                INSERT INTO onchain_external_observations
                (provider, metric, observed_date, observed_at_utc, value,
                 unit, source_endpoint, fetched_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider,metric,observed_date) DO UPDATE SET
                  observed_at_utc=excluded.observed_at_utc,
                  value=excluded.value, unit=excluded.unit,
                  source_endpoint=excluded.source_endpoint,
                  fetched_at_utc=excluded.fetched_at_utc
            """, (PROVIDER, slug, day, asof, value, metric.unit,
                  metric.endpoint + "/" + slug, fetched_at))


def inventory(path):
    """Read-only local coverage, not proof that a similarly named series is equivalent."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        series = {r[0] for r in db.execute("SELECT name FROM series")}
        try:
            external = {r[0]: (r[1], r[2]) for r in db.execute("""
                SELECT metric, COUNT(*), MAX(observed_date)
                FROM onchain_external_observations WHERE provider=?
                GROUP BY metric
            """, (PROVIDER,))}
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            external = {}
    return [{
        "metric": slug,
        "category": item.group,
        "tier": 0,
        "bitview_exact_name_in_catalog": slug in series,
        "researchbitcoin_points": external.get(slug, (0, None))[0],
        "researchbitcoin_latest_utc": external.get(slug, (0, None))[1],
        "needs_methodology_comparison": slug in series,
    } for slug, item in CATALOG.items()]


def main():
    parser = argparse.ArgumentParser(description="Complemento on-chain RBN opcional, aislado de bitview")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--catalog", action="store_true", help="Mostrar fuentes priorizadas sin acceder a API/BD")
    actions.add_argument("--inventory", action="store_true", help="Consultar cobertura real en SQLite, solo lectura")
    actions.add_argument("--init-db", action="store_true", help="Crear únicamente tabla auxiliar, no ingestar")
    actions.add_argument("--sample", metavar="SLUG", help="Inspeccionar forma de respuesta API sin mostrar valores")
    actions.add_argument("--sync", metavar="SLUG", help="Ingesta explícita de una métrica, NO corre con daily.sh")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--days", type=int, default=7, help="Días diarios a solicitar (1..14)")
    args = parser.parse_args()
    if args.catalog:
        print(json.dumps([vars(item) | {"docs": item.docs} for item in CATALOG.values()],
                         ensure_ascii=False, indent=2))
    elif args.inventory:
        print(json.dumps(inventory(args.db), ensure_ascii=False, indent=2))
    elif args.init_db:
        if not args.db.is_file():
            parser.error("La base local no existe; no se crea otra silenciosamente")
        initialize_database(args.db)
        print("Tabla auxiliar onchain_external_observations creada (sin datos)")
    else:
        slug = args.sample or args.sync
        try:
            payload = fetch(slug, days=args.days)
            if args.sample:
                print(json.dumps(describe_shape(payload), ensure_ascii=False, indent=2))
            else:
                if not args.db.is_file():
                    raise ValueError("No existe la base SQLite local")
                rows = parse_scalar_rows(payload, slug)
                store_rows(args.db, slug, rows)
                print(f"RBN {slug}: {len(rows)} días completos guardados por separado")
        except (ValueError, RuntimeError) as exc:
            parser.exit(1, f"RECHAZADO: {exc}\n")


if __name__ == "__main__":
    main()
