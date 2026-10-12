"""Package A source-aware historical COVERAGE MANIFEST over one caller-owned SQLite snapshot.

This is local evidence, not a provider-availability oracle. Every reported
request/observation/window is derived from immutable-ish persisted receipts,
normalized observations and explicit cursors. Calendar-specific absence is
never counted as a missing bar for FRED, Yahoo equities or funding events.

The caller owns the connection and read transaction. NO path, network,
credential, scheduler, SQLite mutation, or live API capability exists here.
"""
from collections import defaultdict
import datetime as dt

from ingestion.package_a_contracts import SeriesSpec, utc
from scripts.package_a_local_gap_candidates import scan_archived_gaps

STATUSES = ("ok", "failed", "empty", "rejected")


def _canonical(value):
    moment = utc(value)
    stamp = moment.isoformat().replace("+00:00", "Z")
    if value != stamp:
        raise ValueError("Noncanonical request or observation timestamp")
    return moment


def _source_semantics(provider, metric, symbol, frequency):
    if provider == "fred":
        return "FRED_OBSERVATION_WITH_RELEASE_VINTAGES", "EXTERNAL_RELEASE_CALENDAR_NOT_CHECKED"
    if metric == "funding_settled" and provider in ("binance", "bybit"):
        return "FUNDING_SETTLEMENT_EVENTS", "NO_FIXED_INTERVAL_ASSUMED"
    if provider == "yahoo" and symbol != "BTC-USD":
        return "YAHOO_EXCHANGE_SESSIONS", "MARKET_CALENDAR_NOT_CHECKED"
    if (provider in ("binance", "bybit") and metric == "open_interest"
            and frequency == "5m"):
        return "FIVE_MINUTE_OI_CANDLES", "LOCAL_FIXED_GRID_ONLY"
    if provider == "yahoo" and symbol == "BTC-USD" and frequency == "d1":
        return "BITCOIN_DAILY_SESSION", "LOCAL_FIXED_GRID_ONLY"
    if provider in ("bitview", "researchbitcoin") and frequency == "d1":
        return "BITCOIN_ONCHAIN_DAILY", "LOCAL_FIXED_GRID_ONLY"
    return "UNKNOWN_OR_CUSTOM_CADENCE", "NO_GRID_INFERRED"


def _union(windows):
    """Union only SUCCESSFUL received request windows (not provider coverage)."""
    spans = []
    for begin, end in sorted(windows):
        if spans and begin <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((begin,end))
    return spans


