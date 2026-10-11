"""R10-E.4: deterministic synthetic one-cycle integration of E0/E1/E2/E3.

No CLI, caller DB path, network client, scheduler or production writer.
Creates a new SQLite in TemporaryDirectory, and injects local fake HTTP.
"""
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.market_r10e_plan_only import plan_snapshot
from scripts.market_r10e_dispatch_offline import dispatch_offline
from scripts.market_r10e2_shadow_gate import evaluate_shadow_gate
from scripts import market_history_forward as forward
from scripts import market_raw_reconcile as raw_audit
from scripts import market_temporal_quality as temporal_audit
from scripts import market_request_lineage_audit as lineage_audit
from scripts.market_r10e3_mock_e2e import (
    CLOCK, BASE, TABLES, _seed, _fingerprints, _body, _endpoint, _timestamp_ms,
)

_ALLOWED = frozenset(("success", "empty", "http429", "gap"))


def fixture_policy(**changes):
    """Return a mock-only control policy; never a real permission."""
    config = {
        "mode": "synthetic_fixture",
        "remaining_request_budget": 1,
        "max_requests_per_cycle": 1,
        "minimum_cooldown_seconds": 60,
        "circuit_open": False,
        "writer_exclusion_simulated": True,
        "last_attempt_utc": None,
    }
    config.update(changes)
    return config


def _audit_existing_fixture(path, now):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN")
        raw = raw_audit.audit(path, connection=db)
        temporal = temporal_audit.inspect(path, now=now, connection=db)
        lineage = lineage_audit.audit(path, connection=db)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = len(db.execute("PRAGMA foreign_key_check").fetchall())
        db.rollback()
    return {
        "RAW": raw.get("state"),
        "TEMPORAL": temporal.get("quality_state"),
        "LINEAGE": lineage.get("quality_state"),
        "SQLITE": integrity,
        "FOREIGN_KEY_ERRORS": fk_errors,
    }


def simulate_synthetic_cycle(*, scenario="success", policy=None,
                             planned_now=CLOCK, elapsed_seconds=0):
    """Execute one fake cycle over an ephemeral SQLite; no production inputs."""
    if scenario not in _ALLOWED:
        raise ValueError("Only predeclared synthetic scenarios permitted")
    if not isinstance(planned_now, dt.datetime) or planned_now.tzinfo is None:
        raise ValueError("Aware test clock required")
    if type(elapsed_seconds) not in (int, float) or not 0 <= elapsed_seconds <= 86400:
        raise ValueError("Invalid synthetic elapsed seconds")
    if policy is None:
        policy = fixture_policy()
    if not isinstance(policy, dict) or policy.get("mode") != "synthetic_fixture":
        raise ValueError("Only synthetic fixture policy accepted")
    gate_now = planned_now + dt.timedelta(seconds=elapsed_seconds)

    with tempfile.TemporaryDirectory(prefix="r10e4-fixture-only-") as directory:
        path = Path(directory) / "synthetic.db"
        with sqlite3.connect(path) as db:
            _seed(db)

        # E0 reads precisely the fixture that mock E3 will later update.
        coverage = plan_snapshot(path, now=planned_now)
        dispatcher = dispatch_offline(coverage)
        gate = evaluate_shadow_gate(dispatcher, policy, now=gate_now)
        if gate["execution_authorized"] is not False:
            raise AssertionError("SHADOW output cannot authorize live acquisition")

        with sqlite3.connect(path) as db:
            before = _fingerprints(db)
        fetch_calls = []
        result = None
        selected = dispatcher.get("selected")
        provider = selected.get("provider") if selected else None

        if gate["decision"] == "SHADOW_ELIGIBLE":
            if provider not in ("binance", "bybit") or provider != gate["selected_provider"]:
                raise ValueError("SHADOW source mismatch")

            def fake_fetch(url, params):
                fetch_calls.append((url, dict(params)))
                if url != _endpoint(provider, "open_interest"):
                    raise AssertionError("Unexpected fake endpoint")
                start_ms = _timestamp_ms(BASE + dt.timedelta(minutes=5))
                if (params.get("startTime") != start_ms
                        or params.get("symbol") != "BTCUSDT"
                        or params.get("limit") != (500 if provider == "binance" else 200)):
                    raise AssertionError("Unexpected fake request")
                if scenario == "http429":
                    raise HTTPError(url, 429, "synthetic only", {}, None)
                if scenario == "empty":
                    return _body(provider, "open_interest", [])
                first = BASE + dt.timedelta(minutes=5)
                if scenario == "gap":
                    first += dt.timedelta(minutes=5)
                return _body(provider, "open_interest", [
                    (first, 1250.0),
                    (first + dt.timedelta(minutes=5), 1251.0),
                ])

            # v3 collector receives exactly one fake callback.
            cutoff = _timestamp_ms(planned_now - dt.timedelta(minutes=10))
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                with patch.object(forward, "_cutoff_now", return_value=cutoff):
                    result = forward.execute(
                        db, provider, "open_interest", fetch=fake_fetch)
                db.commit()

        with sqlite3.connect(path) as db:
            after = _fingerprints(db)
        delta = {
            table: {
                "added": len(set(after[table]) - set(before[table])),
                "deleted": len(set(before[table]) - set(after[table])),
                "modified": sum(after[table].get(pk) != digest for pk, digest
                                in before[table].items() if pk in after[table]),
            }
            for table in TABLES
        }
        immutable = all(x["deleted"] == x["modified"] == 0 for x in delta.values())
        audit = _audit_existing_fixture(path, planned_now)
        audit_ok = (
            audit["RAW"] == "PASS_SQLITE_TO_RAW"
            and audit["TEMPORAL"] == "SNAPSHOT_INTERNAL_QA_OK"
            and audit["SQLITE"] == "ok"
            and audit["FOREIGN_KEY_ERRORS"] == 0
            and audit["LINEAGE"] in ("PASS_REQUEST_LINEAGE", "NO_REQUEST_RECEIPTS_YET")
        )

        state = "MOCK_AUDIT_BLOCKED"
        if gate["decision"] != "SHADOW_ELIGIBLE":
            if not fetch_calls and immutable and all(x["added"] == 0 for x in delta.values()):
                state = ("MOCK_NO_WORK" if gate["decision"] == "NO_WORK"
                         else "MOCK_PRE_GATE_BLOCKED")
        elif (len(fetch_calls) == 1 and immutable and audit_ok and result is not None):
            expected = ("success" if scenario == "success" else
                        "empty" if scenario == "empty" else "failed")
            actual = result.get("status")
            added = delta["market_derivatives"]["added"]
            if actual == expected and added == (2 if scenario == "success" else 0):
                if actual == "success" and audit["LINEAGE"] == "PASS_REQUEST_LINEAGE":
                    state = "MOCK_ONE_PAGE_ACCEPTED"
                elif actual == "empty":
                    state = "MOCK_EMPTY_STOP"
                elif actual == "failed":
                    state = "MOCK_FAILURE_STOP"

        return {
            "state": state,
            "shadow_gate_decision": gate["decision"],
            "dispatcher_decision": dispatcher["decision"],
            "chosen_provider": provider if gate["decision"] == "SHADOW_ELIGIBLE" else None,
            "mock_fetch_calls": len(fetch_calls),
            "http_real_requests": 0,
            "production_db_reads": 0,
            "production_db_writes": 0,
            "live_execution_authorized": False,
            "historical_completeness": "NOT_VERIFIED",
            "forward_result": result,
            "audit_gates": audit,
            "immutable_pre_rows": immutable,
            "table_deltas": delta,
        }
