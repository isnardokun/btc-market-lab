"""Independent native SOURCE REQUEST ↔ receipt identity audit (offline only).

A SHA over request metadata by itself is insufficient: an internally coherent
but forged endpoint or range can be re-signed. This module checks it against
the actual installed legacy source-native request contract for the exact
receipt's provider/metric/symbol/frequency and UTC half-open interval.

This is a caller-owned read-only SQLite connection. No provider calls, no
opener, no file paths, no credential reads, no auth and no writes.
"""
from collections import defaultdict
import json
from urllib.parse import urlsplit

from ingestion.package_a_contracts import SeriesSpec
from ingestion.researchbitcoin_catalog import CATALOG


def _contract_spec(provider, metric, symbol, frequency, units):
    """Recover native builder identity without inventing empty-page units.

    The historical *observation* unit can only be proven on a stream with
    archived observations; failed/empty-only requests remain unverified as
    data contracts. For them, a catalog/scoped placeholder lets us compare
    the request URL and date ranges without claiming observations exist.
    """
    existing=units.get((provider,metric,symbol,frequency),set())
    if len(existing)>1:
        raise ValueError("Mixed unit/scale for source-native request")
    if existing:
        unit,scale=next(iter(existing))
        return SeriesSpec(provider,metric,symbol,frequency,unit,scale),False
    if provider=="researchbitcoin":
        item=CATALOG[metric]
        unit,scale=item.unit,item.raw_scale
    elif provider=="yahoo":
        unit,scale=("native_volume" if metric=="volume" else "USD"),"native"
    elif provider in ("binance","bybit"):
        unit,scale=("BTC" if metric=="open_interest" else "fraction"),"native"
    else:
        # Bitview and FRED legacy builders do not use the unit. No source
        # data exists to validate its actual unit or scale in empty receipts.
        unit,scale="UNVERIFIED_UNIT_NOT_AN_OBSERVATION","native"
    return SeriesSpec(provider,metric,symbol,frequency,unit,scale),True


def audit_native_frozen_request_lineage(conn):
    """Reject validly re-signed but semantically forged frozen HTTP metadata.

    Missing provenance on pre-existing direct-fixture pages is reported,
    NEVER synthesized: v1 legacy/directed fixtures are not retroactively
    attributed to a frozen HTTP request. Only stored provenance is audited.
    """
    # Lazy import avoids an import cycle with package_a_ingest_fixture, which
    # the frozen source builder imports to perform the actual mock page.
    from scripts.package_a_frozen_legacy_transport import _request_for

    units=defaultdict(set)
    for provider,metric,symbol,freq,unit,scale in conn.execute(
        "SELECT provider,metric,symbol,frequency,unit,raw_scale "
        "FROM package_a_observations"
    ):
        units[(provider,metric,symbol,freq)].add((unit,scale))

    valid=errors=undetermined_units=0
    violations=[]
    for rid,provider,metric,symbol,freq,start,stop,origin,path,params_json in conn.execute(
        "SELECT p.receipt_id,r.provider,r.metric,r.symbol,r.frequency,"
        "r.start_utc,r.end_exclusive_utc,p.endpoint_origin,p.endpoint_path,"
        "p.request_params_json FROM package_a_request_provenance p "
        "LEFT JOIN package_a_receipts r ON r.receipt_id=p.receipt_id "
        "ORDER BY p.receipt_id"
    ):
        try:
            spec,inferred=_contract_spec(
                provider,metric,symbol,freq,units)
            expected_url, expected_params=_request_for(spec,start,stop)
            endpoint=urlsplit(expected_url)
            redacted={
                key: ("[REDACTED]" if any(
                    flag in key.lower()
                    for flag in ("key","token","secret","password")) else value)
                for key,value in expected_params.items()
            }
            expected_json=json.dumps(
                redacted,sort_keys=True,separators=(",",":"))
            if (origin!=endpoint.netloc or path!=endpoint.path or
                    params_json!=expected_json):
                raise ValueError("Signed metadata differs from native request")
            valid+=1
            undetermined_units+=int(inferred)
        except (TypeError,ValueError,KeyError,IndexError,AttributeError,
                OverflowError):
            errors+=1
            # Only opaque receipt IDs, never include raw payload/API tokens.
            violations.append(rid)

    missing=conn.execute(
        "SELECT COUNT(*) FROM package_a_receipts r "
        "LEFT JOIN package_a_request_provenance p ON p.receipt_id=r.receipt_id "
        "WHERE p.receipt_id IS NULL"
    ).fetchone()[0]
    return {
        "state":("NATIVE_FROZEN_REQUEST_LINEAGE_PASS" if not errors else
                 "NATIVE_FROZEN_REQUEST_LINEAGE_FAIL"),
        "native_requests_validated":valid,
        "native_request_identity_errors":errors,
        "invalid_receipt_ids":violations,
        "historical_receipts_without_frozen_provenance":missing,
        "requests_without_observed_unit_contract":undetermined_units,
        "missing_provenance_retroactively_fabricated":False,
        "request_source_verified_against_installed_legacy_builder":True,
        "provider_entitlement_verified":False,
        "real_http_requests":0,
        "production_authorized":False,
    }
