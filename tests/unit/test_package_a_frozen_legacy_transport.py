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
from scripts.package_a_batch_fixture import (FrozenLegacyPage, FixturePage, run_synthetic_batch,
    plan_synthetic_resume, recover_synthetic_batch)
from scripts.package_a_ingest_fixture import FixtureResponse, run_fixture_page
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
                "SELECT version FROM package_a_schema_migrations").fetchall(),[(3,)])
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
            for statement in ledger.DDL[:-3]:
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
                "SELECT version FROM package_a_schema_migrations").fetchall(),[(3,)])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_request_provenance").fetchone()[0],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_receipts").fetchone()[0],1)
            self.assertEqual(ledger.audit_frozen_request_provenance(db)[
                "receipts_without_frozen_request_metadata"],1)
            ledger.initialize(db,now=NOW)
            self.assertEqual(db.execute(
                "SELECT version FROM package_a_schema_migrations").fetchall(),[(3,)])

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


    def as_frozen_page(self, scenario, *, status=200, body=None):
        spec,start,end,payload=scenario
        return FrozenLegacyPage(spec,start,end,raw(payload) if body is None else body,status)

    def test_integrated_batch_eight_frozen_providers_reconciles_all_receipts(self):
        pages=[self.as_frozen_page(s) for s in scenarios()]
        result=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        self.assertEqual(result["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(result["frozen_http_requests"],8)
        self.assertEqual(result["new_observations"],14)
        self.assertEqual(result["requests_attempted"],8)
        self.assertEqual(result["audit"]["request_provenance"]["frozen_requests_with_metadata"],8)
        self.assertEqual(result["audit"]["receipt_reconstruction"]["successful_receipts_reparsed"],8)
        self.assertEqual(result["historical_completeness"],"NOT_VERIFIED")
        self.assertFalse(result["production_authorized"])

    def test_frozen_multi_provider_batch_stops_on_429_then_resumes(self):
        first=scenarios()[0]
        second=scenarios()[4]
        third=scenarios()[2]
        failed=run_synthetic_batch(self.path,self.locks,[
            self.as_frozen_page(first),
            self.as_frozen_page(second,status=429,body=b"429 HTML"),
            self.as_frozen_page(third)],now=NOW)
        self.assertEqual(failed["state"],"BATCH_BLOCKED")
        self.assertEqual(failed["requests_attempted"],2)
        self.assertEqual(failed["remaining_requests_not_run"],1)
        self.assertEqual(failed["frozen_http_requests"],2)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(1,)])
        resumed=run_synthetic_batch(self.path,self.locks,[
            self.as_frozen_page(second),self.as_frozen_page(third)],now=NOW)
        self.assertEqual(resumed["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(resumed["new_observations"],4)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(3,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(4,)])
        self.assertEqual(inspect_fixture(self.path,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_frozen_batch_timeout_stops_before_later_mock_requests(self):
        pages=[self.as_frozen_page(scenarios()[4],status=None,body=b""),
               self.as_frozen_page(scenarios()[0])]
        result=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        self.assertEqual(result["state"],"BATCH_BLOCKED")
        self.assertEqual(result["requests_attempted"],1)
        self.assertEqual(result["frozen_http_requests"],1)
        self.assertEqual(result["remaining_requests_not_run"],1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        retry=run_synthetic_batch(self.path,self.locks,
                    [self.as_frozen_page(scenarios()[4]),pages[1]],now=NOW)
        self.assertEqual(retry["state"],"BATCH_OFFLINE_ACCEPTED")

    def test_frozen_batch_lock_is_taken_before_first_provider_mock(self):
        pages=[self.as_frozen_page(scenarios()[0])]
        with hold_writer_lock(self.locks,namespace="btc-research-global-writer-v1"):
            blocked=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        self.assertEqual(blocked["state"],"BATCH_BLOCKED")
        self.assertEqual(blocked["frozen_http_requests"],0)
        self.assertEqual(blocked["pages"][0]["state"],"BLOCKED_WRITER_BUSY")
        self.assertIsNone(blocked["audit"])

    def test_mixed_direct_and_frozen_fixture_provenance_is_not_fabricated(self):
        sc=scenarios()[0]
        direct=FixturePage(sc[0],sc[1],sc[2],FixtureResponse(200,sc[3]))
        other=self.as_frozen_page(scenarios()[4])
        report=run_synthetic_batch(self.path,self.locks,[direct,other],now=NOW)
        self.assertEqual(report["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(report["audit"]["request_provenance"]["frozen_requests_with_metadata"],1)
        self.assertEqual(report["audit"]["request_provenance"]["receipts_without_frozen_request_metadata"],1)
        self.assertFalse(report["audit"]["request_provenance"]["legacy_metadata_retroactively_fabricated"])

    def test_frozen_batch_does_not_commit_when_existing_request_metadata_tampered(self):
        self.apply(scenarios()[0])
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_request_provenance SET request_sha256=?",("0"*64,))
        with self.assertRaisesRegex(ValueError,"BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE"):
            run_synthetic_batch(self.path,self.locks,[self.as_frozen_page(scenarios()[4])],now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(1,)])

    def test_frozen_batch_rejects_over_budget_before_any_sql(self):
        pages=[self.as_frozen_page(scenarios()[0]) for _ in range(33)]
        with self.assertRaisesRegex(ValueError,"Synthetic batch budget"):
            run_synthetic_batch(self.path,self.locks,pages,now=NOW,max_pages=33)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_frozen_batch_replay_is_idempotent_with_original_request_owner(self):
        page=self.as_frozen_page(scenarios()[0])
        batch=run_synthetic_batch(self.path,self.locks,[page,page],now=NOW)
        self.assertEqual(batch["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(batch["new_observations"],2)
        self.assertEqual(batch["frozen_http_requests"],2)
        self.assertEqual(batch["audit"]["request_provenance"]["frozen_requests_with_metadata"],2)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_observations"),[(2,)])


    def test_v3_eight_source_run_pages_atomically_linked_to_receipts(self):
        result=run_synthetic_batch(
            self.path,self.locks,[self.as_frozen_page(s) for s in scenarios()],now=NOW)
        self.assertEqual(result["state"],"BATCH_OFFLINE_ACCEPTED")
        audit=inspect_fixture(self.path,self.locks)["source_runs"]
        self.assertEqual(audit["state"],"BATCH_SOURCE_RUN_LINEAGE_PASS")
        self.assertEqual(audit["runs"],1)
        self.assertEqual(audit["complete_runs"],1)
        self.assertEqual(audit["receipts_without_batch_run"],0)
        self.assertEqual(self.rows(
            "SELECT status,planned_pages,mode FROM package_a_source_runs"),
            [("complete",8,"OFFLINE_SYNTHETIC_BATCH")])
        self.assertEqual(self.rows(
            "SELECT ordinal,receipt_status FROM package_a_run_pages ORDER BY ordinal"),
            [(n,"ok") for n in range(8)])
        self.assertEqual(len(result["run_id"]),32)
        self.assertEqual(self.rows(
            "SELECT COUNT(DISTINCT run_id) FROM package_a_run_pages"),[(1,)])

    def test_v3_failed_batch_and_clean_retry_have_distinct_durable_run_ids(self):
        first=self.as_frozen_page(scenarios()[0])
        fault=self.as_frozen_page(scenarios()[4],status=429,body=b"429 html")
        pending=self.as_frozen_page(scenarios()[2])
        blocked=run_synthetic_batch(self.path,self.locks,[first,fault,pending],now=NOW)
        self.assertEqual(blocked["state"],"BATCH_BLOCKED")
        source=inspect_fixture(self.path,self.locks)["source_runs"]
        self.assertEqual(source["blocked_runs"],1)
        self.assertEqual(source["complete_runs"],0)
        good=run_synthetic_batch(self.path,self.locks,
                    [self.as_frozen_page(scenarios()[4]),pending],now=NOW)
        self.assertNotEqual(blocked["run_id"],good["run_id"])
        source=inspect_fixture(self.path,self.locks)["source_runs"]
        self.assertEqual(source["runs"],2)
        self.assertEqual(source["blocked_runs"],1)
        self.assertEqual(source["complete_runs"],1)
        self.assertEqual(source["receipts_without_batch_run"],0)
        self.assertEqual(self.rows(
            "SELECT status,planned_pages FROM package_a_source_runs ORDER BY status"),
            [("blocked",3),("complete",2)])

    def test_v3_crash_during_source_run_link_rolls_back_entire_first_page(self):
        from unittest.mock import patch
        from storage import package_a_ledger as ledger
        real=ledger.record_batch_run_page
        def crash(*args,**kwargs):
            real(*args,**kwargs)
            raise ledger.SimulatedCrash("after_run_page_link")
        with patch.object(ledger,"record_batch_run_page",side_effect=crash):
            with self.assertRaisesRegex(ledger.SimulatedCrash,"after_run_page_link"):
                run_synthetic_batch(self.path,self.locks,
                    [self.as_frozen_page(scenarios()[0])],now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),[(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_run_pages"),[(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        recovered=run_synthetic_batch(self.path,self.locks,
                        [self.as_frozen_page(scenarios()[0])],now=NOW)
        self.assertEqual(recovered["state"],"BATCH_OFFLINE_ACCEPTED")

    def test_v3_crash_between_pages_marks_first_run_interrupted_not_complete(self):
        from unittest.mock import patch
        import scripts.package_a_batch_fixture as batch
        from storage import package_a_ledger as ledger
        original=batch.run_mocked_legacy_page
        calls=[0]
        def crash_between(*args,**kwargs):
            calls[0]+=1
            if calls[0]==2:
                raise ledger.SimulatedCrash("between_pages")
            return original(*args,**kwargs)
        pages=[self.as_frozen_page(scenarios()[0]),
               self.as_frozen_page(scenarios()[4])]
        with patch.object(batch,"run_mocked_legacy_page",side_effect=crash_between):
            with self.assertRaisesRegex(ledger.SimulatedCrash,"between_pages"):
                run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        audit=inspect_fixture(self.path,self.locks)
        self.assertEqual(audit["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(audit["source_runs"]["interrupted_or_running_runs"],1)
        self.assertEqual(self.rows("SELECT status,planned_pages FROM package_a_source_runs"),
                         [("running",2)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_run_pages"),[(1,)])
        resumed=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        self.assertEqual(resumed["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(resumed["new_observations"],2)
        self.assertEqual(inspect_fixture(self.path,self.locks)["source_runs"]["complete_runs"],1)
        self.assertEqual(inspect_fixture(self.path,self.locks)["source_runs"]["interrupted_or_running_runs"],1)

    def test_v3_truncated_complete_run_is_detected_before_further_writes(self):
        page=self.as_frozen_page(scenarios()[0])
        batch=run_synthetic_batch(self.path,self.locks,[page,page],now=NOW)
        self.assertEqual(batch["state"],"BATCH_OFFLINE_ACCEPTED")
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM package_a_run_pages WHERE ordinal=1")
        quality=inspect_fixture(self.path,self.locks)
        self.assertEqual(quality["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(quality["source_runs"]["run_integrity_errors"],1)
        with self.assertRaisesRegex(ValueError,"BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE"):
            self.apply(scenarios()[4])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])

    def test_v3_run_receipt_status_forgery_trips_integrity_gate(self):
        batch=run_synthetic_batch(self.path,self.locks,
                    [self.as_frozen_page(scenarios()[0])],now=NOW)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_run_pages SET receipt_status='failed'")
        quality=inspect_fixture(self.path,self.locks)
        self.assertEqual(quality["source_runs"]["state"],"BATCH_SOURCE_RUN_LINEAGE_FAIL")
        self.assertEqual(quality["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        with self.assertRaisesRegex(ValueError,"BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE"):
            self.apply(scenarios()[4])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])

    def test_v3_upgrade_from_genuine_version2_retains_receipts_without_fake_run(self):
        from storage import package_a_ledger as ledger
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for ddl in ledger.DDL[:-2]:
                db.execute(ddl)
            db.execute("INSERT INTO package_a_schema_migrations VALUES(2,?)",(NOW,))
            db.execute(
                "INSERT INTO package_a_receipts "
                "(receipt_id,provider,metric,symbol,frequency,start_utc,"
                "end_exclusive_utc,status,captured_utc) "
                "VALUES('legacy','binance','open_interest','BTCUSDT','5m',?,?,'failed',?)",
                (DAY,OI_END,NOW))
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            ledger.initialize(db,now=NOW)
            ledger.initialize(db,now=NOW)
            self.assertEqual(db.execute(
                "SELECT version FROM package_a_schema_migrations").fetchall(),[(3,)])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_receipts").fetchone()[0],1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_source_runs").fetchone()[0],0)
            self.assertEqual(ledger.audit_batch_source_runs(db)[
                "receipts_without_batch_run"],1)
        self.assertEqual(inspect_fixture(self.path,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_v3_source_run_fail_closed_for_duplicate_and_skipped_ordinals(self):
        from storage import package_a_ledger as ledger
        first=self.as_frozen_page(scenarios()[0])
        run=run_synthetic_batch(self.path,self.locks,[first],now=NOW)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaisesRegex(ValueError,"closed"):
                ledger.record_batch_run_page(
                    db,run_id=run["run_id"],ordinal=0,planned_pages=1,
                    receipt_id=self.rows("SELECT receipt_id FROM package_a_receipts")[0][0],
                    receipt_status="ok",now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_run_pages"),[(1,)])

    def test_v3_batch_timeout_is_blocked_and_never_claimed_complete(self):
        page=self.as_frozen_page(scenarios()[0],status=None,body=b"")
        blocked=run_synthetic_batch(self.path,self.locks,[page],now=NOW)
        self.assertEqual(blocked["state"],"BATCH_BLOCKED")
        self.assertEqual(self.rows(
            "SELECT status FROM package_a_source_runs"),[("blocked",)])
        self.assertEqual(self.rows(
            "SELECT receipt_status FROM package_a_run_pages"),[("failed",)])
        self.assertEqual(inspect_fixture(self.path,self.locks)["source_runs"]["blocked_runs"],1)


    def test_v3_forged_run_pages_without_unique_receipt_owner_fails_schema(self):
        # PK and both FKs deliberately match, but one receipt could otherwise
        # be claimed by multiple distinct batch runs.
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE package_a_run_pages ("
                "run_id TEXT NOT NULL,ordinal INTEGER NOT NULL,"
                "receipt_id TEXT NOT NULL,receipt_status TEXT NOT NULL,"
                "PRIMARY KEY(run_id,ordinal),"
                "FOREIGN KEY(run_id) REFERENCES package_a_source_runs(run_id),"
                "FOREIGN KEY(receipt_id) REFERENCES package_a_receipts(receipt_id))")
        with self.assertRaisesRegex(ValueError,"unique Package A run receipt"):
            self.apply(scenarios()[0])
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_run_pages'"),[(1,)])

    def test_v3_run_page_one_receipt_cannot_be_reused_across_runs(self):
        from storage import package_a_ledger as ledger
        first=run_synthetic_batch(self.path,self.locks,
                    [self.as_frozen_page(scenarios()[0])],now=NOW)
        row=self.rows("SELECT receipt_id FROM package_a_run_pages")[0][0]
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO package_a_source_runs VALUES(?,?,?,?,'running',"
                    "'OFFLINE_SYNTHETIC_BATCH')",
                    ("a"*32,1,NOW,NOW))
                db.execute("INSERT INTO package_a_run_pages VALUES(?,?,?,'ok')",
                           ("a"*32,0,row))
            db.rollback()  # the failed UNIQUE statement must not commit its parent row
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_run_pages"),[(1,)])
        self.assertEqual(inspect_fixture(self.path,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")


    def test_recovery_skips_verified_successful_prefix_after_429(self):
        pages=[self.as_frozen_page(s) for s in (scenarios()[0],scenarios()[4],scenarios()[2])]
        failed=[pages[0],self.as_frozen_page(scenarios()[4],status=429,body=b"429 HTML"),pages[2]]
        prior=run_synthetic_batch(self.path,self.locks,failed,now=NOW)
        self.assertEqual(prior["state"],"BATCH_BLOCKED")
        plan=plan_synthetic_resume(self.path,self.locks,pages,prior_run_id=prior["run_id"])
        self.assertEqual((plan["resume_index"],plan["remaining_pages"]),(1,2))
        before=self.rows("SELECT receipt_id FROM package_a_observations ORDER BY observed_utc")
        result=recover_synthetic_batch(self.path,self.locks,pages,
                                       prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(result["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(result["recovery_start_index"],1)
        self.assertEqual(result["frozen_http_requests"],2)
        self.assertNotEqual(result["run_id"],prior["run_id"])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(4,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),[(2,)])
        self.assertEqual(self.rows("SELECT status FROM package_a_source_runs ORDER BY status"),
                         [("blocked",),("complete",)])
        self.assertEqual(self.rows("SELECT receipt_id FROM package_a_observations "
                                   "WHERE provider='researchbitcoin' ORDER BY observed_utc"),before)
        self.assertEqual(inspect_fixture(self.path,self.locks)["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_recovery_crash_between_pages_reads_persisted_running_prefix(self):
        import scripts.package_a_batch_fixture as batch
        from storage.package_a_ledger import SimulatedCrash
        from unittest.mock import patch
        orig=batch.run_mocked_legacy_page
        seen=[0]
        def interrupt(*a,**kw):
            seen[0]+=1
            if seen[0]==2:
                raise SimulatedCrash("interrupted_before_next_request")
            return orig(*a,**kw)
        pages=[self.as_frozen_page(scenarios()[0]),self.as_frozen_page(scenarios()[4])]
        with patch.object(batch,"run_mocked_legacy_page",side_effect=interrupt):
            with self.assertRaisesRegex(SimulatedCrash,"interrupted_before_next_request"):
                run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        original=self.rows("SELECT run_id FROM package_a_source_runs")[0][0]
        self.assertEqual(self.rows("SELECT status FROM package_a_source_runs"),[("running",)])
        preflight=plan_synthetic_resume(self.path,self.locks,pages,
                                        prior_run_id=original)
        self.assertEqual(preflight["resume_index"],1)
        result=recover_synthetic_batch(self.path,self.locks,pages,
                                       prior_run_id=original,now=NOW)
        self.assertEqual(result["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(result["requests_attempted"],1)
        self.assertEqual(self.rows("SELECT status FROM package_a_source_runs ORDER BY status"),
                         [("complete",),("running",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])

    def test_completed_recovery_noop_adds_no_sql_receipts_or_mock_http(self):
        pages=[self.as_frozen_page(scenarios()[0])]
        prior=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        before=self.rows("SELECT COUNT(*) FROM package_a_receipts")
        result=recover_synthetic_batch(self.path,self.locks,pages,
                                       prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(result["state"],"RESUME_ALREADY_COMPLETE")
        self.assertEqual(result["requests_attempted"],0)
        self.assertEqual(result["frozen_http_requests"],0)
        self.assertIsNone(result["new_run_id"])
        self.assertEqual(before,self.rows("SELECT COUNT(*) FROM package_a_receipts"))

    def test_resume_rejects_modified_successful_prefix_before_http_and_sql(self):
        original=[self.as_frozen_page(scenarios()[0]),
                  self.as_frozen_page(scenarios()[4],status=503,body=b"503")]
        prior=run_synthetic_batch(self.path,self.locks,original,now=NOW)
        modified=list(original)
        sc=scenarios()[0]
        modified[0]=self.as_frozen_page(sc,body=raw({"data":[
            {"date":"2026-10-08","value":0.1},
            {"date":"2026-10-09","value":0.2}]}))
        with self.assertRaisesRegex(ValueError,"SUCCESS_RAW_DRIFT"):
            recover_synthetic_batch(self.path,self.locks,modified,
                                    prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])

    def test_resume_rejects_changed_provider_time_window_before_http(self):
        old=[self.as_frozen_page(scenarios()[0]),
             self.as_frozen_page(scenarios()[4],status=429,body=b"429")]
        prior=run_synthetic_batch(self.path,self.locks,old,now=NOW)
        altered=[self.as_frozen_page(scenarios()[0]),
                 self.as_frozen_page(scenarios()[5])]
        with self.assertRaisesRegex(ValueError,"REQUEST_IDENTITY_DRIFT"):
            recover_synthetic_batch(self.path,self.locks,altered,
                                    prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])

    def test_resume_rejects_forged_original_page_count_and_unknown_run(self):
        pages=[self.as_frozen_page(scenarios()[0]),
               self.as_frozen_page(scenarios()[4],status=429,body=b"429")]
        prior=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        with self.assertRaisesRegex(ValueError,"PLAN_LENGTH_DRIFT"):
            plan_synthetic_resume(self.path,self.locks,pages[:1],
                                  prior_run_id=prior["run_id"])
        with self.assertRaisesRegex(ValueError,"UNKNOWN_RUN"):
            plan_synthetic_resume(self.path,self.locks,pages,
                                  prior_run_id="f"*32)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])

    def test_resume_refuses_corrupt_prior_receipt_and_row_sha(self):
        pages=[self.as_frozen_page(scenarios()[0]),
               self.as_frozen_page(scenarios()[4],status=503,body=b"503")]
        prior=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_receipts SET raw_sha256=? WHERE status='ok'",("0"*64,))
        with self.assertRaisesRegex(ValueError,"RECOVERY_DB_INTEGRITY"):
            recover_synthetic_batch(self.path,self.locks,pages,
                                    prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])

    def test_recovery_lock_busy_returns_before_sqlite_and_frozen_http(self):
        original=[self.as_frozen_page(scenarios()[0]),
                  self.as_frozen_page(scenarios()[4],status=429,body=b"429")]
        prior=run_synthetic_batch(self.path,self.locks,original,now=NOW)
        with hold_writer_lock(self.locks,namespace="btc-research-global-writer-v1"):
            blocked=recover_synthetic_batch(self.path,self.locks,original,
                                             prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(blocked["state"],"BLOCKED_WRITER_BUSY")
        self.assertEqual(blocked["frozen_http_requests"],0)
        self.assertEqual(blocked["requests_attempted"],0)
        self.assertEqual(blocked["sqlite_connections_started"],0)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])

    def test_recovery_retries_failed_timeout_as_success_without_fake_old_raw(self):
        page=self.as_frozen_page(scenarios()[4])
        attempt=self.as_frozen_page(scenarios()[4],status=None,body=b"")
        prior=run_synthetic_batch(self.path,self.locks,[attempt],now=NOW)
        self.assertEqual(prior["state"],"BATCH_BLOCKED")
        response=recover_synthetic_batch(self.path,self.locks,[page],
                                         prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(response["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(response["recovery_start_index"],0)
        self.assertEqual(self.rows(
            "SELECT status,http_status FROM package_a_receipts ORDER BY rowid"),
            [("failed",None),("ok",200)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_cursors"),[(1,)])
        self.assertFalse(response["real_provider_windows_verified"])

    def test_read_only_recovery_plan_does_not_add_sql_receipts_or_runs(self):
        pages=[self.as_frozen_page(scenarios()[0]),
               self.as_frozen_page(scenarios()[4],status=429,body=b"429")]
        prior=run_synthetic_batch(self.path,self.locks,pages,now=NOW)
        before=(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),
                self.rows("SELECT COUNT(*) FROM package_a_receipts"))
        p=plan_synthetic_resume(self.path,self.locks,pages,
                                prior_run_id=prior["run_id"])
        self.assertEqual(p["state"],"RESUME_PLANNED_OFFLINE")
        self.assertEqual(before,(self.rows("SELECT COUNT(*) FROM package_a_source_runs"),
                                 self.rows("SELECT COUNT(*) FROM package_a_receipts")))
        self.assertFalse(p["upstream_availability_verified"])
        self.assertFalse(p["production_authorized"])


    def test_recovery_rejects_successful_prefix_unit_scale_change_before_http(self):
        rbn=self.as_frozen_page(scenarios()[0])
        yahoo=self.as_frozen_page(scenarios()[3])
        failure=self.as_frozen_page(scenarios()[4],status=503,body=b"503")
        prior=run_synthetic_batch(self.path,self.locks,[rbn,yahoo,failure],now=NOW)
        spec,start,end,payload=scenarios()[3]
        from ingestion.package_a_contracts import SeriesSpec
        fake_unit=SeriesSpec(spec.provider,spec.metric,spec.symbol,spec.frequency,
                             "EUR",spec.raw_scale)
        changed=FrozenLegacyPage(fake_unit,start,end,raw(payload),200)
        before=self.rows("SELECT COUNT(*) FROM package_a_receipts")
        with self.assertRaisesRegex(ValueError,"UNIT_SCALE_DRIFT"):
            recover_synthetic_batch(self.path,self.locks,[rbn,changed,failure],
                                    prior_run_id=prior["run_id"],now=NOW)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),before)

    def test_recovery_accepts_equivalent_explicit_plus_zero_timezone_windows(self):
        one=self.as_frozen_page(scenarios()[0])
        two=self.as_frozen_page(scenarios()[4],status=429,body=b"429")
        prior=run_synthetic_batch(self.path,self.locks,[one,two],now=NOW)
        spec,start,end,payload=scenarios()[0]
        offset=FrozenLegacyPage(spec,start.replace("Z","+00:00"),
                                end.replace("Z","+00:00"),raw(payload),200)
        plan=plan_synthetic_resume(self.path,self.locks,[offset,two],
                                   prior_run_id=prior["run_id"])
        self.assertEqual(plan["resume_index"],1)
        self.assertEqual(plan["state"],"RESUME_PLANNED_OFFLINE")


    def test_cannot_spoof_parent_flock_with_bare_true_flag(self):
        scenario=scenarios()[0]
        original=self.as_frozen_page(scenario)
        with self.assertRaisesRegex(ValueError,"PARENT_FLOCK_LEASE_REQUIRED"):
            run_synthetic_batch(self.path,self.locks,[original],now=NOW,
                                _parent_lock_held=True)
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_cannot_spoof_parent_held_flag_via_frozen_or_direct_page(self):
        spec,start,end,payload=scenarios()[0]
        from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
        from scripts.package_a_ingest_fixture import run_fixture_page
        with self.assertRaisesRegex(ValueError,"PARENT_FLOCK_LEASE_REQUIRED"):
            run_mocked_legacy_page(self.path,self.locks,spec,start,end,
                raw_bytes=raw(payload),now=NOW,_parent_lock_held=True)
        with self.assertRaisesRegex(ValueError,"PARENT_FLOCK_LEASE_REQUIRED"):
            run_fixture_page(self.path,self.locks,spec,start,end,
                FixtureResponse(200,payload),now=NOW,_parent_lock_held=True)
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_real_offline_parent_lease_allows_child_and_revokes_after_release(self):
        from scripts.package_a_parent_lock import hold_offline_parent_lease
        page=self.as_frozen_page(scenarios()[0])
        with hold_offline_parent_lease(self.locks):
            outcome=run_synthetic_batch(self.path,self.locks,[page],now=NOW,
                                         _parent_lock_held=True)
            self.assertEqual(outcome["state"],"BATCH_OFFLINE_ACCEPTED")
        with self.assertRaisesRegex(ValueError,"PARENT_FLOCK_LEASE_REQUIRED"):
            run_synthetic_batch(self.path,self.locks,[page],now=NOW,
                                _parent_lock_held=True)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(1,)])

    def test_wrong_lock_dir_cannot_reuse_other_parent_lease(self):
        from scripts.package_a_parent_lock import verify_offline_parent_lease
        from scripts.package_a_parent_lock import hold_offline_parent_lease
        import tempfile
        with tempfile.TemporaryDirectory(prefix="package-a-fixture-") as root:
            other=Path(root)/"locks"
            other.mkdir()
            with hold_offline_parent_lease(self.locks):
                with self.assertRaisesRegex(ValueError,"PARENT_FLOCK_LEASE_REQUIRED"):
                    verify_offline_parent_lease(other)
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])


    def test_manifest_eight_streams_exposes_requested_observed_without_external_claim(self):
        report=run_synthetic_batch(self.path,self.locks,
                     [self.as_frozen_page(s) for s in scenarios()],now=NOW)
        self.assertEqual(report["state"],"BATCH_OFFLINE_ACCEPTED")
        manifest=report["audit"]["source_manifest"]
        self.assertEqual(manifest["state"],"SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS")
        self.assertEqual(manifest["stream_count"],8)
        self.assertEqual(manifest["contract_errors"],0)
        keys={(r["provider"],r["metric"]) for r in manifest["streams"]}
        self.assertEqual(len(keys),8)
        for stream in manifest["streams"]:
            self.assertEqual(stream["attempted_requests"],1)
            self.assertEqual(stream["request_status_counts"]["ok"],1)
            self.assertEqual(stream["successful_raw_receipts"],1)
            self.assertEqual(stream["successful_request_window_count"],1)
            self.assertIsNone(stream["earliest_provider_verified_utc"])
            self.assertEqual(stream["entitlement_status"],"NOT_VERIFIED")
            self.assertEqual(stream["provider_availability"],"NOT_CHECKED")
            self.assertEqual(stream["historical_completeness"],"NOT_VERIFIED")
            self.assertFalse(stream["production_authorized"])
            self.assertFalse(stream["request_ready"])
        self.assertFalse(manifest["entitlement_verified"])
        self.assertEqual(manifest["live_request_count"],0)

    def test_manifest_fred_vintages_and_funding_events_have_no_fabricated_holes(self):
        report=run_synthetic_batch(self.path,self.locks,[
                    self.as_frozen_page(scenarios()[2]),
                    self.as_frozen_page(scenarios()[6]),
                    self.as_frozen_page(scenarios()[7])],now=NOW)
        self.assertEqual(report["state"],"BATCH_OFFLINE_ACCEPTED")
        by_provider={(r["provider"],r["metric"]):r
                     for r in report["audit"]["source_manifest"]["streams"]}
        fred=by_provider[("fred","CPIAUCSL")]
        self.assertEqual(fred["source_native_semantics"],
                         "FRED_OBSERVATION_WITH_RELEASE_VINTAGES")
        self.assertEqual(fred["distinct_dimension_keys"],2)
        self.assertEqual(fred["distinct_observation_dates"],2)
        self.assertEqual(fred["local_grid_state"],
                         "NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS")
        for provider in ("binance","bybit"):
            funding=by_provider[(provider,"funding_settled")]
            self.assertEqual(funding["local_grid_state"],
                             "NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS")
            self.assertIsNone(funding["local_gap_evidence"])
            self.assertEqual(funding["source_native_semantics"],
                             "FUNDING_SETTLEMENT_EVENTS")

    def test_manifest_oi_local_grid_can_be_complete_without_provider_verification(self):
        outcome=self.apply(scenarios()[4])
        self.assertEqual(outcome["state"],"PAGE_COMMITTED_OFFLINE")
        streams=inspect_fixture(self.path,self.locks)["source_manifest"]["streams"]
        self.assertEqual(len(streams),1)
        oi=streams[0]
        self.assertEqual(oi["local_grid_state"],"OFFLINE_LOCAL_GAP_CANDIDATES")
        self.assertEqual(oi["local_gap_evidence"]["local_missing_candidate_slots"],0)
        self.assertEqual(oi["distinct_observation_dates"],2)
        self.assertEqual(oi["archived_rows"],2)
        self.assertFalse(oi["local_gap_evidence"]["provider_verified"])
        self.assertEqual(oi["historical_completeness"],"NOT_VERIFIED")

    def test_manifest_local_daily_missing_slot_is_only_candidate_not_request(self):
        spec,start,end,_=scenarios()[0]
        window_end="2026-10-11T00:00:00Z"
        result=run_fixture_page(self.path,self.locks,spec,start,window_end,
                      FixtureResponse(200,{"data":[
                          {"date":"2026-10-08","value":1.01},
                          {"date":"2026-10-10","value":1.02}]}),now=NOW)
        self.assertEqual(result["state"],"PAGE_COMMITTED_OFFLINE")
        manifest=inspect_fixture(self.path,self.locks)["source_manifest"]
        self.assertEqual(manifest["state"],"SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS")
        row=manifest["streams"][0]
        self.assertEqual(row["local_gap_evidence"]["local_missing_candidate_slots"],1)
        self.assertEqual(row["local_gap_evidence"]["windows"][0]["start_utc"],NEXT)
        self.assertFalse(row["local_gap_evidence"]["windows"][0]["request_ready"])
        self.assertEqual(row["provider_availability"],"NOT_CHECKED")

    def test_manifest_failed_only_stream_remains_visible_without_fabricated_rows(self):
        failed=self.as_frozen_page(scenarios()[4],status=503,body=b"<html>503</html>")
        blocked=run_synthetic_batch(self.path,self.locks,[failed],now=NOW)
        self.assertEqual(blocked["state"],"BATCH_BLOCKED")
        report=inspect_fixture(self.path,self.locks)
        self.assertEqual(report["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        row=report["source_manifest"]["streams"][0]
        self.assertEqual(row["attempted_requests"],1)
        self.assertEqual(row["request_status_counts"]["failed"],1)
        self.assertEqual(row["http_error_with_raw"],1)
        self.assertEqual(row["archived_rows"],0)
        self.assertEqual(row["successful_request_window_count"],0)
        self.assertEqual(row["local_grid_state"],"NOT_SCANNED_NO_ARCHIVED_CONTRACT")
        self.assertEqual(row["raw_receipt_coverage"],"NO_SUCCESS_RECEIPTS")

    def test_manifest_timeout_and_empty_are_distinct_from_upstream_unavailable(self):
        spec,start,end,_=scenarios()[0]
        run_fixture_page(self.path,self.locks,spec,start,end,
                         FixtureResponse(None,None),now=NOW)
        run_fixture_page(self.path,self.locks,spec,start,end,
                         FixtureResponse(200,{"data":[]}),now=NOW)
        report=inspect_fixture(self.path,self.locks)
        self.assertEqual(report["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        row=report["source_manifest"]["streams"][0]
        self.assertEqual(row["request_status_counts"]["failed"],1)
        self.assertEqual(row["request_status_counts"]["empty"],1)
        self.assertEqual(row["transport_failures_without_response"],1)
        self.assertEqual(row["archived_rows"],0)
        self.assertIsNone(row["earliest_provider_verified_utc"])
        self.assertEqual(row["historical_completeness"],"NOT_VERIFIED")

    def test_manifest_replay_coalesces_successful_request_windows_but_counts_attempts(self):
        page=self.as_frozen_page(scenarios()[0])
        result=run_synthetic_batch(self.path,self.locks,[page,page],now=NOW)
        self.assertEqual(result["state"],"BATCH_OFFLINE_ACCEPTED")
        row=result["audit"]["source_manifest"]["streams"][0]
        self.assertEqual(row["attempted_requests"],2)
        self.assertEqual(row["successful_raw_receipts"],2)
        self.assertEqual(row["successful_request_window_count"],1)
        self.assertEqual(row["archived_rows"],2)
        self.assertEqual(row["distinct_observation_dates"],2)

    def test_manifest_noncanonical_failed_receipt_window_fails_read_only_gate(self):
        self.apply(scenarios()[4],status=429,source_raw=b"429")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_receipts "
                       "SET start_utc='2026-10-08T00:00:00.500Z'")
        report=inspect_fixture(self.path,self.locks)
        self.assertEqual(report["source_manifest"]["state"],
                         "SOURCE_MANIFEST_LOCAL_EVIDENCE_FAIL")
        self.assertGreaterEqual(report["source_manifest"]["contract_errors"],1)
        self.assertEqual(report["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        with self.assertRaisesRegex(ValueError,"BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE"):
            self.apply(scenarios()[0])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM package_a_receipts"),[(1,)])

    def test_manifest_source_calendar_does_not_scan_fred_daily_business_holidays(self):
        spec=SeriesSpec("fred","DGS10","USA","d1","percent")
        result=run_fixture_page(self.path,self.locks,spec,DAY,AFTER,
                         FixtureResponse(200,{"observations":[
                            {"date":"2026-10-08","value":"4.2",
                             "realtime_start":"2026-10-09"}]}),now=NOW)
        self.assertEqual(result["state"],"PAGE_COMMITTED_OFFLINE")
        manifest=inspect_fixture(self.path,self.locks)["source_manifest"]
        self.assertEqual(manifest["state"],"SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS")
        row=manifest["streams"][0]
        self.assertEqual(row["local_grid_state"],
                         "NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS")
        self.assertIsNone(row["local_gap_evidence"])
        self.assertEqual(row["calendar_assumption"],"EXTERNAL_RELEASE_CALENDAR_NOT_CHECKED")

    def test_manifest_budget_is_fail_closed_and_never_authorizes_requests(self):
        from scripts.package_a_source_coverage_manifest import source_aware_manifest
        self.apply(scenarios()[0])
        with sqlite3.connect(self.path) as db:
            report=source_aware_manifest(db,max_grid_slots=1)
        self.assertEqual(report["state"],"SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS")
        row=report["streams"][0]
        self.assertEqual(row["local_grid_state"],"NOT_SCANNED_GRID_BUDGET_OR_ALIGNMENT")
        self.assertFalse(row["request_ready"])
        self.assertEqual(row["historical_completeness"],"NOT_VERIFIED")
        self.apply(scenarios()[4])
        with sqlite3.connect(self.path) as db:
            blocked=source_aware_manifest(db,max_streams=1)
        self.assertEqual(blocked["stream_count"],2)
        self.assertEqual(blocked["state"],"BLOCKED_SOURCE_MANIFEST_STREAM_BUDGET")
        self.assertEqual(blocked["streams"],[])
        self.assertFalse(blocked["production_authorized"])


    def test_manifest_fred_multiple_revisions_same_date_are_not_duplicate_bar_gaps(self):
        spec=SeriesSpec("fred","CPIAUCSL","USA","monthly","index")
        payload={"observations":[
            {"date":"2026-08-01","value":"322.0","realtime_start":"2026-09-01"},
            {"date":"2026-08-01","value":"322.5","realtime_start":"2026-10-01"}]}
        result=run_fixture_page(self.path,self.locks,spec,
            "2026-08-01T00:00:00Z","2026-09-01T00:00:00Z",
            FixtureResponse(200,payload),now=NOW)
        self.assertEqual(result["state"],"PAGE_COMMITTED_OFFLINE")
        m=inspect_fixture(self.path,self.locks)["source_manifest"]
        self.assertEqual(m["state"],"SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS")
        row=m["streams"][0]
        self.assertEqual(row["archived_rows"],2)
        self.assertEqual(row["distinct_observation_dates"],1)
        self.assertEqual(row["distinct_dimension_keys"],2)
        self.assertEqual(row["local_grid_state"],
                         "NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS")
        self.assertEqual(row["provider_availability"],"NOT_CHECKED")

    def test_manifest_successful_gaps_are_not_counted_again_as_absent_funding_events(self):
        spec,start,end,payload=scenarios()[6]
        result=run_fixture_page(self.path,self.locks,spec,start,end,
                                FixtureResponse(200,payload),now=NOW)
        self.assertEqual(result["state"],"PAGE_COMMITTED_OFFLINE")
        row=inspect_fixture(self.path,self.locks)["source_manifest"]["streams"][0]
        self.assertEqual(row["archived_rows"],1)
        self.assertEqual(row["local_grid_state"],
                         "NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS")
        self.assertIsNone(row["local_gap_evidence"])
        self.assertEqual(row["historical_completeness"],"NOT_VERIFIED")
