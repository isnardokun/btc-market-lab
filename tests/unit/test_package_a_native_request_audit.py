"""Adversarial native request ↔ RAW receipt attestation (100% fake transport)."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.package_a_ingest_fixture import (
    MARKER, MARKER_VALUE, inspect_fixture, FixtureResponse, run_fixture_page,
)
from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
from scripts.package_a_native_request_audit import audit_native_frozen_request_lineage
from ingestion.package_a_contracts import SeriesSpec
from storage.package_a_ledger import audit_frozen_request_provenance
from tests.unit.test_package_a_frozen_legacy_transport import (
    scenarios, raw, NOW, DAY, AFTER,
)


class TestPackageANativeRequestSourceAudit(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.db=self.root/"package_a_synthetic.db"
        self.locks=self.root/"locks"
        self.locks.mkdir()
        (self.root/MARKER).write_text(MARKER_VALUE,encoding="utf-8")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE historical_untouched(id INTEGER PRIMARY KEY, value TEXT)")
            c.execute("INSERT INTO historical_untouched VALUES(1,'ORIGINAL')")

    def source(self,index,*,status=200,body=None):
        spec,start,end,payload=scenarios()[index]
        return run_mocked_legacy_page(
            self.db,self.locks,spec,start,end,
            raw_bytes=raw(payload) if body is None else body,
            status_code=status,now=NOW,
        )

    def sql(self,query):
        with sqlite3.connect(self.db) as c:
            return c.execute(query).fetchall()

    def forge(self,field,value):
        # Simulate a party altering source identity and RE-SIGNING the request
        # metadata hash. The old purely-internal digest validator accepts it.
        with sqlite3.connect(self.db) as c:
            row=c.execute(
                "SELECT endpoint_origin,endpoint_path,request_params_json "
                "FROM package_a_request_provenance LIMIT 1"
            ).fetchone()
            origin,path,params=row
            if field=="endpoint_origin":
                origin=value
            elif field=="endpoint_path":
                path=value
            elif field=="request_params_json":
                params=value
            else:
                raise ValueError("Unknown test mutation")
            canonical=json.dumps(
                [origin,path,"GET",params,"FROZEN_OFFLINE"],
                separators=(",",":"))
            sha=hashlib.sha256(canonical.encode()).hexdigest()
            c.execute("UPDATE package_a_request_provenance SET "
                      "endpoint_origin=?,endpoint_path=?,request_params_json=?,"
                      "request_sha256=?",(origin,path,params,sha))

    def alter_param(self,key,value):
        params=json.loads(self.sql(
            "SELECT request_params_json FROM package_a_request_provenance"
        )[0][0])
        params[key]=value
        self.forge("request_params_json",
                   json.dumps(params,sort_keys=True,separators=(",",":")))

    def test_all_eight_legacy_native_builders_match_stored_receipt_contract(self):
        for i in range(8):
            self.assertEqual(self.source(i)["state"],"PAGE_COMMITTED_OFFLINE")
        r=inspect_fixture(self.db,self.locks)
        self.assertEqual(r["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        proof=r["native_request_lineage"]
        self.assertEqual(proof["state"],"NATIVE_FROZEN_REQUEST_LINEAGE_PASS")
        self.assertEqual(proof["native_requests_validated"],8)
        self.assertEqual(proof["native_request_identity_errors"],0)
        self.assertEqual(proof["requests_without_observed_unit_contract"],0)
        self.assertFalse(proof["provider_entitlement_verified"])
        self.assertFalse(proof["production_authorized"])
        self.assertEqual(proof["real_http_requests"],0)
        self.assertEqual(self.sql("SELECT value FROM historical_untouched"),[("ORIGINAL",)])

    def test_re_signed_rbn_request_window_drift_is_detected_despite_valid_digest(self):
        self.assertEqual(self.source(0)["state"],"PAGE_COMMITTED_OFFLINE")
        self.alter_param("from_time","2026-09-01")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(audit_frozen_request_provenance(c)["state"],
                             "FROZEN_REQUEST_METADATA_PASS")
        report=inspect_fixture(self.db,self.locks)
        self.assertEqual(report["native_request_lineage"]["state"],
                         "NATIVE_FROZEN_REQUEST_LINEAGE_FAIL")
        self.assertEqual(report["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(report["native_request_lineage"]["native_request_identity_errors"],1)

    def test_re_signed_binance_oi_to_funding_endpoint_substitution_is_rejected(self):
        self.source(4)
        self.forge("endpoint_path","/fapi/v1/fundingRate")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(audit_frozen_request_provenance(c)["state"],
                             "FROZEN_REQUEST_METADATA_PASS")
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(2,)])

    def test_bybit_symbol_replacement_in_signed_params_invalidates_source_identity(self):
        self.source(5)
        self.alter_param("symbol","ETHUSDT")
        self.assertEqual(inspect_fixture(self.db,self.locks)[
            "native_request_lineage"]["native_request_identity_errors"],1)

    def test_fred_signed_observation_end_must_equal_receipt_window(self):
        self.source(2)
        self.alter_param("observation_end","2026-09-15")
        r=inspect_fixture(self.db,self.locks)
        self.assertEqual(r["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(r["request_provenance"]["state"],
                         "FROZEN_REQUEST_METADATA_PASS")
        self.assertEqual(r["native_request_lineage"]["native_request_identity_errors"],1)

    def test_changed_signed_url_origin_rejected_even_if_metadata_digest_matches(self):
        self.source(3)
        self.forge("endpoint_origin","evil.fixture.invalid")
        r=inspect_fixture(self.db,self.locks)
        self.assertEqual(r["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(r["native_request_lineage"]["native_request_identity_errors"],1)

    def test_corrupt_existing_native_request_identity_blocks_new_page_transaction(self):
        self.source(0)
        self.alter_param("to_time","2026-08-01")
        before=self.sql("SELECT receipt_id,status FROM package_a_receipts")
        with self.assertRaisesRegex(ValueError,"BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE"):
            self.source(1)
        self.assertEqual(self.sql("SELECT receipt_id,status FROM package_a_receipts"),
                         before)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(2,)])
        self.assertEqual(self.sql("SELECT value FROM historical_untouched"),[("ORIGINAL",)])

    def test_429_failed_only_request_can_be_verified_without_fabricating_units(self):
        self.assertEqual(self.source(0,status=429,body=b"MOCK TOO MANY REQUESTS")[
            "status"],"failed")
        r=inspect_fixture(self.db,self.locks)
        self.assertEqual(r["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        p=r["native_request_lineage"]
        self.assertEqual(p["native_requests_validated"],1)
        self.assertEqual(p["requests_without_observed_unit_contract"],1)
        self.assertEqual(r["rows"],0)
        self.assertFalse(p["provider_entitlement_verified"])
        self.assertEqual(r["historical_completeness"],"NOT_VERIFIED")

    def test_failed_only_bybit_funding_can_validate_request_without_data(self):
        self.assertEqual(self.source(7,status=503,body=b"offline-503")[
            "status"],"failed")
        r=inspect_fixture(self.db,self.locks)
        self.assertEqual(r["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(r["native_request_lineage"]["native_requests_validated"],1)
        self.assertEqual(r["native_request_lineage"][
            "requests_without_observed_unit_contract"],1)
        self.assertEqual(r["rows"],0)

    def test_direct_legacy_fixture_receipt_not_retagged_as_frozen_request(self):
        source,start,stop,payload=scenarios()[0]
        result=run_fixture_page(
            self.db,self.locks,source,start,stop,
            FixtureResponse(200,payload),now=NOW)
        self.assertEqual(result["state"],"PAGE_COMMITTED_OFFLINE")
        proof=inspect_fixture(self.db,self.locks)["native_request_lineage"]
        self.assertEqual(proof["native_requests_validated"],0)
        self.assertEqual(proof["historical_receipts_without_frozen_provenance"],1)
        self.assertFalse(proof["missing_provenance_retroactively_fabricated"])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_request_provenance"),
                         [(0,)])

    def test_rejected_http_200_still_has_valid_request_source_identity(self):
        self.source(0,status=200,body=b'{"not":"valid RBN data"}')
        r=inspect_fixture(self.db,self.locks)
        self.assertEqual(r["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(r["native_request_lineage"]["native_requests_validated"],1)
        self.assertEqual(r["rows"],0)
        self.assertEqual(self.sql("SELECT status FROM package_a_receipts"),
                         [("rejected",)])

    def test_metadata_retaining_digest_with_bad_json_is_blocked(self):
        self.source(6)
        self.forge("request_params_json",'{"not":"the source query"}')
        with sqlite3.connect(self.db) as c:
            self.assertEqual(audit_frozen_request_provenance(c)["state"],
                             "FROZEN_REQUEST_METADATA_PASS")
            self.assertEqual(audit_native_frozen_request_lineage(c)[
                "state"],"NATIVE_FROZEN_REQUEST_LINEAGE_FAIL")
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_FAIL")


if __name__=="__main__":
    unittest.main()
