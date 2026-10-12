"""Read-only independent RAW -> parsed rows -> SQLite lineage reconciliation.

ONLY operates on an explicit, caller-owned SQLite connection. It does not
open a database path, import network adapters, access production, or mutate
anything. The caller must hold a single consistent read transaction.

The receipt's original payload is independently parsed from stored RAW, not
trusted merely because two stored SHA digests happen to be internally valid.
Exact-page replays retain earlier row owners and are accepted when identical.
This is a local-consistency audit, NOT upstream signature verification.
"""
from collections import defaultdict
import hashlib
import json

from ingestion.package_a_contracts import SeriesSpec, parse_fixture
from storage.package_a_ledger import _point_hash


def audit_receipt_reconstruction(conn):
    """Return bounded integer error counts; never claim upstream completeness."""
    observations = {}
    units = defaultdict(set)
    owners = {}
    for provider, metric, symbol, frequency, stamp, dimension, value, unit, scale, digest, receipt_id in conn.execute(
        "SELECT provider,metric,symbol,frequency,observed_utc,dimension_key,"
        "value,unit,raw_scale,row_sha256,receipt_id FROM package_a_observations"
    ):
        stream = (provider, metric, symbol, frequency)
        key = (*stream, stamp, dimension)
        units[stream].add((unit, scale))
        observations[key] = digest
        owners[key] = receipt_id

    outcomes = {
        "successful_receipts_reparsed": 0,
        "receipt_contract_errors": 0,
        "receipt_parsed_count_errors": 0,
        "raw_to_sql_missing_points": 0,
        "raw_to_sql_extra_points": 0,
        "receipt_origin_errors": 0,
        "non_success_receipt_errors": 0,
        "unverified_empty_receipts": 0,
    }
    expected_all = set()
    expected_by_receipt = {}
    for rid, provider, metric, symbol, frequency, start, stop, status, http, raw, digest, count in conn.execute(
        "SELECT receipt_id,provider,metric,symbol,frequency,start_utc,"
        "end_exclusive_utc,status,http_status,raw_payload,raw_sha256,"
        "parsed_count FROM package_a_receipts"
    ):
        stream = (provider, metric, symbol, frequency)
        if status != "ok":
            if count != 0 or (status in ("empty", "rejected") and http != 200):
                outcomes["non_success_receipt_errors"] += 1
            if status == "empty" and not units[stream]:
                # Without a source-specific unit/scale catalog, an empty-only
                # source cannot be independently reparsed. Never certify it.
                outcomes["unverified_empty_receipts"] += 1
            continue
        if http != 200 or not isinstance(raw, bytes) or not raw or not isinstance(digest, str) or hashlib.sha256(raw).hexdigest() != digest:
            outcomes["receipt_contract_errors"] += 1
            continue
        configurations = units[stream]
        if len(configurations) != 1:
            # Missing all rows or mixed unit/scale is not a valid source
            # contract. Never infer it from a successful receipt alone.
            outcomes["receipt_contract_errors"] += 1
            continue
        unit, scale = next(iter(configurations))
        try:
            spec = SeriesSpec(provider, metric, symbol, frequency, unit, scale)
            payload = json.loads(raw)
            points = parse_fixture(spec, payload, start=start, end=stop)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError,
                UnicodeError, AttributeError):
            outcomes["receipt_contract_errors"] += 1
            continue
        if len(points) != count or not points:
            outcomes["receipt_parsed_count_errors"] += 1
        outcomes["successful_receipts_reparsed"] += 1
        expected = {}
        for point in points:
            key = (*stream, point.timestamp, point.dimension)
            expected[key] = _point_hash(spec, point)
            expected_all.add(key)
            if observations.get(key) != expected[key]:
                outcomes["raw_to_sql_missing_points"] += 1
        expected_by_receipt[rid] = expected

    for key, owner in owners.items():
        if key not in expected_all:
            outcomes["raw_to_sql_extra_points"] += 1
        if key not in expected_by_receipt.get(owner, {}):
            outcomes["receipt_origin_errors"] += 1
        elif observations[key] != expected_by_receipt[owner][key]:
            outcomes["receipt_origin_errors"] += 1

    outcomes["state"] = (
        "RAW_SQL_LINEAGE_PASS"
        if not any(value for name, value in outcomes.items()
                   if name.endswith("_errors") or name in (
                       "raw_to_sql_missing_points", "raw_to_sql_extra_points"))
        else "RAW_SQL_LINEAGE_FAIL"
    )
    outcomes["real_upstream_coverage_verified"] = False
    outcomes["production_authorized"] = False
    return outcomes
