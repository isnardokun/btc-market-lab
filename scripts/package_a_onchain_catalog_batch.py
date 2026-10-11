"""Deterministic RBN + Bitview catalogue-wide historical OFFLINE execution.

This is a feature-complete synthetic catalogue batch slice: discover ALL
installed RBN Tier-0 scalar metrics and Bitview configured daily series;
plan exact 14-day native windows, preflight ALL explicitly supplied frozen
responses, and archive across bounded 32-page source_runs while holding ONE
cooperative parent flock. Never a live downloader, scheduler or cutover.

ResearchBitcoin Tier-2 access was reported by the owner, but no entitlement
was verified here and NO undocumented Tier-2 metric/API is fabricated.
Bitview unit semantics cannot be inferred from config descriptions; records
are clearly marked SOURCE_UNIT_UNVERIFIED, not pretended to be known units.
"""
import hashlib
import json

from ingestion import config
from ingestion.package_a_contracts import SeriesSpec, utc
from ingestion.researchbitcoin_catalog import CATALOG
from scripts.package_a_batch_fixture import run_synthetic_batch, FrozenLegacyPage
from scripts.package_a_historical_window_planner import (
    plan_offline_historical_windows, FrozenPageBody, MAX_PAGES,
)
from scripts.package_a_ingest_fixture import _fixture_guard, inspect_fixture
from scripts.package_a_parent_lock import hold_offline_parent_lease
from scripts.market_r10e11_writer_exclusion import WriterLockBusy

_PROVIDERS=("researchbitcoin","bitview")
_MAX_STREAMS=64
_MAX_TOTAL_REQUESTS=128
_MAX_STAGED_RAW=32_000_000
_BITVIEW_UNIT="SOURCE_UNIT_UNVERIFIED"


def configured_onchain_series():
    """Read only locally installed, source-specific catalogue definitions."""
    streams=[]
    for metric,c in sorted(CATALOG.items()):
        # The source catalogue's scalar units/scales are exact RBN source
        # metadata, unlike unverified observed provider history.
        streams.append(SeriesSpec(
            "researchbitcoin",metric,"BTC","d1",c.unit,c.raw_scale))
    for metric,(slug,frequency,_) in sorted(config.SERIES.items()):
        if frequency!="1d" or metric!=slug:
            # Unmapped series must NEVER silently be attributed to another
            # stream or unknown source-native cadence.
            raise ValueError("BLOCKED_BITVIEW_UNRECONCILED_LEGACY_CATALOG")
        streams.append(SeriesSpec(
            "bitview",metric,"BTC","d1",_BITVIEW_UNIT,"native"))
    if len(streams)>_MAX_STREAMS or not streams:
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_SIZE")
    return tuple(streams)


