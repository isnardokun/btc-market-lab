"""R10-E.5 synthetic, one-page BACKWARD history coverage planner.

Pure function consuming E0 read-only coverage and synthetic provider-window
fixtures. No SQLite import, HTTP client, CLI, cron, --apply or database path.
Even a simulated verified window does NOT establish real API availability.
"""

import datetime as dt

from scripts.market_r10e_plan_only import PAGE_LIMITS, SLOT_MS, UTC, utc_ms, iso_ms


def _utc_ms(text):
    if not isinstance(text, str):
        raise ValueError("UTC timestamp must be a string")
    stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    if stamp.utcoffset() != dt.timedelta(0):
        raise ValueError("Explicit UTC offset required")
    return utc_ms(text)


def _verify_envelope(coverage, availability):
    if not isinstance(coverage, dict) or not isinstance(availability, dict):
        raise ValueError("Malformed planner inputs")
    if (coverage.get("state") != "PLAN_ONLY_READ_ONLY"
            or coverage.get("historical_completeness") != "NOT_VERIFIED"
            or coverage.get("provider_availability") != "NOT_CHECKED"
            or coverage.get("http_requests") != 0
            or coverage.get("sqlite_writes") != 0):
        raise ValueError("Coverage must be an unverified read-only E0 snapshot")
    if availability.get("mode") != "synthetic_fixture":
        raise ValueError("Real provider availability assertions not accepted")
    if availability.get("permission_for_requests") is not False:
        raise ValueError("Fixture cannot grant network permission")
    streams = coverage.get("sources")
    bounds = availability.get("sources")
    if (not isinstance(streams, list) or len(streams) != 2
            or not isinstance(bounds, list) or len(bounds) != 2):
        raise ValueError("Exactly two OI streams and two fixture bounds required")
    snapshot, declared = {}, {}
    for stream in streams:
        if not isinstance(stream, dict):
            raise ValueError("Invalid coverage stream")
        provider = stream.get("provider")
        if provider not in PAGE_LIMITS or provider in snapshot:
            raise ValueError("Unknown/duplicate coverage provider")
        if (stream.get("symbol") != "BTCUSDT"
                or stream.get("metric") != "open_interest"
                or stream.get("interval") != "5m"
                or stream.get("page_limit") != PAGE_LIMITS[provider]
                or stream.get("historical_completeness") != "NOT_VERIFIED"
                or stream.get("provider_availability") != "NOT_CHECKED"
                or stream.get("http_requests") != 0
                or stream.get("sqlite_writes") != 0):
            raise ValueError("Unsupported coverage metadata")
        if stream.get("status") not in (
            "PLAN_ONLY", "NO_CLOSED_BAR", "BLOCKED_NO_BASELINE",
            "BLOCKED_UNALIGNED_TIMESTAMP", "BLOCKED_DUPLICATE_OR_ORDER",
            "BLOCKED_INTERNAL_GAP", "BLOCKED_MAX_AFTER_CUTOFF",
        ):
            raise ValueError("Unknown coverage state")
        snapshot[provider] = stream
    for item in bounds:
        if not isinstance(item, dict):
            raise ValueError("Invalid availability fixture")
        provider = item.get("provider")
        if provider not in PAGE_LIMITS or provider in declared:
            raise ValueError("Unknown/duplicate bound")
        if item.get("status") not in ("UNKNOWN", "SYNTHETIC_WINDOW"):
            raise ValueError("Unapproved evidence status")
        if item["status"] == "UNKNOWN":
            if item.get("earliest_available_utc") is not None:
                raise ValueError("Unknown bound cannot have an earliest date")
        elif not isinstance(item.get("earliest_available_utc"), str):
            raise ValueError("Synthetic bound requires a timestamp")
        declared[provider] = item
    if set(snapshot) != set(declared):
        raise ValueError("Coverage provider set differs from bound fixtures")
    if len({x.get("cutoff_utc") for x in streams}) != 1:
        raise ValueError("Inconsistent snapshot cutoff")
    return snapshot, declared


