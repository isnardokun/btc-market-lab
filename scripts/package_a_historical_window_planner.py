"""Package A offline SOURCE-SPECIFIC historical window planning and execution.

This is NOT a live downloader. Explicitly supplied frozen HTTP response bytes
are matched to complete, bounded, contiguous source-native request windows,
then passed to the existing lock/RAW/SQL/lineage/checkpoint integrated batch.

Limits below are conservative synthetic policy budgets, NOT confirmation of
a provider's actual API retention, pagination contract, rate limits or tier.
"""
from dataclasses import dataclass
import datetime as dt
import hashlib
import json

from ingestion.package_a_contracts import SeriesSpec, utc
from scripts.package_a_frozen_legacy_transport import _request_for, MAX_RAW
from scripts.package_a_batch_fixture import (
    FrozenLegacyPage, run_synthetic_batch, recover_synthetic_batch,
)

MAX_PAGES = 32


@dataclass(frozen=True)
class FrozenPageBody:
    """Frozen synthetic response belonging to exactly one planned window."""
    body: bytes
    status_code: int | None = 200


def _iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def _plan_denied(spec, reason, *, candidate_pages=None):
    return {
        "state": reason, "source": spec.provider, "metric": spec.metric,
        "symbol": spec.symbol, "frequency": spec.frequency,
        "candidate_pages": candidate_pages, "windows": [],
        "request_ready": False, "external_history_verified": False,
        "provider_entitlement_verified": False, "live_request_count": 0,
        "production_authorized": False,
    }


def plan_offline_historical_windows(spec, start, end, *, max_pages=MAX_PAGES):
    """Partition one exact interval or FAIL CLOSED without a partial plan.

    The plan covers [start,end) completely with contiguous source-native
    windows. Funding events, FRED release/vintage calendars, and Yahoo's
    legacy 'range=max' endpoint require provider-specific pagination
    verification; they are intentionally not silently window-sliced.
    """
    if not isinstance(spec, SeriesSpec):
        raise ValueError("Typed provider source required")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError("Strict source plan page budget must be 1..32")
    first,last=utc(start),utc(end)
    if first>=last:
        raise ValueError("Half-open window must be nonempty")
    if (type(start) is not str or type(end) is not str or
            _iso(first) != start.replace("+00:00","Z") or
            _iso(last) != end.replace("+00:00","Z")):
        raise ValueError("Source planning forbids fractional/noncanonical timestamps")

    if spec.provider == "researchbitcoin":
        step=dt.timedelta(days=1)
        page_slots=14  # package source parser's tested <=14-day contract
        policy="RBN_SYNTHETIC_14D_NOT_UPSTREAM_VERIFIED"
    elif spec.provider == "bitview":
        step=dt.timedelta(days=1)
        page_slots=14  # conservative local policy, not upstream assertion
        policy="BITVIEW_CONSERVATIVE_14D_NOT_UPSTREAM_VERIFIED"
    elif spec.provider in ("binance","bybit") and spec.metric=="open_interest":
        step=dt.timedelta(minutes=5)
        # Bound by parameter values already constructed by our mock builder.
        page_slots=500 if spec.provider=="binance" else 200
        policy=(
            "BINANCE_OI_SYNTHETIC_QUERY_LIMIT_500"
            if spec.provider=="binance" else "BYBIT_OI_SYNTHETIC_QUERY_LIMIT_200")
    else:
        return _plan_denied(spec,"BLOCKED_SOURCE_NATIVE_PAGINATION_UNVERIFIED")

    if ((last-first) % step != dt.timedelta(0) or
            (step >= dt.timedelta(days=1) and
             (first.time()!=dt.time() or last.time()!=dt.time())) or
            (step == dt.timedelta(minutes=5) and
             (int(first.timestamp())%300!=0 or int(last.timestamp())%300!=0))):
        raise ValueError("Source window is not exactly aligned to native calendar")

    # Require the actual mock request-builder to recognize the provider,
    # specific BTC symbol, supported series, frequency, unit and scale.
    # This never issues HTTP.
    _request_for(spec,_iso(first),_iso(first+step))
    total_slots=(last-first)//step
    count=(total_slots+page_slots-1)//page_slots
    if count>max_pages:
        return _plan_denied(spec,"BLOCKED_HISTORICAL_PAGE_BUDGET",
                            candidate_pages=count)

    spans=[]
    pos=first
    for idx in range(count):
        nxt=min(pos+step*page_slots,last)
        endpoint,params=_request_for(spec,_iso(pos),_iso(nxt))
        if pos>=nxt:
            raise ValueError("Nonadvancing historical page")
        # Bind the plan to the exact source request path/query, not only
        # provider name and time bounds. Never persist query text in the
        # planner result; sensitive params could be added by future clients.
        request_identity=json.dumps(
            [endpoint, sorted(params.items())],
            separators=(",",":"),ensure_ascii=True)
        contract_sha=hashlib.sha256(request_identity.encode()).hexdigest()
        spans.append({
            "request_contract_sha256":contract_sha,
            "ordinal":idx,"start_utc":_iso(pos),"end_exclusive_utc":_iso(nxt),
            "native_slots":(nxt-pos)//step,
            "request_origin":endpoint.split("/")[2],
            "endpoint_path":"/"+"/".join(endpoint.split("/")[3:]),
            "source_query_params_frozen":True,
            "raw_supplied":False,
            "request_ready":False,
        })
        pos=nxt
    if pos!=last or not spans or spans[0]["start_utc"]!=_iso(first) or spans[-1]["end_exclusive_utc"]!=_iso(last):
        raise ValueError("Internal planner violated exact half-open coverage")
    digest=hashlib.sha256(json.dumps(
        [spec.provider,spec.metric,spec.symbol,spec.frequency,
         spec.unit,spec.raw_scale,policy,
         [(w["start_utc"],w["end_exclusive_utc"],
           w["request_contract_sha256"]) for w in spans]],
        separators=(",",":")).encode()).hexdigest()
    return {
        "state":"OFFLINE_SOURCE_WINDOWS_PLANNED",
        "source":spec.provider,"metric":spec.metric,"symbol":spec.symbol,
        "frequency":spec.frequency,"calendar_step_seconds":int(step.total_seconds()),
        "synthetic_max_rows_per_window":page_slots,
        "page_limit_policy":policy,
        "requested_start_utc":_iso(first),
        "requested_end_exclusive_utc":_iso(last),
        "requested_native_slots":total_slots,
        "candidate_pages":count,
        "windows":spans,
        "window_plan_sha256":digest,
        "source_request_contract":"OFFLINE_LEGACY_BUILDER_ONLY",
        "request_ready":False,"external_history_verified":False,
        "provider_entitlement_verified":False,
        "live_request_count":0,"production_authorized":False,
    }


