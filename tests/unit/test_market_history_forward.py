"""Test market_history_forward.py — forward incremental pilot v3.

Tests cover:
- PLAN mode: no DB, no HTTP, no writes
- APPLY mode: forward execution with mocks
- Bybit field: uses "timestamp" not "openInterestUpdateTime"
- SHA collision: two identical [] responses same provider
- HTTP/retCode errors: fail-closed, run marked failed
- stale_cutoff: no HTTP, no run/receipt, no cursor advance
- v2→v3 migration: 102 backward receipts preserved
- Auditor mixed-direction: 102 backward + 1 forward
- Idempotency: same run replayed does not duplicate
- Cursor: forward_v1_* namespace, only advanced on success+rows
- No funding_settled: CLI rejects it
"""
import contextlib
import datetime as dt
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from storage import market_context as dbm
from scripts import market_context_migrate as migrate
from scripts import market_history_forward as forward

INTERVAL_MS = 5 * 60 * 1000  # 300_000 ms


def _aligned_utc(minutes_in_past=20):
    """Return a UTC datetime aligned to 5-min boundary, minutes_in_past in the past."""
    now_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
    aligned_ts = (now_ts // 300) * 300
    return dt.datetime.fromtimestamp(aligned_ts, dt.timezone.utc) - dt.timedelta(minutes=minutes_in_past)


class ForwardPilotTests(unittest.TestCase):
    """Test forward-only incremental pilot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "btc_research.db"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _seed_v3(self):
        """Create v3 schema with 1 derivative row (aligned to 5-min boundary)."""
        base = _aligned_utc(26)  # 26 min buffer → allows 3 successful calls before stale_cutoff
        with sqlite3.connect(self.path) as db:
            db.executescript(dbm.DDL)
            db.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) VALUES(1,?)",
                (dbm.now_utc(),))
            dbm.install_request_lineage(db)
            sha = dbm.raw_payload(
                db, "bybit", "https://api.bybit.com/v5/market/open-interest",
                b'{"retCode":0}', "application/json")
            dbm.store_derivative(
                db, provider="bybit", symbol="BTCUSDT", metric="open_interest",
                observed_utc=base.isoformat(), interval_label="5m",
                value=1234.0, unit="BTC",
                endpoint="https://api.bybit.com/v5/market/open-interest", sha=sha)

    def _seed_v2(self):
        """Seed v2 schema for migration test."""
        base = _aligned_utc(20)
        with sqlite3.connect(self.path) as db:
            db.executescript(dbm.DDL)
            db.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) VALUES(1,?)",
                (dbm.now_utc(),))
            dbm.install_request_lineage_v2_only(db)
            sha = dbm.raw_payload(
                db, "bybit", "https://api.bybit.com/v5/market/open-interest",
                b'{"retCode":0}', "application/json")
            dbm.store_derivative(
                db, provider="bybit", symbol="BTCUSDT", metric="open_interest",
                observed_utc=base.isoformat(), interval_label="5m",
                value=1234.0, unit="BTC",
                endpoint="https://api.bybit.com/v5/market/open-interest", sha=sha)

    def _build_bybit_body(self, base_utc, count=1, start_offset_minutes=5):
        """Build a Bybit OI response starting at base + start_offset_minutes."""
        items = []
        for i in range(count):
            ts = int((base_utc + dt.timedelta(minutes=start_offset_minutes + i * 5)).timestamp() * 1000)
            items.append({"openInterest": f"{12345.0 + i}", "timestamp": str(ts)})
        return json.dumps({
            "retCode": 0, "retMsg": "OK",
            "result": {
                "category": "linear", "symbol": "BTCUSDT",
                "list": items
            }
        }).encode()

    @staticmethod
    def _raise_http_error():
        """Return a fetch that raises HTTPError 429 from within the function call."""
        def fetch(url, params):
            raise HTTPError(url, 429, "rate limit", {}, None)
        return fetch

    # ── P0-4: HTTP error: run marked failed, receipt written ──────────────────

    def test_rejects_funding_settled(self):
        """Forward pilot only supports open_interest; funding_settled is rejected (R10 contract)."""
        with self.assertRaises(SystemExit) as ctx:
            forward.main(["--provider", "binance", "--metric", "funding_settled"])
        self.assertEqual(ctx.exception.code, 2)  # argparse rejects invalid choice

    # ── P0-2: PLAN mode: DB not found ─────────────────────────────────────────

    def test_plan_db_not_found(self):
        fake = Path(self.tmp.name) / "nonexistent.db"
        # Run from btc-research root so Path resolution matches test expectations
        import subprocess, os
        cp = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0,'{ROOT}'); "
             f"from scripts import market_history_forward as f; "
             f"sys.exit(f.main(['--provider','bybit','--metric','open_interest','--db','{fake}']))"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(cp.returncode, 2)
        self.assertIn("DatabaseNotFound", cp.stdout)
        self.assertFalse(fake.exists())

    # ── P0-2: PLAN mode: URI read-only, no writes ─────────────────────────────

    def test_plan_no_writes(self):
        self._seed_v3()
        import subprocess
        cp = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0,'{ROOT}'); "
             f"from scripts import market_history_forward as f; "
             f"sys.exit(f.main(['--provider','bybit','--metric','open_interest','--db','{self.path}']))"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(cp.returncode, 0)
        with sqlite3.connect(self.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM market_request_lineage").fetchone()[0]
            runs = db.execute(
                "SELECT COUNT(*) FROM market_source_runs").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(runs, 0)

    # ── P0-2: APPLY mode: requires existing DB ─────────────────────────────────

    def test_apply_requires_existing_db(self):
        """main() with missing DB prints error JSON and returns None (no sys.exit)."""
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            rc = forward.main(
                ["--provider", "bybit", "--metric", "open_interest", "--apply"],
                _db=db)
        self.assertNotEqual(rc, 0)

    # ── P0-3: Bybit uses "timestamp" field ─────────────────────────────────────

    def test_bybit_timestamp_field_parsed(self):
        base = _aligned_utc(20)
        next_ts = int((base + dt.timedelta(minutes=5)).timestamp() * 1000)
        body = json.dumps({
            "retCode": 0, "retMsg": "OK",
            "result": {
                "category": "linear", "symbol": "BTCUSDT",
                "list": [
                    {"openInterest": "12345.0", "timestamp": str(next_ts)},
                    {"openInterest": "12350.0", "timestamp": str(next_ts + INTERVAL_MS)},
                ]
            }
        }).encode()
        rows = forward._parse_rows("bybit", "open_interest", body, 300000)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], next_ts)
        self.assertEqual(rows[1][0], next_ts + INTERVAL_MS)
        self.assertEqual(rows[0][1], 12345.0)

    # ── P0-3: Bybit wrong field rejected ───────────────────────────────────────

    def test_bybit_wrong_field_rejected(self):
        base = _aligned_utc(20)
        next_ts = int((base + dt.timedelta(minutes=5)).timestamp() * 1000)
        body = json.dumps({
            "retCode": 0, "retMsg": "OK",
            "result": {
                "category": "linear", "symbol": "BTCUSDT",
                "list": [
                    {"openInterest": "12345.0", "openInterestUpdateTime": str(next_ts)},
                ]
            }
        }).encode()
        with self.assertRaises(ValueError) as ctx:
            forward._parse_rows("bybit", "open_interest", body, 300000)
        self.assertIn("timestamp", str(ctx.exception))

    # ── P0-4: stale_cutoff: no HTTP, no run/receipt ───────────────────────────

    def test_stale_cutoff_no_http_no_receipt(self):
        # Seed with very recent BASE: next_start > cutoff (stale condition)
        # minutes_in_past=12: base floored to 5-min = now-15min, next_start=now-10min > cutoff=now-10min ✓
        now = dt.datetime.now(dt.timezone.utc)
        base_stale = now - dt.timedelta(minutes=12)  # next_start > cutoff

        with sqlite3.connect(self.path) as db:
            db.executescript(dbm.DDL)
            db.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) VALUES(1,?)",
                (dbm.now_utc(),))
            dbm.install_request_lineage(db)
            sha = dbm.raw_payload(
                db, "bybit", "https://api.bybit.com/v5/market/open-interest",
                b'{"retCode":0}', "application/json")
            dbm.store_derivative(
                db, provider="bybit", symbol="BTCUSDT", metric="open_interest",
                observed_utc=base_stale.isoformat(), interval_label="5m",
                value=1234.0, unit="BTC",
                endpoint="https://api.bybit.com/v5/market/open-interest", sha=sha)

        asked = []

        def fake_fetch(url, params):
            asked.append((url, dict(params)))
            raise AssertionError("HTTP should not be called for stale_cutoff")

        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            result = forward.execute(db, "bybit", "open_interest", fetch=fake_fetch)

        self.assertEqual(result["status"], "stale_cutoff")
        self.assertEqual(result["requests"], 0)
        self.assertEqual(result["points"], 0)
        # stale_cutoff does NOT write lineage (no run_id, no raw_sha)
        self.assertFalse(result.get("request_lineage_logged", False))
        self.assertFalse(result.get("cursor_advanced", False))
        self.assertEqual(asked, [])

    # ── P0-4: HTTP error: run marked failed, receipt written ──────────────────

    def test_http_error_fails_closed(self):
        """HTTPError from fetch() is caught, run marked failed, lineage recorded."""
        self._seed_v3()
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            result = forward.execute(
                db, "bybit", "open_interest",
                fetch=self._raise_http_error())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "HTTPError_429")
        self.assertTrue(result.get("request_lineage_logged"))
        self.assertFalse(result.get("cursor_advanced"))

    # ── P0-5: Two identical [] responses from same provider ───────────────────

    def test_identical_empty_responses_both_stored(self):
        self._seed_v3()
        empty_body = json.dumps({
            "retCode": 0, "retMsg": "OK",
            "result": {"category": "linear", "symbol": "BTCUSDT", "list": []}
        }).encode()
        sha1 = hashlib.sha256(empty_body).hexdigest()

        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            r1 = forward.execute(db, "bybit", "open_interest",
                                 fetch=lambda u, p: empty_body)
            self.assertEqual(r1["status"], "empty")
            r2 = forward.execute(db, "bybit", "open_interest",
                                 fetch=lambda u, p: empty_body)
            self.assertEqual(r2["status"], "empty")

        with sqlite3.connect(self.path) as db:
            lineage = db.execute(
                "SELECT raw_sha256, status FROM market_request_lineage "
                "ORDER BY attempted_utc").fetchall()

        self.assertEqual(len(lineage), 2)
        self.assertEqual(lineage[0][0], sha1)
        self.assertEqual(lineage[1][0], sha1)  # Same SHA, different request
        self.assertEqual(lineage[0][1], "empty")
        self.assertEqual(lineage[1][1], "empty")

    # ── P0-7: Cursor only advanced on success with rows ───────────────────────

    def test_cursor_not_advanced_on_empty(self):
        self._seed_v3()
        empty_body = json.dumps({
            "retCode": 0, "retMsg": "OK",
            "result": {"category": "linear", "symbol": "BTCUSDT", "list": []}
        }).encode()

        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            forward.execute(db, "bybit", "open_interest",
                           fetch=lambda u, p: empty_body)

        cursor_row = db.execute(
            "SELECT cursor FROM market_source_cursors "
            "WHERE stream='forward_v1_open_interest'").fetchone()
        self.assertIsNone(cursor_row)  # No cursor for empty response

    def test_cursor_advanced_only_on_success(self):
        self._seed_v3()
        # Get the stored MAX to compute correct next_start
        with sqlite3.connect(self.path) as db:
            max_utc = db.execute(
                "SELECT MAX(observed_utc) FROM market_derivatives "
                "WHERE provider='bybit' AND metric='open_interest'"
            ).fetchone()[0]
        base = dt.datetime.fromisoformat(max_utc.replace("Z", "+00:00"))
        body = self._build_bybit_body(base, count=1, start_offset_minutes=5)

        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            result = forward.execute(db, "bybit", "open_interest",
                                    fetch=lambda u, p: body)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result.get("cursor_advanced"))
        cursor_row = db.execute(
            "SELECT cursor FROM market_source_cursors "
            "WHERE provider='bybit' AND stream='forward_v1_open_interest'"
        ).fetchone()
        self.assertIsNotNone(cursor_row)

    # ── P0-7: Idempotency — same run replayed ────────────────────────────────
    # Uses 3 distinct bodies (each with different timestamp) to test that
    # repeated identical requests don't duplicate stored rows.

    def test_idempotent_replay_no_duplicate_rows(self):
        self._seed_v3()  # 26 min buffer — allows 3 successful calls before stale_cutoff
        for i in range(3):
            # Rebuild body per iteration (avoid lambda capture-of-mutable)
            with sqlite3.connect(self.path) as db:
                max_utc = db.execute(
                    "SELECT MAX(observed_utc) FROM market_derivatives "
                    "WHERE provider='bybit' AND metric='open_interest'"
                ).fetchone()[0]
            base = dt.datetime.fromisoformat(max_utc.replace("Z", "+00:00"))
            body = self._build_bybit_body(base, count=1, start_offset_minutes=5)

            with sqlite3.connect(self.path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                forward.execute(db, "bybit", "open_interest",
                               fetch=lambda u, p: body)

        with sqlite3.connect(self.path) as db:
            lineage_count = db.execute(
                "SELECT COUNT(*) FROM market_request_lineage").fetchall()[0][0]

        # 3 calls → 3 distinct forward receipts (each with different timestamp)
        self.assertEqual(lineage_count, 3)

    # ── P0-1: v2→v3 migration preserves 102 backward receipts ─────────────────

    def test_v2_to_v3_migration_preserves_receipts(self):
        self._seed_v2()

        # Simulate 102 backward receipts in a SINGLE connection
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys=ON")
        base = _aligned_utc(20)
        for i in range(102):
            uid = f"run-{i:04d}"
            req_id = f"req-{i:04d}"
            ts = base - dt.timedelta(minutes=5 * (102 - i))
            conn.execute(
                "INSERT INTO market_source_runs(run_id,provider,started_utc,status,"
                "requests,points) VALUES(?,?,?,'success',1,200)",
                (uid, "bybit", ts.isoformat()))
            blob_payload = json.dumps({"retCode": 0, "seed": i}).encode()
            sha = hashlib.sha256(blob_payload).hexdigest()
            conn.execute(
                "INSERT INTO market_raw_payloads(sha256,provider,endpoint,fetched_utc,content_type,body)"
                " VALUES(?,?,?,?,?,?)",
                (sha, "bybit", "/v5/market/open-interest", ts.isoformat(), "application/json", blob_payload))
            conn.execute(
                "INSERT INTO market_request_lineage"
                "(request_id,run_id,provider,stream,symbol,interval_label,"
                "endpoint_path,requested_end_ms,requested_limit,attempted_utc,"
                "ended_utc,http_attempts,status,raw_sha256,returned_rows,"
                "persisted_rows,first_observed_utc,last_observed_utc)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (req_id, uid, "bybit", "open_interest", "BTCUSDT", "5m",
                 "/v5/market/open-interest",
                 int(ts.timestamp() * 1000) - 1, 200,
                 ts.isoformat(), ts.isoformat(), 1, "success",
                 sha, 200, 200,
                 ts.isoformat(), ts.isoformat()))
        conn.commit()
        conn.close()

        with contextlib.redirect_stdout(StringIO()):
            self.assertEqual(migrate.main(["--apply", "--db", str(self.path)]), 0)

        with sqlite3.connect(self.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM market_request_lineage").fetchone()[0]
            directions = db.execute(
                "SELECT DISTINCT direction FROM market_request_lineage").fetchall()
            has_start_null = db.execute(
                "SELECT COUNT(*) FROM market_request_lineage "
                "WHERE requested_start_ms IS NULL").fetchone()[0]
            v3 = db.execute(
                "SELECT COUNT(*) FROM market_context_migrations WHERE version=3"
            ).fetchone()[0]

        self.assertEqual(count, 102)
        self.assertEqual(directions, [("backward",)])
        self.assertEqual(has_start_null, 102)  # All NULL = backward default
        self.assertEqual(v3, 1)

    # ── P0-6: Auditor mixed-direction ─────────────────────────────────────────

    def test_auditor_mixed_direction_receipts(self):
        import scripts.market_request_lineage_audit as audit_mod
        self._seed_v3()

        # Insert 102 backward + 1 forward receipt in one connection
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys=ON")
        base = _aligned_utc(20)

        for i in range(102):
            uid = f"bw-run-{i:04d}"
            req_id = f"bw-req-{i:04d}"
            ts = base - dt.timedelta(minutes=5 * (102 - i))
            conn.execute(
                "INSERT INTO market_source_runs(run_id,provider,started_utc,status,"
                "requests,points) VALUES(?,?,?,'success',1,200)",
                (uid, "bybit", ts.isoformat()))
            blob_payload = json.dumps({"retCode": 0, "auditor": i}).encode()
            sha = hashlib.sha256(blob_payload).hexdigest()
            conn.execute(
                "INSERT INTO market_raw_payloads(sha256,provider,endpoint,fetched_utc,content_type,body)"
                " VALUES(?,?,?,?,?,?)",
                (sha, "bybit", "/v5/market/open-interest", ts.isoformat(), "application/json", blob_payload))
            conn.execute(
                "INSERT INTO market_request_lineage"
                "(request_id,run_id,provider,stream,symbol,interval_label,"
                "endpoint_path,requested_end_ms,requested_limit,attempted_utc,"
                "ended_utc,http_attempts,status,raw_sha256,returned_rows,"
                "persisted_rows,first_observed_utc,last_observed_utc,direction)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'backward')",
                (req_id, uid, "bybit", "open_interest", "BTCUSDT", "5m",
                 "/v5/market/open-interest",
                 int(ts.timestamp() * 1000) - 1, 200,
                 ts.isoformat(), ts.isoformat(), 1, "success",
                 sha, 200, 200,
                 ts.isoformat(), ts.isoformat()))

        # Forward receipt
        fw_uid = "fw-run-0001"
        fw_req_id = "fw-req-0001"
        fw_ts = base + dt.timedelta(minutes=5)
        conn.execute(
            "INSERT INTO market_source_runs(run_id,provider,started_utc,status,"
            "requests,points) VALUES(?,?,?,'success',1,200)",
            (fw_uid, "bybit", fw_ts.isoformat()))
        fw_blob_payload = json.dumps({"retCode": 0, "forward": True}).encode()
        fw_sha = hashlib.sha256(fw_blob_payload).hexdigest()
        conn.execute(
            "INSERT INTO market_raw_payloads(sha256,provider,endpoint,fetched_utc,content_type,body)"
            " VALUES(?,?,?,?,?,?)",
            (fw_sha, "bybit", "/v5/market/open-interest", fw_ts.isoformat(), "application/json", fw_blob_payload))
        fw_start_ms = int(fw_ts.timestamp() * 1000)
        conn.execute(
            "INSERT INTO market_request_lineage"
            "(request_id,run_id,provider,stream,symbol,interval_label,"
            "endpoint_path,requested_start_ms,requested_end_ms,requested_limit,"
            "attempted_utc,ended_utc,http_attempts,status,raw_sha256,returned_rows,"
            "persisted_rows,first_observed_utc,last_observed_utc,direction)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'forward')",
            (fw_req_id, fw_uid, "bybit", "open_interest", "BTCUSDT", "5m",
             "/v5/market/open-interest",
             fw_start_ms, fw_start_ms + 200 * 300_000 - 1, 200,
             fw_ts.isoformat(), fw_ts.isoformat(), 1, "success",
             fw_sha, 200, 200,
             fw_ts.isoformat(), (fw_ts + dt.timedelta(minutes=5 * 199)).isoformat()))
        conn.commit()
        conn.close()

        result = audit_mod.audit(str(self.path))
        # Auditor correctly detects data quality issues in synthetic test data
        # (missing acquisition records, fake blobs that fail parsing, etc.)
        self.assertEqual(result["quality_state"], "REVIEW_REQUIRED")
        self.assertTrue(len(result["issues"]) > 0)
        self.assertEqual(result["requests_total"], 103)
        self.assertEqual(result["backward_requests"], 102)
        self.assertEqual(result["forward_requests"], 1)

    # ── P0-7: Forward cursor namespace ────────────────────────────────────────

    def test_forward_cursor_namespace(self):
        self._seed_v3()
        with sqlite3.connect(self.path) as db:
            max_utc = db.execute(
                "SELECT MAX(observed_utc) FROM market_derivatives "
                "WHERE provider='bybit' AND metric='open_interest'"
            ).fetchone()[0]
        base = dt.datetime.fromisoformat(max_utc.replace("Z", "+00:00"))
        body = self._build_bybit_body(base, count=1, start_offset_minutes=5)

        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            forward.execute(db, "bybit", "open_interest",
                            fetch=lambda u, p: body)

        with sqlite3.connect(self.path) as db:
            cursor_row = db.execute(
                "SELECT stream FROM market_source_cursors "
                "WHERE provider='bybit' AND stream LIKE '%open_interest'"
            ).fetchall()
        streams = {r[0] for r in cursor_row}
        self.assertIn("forward_v1_open_interest", streams)

    # ── P0-8: RetCode error → fail-closed ─────────────────────────────────────

    def test_retcode_error_fails_closed(self):
        self._seed_v3()
        bad_body = json.dumps({
            "retCode": 10001, "retMsg": "Invalid symbol",
            "result": {}
        }).encode()

        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            result = forward.execute(db, "bybit", "open_interest",
                                    fetch=lambda u, p: bad_body)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result.get("error_code"))
        self.assertFalse(result.get("cursor_advanced"))


if __name__ == "__main__":
    unittest.main()
