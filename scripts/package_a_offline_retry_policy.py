"""Bounded OFFLINE retry rehearsal for Package A persisted historical source runs.

Not a scheduler, live downloader or delay executor. Explicit caller-provided
frozen response manifests are the ONLY input, with a deterministic virtual
backoff recommendation. Every attempt runs via existing v3 source_run replay
checks, one global cooperative lock and RAW->SQL precommit audits.
"""
from dataclasses import dataclass
import hashlib

from scripts.package_a_historical_window_planner import bind_offline_frozen_history
from scripts.package_a_batch_fixture import run_synthetic_batch, recover_synthetic_batch
from scripts.package_a_ingest_fixture import _fixture_guard
from ingestion.package_a_contracts import utc

_MAX_ATTEMPTS = 4
_MAX_FAKE_REQUESTS = 128
_MAX_STAGED_BYTES = 32_000_000
_RETRYABLE = frozenset({
    "SYNTHETIC_HTTP_429", "SYNTHETIC_HTTP_500", "SYNTHETIC_HTTP_503",
    "SYNTHETIC_TRANSPORT_TIMEOUT",
})


@dataclass(frozen=True)
class OfflineRetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: int = 5
    max_backoff_seconds: int = 60
    max_fake_requests: int = 128

    def __post_init__(self):
        if (type(self.max_attempts) is not int or
                not 1 <= self.max_attempts <= _MAX_ATTEMPTS or
                type(self.initial_backoff_seconds) is not int or
                not 1 <= self.initial_backoff_seconds <= 120 or
                type(self.max_backoff_seconds) is not int or
                not self.initial_backoff_seconds <= self.max_backoff_seconds <= 600 or
                type(self.max_fake_requests) is not int or
                not 1 <= self.max_fake_requests <= _MAX_FAKE_REQUESTS):
            raise ValueError("BLOCKED_RETRY_POLICY_BUDGET")


def _advice(last_page, *, completed_attempts, policy, plan_sha):
    """Return an advisory next action, NOT a timer or permission to fetch."""
    reason = last_page.get("failure_class")
    eligible = (
        last_page.get("state") == "PAGE_NOT_COMMITTED" and
        last_page.get("status") == "failed" and
        reason in _RETRYABLE)
    if not eligible:
        return {
            "state": "RETRY_DENIED_PERMANENT_OR_LOCK",
            "reason": reason or last_page.get("state"),
            "retry_allowed": False, "virtual_delay_seconds": None,
            "clock_wait_executed": False, "real_http_authorized": False,
        }
    if completed_attempts >= policy.max_attempts:
        return {
            "state": "RETRY_BUDGET_EXHAUSTED",
            "reason": reason, "retry_allowed": False,
            "virtual_delay_seconds": None,
            "clock_wait_executed": False, "real_http_authorized": False,
        }
    # Deterministic jitter: 0..3s from immutable plan + failure position.
    # No real sleep; a future live operator would need independently verified
    # Retry-After/server quota policies. Do NOT parse opaque 429 body as headers.
    seed = (plan_sha + ":" + str(last_page["index"]) +
            ":" + str(completed_attempts)).encode()
    jitter = hashlib.sha256(seed).digest()[0] % 4
    backoff = min(
        policy.max_backoff_seconds,
        policy.initial_backoff_seconds * (2 ** (completed_attempts-1))
        + jitter,
    )
    return {
        "state": "RETRY_ADVISORY_ONLY",
        "reason": reason, "retry_allowed": True,
        "virtual_delay_seconds": backoff,
        "next_attempt_number": completed_attempts+1,
        "clock_wait_executed": False,
        "real_http_authorized": False,
        "upstream_retry_after_verified": False,
    }


def plan_offline_retry_advice(result, *, policy, completed_attempts,
                              window_plan_sha256):
    """Pure fail-closed classifier over exactly one committed batch outcome."""
    if not isinstance(policy, OfflineRetryPolicy):
        raise ValueError("Explicit retry policy required")
    if (type(completed_attempts) is not int or
            not 1 <= completed_attempts <= policy.max_attempts or
            type(window_plan_sha256) is not str or
            len(window_plan_sha256) != 64 or
            any(ch not in "0123456789abcdef" for ch in window_plan_sha256)):
        raise ValueError("Invalid deterministic retry identity")
    if not isinstance(result, dict):
        raise ValueError("Source-run outcome required")
    if result.get("state") in ("BATCH_OFFLINE_ACCEPTED", "RESUME_ALREADY_COMPLETE"):
        return {
            "state": "RETRY_NOT_REQUIRED",
            "retry_allowed": False, "virtual_delay_seconds": None,
            "clock_wait_executed": False, "real_http_authorized": False,
        }
    pages = result.get("pages")
    if not isinstance(pages, list) or not pages:
        return {
            "state": "RETRY_DENIED_NO_PERSISTED_PAGE",
            "retry_allowed": False, "virtual_delay_seconds": None,
            "clock_wait_executed": False, "real_http_authorized": False,
        }
    last = pages[-1]
    if type(last) is not dict:
        raise ValueError("Malformed failed source-run page")
    return _advice(last,completed_attempts=completed_attempts,
                   policy=policy,plan_sha=window_plan_sha256)


