"""Atomic Yahoo OHLCV fan-out: ONE frozen legacy fetch -> FIVE independent streams.

No real transport, no production DB, no arbitrary opener, no CLI. Applies
only to the explicit marker-protected SQLite fixture and synthetic bytes.
All five receipts, coverage rows, cursors and request provenance commit in
ONE SQLite transaction after the shared PRECOMMIT quality gate. No five
independent HTTP calls and no partial OHLCV ingestion.
"""
import hashlib
import json
import sqlite3
from urllib.error import HTTPError

from ingestion.package_a_contracts import SeriesSpec, utc
from ingestion.package_a_legacy_bridge import reconciled_fixture_points
from scripts.package_a_frozen_legacy_transport import (
    FrozenLegacyHTTP, _legacy_fake_fetch, _request_for,
)
from scripts.package_a_ingest_fixture import (
    _fixture_guard, require_offline_precommit_integrity,
)
from scripts.package_a_parent_lock import hold_offline_parent_lease
from scripts.market_r10e11_writer_exclusion import WriterLockBusy
from storage import package_a_ledger as ledger


METRICS=("open","high","low","close","volume")
SYMBOLS=frozenset(("BTC-USD","SPY","GLD"))
_MAX_RAW_PER_FETCH=2_000_000
_FAULTS=frozenset((None,"after_second_metric","after_fourth_metric",
                   "before_precommit"))


def _specs(symbol):
    if type(symbol) is not str or symbol not in SYMBOLS:
        raise ValueError("BLOCKED_YAHOO_FANOUT_UNCATALOGUED_SYMBOL")
    return tuple(SeriesSpec("yahoo",metric,symbol,"d1",
                            "native_volume" if metric=="volume" else "USD")
                 for metric in METRICS)


