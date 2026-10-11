"""R10-E.6: ResearchBitcoin daily archive plan with SYNTHETIC entitlement gate.

Pure planning only: no HTTP, SQLite, CLI, cron, --apply or environment secrets.
The catalog describes code support, NOT verified API/Tier 2 availability.
"""

import datetime as dt

from ingestion.researchbitcoin_catalog import CATALOG, PROVIDER

MAX_DAYS_PER_WINDOW = 14
MAX_REQUESTS_PER_PLAN = 13
GENESIS = dt.date(2009, 1, 3)
UTC = dt.timezone.utc


def _date(value):
    if not isinstance(value, str):
        raise ValueError("ISO date string required")
    date = dt.date.fromisoformat(value)
    if date.isoformat() != value:
        raise ValueError("Canonical ISO date required")
    return date


def _clock(value):
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ValueError("Timezone-aware UTC clock required")
    if value.utcoffset() != dt.timedelta(0):
        raise ValueError("Explicit UTC clock required")
    return value.astimezone(UTC)


def _blocks(missing):
    """Group absent days into <=14-day inclusive/exclusive windows."""
    if not missing:
        return []
    chunks = []
    first = last = missing[0]
    for day in missing[1:]:
        if day != last + dt.timedelta(days=1) or (day - first).days >= 14:
            chunks.append((first, last + dt.timedelta(days=1)))
            first = day
        last = day
    chunks.append((first, last + dt.timedelta(days=1)))
    return chunks


def inventory_static():
    """Catalog declaration (13 scalar d1), not provider entitlement or coverage."""
    return [
        {"provider": PROVIDER, "metric": slug,
         "endpoint_path": metric.endpoint + "/" + slug,
         "frequency": "d1", "shape": "scalar",
         "raw_unit": metric.unit, "raw_scale": metric.raw_scale,
         "catalogued": True, "tier2_entitlement_verified": False,
         "historical_completeness": "NOT_VERIFIED"}
        for slug, metric in sorted(CATALOG.items())
    ]


def plan_rbn_tier2_fixture(coverage, entitlement, *, start_day, now_utc,
                           request_budget=13):
    """Plan missing calendar days; never authorize or perform a provider call.

    Synthetic entitlement evidence can unlock a simulated plan only.
    A real token's entitlement requires later independent verification.
    """
    now = _clock(now_utc)
    first = _date(start_day)
    cutoff = now.date() - dt.timedelta(days=1)
    if first < GENESIS or first > cutoff:
        raise ValueError("Date outside safe closed-day historical planning span")
    if type(request_budget) is not int or not 1 <= request_budget <= MAX_REQUESTS_PER_PLAN:
        raise ValueError("Invalid bounded request budget")
    if not isinstance(coverage, dict) or coverage.get("mode") != "synthetic_fixture":
        raise ValueError("Synthetic coverage required")
    if not isinstance(entitlement, dict) or entitlement.get("mode") != "synthetic_fixture":
        raise ValueError("Synthetic entitlement metadata required")
    if (entitlement.get("provider") != PROVIDER
            or type(entitlement.get("reported_tier")) is not int
            or entitlement.get("reported_tier") != 2
            or entitlement.get("network_authorized") is not False
            or entitlement.get("attestation") not in ("MISSING", "SIMULATED")):
        raise ValueError("Untrusted Tier 2 fixture entitlement declaration")

    data = coverage.get("present_dates")
    if not isinstance(data, dict) or set(data) != set(CATALOG):
        raise ValueError("Explicit coverage required for each catalog metric")
    parsed = {}
    for metric, days in data.items():
        if not isinstance(days, list) or len(days) > 20000:
            raise ValueError("Invalid metric date inventory")
        dates = [_date(day) for day in days]
        if len(dates) != len(set(dates)):
            raise ValueError("Duplicate observations in supplied fixture")
        if any(day < GENESIS or day > cutoff for day in dates):
            raise ValueError("Stored fixture date exceeds safe BTC daily range")
        parsed[metric] = set(dates)

    result = {
        "state": "SYNTHETIC_RBN_TIER2_HISTORY_PLAN",
        "provider": PROVIDER,
        "reported_tier": 2,
        "catalog_metric_count": len(CATALOG),
        "catalog_shapes_supported": ["scalar"],
        "start_date": first.isoformat(),
        "closed_cutoff_day": cutoff.isoformat(),
        "max_days_per_window": MAX_DAYS_PER_WINDOW,
        "request_budget": request_budget,
        "execution_authorized": False,
        "real_entitlement_verified": False,
        "real_provider_window_verified": False,
        "historical_completeness": "NOT_VERIFIED",
        "http_requests": 0,
        "sqlite_reads": 0,
        "sqlite_writes": 0,
        "metrics": [],
        "proposed_windows": [],
    }
    if entitlement["attestation"] != "SIMULATED":
        return {**result, "decision": "BLOCKED_NO_ENTITLEMENT_EVIDENCE",
                "reason": "Tier 2 from CLI or user declaration is not provider proof"}

    possible = []
    cursor = first
    span = (cutoff - first).days + 1
    required_days = [first + dt.timedelta(days=i) for i in range(span)]
    for slug in sorted(CATALOG):
        metric = CATALOG[slug]
        missing = [day for day in required_days if day not in parsed[slug]]
        windows = _blocks(missing)
        result["metrics"].append({
            "metric": slug, "shape": "scalar", "frequency": "d1",
            "unit": metric.unit, "raw_scale": metric.raw_scale,
            "stored_dates_in_range": len([d for d in parsed[slug] if first <= d <= cutoff]),
            "missing_days_in_requested_range": len(missing),
            "proposed_windows_total": len(windows),
            "provider_coverage_proved": False,
        })
        possible.append((slug, metric, windows))

    # Fair round-robin: each metric gets one opportunity before the second.
    rounds = max(len(windows) for _, _, windows in possible)
    for offset in range(rounds):
        for slug, metric, windows in possible:
            if len(result["proposed_windows"]) >= request_budget:
                break
            if offset >= len(windows):
                continue
            lo, hi = windows[offset]
            if not 1 <= (hi - lo).days <= MAX_DAYS_PER_WINDOW:
                raise AssertionError("Invalid synthetic d1 window")
            result["proposed_windows"].append({
                "metric": slug, "endpoint_path": metric.endpoint + "/" + slug,
                "from_time": lo.isoformat(),
                "to_time_exclusive": hi.isoformat(),
                "resolution": "d1", "shape": "scalar",
                "raw_unit": metric.unit, "raw_scale": metric.raw_scale,
                "expected_days": (hi - lo).days,
                "window_is_synthetic_only": True,
            })
        if len(result["proposed_windows"]) >= request_budget:
            break

    return {
        **result,
        "decision": ("SIMULATED_WINDOWS_PROPOSED" if result["proposed_windows"]
                     else "NO_MISSING_DATES_IN_FIXTURE"),
        "reason": "Synthetic planning only; API access and history remain unverified",
    }