def run_offline_retry_rehearsal(db_path, lock_dir, spec, start, end,
                                response_attempts, *, now,
                                policy=OfflineRetryPolicy(), max_pages=32):
    """Execute ONLY explicitly staged fake-HTTP attempt manifests.

    Every attempt must supply a COMPLETE source-native response manifest.
    Even attempts never reached are validated before SQLite opens. The
    persisted source-run pages of subsequent attempts describe only their
    own suffix. `base_index` maps each suffix back to ORIGINAL window indices,
    preventing erroneous reuse of the initial total-page count after a
    second consecutive failure.
    """
    if not isinstance(policy, OfflineRetryPolicy):
        raise ValueError("Explicit bounded offline retry policy required")
    utc(now)
    _fixture_guard(db_path, lock_dir)
    if (type(response_attempts) not in (list,tuple)
            or not 1 <= len(response_attempts) <= policy.max_attempts):
        raise ValueError("BLOCKED_RETRY_ATTEMPT_MANIFEST_COUNT")
    staged=[]
    plan=None
    staged_bytes=0
    for manifest in response_attempts:
        current,pages=bind_offline_frozen_history(
            spec,start,end,manifest,max_pages=max_pages)
        if plan is not None and current["window_plan_sha256"]!=plan["window_plan_sha256"]:
            raise ValueError("BLOCKED_RETRY_SOURCE_PLAN_DRIFT")
        plan=current
        staged.append(pages)
        staged_bytes+=sum(len(page.raw_bytes) for page in pages)
    if (staged_bytes > _MAX_STAGED_BYTES or
            len(staged)*len(staged[0]) > policy.max_fake_requests):
        raise ValueError("BLOCKED_RETRY_TOTAL_MOCK_RESOURCE_BUDGET")

    results=[]
    previous_run_id=None
    current_base=0
    executed_fake=0
    last_outcome=None
    decision=None
    for attempt_idx,pages in enumerate(staged):
        if previous_run_id is None:
            outcome=run_synthetic_batch(
                db_path,lock_dir,pages,now=now,max_pages=max_pages)
            new_base=0
        else:
            # Prior NEW run owns pages[current_base:], not original length.
            outcome=recover_synthetic_batch(
                db_path,lock_dir,pages[current_base:],
                prior_run_id=previous_run_id,now=now,max_pages=max_pages)
            new_base=current_base+outcome.get("recovery_start_index",0)

        executed_fake+=outcome.get("frozen_http_requests",0)
        rows=outcome.get("pages") or []
        completed=attempt_idx+1
        decision=plan_offline_retry_advice(
            outcome,policy=policy,completed_attempts=completed,
            window_plan_sha256=plan["window_plan_sha256"])
        # A busy global lock can return an in-memory batch UUID without
        # ever opening SQLite. Never advertise that UUID as a durable run.
        source_run_id=(
            outcome.get("new_run_id") or outcome.get("run_id")
            if any(row.get("receipt_id") for row in rows) else None)
        result_record={
            "attempt_number":completed,
            "source_run_id":source_run_id,
            "prior_run_id":previous_run_id,
            "source_window_base_index":new_base,
            "state":outcome["state"],
            "fake_http_requests":outcome.get("frozen_http_requests",0),
            "new_observations":outcome.get("new_observations",0),
            "last_page":({
                **rows[-1], "original_window_index":new_base+rows[-1]["index"],
            } if rows else None),
            "retry_advice":decision,
        }
        results.append(result_record)
        last_outcome=outcome
        if not decision["retry_allowed"]:
            break
        previous_run_id=outcome.get("new_run_id") or outcome.get("run_id")
        if not previous_run_id:
            raise ValueError("Retryable failure requires durable source-run")
        current_base=new_base

    completed_ok = last_outcome["state"] in (
        "BATCH_OFFLINE_ACCEPTED","RESUME_ALREADY_COMPLETE")
    exhausted = decision["state"]=="RETRY_BUDGET_EXHAUSTED"
    return {
        "state": (
            "OFFLINE_RETRY_REHEARSAL_ACCEPTED" if completed_ok else
            "OFFLINE_RETRY_REHEARSAL_EXHAUSTED" if exhausted else
            "OFFLINE_RETRY_REHEARSAL_AWAITING_OPERATOR" if decision["retry_allowed"]
            else "OFFLINE_RETRY_REHEARSAL_STOPPED"
        ),
        "window_plan_sha256":plan["window_plan_sha256"],
        "attempts_staged":len(staged),
        "attempts_executed":len(results),
        "mock_requests_executed":executed_fake,
        "attempts":results,
        "final_source_run_id":results[-1]["source_run_id"],
        "final_source_audit":(
            last_outcome.get("audit") if last_outcome is not None else None),
        "final_retry_advice":decision,
        "virtual_backoff_only":True, "clock_wait_executed":False,
        "real_http_requests":0, "provider_history_verified":False,
        "production_authorized":False,
    }