def source_aware_manifest(conn, *, max_streams=128, max_grid_slots=4000,
                          max_grid_windows=100):
    """Return bounded local source×metric×frequency evidence with SAFE gap hints.

    Caller should run within the SAME audit snapshot as RAW→SQL provenance,
    FK and request audits. This function does not claim source availability
    even when all local requested slots happen to be archived.
    """
    if (type(max_streams) is not int or not 1 <= max_streams <= 512
            or type(max_grid_slots) is not int or not 1 <= max_grid_slots <= 10000
            or type(max_grid_windows) is not int or not 1 <= max_grid_windows <= 1000):
        raise ValueError("Bounded manifest budget required")
    all_receipts = list(conn.execute(
        "SELECT provider,metric,symbol,frequency,start_utc,end_exclusive_utc,"
        "status,http_status,(raw_payload IS NOT NULL),raw_sha256 "
        "FROM package_a_receipts ORDER BY rowid"))
    all_points = list(conn.execute(
        "SELECT provider,metric,symbol,frequency,observed_utc,dimension_key,"
        "unit,raw_scale FROM package_a_observations"))
    all_cursors = dict((
        (p,m,s,f), end
        ) for p,m,s,f,end in conn.execute(
        "SELECT provider,metric,symbol,frequency,end_exclusive_utc "
        "FROM package_a_cursors"))
    seen = set((p,m,s,f) for p,m,s,f,*_ in all_receipts)
    seen.update((p,m,s,f) for p,m,s,f,*_ in all_points)
    seen.update(all_cursors)
    if len(seen) > max_streams:
        return {
            "state":"BLOCKED_SOURCE_MANIFEST_STREAM_BUDGET",
            "streams":[],
            "stream_count":len(seen),
            "contract_errors":1,
            "historical_completeness":"NOT_VERIFIED",
            "provider_availability":"NOT_CHECKED",
            "production_authorized":False,
        }

    buckets = {}
    for key in sorted(seen):
        source,metric,symbol,freq=key
        native,calendar=_source_semantics(*key)
        buckets[key]={
            "provider":source, "metric":metric, "symbol":symbol,"frequency":freq,
            "source_native_semantics":native, "calendar_assumption":calendar,
            "attempted_requests":0,
            "request_status_counts":{status:0 for status in STATUSES},
            "requested_first_utc":None,"requested_end_exclusive_utc":None,
            "successful_request_windows":[],
            "successful_request_window_count":0,
            "successful_raw_receipts":0,
            "successful_missing_raw_receipts":0,
            "http_error_with_raw":0,
            "transport_failures_without_response":0,
            "archived_rows":0,"distinct_observation_dates":0,
            "distinct_dimension_keys":0,
            "archived_first_utc":None,"archived_last_utc":None,
            "archived_unit":None,"archived_raw_scale":None,
            "checkpoint_end_exclusive_utc":all_cursors.get(key),
            "local_gap_evidence":None,
            "local_grid_state":"NOT_ASSESSED",
            "local_integrity_errors":0,
            "earliest_provider_verified_utc":None,
            "latest_provider_verified_utc":None,
            "entitlement_status":"NOT_VERIFIED",
            "provider_availability":"NOT_CHECKED",
            "historical_completeness":"NOT_VERIFIED",
            "live_request_count":0,
            "request_ready":False,
            "production_authorized":False,
        }

    durations=defaultdict(list)
    point_rows=defaultdict(list)
    for p,m,s,f,observed,dimension,unit,scale in all_points:
        key=(p,m,s,f)
        point_rows[key].append((observed,dimension,unit,scale))

    for p,m,s,f,start,end,status,http,has_raw,sha in all_receipts:
        key=(p,m,s,f)
        bucket=buckets[key]
        bucket["attempted_requests"]+=1
        if status not in STATUSES:
            bucket["local_integrity_errors"]+=1
            continue
        bucket["request_status_counts"][status]+=1
        try:
            begin,stop=_canonical(start),_canonical(end)
            if begin>=stop:
                raise ValueError("Reversed receipt window")
        except (ValueError,TypeError,OverflowError):
            bucket["local_integrity_errors"]+=1
            continue
        bucket["requested_first_utc"]=(
            min(start,bucket["requested_first_utc"])
            if bucket["requested_first_utc"] else start)
        bucket["requested_end_exclusive_utc"]=(
            max(end,bucket["requested_end_exclusive_utc"])
            if bucket["requested_end_exclusive_utc"] else end)
        if status=="ok":
            if not has_raw or sha is None:
                bucket["successful_missing_raw_receipts"]+=1
                bucket["local_integrity_errors"]+=1
            else:
                bucket["successful_raw_receipts"]+=1
            durations[key].append((begin,stop))
        elif status=="failed":
            if http is None:
                bucket["transport_failures_without_response"]+=1
            elif has_raw:
                bucket["http_error_with_raw"]+=1

    errors=0
    output=[]
    for key in sorted(seen):
        bucket=buckets[key]
        raw_points=point_rows.get(key,[])
        bucket["archived_rows"]=len(raw_points)
        if raw_points:
            stamps=[row[0] for row in raw_points]
            bucket["archived_first_utc"]=min(stamps)
            bucket["archived_last_utc"]=max(stamps)
            bucket["distinct_observation_dates"]=len(set(stamps))
            bucket["distinct_dimension_keys"]=len(set(row[1] for row in raw_points))
            units={(row[2],row[3]) for row in raw_points}
            if len(units)==1:
                bucket["archived_unit"],bucket["archived_raw_scale"]=next(iter(units))
            else:
                bucket["local_integrity_errors"]+=1
            for stamp in stamps:
                try:
                    _canonical(stamp)
                except (ValueError,TypeError,OverflowError):
                    bucket["local_integrity_errors"]+=1
        if bucket["checkpoint_end_exclusive_utc"] is not None:
            try:
                _canonical(bucket["checkpoint_end_exclusive_utc"])
            except (ValueError,TypeError,OverflowError):
                bucket["local_integrity_errors"]+=1
        combined=_union(durations.get(key,[]))
        bucket["successful_request_window_count"]=len(combined)
        bucket["successful_request_windows"]=[
            {"start_utc":a.isoformat().replace("+00:00","Z"),
             "end_exclusive_utc":b.isoformat().replace("+00:00","Z"),
             "classification":"LOCAL_SUCCESS_RECEIPT_ONLY"}
            for a,b in combined
        ]
        if bucket["archived_rows"] and not combined:
            bucket["local_integrity_errors"]+=1
        if bucket["successful_missing_raw_receipts"]:
            bucket["local_integrity_errors"]+=1
        bucket["raw_receipt_coverage"]=(
            "LOCAL_SUCCESS_RECEIPTS_PRESENT"
            if bucket["successful_raw_receipts"] and not
               bucket["successful_missing_raw_receipts"] else
            "MISSING_SUCCESS_RAW" if bucket["successful_missing_raw_receipts"] else
            "NO_SUCCESS_RECEIPTS")
        if bucket["calendar_assumption"]=="LOCAL_FIXED_GRID_ONLY":
            if (bucket["archived_unit"] is None or
                    bucket["requested_first_utc"] is None):
                bucket["local_grid_state"]="NOT_SCANNED_NO_ARCHIVED_CONTRACT"
            else:
                a=utc(bucket["requested_first_utc"])
                b=utc(bucket["requested_end_exclusive_utc"])
                step=dt.timedelta(minutes=5) if key[3]=="5m" else dt.timedelta(days=1)
                slots=(b-a)/step
                if slots>max_grid_slots or slots!=int(slots):
                    bucket["local_grid_state"]="NOT_SCANNED_GRID_BUDGET_OR_ALIGNMENT"
                else:
                    spec=SeriesSpec(*key,bucket["archived_unit"],
                                    bucket["archived_raw_scale"])
                    scan=scan_archived_gaps(conn,spec,bucket["requested_first_utc"],
                        bucket["requested_end_exclusive_utc"],
                        max_expected=max_grid_slots,max_windows=max_grid_windows)
                    bucket["local_grid_state"]=scan["state"]
                    bucket["local_gap_evidence"]={
                        "state":scan["state"],
                        "local_missing_candidate_slots":scan.get("missing_candidate_slots"),
                        "windows":scan.get("candidates",[]),
                        "request_ready":False,"provider_verified":False}
                    if scan["state"]=="BLOCKED_SOURCE_OBSERVATION_DRIFT":
                        bucket["local_integrity_errors"]+=1
                    # Too many candidate windows is a planning budget
                    # limitation, NOT evidence that archived rows are corrupt.
        else:
            bucket["local_grid_state"]="NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS"
        errors+=bucket["local_integrity_errors"]
        output.append(bucket)
    return {
        "state":"SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS" if not errors else
                "SOURCE_MANIFEST_LOCAL_EVIDENCE_FAIL",
        "stream_count":len(output),
        "contract_errors":errors,
        "streams":output,
        "historical_completeness":"NOT_VERIFIED",
        "provider_availability":"NOT_CHECKED",
        "entitlement_verified":False,
        "live_request_count":0,
        "production_authorized":False,
    }
