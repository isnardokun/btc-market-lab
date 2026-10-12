"""Package A controlled multi-provider BATCH runner for synthetic fixtures only.

No HTTP/network, production DB paths, scheduler or CLI. Real API availability
and quota/retention have NOT been verified by this offline test harness.
"""
from dataclasses import dataclass
from pathlib import Path
from contextlib import nullcontext
import hashlib
import json
import sqlite3
import uuid

from ingestion.package_a_contracts import SeriesSpec, utc
from scripts.package_a_ingest_fixture import (
    FixtureResponse, run_fixture_page, inspect_fixture, _fixture_guard,
)
from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy
from scripts.package_a_ingest_fixture import LOCK_NAMESPACE
from scripts.package_a_parent_lock import (hold_offline_parent_lease,
    verify_offline_parent_lease)


@dataclass(frozen=True)
class FixturePage:
    source: SeriesSpec
    start: str
    end: str
    response: FixtureResponse


@dataclass(frozen=True)
class FrozenLegacyPage:
    """A strictly in-memory response to one actual legacy HTTP request builder."""
    source: SeriesSpec
    start: str
    end: str
    raw_bytes: bytes
    status_code: int | None = 200


def run_synthetic_batch(db_path, lock_dir, pages, *, now, max_pages=32,
                        _parent_lock_held=False):
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
    if type(_parent_lock_held) is not bool:
        raise ValueError("Invalid offline parent-lock marker")
    if _parent_lock_held:
        verify_offline_parent_lease(lock_dir)
    for page in pages:
        if not isinstance(page, (FixturePage, FrozenLegacyPage)) or not isinstance(page.source, SeriesSpec):
            raise ValueError("Typed synthetic fixture pages required")
        if isinstance(page, FixturePage):
            if not isinstance(page.response, FixtureResponse):
                raise ValueError("Explicit synthetic HTTP fixture required")
        elif (type(page.raw_bytes) is not bytes or
              page.status_code is not None and (
                  type(page.status_code) is not int or
                  page.status_code not in (200,429,500,503))):
            raise ValueError("Bounded frozen legacy response required")
        if utc(page.start) >= utc(page.end):
            raise ValueError("Invalid requested window")
    # Fresh run ID each invocation. Failed runs retain durable page evidence;
    # restarted batches are separate attempts, never rewritten histories.
    run_id=uuid.uuid4().hex
    outcomes = []
    for index, page in enumerate(pages):
        if isinstance(page, FrozenLegacyPage):
            result = run_mocked_legacy_page(
                db_path, lock_dir, page.source, page.start, page.end,
                raw_bytes=page.raw_bytes, status_code=page.status_code, now=now,
                _batch_run=(run_id,index,len(pages)),
                _parent_lock_held=_parent_lock_held)
        else:
            result = run_fixture_page(
                db_path, lock_dir, page.source, page.start, page.end,
                page.response, now=now,
                _batch_run=(run_id,index,len(pages)),
                _parent_lock_held=_parent_lock_held)
        outcomes.append({
            "index": index, "source": page.source.provider,
            "metric": page.source.metric, "state": result["state"],
            # Preserve classified failure reason in the whole-batch outcome,
            # not only the raw page receipt. This permits a retry policy to
            # distinguish 429/503/timeout/200-parse-rejection without
            # claiming that a rejected page advanced its cursor.
            "status": result.get("status","blocked"),
            "failure_class": result.get("failure_class"),
            "receipt_id": result.get("receipt_id"),
            "new_rows": result.get("new_rows",0),
            "cursor_advanced": result.get("cursor_advanced",False),
            "frozen_http_requests": result.get("frozen_http_requests",0),
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
        "run_id":run_id,
        "requests_planned": len(pages), "requests_attempted": len(outcomes),
        "remaining_requests_not_run": len(pages) - len(outcomes),
        "new_observations": sum(row["new_rows"] for row in outcomes),
        "pages": outcomes,
        "frozen_http_requests": sum(row["frozen_http_requests"] for row in outcomes),
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


def _request_identity(page):
    """Canonical original request-window identity and expected successful RAW."""
    if not isinstance(page, (FixturePage, FrozenLegacyPage)) or not isinstance(page.source, SeriesSpec):
        raise ValueError("Exact typed original page list required for recovery")
    begin,end=utc(page.start),utc(page.end)
    if begin >= end:
        raise ValueError("Invalid original recovery window")
    stamp=lambda dt: dt.isoformat().replace("+00:00","Z")
    if isinstance(page, FrozenLegacyPage):
        raw=page.raw_bytes
        frozen=True
        if (type(raw) is not bytes or
                not (0 < len(raw) <= 2_000_000 or
                     (page.status_code is None and raw == b""))):
            raise ValueError("Bounded frozen page RAW required")
        # A timed-out mock carries no HTTP response body by design.
        # Its empty *fixture* bytes are NOT a stored RAW payload, and the
        # original failure receipt has raw_sha256=NULL. Successful replay
        # prefixes still require a byte-exact nonempty RAW digest.
    else:
        frozen=False
        if not isinstance(page.response, FixtureResponse):
            raise ValueError("Synthetic response required")
        raw=(page.response.raw_bytes if page.response.raw_bytes is not None else
             json.dumps(page.response.payload,sort_keys=True,separators=(",",":"),
                        allow_nan=False).encode("utf-8")
             if page.response.payload is not None else None)
    return ((*(
        page.source.provider,page.source.metric,page.source.symbol,page.source.frequency),
        stamp(begin),stamp(end)),None if raw is None else hashlib.sha256(raw).hexdigest(),frozen)


def plan_synthetic_resume(db_path, lock_dir, pages, *, prior_run_id):
    """Read-only recovery preflight from durable source-run pages.

    Requires the original entire ordered fixture plan. Validates all persisted
    successful prefix receipts against EXACT intended source/window and RAW
    bytes. Failed last page is RETRIED (not counted as completed). Never opens
    HTTP, updates SQLite, infers provider availability, or invents prior pages.
    The returned plan alone is advisory: call recover_synthetic_batch for a
    lock-serialized atomic preflight + execution.
    """
    path=_fixture_guard(db_path,lock_dir)
    if (type(prior_run_id) is not str or len(prior_run_id)!=32 or
            any(ch not in "0123456789abcdef" for ch in prior_run_id)):
        raise ValueError("Exact persisted source-run UUID required")
    if type(pages) not in (tuple,list) or not 1 <= len(pages) <= 32:
        raise ValueError("Bounded original recovery plan required")
    plan=[_request_identity(page) for page in pages]
    if inspect_fixture(path,lock_dir)["state"]!="LOCAL_FIXTURE_INTEGRITY_PASS":
        raise ValueError("BLOCKED_RECOVERY_DB_INTEGRITY")
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        row=db.execute(
            "SELECT planned_pages,status FROM package_a_source_runs WHERE run_id=?",
            (prior_run_id,)).fetchone()
        if row is None:
            raise ValueError("BLOCKED_RECOVERY_UNKNOWN_RUN")
        planned,status=row
        if planned!=len(pages):
            raise ValueError("BLOCKED_RECOVERY_PLAN_LENGTH_DRIFT")
        rows=db.execute(
            "SELECT p.ordinal,p.receipt_status,r.provider,r.metric,r.symbol,"
            "r.frequency,r.start_utc,r.end_exclusive_utc,r.raw_sha256,"
            "r.status,pr.receipt_id IS NOT NULL "
            "FROM package_a_run_pages p "
            "JOIN package_a_receipts r ON r.receipt_id=p.receipt_id "
            "LEFT JOIN package_a_request_provenance pr ON pr.receipt_id=p.receipt_id "
            "WHERE p.run_id=? ORDER BY p.ordinal",(prior_run_id,)).fetchall()
        if not rows:
            raise ValueError("BLOCKED_RECOVERY_EMPTY_RUN")
        for ordinal,receipt_status,*stored in rows:
            if ordinal>=len(plan) or (
                    tuple(stored[:6])!=plan[ordinal][0] or
                    receipt_status!=stored[7] or
                    bool(stored[8])!=plan[ordinal][2]):
                raise ValueError("BLOCKED_RECOVERY_REQUEST_IDENTITY_DRIFT")
            if receipt_status=="ok":
                if stored[6]!=plan[ordinal][1]:
                    raise ValueError("BLOCKED_RECOVERY_SUCCESS_RAW_DRIFT")
                spec=pages[ordinal].source
                units=db.execute(
                    "SELECT DISTINCT unit,raw_scale FROM package_a_observations "
                    "WHERE provider=? AND metric=? AND symbol=? AND frequency=?",
                    (spec.provider,spec.metric,spec.symbol,spec.frequency)
                ).fetchall()
                if units != [(spec.unit,spec.raw_scale)]:
                    raise ValueError("BLOCKED_RECOVERY_UNIT_SCALE_DRIFT")
        resume_at=len(rows) if status=="running" else (
            len(pages) if status=="complete" else len(rows)-1)
        if not 0 <= resume_at <= len(pages):
            raise ValueError("BLOCKED_RECOVERY_BAD_PREFIX")
        return {
            "state":"RESUME_ALREADY_COMPLETE" if resume_at==len(pages)
                    else "RESUME_PLANNED_OFFLINE",
            "prior_run_id":prior_run_id,
            "prior_run_status":status,
            "resume_index":resume_at,
            "remaining_pages":len(pages)-resume_at,
            "verified_successful_prefix":resume_at,
            "source_integrity":"LOCAL_FIXTURE_INTEGRITY_PASS",
            "upstream_availability_verified":False,
            "live_http_requests":0,
            "production_authorized":False,
        }


def recover_synthetic_batch(db_path, lock_dir, pages, *, prior_run_id,
                            now, max_pages=32, _parent_lock_held=False):
    """Atomic preflight + fake page execution under ONE cooperative parent flock.

    Never runs a real client/DB. No TOCTOU window between reading source-run
    evidence and executing the next frozen HTTP page. The existing batch
    creates a NEW run ID; the original blocked/interrupted run stays intact.
    """
    _fixture_guard(db_path,lock_dir)
    if type(max_pages) is not int or not 1 <= max_pages <= 32:
        raise ValueError("Bounded recovery budget required")
    if type(_parent_lock_held) is not bool:
        raise ValueError("Invalid offline recovery parent-lock marker")
    if _parent_lock_held:
        verify_offline_parent_lease(lock_dir)
    try:
        with (nullcontext() if _parent_lock_held else
              hold_offline_parent_lease(lock_dir)):
            if _parent_lock_held:
                verify_offline_parent_lease(lock_dir)
            preflight=plan_synthetic_resume(db_path,lock_dir,pages,
                                             prior_run_id=prior_run_id)
            if preflight["remaining_pages"]==0:
                return {**preflight,"state":"RESUME_ALREADY_COMPLETE",
                        "requests_attempted":0,"frozen_http_requests":0,
                        "new_run_id":None}
            remaining=pages[preflight["resume_index"]:]
            # Never increase max_pages to bypass the original budget.
            result=run_synthetic_batch(db_path,lock_dir,remaining,now=now,
                      max_pages=min(max_pages,len(remaining)),
                      _parent_lock_held=True)
            return {**result,
                    "recovered_from_run_id":prior_run_id,
                    "new_run_id":result["run_id"],
                    "recovery_start_index":preflight["resume_index"],
                    "old_run_remains_immutable":True}
    except WriterLockBusy:
        return {"state":"BLOCKED_WRITER_BUSY","child_started":False,
                "frozen_http_requests":0,"requests_attempted":0,
                "sqlite_connections_started":0,"live_http_requests":0,
                "production_authorized":False}
