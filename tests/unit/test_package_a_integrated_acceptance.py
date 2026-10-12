"""Cross-provider complete-frozen-RAW acceptance matrix: 46 real source contracts.

All HTTP is one-shot simulated through installed legacy builders; only a
TemporaryDirectory marker-protected SQLite is ever opened. No real APIs.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ingestion.package_a_contracts import SeriesSpec
from scripts.package_a_integrated_acceptance import (
    EXPECTED_STREAMS, audit_integrated_offline_acceptance,
)
from scripts.package_a_onchain_catalog_batch import (
    plan_offline_onchain_catalog,run_offline_onchain_catalog,
)
from scripts.package_a_yahoo_fanout import run_offline_yahoo_ohlcv_fanout
from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
from scripts.package_a_ingest_fixture import (
    MARKER,MARKER_VALUE,inspect_fixture,FixtureResponse,run_fixture_page,
)
from tests.unit.test_package_a_onchain_catalog_batch import bodies
from tests.unit.test_package_a_frozen_legacy_transport import (
    scenarios,raw,FrozenLegacyTransportTests,
)

NOW="2026-10-14T00:00:00Z"
START="2026-10-08T00:00:00Z"
END="2026-10-09T00:00:00Z"


class TestPackageAIntegratedAcceptanceMatrix(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.db=self.root/"package_a_synthetic.db"
        self.lock=self.root/"locks"
        self.lock.mkdir()
        (self.root/MARKER).write_text(MARKER_VALUE,encoding="utf-8")
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE protected_historical_data(id INTEGER PRIMARY KEY, content TEXT)")
            db.execute("INSERT INTO protected_historical_data VALUES (1,'SAFE')")
        self.plan=plan_offline_onchain_catalog(START,END)

    def baseline(self):
        outcome=run_offline_onchain_catalog(
            self.db,self.lock,self.plan,bodies(self.plan),now=NOW)
        self.assertEqual(outcome["state"],"OFFLINE_ONCHAIN_CATALOG_ACCEPTED")
        return outcome

    def yahoo(self):
        return run_offline_yahoo_ohlcv_fanout(
            self.db,self.lock,"SPY",START,"2026-10-13T00:00:00Z",
            raw_bytes=raw(FrozenLegacyTransportTests._yahoo_daily_body(self)),
            now=NOW)

    def other_providers(self):
        for i in (2,4,5,6,7):
            spec,start,end,payload=scenarios()[i]
            out=run_mocked_legacy_page(
                self.db,self.lock,spec,start,end,
                raw_bytes=raw(payload),now=NOW)
            self.assertEqual(out["state"],"PAGE_COMMITTED_OFFLINE",out)

    def completed(self):
        self.baseline()
        self.assertEqual(self.yahoo()["status"],"ok")
        self.other_providers()
        return audit_integrated_offline_acceptance(self.db,self.lock)

    def sql(self,q):
        with sqlite3.connect(self.db) as db:
            return db.execute(q).fetchall()

    def test_all_six_provider_native_contracts_46_streams_are_local_qa_ready(self):
        self.assertEqual(len(EXPECTED_STREAMS),46)
        gate=self.completed()
        self.assertEqual(gate["state"],"OFFLINE_INTEGRATED_EVIDENCE_READY_FOR_INDEPENDENT_QA")
        self.assertEqual(gate["failures"],[])
        self.assertEqual(gate["expected_streams"],46)
        self.assertEqual(gate["observed_streams"],46)
        self.assertEqual(gate["expected_source_counts"],{
            "researchbitcoin":13,"bitview":23,"yahoo":5,
            "fred":1,"binance":2,"bybit":2,
        })
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(46,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_request_provenance"),[(46,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(46,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_run_pages"),[(36,)])
        self.assertTrue(gate["yahoo_five_metric_local_shared_request_envelope"])
        self.assertEqual(gate["existing_fixture_audit_state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        for unsafe in (
            "native_http_requests_verified_as_REAL",
            "bitview_source_units_verified",
            "researchbitcoin_tier2_entitlement_verified",
            "source_retention_limits_verified",
            "entire_real_history_downloaded",
            "production_global_writer_exclusion_verified",
            "production_authorized",
            "hermes_independent_qa_completed",
        ):
            self.assertFalse(gate[unsafe],unsafe)
        self.assertEqual(gate["historical_completeness"],"NOT_VERIFIED")
        self.assertEqual(self.sql("SELECT content FROM protected_historical_data"),[("SAFE",)])

    def test_integrity_pass_alone_must_not_claim_complete_source_coverage(self):
        report=inspect_fixture(self.db,self.lock) if False else None
        self.baseline()
        audit=inspect_fixture(self.db,self.lock)
        self.assertEqual(audit["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertEqual(gate["state"],"BLOCKED_INCOMPLETE_OFFLINE_ACCEPTANCE_EVIDENCE")
        self.assertEqual(gate["observed_streams"],36)
        self.assertIn("EXPECTED_STREAMS_MISSING",gate["failures"])
        self.assertEqual(len(gate["missing_expected_streams"]),10)

    def test_yahoo_and_other_sources_missing_fail_without_claiming_six_providers(self):
        self.baseline()
        self.other_providers()
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertIn("EXPECTED_STREAMS_MISSING",gate["failures"])
        self.assertIn("YAHOO_FIVE_METRIC_SINGLE_ENVELOPE_UNVERIFIED",gate["failures"])
        self.assertEqual(len(gate["missing_expected_streams"]),5)

    def test_failed_recent_fetch_after_all_historical_streams_blocks_acceptance(self):
        self.completed()
        spec,start,end,payload=scenarios()[6]
        failure=run_mocked_legacy_page(
            self.db,self.lock,spec,start,end,
            raw_bytes=b"offline-429",status_code=429,now=NOW)
        self.assertEqual(failure["status"],"failed")
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertIn("FAILED_REJECTED_OR_EMPTY_PAGE_UNRESOLVED",gate["failures"])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(46,)])
        self.assertEqual(self.sql("SELECT content FROM protected_historical_data"),[("SAFE",)])

    def test_unknown_extra_source_cannot_be_accepted_by_source_total_count(self):
        self.completed()
        result=run_offline_yahoo_ohlcv_fanout(
            self.db,self.lock,"GLD",START,"2026-10-13T00:00:00Z",
            raw_bytes=raw(FrozenLegacyTransportTests._yahoo_daily_body(self)),
            now=NOW)
        self.assertEqual(result["status"],"ok")
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertIn("UNCATALOGUED_STREAM_IN_FIXTURE",gate["failures"])
        self.assertEqual(len(gate["unexpected_streams"]),5)

    def test_extra_unsourced_direct_fixture_receipt_blocks_complete_audit(self):
        self.completed()
        spec=SeriesSpec("researchbitcoin","sopr_sth","BTC","d1","ratio")
        blob={"data":[{"date":"2026-10-08","value":1.125}]}
        p=run_fixture_page(self.db,self.lock,spec,START,END,
                           FixtureResponse(200,blob),now=NOW)
        self.assertEqual(p["state"],"PAGE_COMMITTED_OFFLINE")
        self.assertEqual(inspect_fixture(self.db,self.lock)["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertIn("RECEIPTS_WITHOUT_FROZEN_NATIVE_REQUEST_PROVENANCE",gate["failures"])
        self.assertIn("ONCHAIN_RECEIPT_WITHOUT_SOURCE_RUN",gate["failures"])

    def test_digest_corruption_blocks_integrated_readonly_gate(self):
        self.completed()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE package_a_receipts SET raw_sha256=? "
                       "WHERE provider='fred'",("a"*64,))
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertEqual(gate["state"],"BLOCKED_INCOMPLETE_OFFLINE_ACCEPTANCE_EVIDENCE")
        self.assertIn("RAW_SQL_AUDITS_NOT_PASS",gate["failures"])
        self.assertIn("RECEIPT_RECONSTRUCTION_NOT_PASS",gate["failures"])
        self.assertEqual(self.sql("SELECT content FROM protected_historical_data"),[("SAFE",)])

    def test_remove_single_provenance_row_blocks_evidence(self):
        self.completed()
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM package_a_request_provenance WHERE "
                       "receipt_id IN (SELECT receipt_id FROM package_a_receipts "
                       "WHERE provider='fred' LIMIT 1)")
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertIn("RECEIPTS_WITHOUT_FROZEN_NATIVE_REQUEST_PROVENANCE",gate["failures"])
        self.assertEqual(gate["state"],"BLOCKED_INCOMPLETE_OFFLINE_ACCEPTANCE_EVIDENCE")

    def test_remove_run_page_custody_from_onchain_source_denies_evidence(self):
        self.completed()
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM package_a_run_pages "
                       "WHERE receipt_id IN (SELECT receipt_id FROM package_a_receipts "
                       "WHERE provider='bitview' LIMIT 1)")
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertIn("ONCHAIN_RECEIPT_WITHOUT_SOURCE_RUN",gate["failures"])
        self.assertEqual(gate["state"],"BLOCKED_INCOMPLETE_OFFLINE_ACCEPTANCE_EVIDENCE")

    def test_only_complete_local_fixture_is_explicitly_not_production_or_hermes_approval(self):
        self.completed()
        gate=audit_integrated_offline_acceptance(self.db,self.lock)
        self.assertEqual(gate["state"],"OFFLINE_INTEGRATED_EVIDENCE_READY_FOR_INDEPENDENT_QA")
        self.assertFalse(gate["production_authorized"])
        self.assertFalse(gate["hermes_independent_qa_completed"])
        self.assertFalse(gate["native_http_requests_verified_as_REAL"])
        self.assertFalse(gate["entire_real_history_downloaded"])
        self.assertEqual(self.sql("SELECT content FROM protected_historical_data"),[("SAFE",)])


if __name__=="__main__":
    unittest.main()
