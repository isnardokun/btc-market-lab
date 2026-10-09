#!/usr/bin/env python3
"""ResearchBitcoin V2 incremental + historical catch-up, quota-bounded and resumable.

Without --apply: PLAN ONLY (no network, no writes). Unlike a one-time --sync,
historical mode enumerates every missing daily date in the allowed provider
window, reuses already-stored observations, and stops after --max-requests.
Never part of daily.sh unless the operator explicitly enables it.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.researchbitcoin_catalog import CATALOG, PROVIDER
from storage.archive_schema import migrate, record_batch
from ingestion.researchbitcoin_v2 import (
    DEFAULT_DB, fetch, last_completed_day, parse_scalar_rows, store_rows,
)


def allowed_earliest(cutoff, tier, requested_start):
    """Tier 0 has a rolling 1-year history limit, higher tiers do not."""
    if tier not in (0, 1, 2):
        raise ValueError("ResearchBitcoin: tier no reconocido")
    earliest = cutoff - dt.timedelta(days=364)
    if requested_start:
        if isinstance(requested_start, str):
            requested_start = dt.date.fromisoformat(requested_start)
        if requested_start > cutoff:
            raise ValueError("Historia comienza después del último día UTC completado")
        if tier == 0 and requested_start < earliest:
            raise ValueError("Tier 0 solo permite el último año; no inventar historia")
        return requested_start
    if tier > 0:
        raise ValueError("Tier 1/2: --from YYYY-MM-DD es obligatorio para histórico completo")
    return earliest


def group_missing_dates(dates, *, max_days=14):
    """Turn nonconsecutive missing days into limited contiguous request windows."""
    if not dates:
        return []
    sorted_days = sorted(set(dates))
    runs = []
    first = last = sorted_days[0]
    for day in sorted_days[1:]:
        if day != last + dt.timedelta(days=1) or (day - first).days >= max_days:
            runs.append((first, last + dt.timedelta(days=1)))
            first = day
        last = day
    runs.append((first, last + dt.timedelta(days=1)))
    return runs


def read_dates(db_path, slug):
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        try:
            rows = conn.execute(
                "SELECT observed_date FROM onchain_external_observations "
                "WHERE provider=? AND metric=?",
                (PROVIDER, slug),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            rows = []
    return {dt.date.fromisoformat(day) for (day,) in rows}


def plan_metric(slug, present_dates, *, mode, cutoff, tier=0, history_start=None):
    if slug not in CATALOG:
        raise ValueError("Slug fuera del catálogo")
    if mode == "history":
        start = allowed_earliest(cutoff, tier, history_start)
        if present_dates:
            # Never skip old missing history because a newer point exists.
            start = min(start, cutoff)
    else:
        # First incremental run is a small catch-up; historical mode is separate.
        start = max(present_dates, default=cutoff - dt.timedelta(days=6)) + dt.timedelta(days=1)
        if not present_dates:
            start = cutoff - dt.timedelta(days=6)
    if start > cutoff:
        return []
    all_days = (start + dt.timedelta(days=i)
                for i in range((cutoff - start).days + 1))
    missing = [day for day in all_days if day not in present_dates]
    return group_missing_dates(missing)


def plan_all(db, *, mode, slugs=None, tier=0, history_start=None, cutoff=None):
    cutoff = cutoff or last_completed_day()
    selected = sorted(slugs or CATALOG)
    return {slug: plan_metric(slug, read_dates(db, slug), mode=mode, cutoff=cutoff,
                              tier=tier, history_start=history_start)
            for slug in selected}


def run(db_path, *, mode, slugs, tier=2, history_start=None, max_requests=13,
        apply=False, client=fetch, today=None):
    if not 1 <= max_requests <= 100:
        raise ValueError("max-requests fuera de rango 1..100")
    if not Path(db_path).is_file():
        raise ValueError("SQLite local no existe (no se creará una base vacía)")
    cutoff = last_completed_day(today)
    if apply:
        migrate(db_path)
    plan = plan_all(db_path, mode=mode, slugs=slugs, tier=tier,
                    history_start=history_start, cutoff=cutoff)
    windows = [(slug, a, b) for slug, pairs in plan.items() for a, b in pairs]
    # Interleave metric requests. Repeated calls steadily fill the oldest gaps.
    selected = windows[:max_requests]
    estimated = sum((end - start).days for _, start, end in selected)
    results = {
        "mode": mode, "cutoff_utc": cutoff.isoformat(), "tier": tier,
        "planned_windows": len(windows), "selected_windows": len(selected),
        "estimated_max_data_points": estimated, "executed": bool(apply),
        "requested": [], "failed": [], "saved_observations": 0,
    }
    for slug, start, end in selected:
        result = {"metric": slug, "start": start.isoformat(), "end_exclusive": end.isoformat()}
        if not apply:
            results["requested"].append(result)
            continue
        try:
            payload = client(slug, start_day=start, end_day=end)
            observed = parse_scalar_rows(payload, slug, now=today)
            # Reject response observations outside the requested chunk.
            if any(not start <= dt.date.fromisoformat(day) < end for day in observed):
                raise ValueError("Proveedor devolvió observaciones fuera de la ventana solicitada")
            record_batch(db_path, PROVIDER, slug, CATALOG[slug],
                         start.isoformat(), end.isoformat(), observed,
                         f"Tier {tier}")
            store_rows(db_path, slug, observed)
            result["rows"] = len(observed)
            results["saved_observations"] += len(observed)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            # Keep payload credentials / URLs / raw values out of public logs.
            result["error_type"] = type(exc).__name__
            results["failed"].append(result)
            # Stop rather than consuming the rest of the quota on failures.
            results["requested"].append(result)
            break
        results["requested"].append(result)
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--incremental", action="store_true", help="Solo días posteriores al último observado")
    m.add_argument("--history", action="store_true", help="Rellenar todos los días faltantes en historia autorizada")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--tier", type=int, choices=(0, 1, 2), default=2)
    p.add_argument("--from", dest="history_start", metavar="YYYY-MM-DD")
    p.add_argument("--slug", action="append", choices=sorted(CATALOG), help="Filtrar a uno o varios slugs")
    p.add_argument("--max-requests", type=int, default=13,
                   help="Máximo solicitudes HTTP por ejecución (1..100), default 13")
    p.add_argument("--apply", action="store_true", help="AUTORIZACIÓN: consultar API y archivar SQLite")
    args = p.parse_args()
    try:
        if args.incremental and (args.history_start is not None or args.tier != 0):
            raise ValueError("Opciones --from/--tier aplican solo a --history")
        report = run(args.db, mode="history" if args.history else "incremental",
                     slugs=args.slug, tier=args.tier, history_start=args.history_start,
                     max_requests=args.max_requests, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["failed"] else 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print("ARCHIVE STOP: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