def bind_offline_frozen_history(spec,start,end,responses,*,max_pages=MAX_PAGES):
    """Materialize exactly planned windows from bounded, explicitly fake bytes.

    Validate the ENTIRE response manifest before a caller can initialize
    SQLite. No callback, URL, credential, real HTTP or production DB path.
    """
    plan=plan_offline_historical_windows(spec,start,end,max_pages=max_pages)
    if plan["state"]!="OFFLINE_SOURCE_WINDOWS_PLANNED":
        raise ValueError(plan["state"])
    if (type(responses) not in (list,tuple) or
            len(responses)!=len(plan["windows"])):
        raise ValueError("BLOCKED_HISTORICAL_PAGE_RESPONSE_COUNT")
    pages=[]
    for item,window in zip(responses,plan["windows"]):
        if (not isinstance(item,FrozenPageBody) or
                type(item.body) is not bytes or
                type(item.status_code) not in (int,type(None)) or
                (item.status_code is not None and
                 item.status_code not in (200,429,500,503)) or
                (item.status_code is None and item.body!=b"") or
                (item.status_code is not None and
                 not 0 < len(item.body) <= MAX_RAW)):
            raise ValueError("BLOCKED_HISTORICAL_FROZEN_RESPONSE_CONTRACT")
        pages.append(FrozenLegacyPage(
            spec,window["start_utc"],window["end_exclusive_utc"],
            item.body,item.status_code))
    return plan,tuple(pages)


def run_offline_planned_history(db_path, lock_dir, spec,start,end,responses,*,
                                now,max_pages=MAX_PAGES):
    """Planner -> native fake requests -> bounded batch -> audited SQLite."""
    plan,pages=bind_offline_frozen_history(
        spec,start,end,responses,max_pages=max_pages)
    result=run_synthetic_batch(db_path,lock_dir,pages,now=now,max_pages=max_pages)
    return {
        **result,"window_plan_sha256":plan["window_plan_sha256"],
        "source_window_plan":plan,
        "source_history_availability":"NOT_VERIFIED",
        "real_provider_pagination_verified":False,
    }


def recover_offline_planned_history(db_path,lock_dir,spec,start,end,responses,*,
                                    prior_run_id,now,max_pages=MAX_PAGES):
    """Verify planned source windows, then recover persisted v3 run in-place.

    Frozen response bytes for the successful old prefix must match exactly;
    the failed/missing suffix may supply corrected fake HTTP outcomes.
    """
    plan,pages=bind_offline_frozen_history(
        spec,start,end,responses,max_pages=max_pages)
    result=recover_synthetic_batch(
        db_path,lock_dir,pages,prior_run_id=prior_run_id,
        now=now,max_pages=max_pages)
    return {
        **result,"window_plan_sha256":plan["window_plan_sha256"],
        "source_window_plan":plan,
        "source_history_availability":"NOT_VERIFIED",
        "real_provider_pagination_verified":False,
    }
