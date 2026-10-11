"""Package A controlled multi-provider BATCH runner for synthetic fixtures only.

No HTTP/network, production DB paths, scheduler or CLI. Real API availability
and quota/retention have NOT been verified by this offline test harness.
"""
from dataclasses import dataclass
from pathlib import Path

from ingestion.package_a_contracts import SeriesSpec, utc
from scripts.package_a_ingest_fixture import (
    FixtureResponse, run_fixture_page, inspect_fixture, _fixture_guard,
)


@dataclass(frozen=True)
class FixturePage:
    source: SeriesSpec
    start: str
    end: str
    response: FixtureResponse


def run_synthetic_batch(db_path, lock_dir, pages, *, now, max_pages=32):
    """Process bounded fixture pages serially with fail-stop/checkpoint behavior.

    Each page is a separate short SQL transaction under the SAME global lock
    namespace; no single write transaction spans the complete batch. This
    is an offline rehearsal, not a batch API client.
    """
    if (type(max_pages) is not int or max_pages < 1 or max_pages > 32):
        raise ValueError("Synthetic batch budget must be 1..32")
    if (not isinstance(pages, (tuple,list)) or not 1 <= len(pages) <= max_pages):
        raise ValueError("Nonempty bounded fixture page list required")
    utc(now)
    _fixture_guard(db_path, lock_dir)
    for page in pages:
        if not isinstance(page, FixturePage) or not isinstance(page.source, SeriesSpec):
            raise ValueError("Typed synthetic fixture pages required")
        if not isinstance(page.response, FixtureResponse):
            raise ValueError("Explicit synthetic HTTP fixture required")
        if utc(page.start) >= utc(page.end):
            raise ValueError("Invalid requested window")
    outcomes = []
    for index, page in enumerate(pages):
        result = run_fixture_page(
            db_path, lock_dir, page.source, page.start, page.end,
            page.response, now=now)
        outcomes.append({
            "index": index, "source": page.source.provider,
            "metric": page.source.metric, "state": result["state"],
            "receipt_id": result.get("receipt_id"),
            "new_rows": result.get("new_rows",0),
            "cursor_advanced": result.get("cursor_advanced",False),
        })
        if result["state"] != "PAGE_COMMITTED_OFFLINE":
            break

    # In a fresh fixture a busy lock can prevent table creation; skip the
    # readback rather than opening SQLite after a rejection.
    if outcomes[-1]["state"] == "BLOCKED_WRITER_BUSY":
        quality = None
    else:
        quality = inspect_fixture(db_path, lock_dir)
    fully_applied = (
        len(outcomes) == len(pages)
        and all(row["state"] == "PAGE_COMMITTED_OFFLINE" for row in outcomes)
        and quality is not None
        and quality["state"] == "LOCAL_FIXTURE_INTEGRITY_PASS"
    )
    return {
        "state": "BATCH_OFFLINE_ACCEPTED" if fully_applied else "BATCH_BLOCKED",
        "requests_planned": len(pages), "requests_attempted": len(outcomes),
        "remaining_requests_not_run": len(pages) - len(outcomes),
        "new_observations": sum(row["new_rows"] for row in outcomes),
        "pages": outcomes,
        "audit": quality,
        "unprocessed_pages_may_be_resumed": not fully_applied,
        "historical_completeness": "NOT_VERIFIED",
        "real_provider_windows_verified": False,
        "real_tier2_entitlement_verified": False,
        "production_authorized": False,
        "http_real_requests": 0,
        "sqlite_productive_writes": 0,
        "global_host_writer_exclusion_verified": False,
    }
