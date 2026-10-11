"""Single fake HTTP transport -> legacy source request builders -> Package A SQLite.

NO production SQLite, network, cron or provider token. Full regression uses
explicit TemporaryDirectory fixture marker and in-memory original RAW bytes.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.request import Request

from ingestion.package_a_contracts import SeriesSpec
from scripts.package_a_frozen_legacy_transport import (
    FrozenLegacyHTTP, FAKE_KEY, _request_for, run_mocked_legacy_page,
)
from scripts.package_a_ingest_fixture import MARKER, MARKER_VALUE, inspect_fixture
from scripts.market_r10e11_writer_exclusion import hold_writer_lock

DAY = "2026-10-08T00:00:00Z"
NEXT = "2026-10-09T00:00:00Z"
AFTER = "2026-10-10T00:00:00Z"
NOW = "2026-10-11T00:00:00Z"
OI_END = "2026-10-08T00:10:00Z"


def unix(value, *, ms=False):
    value = dt.datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
    return int(value * (1000 if ms else 1))


def scenarios():
    """Eight source-native request shapes, not invented live API evidence."""
    return [
        (SeriesSpec("researchbitcoin","sopr_sth","BTC","d1","ratio"),
         DAY,AFTER,{"data":[{"date":"2026-10-08","value":0.997},
                            {"date":"2026-10-09","value":1.002}]}),
        (SeriesSpec("bitview","mvrv","BTC","d1","ratio"),
         DAY,AFTER,{"index":"1d","start":(dt.date(2026,10,8)-dt.date(2009,1,1)).days,
                    "data":[1.5,1.6]}),
        (SeriesSpec("fred","CPIAUCSL","USA","monthly","index"),
         "2026-08-01T00:00:00Z","2026-10-01T00:00:00Z",
         {"observations":[{"date":"2026-08-01","value":"322.5","realtime_start":"2026-09-11"},
                           {"date":"2026-09-01","value":"323.5","realtime_start":"2026-10-09"}]}),
        (SeriesSpec("yahoo","close","BTC-USD","d1","USD"),
         DAY,AFTER,{"chart":{"result":[{"timestamp":[unix(DAY),unix(NEXT)],
                                      "indicators":{"quote":[{"close":[70100,70200]}]}}]}}),
        (SeriesSpec("binance","open_interest","BTCUSDT","5m","BTC"),
         DAY,OI_END,[{"timestamp":unix(DAY,ms=True),"sumOpenInterest":"50000"},
                     {"timestamp":unix("2026-10-08T00:05:00Z",ms=True),"sumOpenInterest":"50020"}]),
        (SeriesSpec("bybit","open_interest","BTCUSDT","5m","BTC"),
         DAY,OI_END,{"result":{"list":[
             {"timestamp":str(unix(DAY,ms=True)),"openInterest":"48000"},
             {"timestamp":str(unix("2026-10-08T00:05:00Z",ms=True)),"openInterest":"48020"}]}}),
        (SeriesSpec("binance","funding_settled","BTCUSDT","event","fraction"),
         DAY,NEXT,[{"symbol":"BTCUSDT","fundingTime":unix("2026-10-08T08:00:00Z",ms=True),
                    "fundingRate":"-0.0001"}]),
        (SeriesSpec("bybit","funding_settled","BTCUSDT","event","fraction"),
         DAY,NEXT,{"retCode":0,"result":{"symbol":"BTCUSDT","category":"linear",
             "list":[{"fundingRateTimestamp":str(unix("2026-10-08T09:00:00Z",ms=True)),
                      "fundingRate":"0.0002"}]}}),
    ]


def raw(payload):
    # Intentionally non-canonical formatting to ensure byte-preservation.
    return json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")


class FrozenLegacyTransportTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.path = self.root / "package_a_synthetic.db"
        self.locks = self.root / "locks"
        self.locks.mkdir()
        (self.root / MARKER).write_text(MARKER_VALUE, encoding="utf-8")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE legacy_untouched(id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO legacy_untouched VALUES(1,'preserve')")

    def apply(self, scenario, *, status=200, source_raw=None, fault=None):
        spec, start, end, payload = scenario
        return run_mocked_legacy_page(
            self.path,self.locks,spec,start,end,raw_bytes=raw(payload) if source_raw is None
            else source_raw,status_code=status,now=NOW,fault=fault)

    def rows(self, sql):
        with sqlite3.connect(self.path) as db:
            return db.execute(sql).fetchall()

    def test_eight_source_native_http_builders_roundtrip_into_one_sqlite(self):
        for scenario in scenarios():
            result = self.apply(scenario)
            self.assertEqual(result["state"],"PAGE_COMMITTED_OFFLINE",result)
            self.assertTrue(result["legacy_source_request_builder_used"])
            self.assertEqual(result["frozen_http_requests"],1)
            self.assertEqual(result["page_provenance_gate"],"RAW_SQL_LINEAGE_PASS")
            self.assertEqual(result["live_http_requests"],0)
        report=inspect_fixture(self.path,self.locks)
        self.assertEqual(report["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(report["receipt_reconstruction"]["successful_receipts_reparsed"],8)
        self.assertEqual(len(report["source_coverage"]),8)
        self.assertEqual(report["rows"],14)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(8,)])
        self.assertEqual(self.rows("SELECT value FROM legacy_untouched"),[("preserve",)])
        self.assertTrue(all(row["historical_completeness"]=="NOT_VERIFIED"
                            for row in report["source_coverage"]))

    def test_original_raw_bytes_persist_exactly_including_whitespace(self):
        scenario=scenarios()[0]
        data=raw(scenario[3])+b" "
        result=self.apply(scenario,source_raw=data)
        self.assertEqual(result["state"],"PAGE_COMMITTED_OFFLINE")
        stored=self.rows("SELECT raw_payload,raw_sha256 FROM package_a_receipts")[0]
        self.assertEqual(stored[0],data)
        self.assertEqual(stored[1],hashlib.sha256(data).hexdigest())

    def test_idempotent_exact_page_replay_does_not_replace_original_owner(self):
        scenario=scenarios()[0]
        first=self.apply(scenario)
        before=self.rows("SELECT receipt_id FROM package_a_observations")
        again=self.apply(scenario)
        self.assertFalse(again["cursor_advanced"])
        self.assertEqual(again["new_rows"],0)
        self.assertEqual(before,self.rows("SELECT receipt_id FROM package_a_observations"))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])
        self.assertNotEqual(first["receipt_id"],again["receipt_id"])

    def test_offline_frozen_429_is_a_failed_receipt_without_cursor(self):
        scenario=scenarios()[4]
        outcome=self.apply(scenario,status=429,source_raw=b'{"error":"fixture-429"}')
        self.assertEqual(outcome["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(outcome["failure_class"],"SYNTHETIC_HTTP_429")
        self.assertEqual(outcome["frozen_http_requests"],1)
        self.assertEqual(self.rows("SELECT status,http_status FROM package_a_receipts"),
                         [("failed",429)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.path,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_rbn_native_client_handles_mock_503_without_real_token(self):
        scenario=scenarios()[0]
        outcome=self.apply(scenario,status=503,source_raw=b'{"error":"fixture-503"}')
        self.assertEqual(outcome["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(outcome["frozen_http_requests"],1)
        self.assertEqual(self.rows("SELECT status,http_status FROM package_a_receipts"),
                         [("failed",503)])

    def test_explicit_empty_response_does_not_verify_provider_history(self):
        spec,start,end,_=scenarios()[0]
        outcome=run_mocked_legacy_page(self.path,self.locks,spec,start,end,
                    raw_bytes=b'{"data":[]}',now=NOW)
        self.assertEqual(outcome["state"],"PAGE_EMPTY_UNVERIFIED")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(outcome["historical_completeness"],"NOT_VERIFIED")

    def test_lock_contention_occurs_before_frozen_request_or_sql(self):
        spec,start,end,payload=scenarios()[0]
        with hold_writer_lock(self.locks,namespace="btc-research-global-writer-v1"):
            blocked=self.apply((spec,start,end,payload))
        self.assertEqual(blocked["state"],"BLOCKED_WRITER_BUSY")
        self.assertEqual(blocked["frozen_http_requests"],0)
        self.assertEqual(blocked["sqlite_connections_started"],0)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])
        self.assertEqual(self.apply((spec,start,end,payload))["state"],
                         "PAGE_COMMITTED_OFFLINE")

    def test_crash_after_receipt_preserves_schema_and_no_partial_page(self):
        scenario=scenarios()[0]
        from storage.package_a_ledger import SimulatedCrash
        with self.assertRaises(SimulatedCrash):
            self.apply(scenario,fault="after_receipt")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(self.apply(scenario)["state"],"PAGE_COMMITTED_OFFLINE")

    def test_frozen_http_rejects_unexpected_host_path_and_query(self):
        url,params=_request_for(*scenarios()[0][:3])
        frozen=FrozenLegacyHTTP(url=url,params=params,body=b'{}',status=200,rbn=True)
        with self.assertRaisesRegex(ValueError,"ENDPOINT_OR_QUERY_DRIFT"):
            frozen.open(Request(url.replace("api.researchbitcoin.net","evil.invalid")
                                +"?from_time=2026-10-08"),timeout=20)
        self.assertEqual(frozen.calls,0)

    def test_frozen_http_rejects_nonfixture_auth_header(self):
        url,params=_request_for(*scenarios()[0][:3])
        from urllib.parse import urlencode
        frozen=FrozenLegacyHTTP(url=url,params=params,body=b'{}',status=200,rbn=True)
        req=Request(url+"?"+urlencode(params),headers={"X-API-Token":"REAL_TOKEN_NEVER_ALLOWED"})
        with self.assertRaisesRegex(ValueError,"NON_FIXTURE_AUTH_TOKEN"):
            frozen.open(req,timeout=20)
        self.assertEqual(frozen.calls,0)

    def test_frozen_http_allows_only_one_request(self):
        url,params=_request_for(*scenarios()[4][:3])
        from urllib.parse import urlencode
        frozen=FrozenLegacyHTTP(url=url,params=params,body=b'[]',status=200)
        req=Request(url+"?"+urlencode(params))
        with frozen.open(req,timeout=15) as res:
            self.assertEqual(res.read(),b'[]')
        with self.assertRaisesRegex(ValueError,"MULTI_REQUEST"):
            frozen.open(req,timeout=15)
        self.assertEqual(frozen.calls,1)

    def test_noncatalogued_bitview_series_is_rejected_before_mock_http(self):
        spec=SeriesSpec("bitview","unknown","BTC","d1","ratio")
        with self.assertRaisesRegex(ValueError,"legacy d1 catalog"):
            run_mocked_legacy_page(self.path,self.locks,spec,DAY,NEXT,
                                   raw_bytes=b'{"data":[]}',now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_fred_frequency_mismatch_is_rejected(self):
        spec=SeriesSpec("fred","CPIAUCSL","USA","d1","index")
        with self.assertRaisesRegex(ValueError,"frequency mismatch"):
            run_mocked_legacy_page(self.path,self.locks,spec,DAY,NEXT,
                                   raw_bytes=b'{"observations":[]}',now=NOW)

    def test_yahoo_client_wrong_date_is_rejected_before_any_insert(self):
        spec,start,end,payload=scenarios()[3]
        invalid={"chart":{"result":[{"timestamp":[unix("2026-10-06T00:00:00Z")],
                                    "indicators":{"quote":[{"close":[60000]}]}}]}}
        result=run_mocked_legacy_page(self.path,self.locks,spec,start,end,
                     raw_bytes=raw(invalid),now=NOW)
        self.assertEqual(result["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_observations"),[(0,)])

    def test_payload_larger_than_budget_is_rejected_before_lock_or_sql(self):
        scenario=scenarios()[0]
        with self.assertRaisesRegex(ValueError,"bounded in-memory"):
            self.apply(scenario,source_raw=b'x'*2_000_001)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_test_only_transport_does_not_store_real_api_credential(self):
        self.apply(scenarios()[0])
        stored=self.rows("SELECT raw_payload FROM package_a_receipts")[0][0]
        self.assertNotIn(FAKE_KEY.encode(),stored)
        self.assertNotIn(b'api_key',stored)


    def test_binance_429_opaque_html_body_preserves_byte_exact_raw(self):
        scenario=scenarios()[4]
        opaque=b'<html><body>rate limit / request denied</body></html>'
        report=self.apply(scenario,status=429,source_raw=opaque)
        self.assertEqual(report["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(report["frozen_http_requests"],1)
        self.assertEqual(report["page_provenance_gate"],"RAW_SQL_LINEAGE_PASS")
        self.assertEqual(self.rows("SELECT raw_payload,raw_sha256 FROM package_a_receipts"),
                         [(opaque,hashlib.sha256(opaque).hexdigest())])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_observations"),[(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])

    def test_rbn_503_plain_text_body_is_not_falsely_interpreted_as_empty(self):
        scenario=scenarios()[0]
        opaque=b"temporarily unavailable"
        outcome=self.apply(scenario,status=503,source_raw=opaque)
        self.assertEqual(outcome["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(self.rows("SELECT status,http_status,raw_payload FROM package_a_receipts"),
                         [("failed",503,opaque)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.path,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_successful_http_200_invalid_json_blocks_without_sql_mutation(self):
        with self.assertRaises(json.JSONDecodeError):
            self.apply(scenarios()[4],source_raw=b'{"broken":}')
        self.assertEqual(self.rows("SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_failed_opaque_replay_does_not_fake_availability_or_cursor(self):
        scenario=scenarios()[7]
        opaque=b"503 Service Unavailable"
        first=self.apply(scenario,status=503,source_raw=opaque)
        second=self.apply(scenario,status=503,source_raw=opaque)
        self.assertEqual(first["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(second["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.path,self.locks)["historical_completeness"],
                         "NOT_VERIFIED")


    def test_injected_transport_timeout_records_no_http_code_or_raw_and_no_cursor(self):
        scenario=scenarios()[4]
        timeout=self.apply(scenario,status=None,source_raw=b"")
        self.assertEqual(timeout["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(timeout["failure_class"],"SYNTHETIC_TRANSPORT_TIMEOUT")
        self.assertEqual(timeout["frozen_http_requests"],1)
        self.assertFalse(timeout["original_raw_bytes_preserved"])
        self.assertEqual(self.rows(
            "SELECT status,http_status,raw_payload,raw_sha256 FROM package_a_receipts"),
            [("failed",None,None,None)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.path,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_timeout_then_exact_recovery_retains_failed_attempt_provenance(self):
        scenario=scenarios()[0]
        timeout=self.apply(scenario,status=None,source_raw=b"")
        recovered=self.apply(scenario)
        self.assertEqual(timeout["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(recovered["state"],"PAGE_COMMITTED_OFFLINE")
        self.assertEqual(self.rows(
            "SELECT status,http_status FROM package_a_receipts ORDER BY rowid"),
            [("failed",None),("ok",200)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(1,)])
        audit=inspect_fixture(self.path,self.locks)
        self.assertEqual(audit["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(audit["historical_request_evidence"][0]["attempted_requests"],2)
        self.assertEqual(audit["historical_request_evidence"][0]["historical_completeness"],"NOT_VERIFIED")


    def test_all_eight_mock_requests_have_exact_sanitized_v2_provenance(self):
        for scenario in scenarios():
            self.apply(scenario)
        report=inspect_fixture(self.path,self.locks)
        self.assertEqual(report["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        evidence=report["request_provenance"]
        self.assertEqual(evidence["state"],"FROZEN_REQUEST_METADATA_PASS")
        self.assertEqual(evidence["frozen_requests_with_metadata"],8)
        self.assertEqual(evidence["receipts_without_frozen_request_metadata"],0)
        self.assertFalse(evidence["upstream_requests_verified"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT version FROM package_a_schema_migrations").fetchall(),[(2,)])
            fred=db.execute(
                "SELECT request_params_json FROM package_a_request_provenance "
                "WHERE endpoint_origin='api.stlouisfed.org'").fetchone()[0]
        self.assertEqual(json.loads(fred)["api_key"],"[REDACTED]")
        self.assertNotIn(FAKE_KEY,fred)
        self.assertNotIn("FRED_API_KEY",fred)

    def test_direct_legacy_fixture_v1_upgrades_without_fabricating_old_requests(self):
        from storage import package_a_ledger as ledger
        # Build a faithful old version-1 Package A schema in disposable SQLite.
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for statement in ledger.DDL[:-1]:
                db.execute(statement)
            db.execute(
                "INSERT INTO package_a_schema_migrations VALUES(1,?)", (NOW,))
            db.execute(
                "INSERT INTO package_a_receipts "
                "(receipt_id,provider,metric,symbol,frequency,start_utc,"
                "end_exclusive_utc,status,captured_utc) VALUES "
                "('old','binance','open_interest','BTCUSDT','5m',?,?, 'failed',?)",
                (DAY,OI_END,NOW))
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            ledger.initialize(db,now=NOW)
            self.assertEqual(db.execute(
                "SELECT version FROM package_a_schema_migrations").fetchall(),[(2,)])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_request_provenance").fetchone()[0],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_receipts").fetchone()[0],1)
            self.assertEqual(ledger.audit_frozen_request_provenance(db)[
                "receipts_without_frozen_request_metadata"],1)
            ledger.initialize(db,now=NOW)
            self.assertEqual(db.execute(
                "SELECT version FROM package_a_schema_migrations").fetchall(),[(2,)])

    def test_tampered_request_metadata_blocks_new_atomic_page_write(self):
        first=scenarios()[0]
        second=scenarios()[4]
        self.apply(first)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_request_provenance "
                       "SET request_params_json='{}'")
        audit=inspect_fixture(self.path,self.locks)
        self.assertEqual(audit["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(audit["request_provenance"]["metadata_digest_errors"],1)
        with self.assertRaisesRegex(ValueError,"BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE"):
            self.apply(second)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(1,)])

    def test_fake_v2_metadata_table_without_primary_key_is_rejected(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE package_a_request_provenance("
                "receipt_id TEXT,endpoint_origin TEXT,endpoint_path TEXT,"
                "request_params_json TEXT,request_sha256 TEXT,"
                "transport TEXT,method TEXT,captured_utc TEXT)")
        with self.assertRaisesRegex(ValueError,"primary key"):
            self.apply(scenarios()[0])
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='package_a_receipts'"),[(0,)])

    def test_opaque_rate_limit_still_keeps_sanitized_query_lineage(self):
        scenario=scenarios()[0]
        self.apply(scenario,status=429,source_raw=b"<html>429</html>")
        with sqlite3.connect(self.path) as db:
            details=db.execute(
                "SELECT request_params_json,request_sha256,transport FROM "
                "package_a_request_provenance").fetchone()
        self.assertTrue(len(details[1])==64)
        self.assertEqual(details[2],"FROZEN_OFFLINE")
        self.assertEqual(json.loads(details[0])["from_time"],"2026-10-08")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
