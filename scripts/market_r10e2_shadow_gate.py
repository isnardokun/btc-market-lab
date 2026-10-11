#!/usr/bin/env python3
"""R10-E.2 synthetic SHADOW gate: JSON-only; never authorizes live execution.

No network, no SQLite, no --apply, no scheduler and no credentials.
"""

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.market_r10e_plan_only import PAGE_LIMITS, SLOT_MS, UTC, cutoff_ms

MAX_PLAN_AGE_SECONDS = 300


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return stamp.astimezone(UTC)


def evaluate_shadow_gate(dispatch, runtime, *, now):
    """Pure evaluation of an advisory request using mock policy evidence only."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware clock required")
    now = now.astimezone(UTC)
    if not isinstance(dispatch, dict) or not isinstance(runtime, dict):
        raise ValueError("Expected JSON objects")
    if (dispatch.get("state") != "PLAN_ONLY_DISPATCH"
            or dispatch.get("execution_authorized") is not False
            or dispatch.get("live_apply_available") is not False
            or dispatch.get("http_requests") != 0
            or dispatch.get("sqlite_writes") != 0
            or dispatch.get("historical_completeness") != "NOT_VERIFIED"
            or dispatch.get("provider_availability") != "NOT_CHECKED"
            or dispatch.get("maximum_future_requests_if_authorized") != 1):
        raise ValueError("Unsafe dispatch metadata")
    if runtime.get("mode") != "synthetic_fixture":
        raise ValueError("Only synthetic policy fixtures supported")
    if (type(runtime.get("remaining_request_budget")) is not int
            or not 0 <= runtime["remaining_request_budget"] <= 100
            or type(runtime.get("max_requests_per_cycle")) is not int
            or runtime["max_requests_per_cycle"] != 1
            or type(runtime.get("minimum_cooldown_seconds")) is not int
            or not 30 <= runtime["minimum_cooldown_seconds"] <= 3600
            or type(runtime.get("circuit_open")) is not bool
            or type(runtime.get("writer_exclusion_simulated")) is not bool):
        raise ValueError("Invalid fixture policy")
    decision = dispatch.get("decision")
    if decision not in ("PROPOSE_ONE_PAGE_ONLY", "NO_CLOSED_BAR", "BLOCKED_SOURCE_QA"):
        raise ValueError("Unknown dispatch decision")
    candidates = dispatch.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise ValueError("Both candidates required")
    if sorted(x.get("provider") for x in candidates if isinstance(x, dict)) != sorted(PAGE_LIMITS):
        raise ValueError("Duplicate/missing providers")

    out = {"state": "SHADOW_GATE_ONLY", "execution_authorized": False,
           "http_requests": 0, "sqlite_reads": 0, "sqlite_writes": 0,
           "historical_completeness": "NOT_VERIFIED",
           "mock_policy_only": True, "selected_provider": None}

    def blocked(code):
        return {**out, "decision": code, "reason": code}

    if decision == "BLOCKED_SOURCE_QA":
        if dispatch.get("selected") is not None:
            raise ValueError("Unexpected selected source when blocked")
        return blocked("BLOCKED_SOURCE_QA")
    if decision == "NO_CLOSED_BAR":
        if dispatch.get("selected") is not None:
            raise ValueError("Unexpected selected source without work")
        return {**out, "decision": "NO_WORK", "reason": "All closed bars caught up"}

    selected = dispatch.get("selected")
    if not isinstance(selected, dict):
        raise ValueError("Missing selected work item")
    provider = selected.get("provider")
    if provider not in PAGE_LIMITS:
        raise ValueError("Unknown provider")
    for item in candidates:
        status = item.get("status")
        pending_slots = item.get("closed_intervals_waiting")
        if status == "PLAN_ONLY":
            if type(pending_slots) is not int or pending_slots < 1:
                raise ValueError("Malformed eligible candidate")
        elif status == "NO_CLOSED_BAR":
            if pending_slots != 0:
                raise ValueError("Malformed caught-up candidate")
        else:
            raise ValueError("Blocked candidate cannot yield a proposal")
    ranked = sorted((x for x in candidates if x["status"] == "PLAN_ONLY"),
                    key=lambda x: (-x["closed_intervals_waiting"], x["provider"]))
    if not ranked or ranked[0]["provider"] != provider:
        raise ValueError("Selection does not match deterministic priority")
    candidate = next(x for x in candidates if x["provider"] == provider)
    if (candidate.get("status") != "PLAN_ONLY"
            or selected.get("direction") != "forward"
            or selected.get("symbol") != "BTCUSDT"
            or selected.get("metric") != "open_interest"
            or selected.get("interval") != "5m"
            or selected.get("provider_history_window_verified") is not False):
        raise ValueError("Untrusted work proposal")
    count = selected.get("estimated_slots_this_page")
    pending = candidate.get("closed_intervals_waiting")
    if (type(count) is not int or type(pending) is not int
            or not 1 <= count <= PAGE_LIMITS[provider]
            or count != min(pending, PAGE_LIMITS[provider])):
        raise ValueError("Invalid slot budget")

    start = _utc(selected.get("next_start_utc"))
    end = _utc(selected.get("first_page_end_utc"))
    planned = _utc(dispatch.get("planned_at_utc"))
    cutoff = _utc(dispatch.get("closed_bar_cutoff_utc"))
    if ((end - start).total_seconds() * 1000 != (count - 1) * SLOT_MS
            or int(start.timestamp() * 1000) % SLOT_MS
            or int(end.timestamp() * 1000) % SLOT_MS
            or end > cutoff or cutoff > planned
            or int(cutoff.timestamp() * 1000) != cutoff_ms(planned)):
        raise ValueError("Unsafe time boundaries")

    extra = {"selected_provider": provider}
    age = (now - planned).total_seconds()
    if not 0 <= age <= MAX_PLAN_AGE_SECONDS:
        return {**blocked("BLOCKED_STALE_OR_FUTURE_PLAN"), **extra}
    if runtime["circuit_open"]:
        return {**blocked("BLOCKED_CIRCUIT_OPEN"), **extra}
    if runtime["remaining_request_budget"] < 1:
        return {**blocked("BLOCKED_QUOTA_EXHAUSTED"), **extra}
    if not runtime["writer_exclusion_simulated"]:
        return {**blocked("BLOCKED_WRITER_EXCLUSION_UNVERIFIED"), **extra}
    last_attempt = runtime.get("last_attempt_utc")
    if last_attempt is not None:
        elapsed = (now - _utc(last_attempt)).total_seconds()
        if elapsed < 0:
            return {**blocked("BLOCKED_FUTURE_ATTEMPT"), **extra}
        if elapsed < runtime["minimum_cooldown_seconds"]:
            return {**blocked("BLOCKED_COOLDOWN"), **extra}
    return {**out, **extra, "decision": "SHADOW_ELIGIBLE",
            "simulated_max_requests_next_cycle": 1,
            "reason": "Mock controls passed; NO permission for HTTP or production"}


def main(argv=None):
    p = argparse.ArgumentParser(description="Offline synthetic policy gate, no apply")
    p.add_argument("--dispatch-json", type=Path, required=True)
    p.add_argument("--fixture-policy-json", type=Path, required=True)
    p.add_argument("--now-utc", required=True)
    args = p.parse_args(argv)
    try:
        d = json.loads(args.dispatch_json.read_text(encoding="utf-8"))
        runtime = json.loads(args.fixture_policy_json.read_text(encoding="utf-8"))
        result = evaluate_shadow_gate(d, runtime, now=_utc(args.now_utc))
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result["decision"] in ("SHADOW_ELIGIBLE", "NO_WORK") else 2
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(json.dumps({"state": "SHADOW_GATE_ONLY", "decision": "BLOCKED_BAD_INPUT",
                          "reason": type(exc).__name__, "execution_authorized": False,
                          "http_requests": 0, "sqlite_reads": 0, "sqlite_writes": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