def run_offline_yahoo_ohlcv_fanout(db_path, lock_dir, symbol, start, end, *,
                                   raw_bytes, status_code=200, now,
                                   fault=None):
    """One source-native fake fetch with ALL-OR-NOTHING five-metric SQL.

    An HTTP-200 invalid or partly unavailable multi-field quote yields five
    rejected RAW receipts. An HTTP failure yields five failed receipts. Neither
    advances any metric cursor. Injected crash/old conflicting observation
    instead rolls back the ENTIRE group (zero newly persisted receipts).
    """
    specs=_specs(symbol)
    if fault not in _FAULTS:
        raise ValueError("BLOCKED_YAHOO_FANOUT_FAULT_PARAMETER")
    if utc(start)>=utc(end):
        raise ValueError("BLOCKED_YAHOO_FANOUT_TIME_RANGE")
    utc(now)
    db_path=_fixture_guard(db_path,lock_dir)
    endpoint,params=_request_for(specs[0],start,end)
    frozen=FrozenLegacyHTTP(url=endpoint,params=params,body=raw_bytes,
                            status=status_code)
    response_digest=(hashlib.sha256(raw_bytes).hexdigest()
                     if status_code is not None else None)
    # This is a LOCAL evidence identifier only; equality of receipt hashes
    # by itself does not cryptographically attest a real upstream request.
    group_id=hashlib.sha256(json.dumps(
        [symbol,utc(start).isoformat(),utc(end).isoformat(),response_digest,
         endpoint,params],sort_keys=True,separators=(",",":")).encode()).hexdigest()
    try:
        with hold_offline_parent_lease(lock_dir):
            parse_error=None
            payload=None
            parsed={}
            if status_code==200:
                try:
                    payload=_legacy_fake_fetch(specs[0],start,end,frozen)
                    if frozen.calls!=1 or payload!=json.loads(raw_bytes):
                        raise ValueError("BLOCKED_YAHOO_FANOUT_LEGACY_RAW_DRIFT")
                    for spec in specs:
                        parsed[spec.metric]=reconciled_fixture_points(
                            spec,payload,start=start,end=end,now=now)
                    first=parsed["close"]
                    if any(
                        len(points)!=len(first) or
                        tuple((p.timestamp,p.dimension) for p in points)!=
                        tuple((p.timestamp,p.dimension) for p in first)
                        for points in parsed.values()
                    ):
                        raise ValueError("BLOCKED_YAHOO_FANOUT_PARTIAL_OHLCV")
                except (ValueError,KeyError,TypeError,IndexError,OverflowError,
                        AttributeError) as exc:
                    parse_error=type(exc).__name__
            else:
                try:
                    _legacy_fake_fetch(specs[0],start,end,frozen)
                except (HTTPError,TimeoutError,RuntimeError):
                    pass
            if frozen.calls!=1:
                raise ValueError("BLOCKED_YAHOO_FANOUT_EXPECTED_EXACTLY_ONE_HTTP_MOCK")
            if status_code!=200:
                status="failed"
                failure_class=("SYNTHETIC_TRANSPORT_TIMEOUT" if status_code is None
                               else "SYNTHETIC_HTTP_"+str(status_code))
            elif parse_error:
                status="rejected"
                failure_class=parse_error
            elif not parsed["close"]:
                status="empty"
                failure_class=None
            else:
                status="ok"
                failure_class=None

            outcomes=[]
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                ledger.initialize(conn,now=now)
                conn.execute("BEGIN IMMEDIATE")
                for spec in specs:
                    if status=="ok":
                        stored=ledger.store_ok_page(
                            conn,spec,start,end,raw=raw_bytes,
                            points=parsed[spec.metric],now=now)
                        outcome={"metric":spec.metric,"status":"ok",**stored}
                    else:
                        receipt=ledger.record_failure(
                            conn,spec,start,end,status=status,
                            raw=(raw_bytes if status_code is not None else None),
                            http_status=status_code,now=now)
                        outcome={"metric":spec.metric,"status":status,
                                 "receipt_id":receipt,"new_rows":0,
                                 "cursor_advanced":False}
                    ledger.record_frozen_request_provenance(
                        conn,outcome["receipt_id"],endpoint,params,now=now)
                    outcomes.append(outcome)
                    if ((fault=="after_second_metric" and len(outcomes)==2)
                            or (fault=="after_fourth_metric" and len(outcomes)==4)):
                        raise ledger.SimulatedCrash("YAHOO_FANOUT_ATOMIC_ROLLBACK")
                if fault=="before_precommit":
                    raise ledger.SimulatedCrash("YAHOO_FANOUT_ATOMIC_ROLLBACK")
                quality=require_offline_precommit_integrity(conn)
            return {
                "state":("YAHOO_FANOUT_COMMITTED_OFFLINE" if status=="ok" else
                         "YAHOO_FANOUT_EMPTY_UNVERIFIED" if status=="empty" else
                         "YAHOO_FANOUT_NOT_COMMITTED"),
                "status":status,"failure_class":failure_class,
                "symbol":symbol,"metric_order":list(METRICS),
                "provider":"yahoo","frequency":"d1",
                "source_request_group_id":group_id,
                "group_id_persisted":False,
                "raw_sha256":response_digest,
                "frozen_http_requests":frozen.calls,
                "receipts_persisted":len(outcomes),
                "parsed_observations":sum(x.get("parsed_rows",0) for x in outcomes),
                "new_observations":sum(x["new_rows"] for x in outcomes),
                "all_five_cursors_advanced":all(
                    x["cursor_advanced"] for x in outcomes),
                "all_metric_states_identical":all(
                    x["status"]==status for x in outcomes),
                "individual_receipts":outcomes,
                "source_native_builder_used":True,
                "all_five_metrics_one_sqlite_transaction":True,
                "precommit_evidence":quality,
                "historical_completeness":"NOT_VERIFIED",
                "provider_entitlement_verified":False,
                "actual_exchange_calendar_verified":False,
                "real_http_requests":0,
                "production_authorized":False,
            }
    except WriterLockBusy:
        return {
            "state":"BLOCKED_WRITER_BUSY",
            "frozen_http_requests":frozen.calls,
            "receipts_persisted":0,
            "sqlite_connections_started":0,
            "real_http_requests":0,
            "historical_completeness":"NOT_VERIFIED",
            "production_authorized":False,
        }