def plan_offline_onchain_catalog(start,end,*,selection=None,
                                 max_total_requests=_MAX_TOTAL_REQUESTS,
                                 max_pages_per_series=MAX_PAGES):
    """All-or-nothing full source-catalogue plan with round-robin fairness.

    A 32-page persisted source-run may contain multiple source series.
    The plan is split into independent finite shards only at execution;
    no historical sources are silently omitted due to batch page limit.
    """
    if (type(max_total_requests) is not int or
            not 1<=max_total_requests<=_MAX_TOTAL_REQUESTS or
            type(max_pages_per_series) is not int or
            not 1<=max_pages_per_series<=MAX_PAGES):
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_REQUEST_BUDGET")
    known=configured_onchain_series()
    keys={(s.provider,s.metric):s for s in known}
    if selection is not None:
        if (type(selection) not in (list,tuple) or not selection or
                len(selection)>_MAX_STREAMS):
            raise ValueError("BLOCKED_ONCHAIN_CATALOG_SELECTION")
        if any(type(x) not in (list,tuple) or len(x)!=2 or
               type(x[0]) is not str or type(x[1]) is not str
               for x in selection):
            raise ValueError("BLOCKED_ONCHAIN_CATALOG_SELECTION")
        ids=[tuple(x) for x in selection]
        if len(ids)!=len(set(ids)) or any(k not in keys for k in ids):
            raise ValueError("BLOCKED_ONCHAIN_CATALOG_SELECTION")
        chosen=tuple(keys[k] for k in sorted(ids))
    else:
        chosen=known
    requests=[]
    subplans=[]
    for spec in chosen:
        plan=plan_offline_historical_windows(
            spec,start,end,max_pages=max_pages_per_series)
        if plan["state"]!="OFFLINE_SOURCE_WINDOWS_PLANNED":
            raise ValueError("BLOCKED_ONCHAIN_CATALOG_INCOMPLETE_SOURCE_PLAN")
        subplans.append({
            "provider":spec.provider,"metric":spec.metric,
            "native_window_plan_sha256":plan["window_plan_sha256"],
            "requests":len(plan["windows"]),
            "unit_source":("RBN_CATALOG_DECLARED" if spec.provider=="researchbitcoin"
                           else "BITVIEW_UNVERIFIED_SOURCE_UNIT"),
            "upstream_history_verified":False,
        })
        for win in plan["windows"]:
            requests.append({
                "provider":spec.provider,"metric":spec.metric,
                "symbol":spec.symbol,"frequency":spec.frequency,
                "unit":spec.unit,"raw_scale":spec.raw_scale,
                "start_utc":win["start_utc"],
                "end_exclusive_utc":win["end_exclusive_utc"],
                "source_page_ordinal":win["ordinal"],
                "request_contract_sha256":win["request_contract_sha256"],
            })
    # Interleave page 0 of every source before page 1 (and so forth).
    # This is deterministic and never violates any individual cursor.
    requests.sort(key=lambda x:(x["source_page_ordinal"],
                                x["provider"],x["metric"]))
    if len(requests)>max_total_requests:
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_TOTAL_REQUEST_BUDGET")
    digest=hashlib.sha256(json.dumps(
        {"source_plans":subplans,"ordered_requests":requests},
        sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
    return {
        "state":"OFFLINE_ONCHAIN_CATALOG_PLANNED",
        "onchain_plan_sha256":digest,
        "requested_streams":len(chosen),
        "requested_source_native_pages":len(requests),
        "source_plans":subplans,
        "ordered_requests":requests,
        "shard_count":(len(requests)+MAX_PAGES-1)//MAX_PAGES,
        "max_persisted_run_pages":MAX_PAGES,
        "no_catalog_stream_silently_truncated":True,
        "source_calendar":"BTC_ONCHAIN_DAILY_UTC_SYNTHETIC_ONLY",
        "bitview_source_units_verified":False,
        "tier2_entitlement_verified":False,
        "earliest_real_provider_data_verified":False,
        "external_http_allowed":False,
        "production_authorized":False,
    }


def run_offline_onchain_catalog(db_path,lock_dir,plan,responses,*,now):
    """All staged fake bodies checked BEFORE SQLite; one flock across shards.

    Individual pages have durable v3 source_runs with per-page PRECOMMIT
    provenance and stop after the first failed/empty/rejected page. A failed
    shard is not automatically retried and later shards are NOT touched.
    """
    utc(now)
    if (not isinstance(plan,dict) or
            plan.get("state")!="OFFLINE_ONCHAIN_CATALOG_PLANNED" or
            type(responses) not in (list,tuple)):
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_EXECUTION_PLAN")
    entries=plan.get("ordered_requests")
    if (type(entries) is not list or not entries or
            len(entries)>_MAX_TOTAL_REQUESTS or len(entries)!=len(responses)):
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_FROZEN_MANIFEST_LENGTH")
    if (not isinstance(plan.get("onchain_plan_sha256"),str) or
            len(plan["onchain_plan_sha256"])!=64):
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_PLAN_SHA")
    if len(entries)!=sum(p["requests"] for p in plan["source_plans"]):
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_PLAN_COUNTS")
    # Bind exact native plan and source catalogue again, no trusted
    # arbitrary objects/serialized bytes as runnable source instructions.
    original=plan_offline_onchain_catalog(
        entries[0]["start_utc"],
        # The last requested end may be for a shorter final page: use
        # absolute endpoints from all series instead.
        max(e["end_exclusive_utc"] for e in entries),
        selection=[(p["provider"],p["metric"]) for p in plan["source_plans"]],
        max_total_requests=_MAX_TOTAL_REQUESTS,
    )
    if original["onchain_plan_sha256"]!=plan["onchain_plan_sha256"]:
        raise ValueError("BLOCKED_ONCHAIN_CATALOG_PLAN_DRIFT")
    pages=[]
    staged_bytes=0
    for item,response in zip(entries,responses):
        if (not isinstance(response,FrozenPageBody) or
                type(response.body) is not bytes or
                (response.status_code is not None and
                 (type(response.status_code) is not int or
                  response.status_code not in (200,429,500,503) or
                  not 0<len(response.body)<=2_000_000)) or
                (response.status_code is None and response.body!=b"")):
            raise ValueError("BLOCKED_ONCHAIN_CATALOG_FROZEN_RESPONSE")
        staged_bytes+=len(response.body)
        if staged_bytes>_MAX_STAGED_RAW:
            raise ValueError("BLOCKED_ONCHAIN_CATALOG_RAW_BUDGET")
        spec=SeriesSpec(item["provider"],item["metric"],item["symbol"],
                        item["frequency"],item["unit"],item["raw_scale"])
        pages.append(FrozenLegacyPage(spec,item["start_utc"],
                                       item["end_exclusive_utc"],
                                       response.body,response.status_code))
    path=_fixture_guard(db_path,lock_dir)
    runs=[]
    try:
        with hold_offline_parent_lease(lock_dir):
            for off in range(0,len(pages),MAX_PAGES):
                shard=pages[off:off+MAX_PAGES]
                outcome=run_synthetic_batch(
                    path,lock_dir,shard,now=now,
                    max_pages=len(shard),_parent_lock_held=True)
                runs.append({
                    "shard_index":len(runs),
                    "absolute_first_page":off,
                    "source_run_id":outcome["run_id"],
                    "state":outcome["state"],
                    "requests_attempted":outcome["requests_attempted"],
                    "frozen_http_requests":outcome["frozen_http_requests"],
                    "new_observations":outcome["new_observations"],
                    "last_page":outcome["pages"][-1],
                    "persisted_source_run_lineage":True,
                })
                if outcome["state"]!="BATCH_OFFLINE_ACCEPTED":
                    break
            audit=inspect_fixture(path,lock_dir)
    except WriterLockBusy:
        return {
            "state":"BLOCKED_WRITER_BUSY",
            "frozen_http_requests":0,"new_observations":0,
            "source_runs":[],"sqlite_connections_started":0,
            "actual_history_downloaded":False,
            "production_authorized":False,
        }
    attempted=sum(x["requests_attempted"] for x in runs)
    done=len(runs)==plan["shard_count"] and all(
        x["state"]=="BATCH_OFFLINE_ACCEPTED" for x in runs)
    return {
        "state":("OFFLINE_ONCHAIN_CATALOG_ACCEPTED" if done else
                 "OFFLINE_ONCHAIN_CATALOG_BLOCKED"),
        "onchain_plan_sha256":plan["onchain_plan_sha256"],
        "catalog_streams_requested":plan["requested_streams"],
        "pages_planned":len(entries),
        "pages_attempted":attempted,
        "pages_not_attempted":len(entries)-attempted,
        "frozen_http_requests":sum(x["frozen_http_requests"] for x in runs),
        "new_observations":sum(x["new_observations"] for x in runs),
        "source_runs":runs,"audit":audit,
        "stopped_before_future_source_calls":not done,
        "bitview_source_units_verified":False,
        "tier2_entitlement_verified":False,
        "real_provider_history_downloaded":False,
        "historical_completeness":"NOT_VERIFIED",
        "external_http_allowed":False,
        "production_authorized":False,
    }