def plan_backward_fixture(coverage, availability):
    """Build maximum one ADVISORY backward window; no request execution."""
    snapshot, declared = _verify_envelope(coverage, availability)
    report = {
        "state": "SYNTHETIC_BACKWARD_PLAN_ONLY",
        "historical_completeness": "NOT_VERIFIED",
        "provider_availability": "NOT_CHECKED",
        "real_provider_bound_verified": False,
        "execution_authorized": False,
        "http_requests": 0,
        "sqlite_reads": 0,
        "sqlite_writes": 0,
        "future_request_budget": 1,
        "backward_cursor_namespace": "history_backward_v1_open_interest",
        "forward_cursor_mutated": False,
        "candidates": [], "selected": None,
    }
    blocked = []
    ready = []
    for provider in sorted(PAGE_LIMITS):
        stream, bound = snapshot[provider], declared[provider]
        status = stream["status"]
        candidate = {"provider": provider, "status": None, "estimated_missing_slots": None}
        if status.startswith("BLOCKED_"):
            candidate["status"] = "BLOCKED_SOURCE_QA"
            blocked.append(provider)
        elif stream.get("internal_gap_count") != 0:
            candidate["status"] = "BLOCKED_INTERNAL_GAP"
            blocked.append(provider)
        elif bound["status"] == "UNKNOWN":
            candidate["status"] = "BLOCKED_UNVERIFIED_PROVIDER_WINDOW"
            blocked.append(provider)
        else:
            oldest = _utc_ms(stream.get("first_stored_utc"))
            newest = _utc_ms(stream.get("max_stored_utc"))
            earliest = _utc_ms(bound["earliest_available_utc"])
            if oldest % SLOT_MS or newest % SLOT_MS or earliest % SLOT_MS:
                raise ValueError("Unaligned fixture timestamps")
            if earliest > oldest:
                # A provider window may start later than the locally stored series.
                candidate["status"] = "NO_EARLIER_WINDOW_IN_FIXTURE"
                candidate["estimated_missing_slots"] = 0
            elif oldest > newest:
                raise ValueError("Oldest stored observation exceeds latest")
            else:
                missing = (oldest - earliest) // SLOT_MS
                candidate["estimated_missing_slots"] = missing
                if missing == 0:
                    candidate["status"] = "NO_EARLIER_WINDOW_IN_FIXTURE"
                else:
                    candidate["status"] = "SYNTHETIC_BACKFILL_CANDIDATE"
                    ready.append((missing, provider, oldest, earliest))
        report["candidates"].append(candidate)
    if blocked:
        return {**report, "decision": "BLOCKED_INCOMPLETE_EVIDENCE",
                "blocked_providers": sorted(blocked)}
    if not ready:
        return {**report, "decision": "NO_BACKFILL_IN_FIXTURE_WINDOW"}

    # Largest backlog in 5m slots, then stable provider-name tie-break.
    missing, provider, oldest, earliest = sorted(
        ready, key=lambda row: (-row[0], row[1]))[0]
    slots = min(missing, PAGE_LIMITS[provider])
    expected_oldest = oldest - SLOT_MS * slots
    expected_newest = oldest - SLOT_MS
    if expected_oldest < earliest:
        raise ValueError("Synthetic window crosses earliest bound")
    return {
        **report,
        "decision": "PROPOSE_SYNTHETIC_BACKWARD_PAGE",
        "selected": {
            "provider": provider, "symbol": "BTCUSDT", "metric": "open_interest",
            "interval": "5m", "direction": "backward",
            "requested_end_ms": oldest - 1,
            "requested_start_ms": None,
            "stored_min_utc": iso_ms(oldest),
            "synthetic_earliest_available_utc": iso_ms(earliest),
            "estimated_first_row_utc": iso_ms(expected_oldest),
            "estimated_last_row_utc": iso_ms(expected_newest),
            "estimated_points_this_page": slots,
            "estimated_pages_for_fixture_window": (
                missing + PAGE_LIMITS[provider] - 1) // PAGE_LIMITS[provider],
            "api_limit": PAGE_LIMITS[provider],
            "provider_window_is_only_simulated": True,
        },
    }
