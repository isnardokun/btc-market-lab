#!/usr/bin/env python3
"""R10-C.3 read-only PRE evidence gate for existing SQLite v2.

No schema migration, backup creation, live HTTP, data writes, scheduler or
publication. The CLI only reads an existing database and emits bounded JSON.
This gate never grants authorization to run DDL or R10-D.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.config import DB_PATH
from scripts import market_v3_preflight
from scripts import market_raw_reconcile
from scripts import market_temporal_quality
from scripts import market_request_lineage_audit

CONTRACT = "R10-C3-PRE-QUALITY-GATE-v1"
EXPECTED = {
    "RAW": "PASS_SQLITE_TO_RAW",
    "TEMPORAL": "SNAPSHOT_INTERNAL_QA_OK",
    "LINEAGE": "PASS_REQUEST_LINEAGE",
}
# All four observed streams must exist. This is a market data provenance gate,
# not a way to declare a blank or half-populated snapshot ready for migration.
REQUIRED_STREAMS = frozenset({
    "binance_open_interest", "binance_funding_settled",
    "bybit_open_interest", "bybit_funding_settled",
})


def _summary_raw(result):
    return {key: result.get(key) for key in
            ("state", "issues_count", "rows", "verified", "blobs_seen",
             "blob_sha256_verified")}


def _summary_temporal(result):
    return {
        "quality_state": result.get("quality_state"),
        "issues_count": len(result.get("issues", []))
            if isinstance(result.get("issues"), list) else None,
        "historical_completeness": result.get("historical_completeness"),
    }


def _summary_lineage(result):
    return {key: result.get(key) for key in
            ("quality_state", "requests_total", "requests_verified",
             "backward_requests", "forward_requests",
             "historical_completeness")}


def _valid_raw(r):
    return (
        isinstance(r, dict) and r.get("state") == EXPECTED["RAW"]
        and type(r.get("rows")) is int and r["rows"] > 0
        and type(r.get("verified")) is int and r["verified"] == r["rows"]
        and type(r.get("issues_count")) is int and r["issues_count"] == 0
        and isinstance(r.get("issues"), list) and not r["issues"]
        and type(r.get("blobs_seen")) is int and r["blobs_seen"] > 0
        and type(r.get("blob_sha256_verified")) is int
        and r["blob_sha256_verified"] == r["blobs_seen"]
    )


def _valid_temporal(r):
    return (
        isinstance(r, dict) and r.get("quality_state") == EXPECTED["TEMPORAL"]
        and r.get("historical_completeness") == "NOT_VERIFIED"
        and isinstance(r.get("issues"), list) and not r["issues"]
        and isinstance(r.get("streams"), dict)
        and REQUIRED_STREAMS <= r["streams"].keys()
        and all(
            isinstance(r["streams"].get(key), dict)
            and type(r["streams"][key].get("rows")) is int
            and r["streams"][key]["rows"] > 0
            and r["streams"][key].get("valid_timestamps") == r["streams"][key]["rows"]
            and r["streams"][key].get("duplicate_timestamps") == 0
            and (
                key.endswith("_funding_settled")
                or r["streams"][key].get("five_minute_gaps_inside_observed_window") == 0
            )
            for key in REQUIRED_STREAMS
        )
        and r.get("source_rows_unstored") == []
    )


def _valid_lineage(r, expected_backward):
    return (
        isinstance(r, dict) and r.get("quality_state") == EXPECTED["LINEAGE"]
        and isinstance(r.get("issues"), list) and not r["issues"]
        and r.get("historical_completeness") == "NOT_VERIFIED"
        and all(type(r.get(k)) is int for k in
                ("requests_total", "requests_verified",
                 "backward_requests", "forward_requests"))
        and r["requests_total"] == expected_backward
        and r["requests_verified"] == expected_backward
        and r["backward_requests"] == expected_backward
        and r["forward_requests"] == 0
    )


def evaluate(db_path, *, expected_backward, auditors=None):
    """Fail closed unless all separate read-only audits and two fingerprints agree.

    Optional auditors mapping is for offline test injection only; CLI always
    calls the real audit functions. Two fingerprints detect some concurrent
    changes but DO NOT provide a globally atomic view across audit connections.
    """
    if type(expected_backward) is not int or expected_backward <= 0:
        raise ValueError("positive expected-backward count is mandatory")
    report = {
        "contract": CONTRACT,
        "phase": "pre",
        "state": "BLOCKED",
        "authorization": "EVIDENCE_ONLY_NO_MIGRATION_NO_HTTP",
        "historical_completeness": "NOT_VERIFIED",
        "expected_backward": expected_backward,
        "preflight": None,
        "audits": {},
        "issues": [],
    }
    p = Path(db_path)
    audit_map = auditors or {
        "RAW": market_raw_reconcile.audit,
        "TEMPORAL": market_temporal_quality.inspect,
        "LINEAGE": market_request_lineage_audit.audit,
    }
    first = market_v3_preflight.inspect(
        p, phase="pre", expected_backward=expected_backward
    )
    # Refuse even to proceed if the original PRE is invalid or lacks v2.
    report["preflight"] = first
    if first.get("quality_state") != "PASS_READ_ONLY_PRECHECK":
        report["issues"].append("PRE_FINGERPRINT_BLOCKED")
        return report
    if first.get("checks", {}).get("backward_receipts") != expected_backward:
        report["issues"].append("PRE_RECEIPT_COUNT_MISMATCH")
        return report

    for name, normalize, validate in (
        ("RAW", _summary_raw, _valid_raw),
        ("TEMPORAL", _summary_temporal, _valid_temporal),
        ("LINEAGE", _summary_lineage,
         lambda r: _valid_lineage(r, expected_backward)),
    ):
        try:
            response = audit_map[name](p)
            report["audits"][name] = normalize(response) if isinstance(
                response, dict) else {"error_class": "InvalidAuditOutput"}
            if not validate(response):
                report["issues"].append(name + "_AUDIT_BLOCKED")
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error,
                RuntimeError) as exc:
            report["audits"][name] = {"error_class": type(exc).__name__}
            report["issues"].append(name + "_AUDIT_UNAVAILABLE")

    # Prevent a "green" gate when the underlying database changed during
    # the multiple independent audit connections. The operator must also
    # quiesce DB writers, which this tool cannot enforce.
    try:
        second = market_v3_preflight.inspect(
            p, phase="pre", expected_backward=expected_backward
        )
        if (second.get("quality_state") != "PASS_READ_ONLY_PRECHECK" or
                second.get("snapshot") != first.get("snapshot")):
            report["issues"].append("SQLITE_CHANGED_DURING_AUDIT")
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        report["issues"].append("SECOND_PRECHECK_UNAVAILABLE")
        report["second_precheck_error_class"] = type(exc).__name__

    if not report["issues"]:
        report["state"] = "PASS_OFFLINE_PRE_EVIDENCE"
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path(DB_PATH))
    ap.add_argument("--expected-backward", required=True, type=int)
    args = ap.parse_args(argv)
    try:
        outcome = evaluate(args.db, expected_backward=args.expected_backward)
        print(json.dumps(outcome, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if outcome["state"] == "PASS_OFFLINE_PRE_EVIDENCE" else 1
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"state": "GATE_UNAVAILABLE",
                          "error_class": type(exc).__name__},
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
