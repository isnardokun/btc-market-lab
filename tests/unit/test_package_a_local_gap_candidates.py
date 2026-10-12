"""Offline only: gap candidates never imply API entitlement or request readiness."""
import sqlite3
import unittest

from ingestion.package_a_contracts import SeriesSpec
from scripts.package_a_local_gap_candidates import scan_archived_gaps
from storage.package_a_ledger import initialize

A="2026-10-08T00:00:00Z"
B="2026-10-08T00:05:00Z"
C="2026-10-08T00:10:00Z"
D="2026-10-08T00:15:00Z"
NEXT="2026-10-09T00:00:00Z"
LATER="2026-10-10T00:00:00Z"


class OfflineGapCandidates(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        initialize(self.db,now=A)
        self.db.execute("INSERT INTO package_a_receipts "
                        "(receipt_id,provider,metric,symbol,frequency,start_utc,"
                        "end_exclusive_utc,status,captured_utc) VALUES "
                        "('r','binance','open_interest','BTCUSDT','5m',?,?, 'ok',?)",
                        (A,D,A))

    def add(self,timestamp,*,provider="binance",metric="open_interest",symbol="BTCUSDT",freq="5m",rid="r"):
        self.db.execute("INSERT INTO package_a_observations "
                        "(provider,metric,symbol,frequency,observed_utc,"
                        "dimension_key,value,unit,raw_scale,row_sha256,receipt_id)"
                        " VALUES (?,?,?,?,?,'',1,'BTC','native','digest',?)",
                        (provider,metric,symbol,freq,timestamp,rid))

    def oi(self):
        return SeriesSpec("binance","open_interest","BTCUSDT","5m","BTC")

    def test_missing_middle_oi_slot_is_clearly_not_verified(self):
        self.add(A);self.add(C)
        result=scan_archived_gaps(self.db,self.oi(),A,D)
        self.assertEqual(result["missing_candidate_slots"],1)
        self.assertEqual(result["candidates"][0]["start_utc"],B)
        self.assertEqual(result["candidates"][0]["end_exclusive_utc"],C)
        self.assertFalse(result["candidates"][0]["request_ready"])
        self.assertEqual(result["provider_availability"],"NOT_CHECKED")

    def test_missing_tail_does_not_claim_completed_cursor(self):
        self.add(A)
        result=scan_archived_gaps(self.db,self.oi(),A,D)
        self.assertEqual(result["candidates"][0]["start_utc"],B)
        self.assertEqual(result["candidates"][0]["end_exclusive_utc"],D)
        self.assertEqual(result["historical_completeness"],"NOT_VERIFIED")

    def test_no_local_gaps_still_not_complete_upstream(self):
        for timestamp in (A,B,C):self.add(timestamp)
        result=scan_archived_gaps(self.db,self.oi(),A,D)
        self.assertEqual(result["candidates"],[])
        self.assertEqual(result["missing_candidate_slots"],0)
        self.assertFalse(result["execution_authorized"])

    def test_zero_local_rows_yields_single_bounded_candidate(self):
        result=scan_archived_gaps(self.db,self.oi(),A,D)
        self.assertEqual(result["missing_candidate_slots"],3)
        self.assertEqual(len(result["candidates"]),1)

    def test_daily_btc_midnight_grid(self):
        spec=SeriesSpec("yahoo","close","BTC-USD","d1","USD")
        result=scan_archived_gaps(self.db,spec,A,LATER)
        self.assertEqual(result["missing_candidate_slots"],2)
        self.assertEqual(result["candidates"][0]["end_exclusive_utc"],LATER)

    def test_funding_event_does_not_invent_five_minute_gaps(self):
        spec=SeriesSpec("bybit","funding_settled","BTCUSDT","event","fraction")
        result=scan_archived_gaps(self.db,spec,A,LATER)
        self.assertEqual(result["state"],"UNSUPPORTED_CADENCE_NO_INFERRED_GAPS")
        self.assertEqual(result["candidates"],[])

    def test_fred_monthly_has_no_fixed_daily_hole_claims(self):
        spec=SeriesSpec("fred","CPIAUCSL","USA","monthly","index")
        self.assertEqual(scan_archived_gaps(self.db,spec,A,LATER)["candidates"],[])

    def test_inexact_five_minute_bounds_rejected(self):
        with self.assertRaises(ValueError):
            scan_archived_gaps(self.db,self.oi(),A,"2026-10-08T00:14:00Z")

    def test_grid_budget_prevents_unbounded_scan(self):
        with self.assertRaises(ValueError):
            scan_archived_gaps(self.db,self.oi(),A,D,max_expected=2)

    def test_window_budget_fails_closed_not_truncated(self):
        self.add(B)
        result=scan_archived_gaps(self.db,self.oi(),A,D,max_windows=1)
        self.assertEqual(result["state"],"BLOCKED_GAP_BUDGET")
        self.assertEqual(result["candidates"],[])

    def test_boundaries_are_half_open(self):
        self.add(A)
        result=scan_archived_gaps(self.db,self.oi(),A,B)
        self.assertEqual(result["expected_grid_slots"],1)
        self.assertEqual(result["candidates"],[])


    def test_off_grid_observation_blocks_rather_than_understates_missing(self):
        self.add(A)
        self.add("2026-10-08T00:06:00Z")
        result = scan_archived_gaps(self.db, self.oi(), A, D)
        self.assertEqual(result["state"], "BLOCKED_SOURCE_OBSERVATION_DRIFT")
        self.assertEqual(result["candidates"], [])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["historical_completeness"], "NOT_VERIFIED")

    def test_corrupt_dimension_blocks_oi_completeness(self):
        self.add(A)
        self.db.execute("UPDATE package_a_observations SET dimension_key='vintage'")
        result = scan_archived_gaps(self.db, self.oi(), A, B)
        self.assertEqual(result["state"], "BLOCKED_SOURCE_OBSERVATION_DRIFT")

    def test_mismatched_unit_and_scale_are_not_valid_points(self):
        self.add(A)
        self.db.execute("UPDATE package_a_observations SET unit='USD'")
        self.assertEqual(scan_archived_gaps(self.db, self.oi(), A, B)["state"],
                         "BLOCKED_SOURCE_OBSERVATION_DRIFT")
        self.db.execute("UPDATE package_a_observations SET unit='BTC',raw_scale='scaled'")
        self.assertEqual(scan_archived_gaps(self.db, self.oi(), A, B)["state"],
                         "BLOCKED_SOURCE_OBSERVATION_DRIFT")

    def test_failed_receipt_cannot_certify_an_archived_observation(self):
        self.add(A)
        self.db.execute("UPDATE package_a_receipts SET status='failed'")
        result = scan_archived_gaps(self.db, self.oi(), A, B)
        self.assertEqual(result["state"], "BLOCKED_SOURCE_OBSERVATION_DRIFT")
        self.assertEqual(result["provider_availability"], "NOT_CHECKED")

    def test_receipt_provider_mismatch_blocks_false_coverage(self):
        self.add(A)
        self.db.execute("UPDATE package_a_receipts SET provider='bybit'")
        self.assertEqual(scan_archived_gaps(self.db, self.oi(), A, B)["state"],
                         "BLOCKED_SOURCE_OBSERVATION_DRIFT")

    def test_receipt_time_boundary_mismatch_blocks_false_coverage(self):
        self.add(A)
        self.db.execute("UPDATE package_a_receipts SET start_utc=?", (B,))
        self.assertEqual(scan_archived_gaps(self.db, self.oi(), A, B)["state"],
                         "BLOCKED_SOURCE_OBSERVATION_DRIFT")

    def test_utc_plus_zero_offset_in_stored_row_is_detected_not_skipped(self):
        self.add(A)
        self.db.execute("UPDATE package_a_observations SET observed_utc=?", (
            "2026-10-08T00:00:00+00:00",))
        result = scan_archived_gaps(self.db, self.oi(), A, B)
        self.assertEqual(result["state"], "BLOCKED_SOURCE_OBSERVATION_DRIFT")

    def test_fractional_stored_time_is_detected_not_silently_truncated(self):
        self.add(A)
        self.db.execute("UPDATE package_a_observations SET observed_utc=?", (
            "2026-10-08T00:00:00.500Z",))
        self.assertEqual(scan_archived_gaps(self.db, self.oi(), A, B)["state"],
                         "BLOCKED_SOURCE_OBSERVATION_DRIFT")

    def test_explicit_boundaries_with_utc_offset_are_canonicalized(self):
        self.add(A)
        self.add(B)
        result = scan_archived_gaps(self.db, self.oi(),
                    "2026-10-08T00:00:00+00:00",
                    "2026-10-08T00:10:00+00:00")
        self.assertEqual(result["state"], "OFFLINE_LOCAL_GAP_CANDIDATES")
        self.assertEqual(result["missing_candidate_slots"], 0)
        self.assertEqual(result["observed_distinct_timestamps"], 2)

    def test_fred_daily_calendar_is_not_a_five_minute_or_day_grid(self):
        spec = SeriesSpec("fred", "DGS10", "USA", "d1", "percent")
        result = scan_archived_gaps(self.db, spec, A, LATER)
        self.assertEqual(result["state"], "UNSUPPORTED_CADENCE_NO_INFERRED_GAPS")
        self.assertEqual(result["candidates"], [])
        self.assertFalse(result["request_ready"])

    def test_noncanonical_duplicate_dimension_cannot_cause_negative_gaps(self):
        self.add(A)
        self.db.execute(
            "INSERT INTO package_a_observations "
            "(provider,metric,symbol,frequency,observed_utc,"
            "dimension_key,value,unit,raw_scale,row_sha256,receipt_id) "
            "VALUES ('binance','open_interest','BTCUSDT','5m',?"
            ",'revised',1,'BTC','native','digest','r')", (A,))
        result = scan_archived_gaps(self.db, self.oi(), A, B)
        self.assertEqual(result["state"], "BLOCKED_SOURCE_OBSERVATION_DRIFT")
        self.assertEqual(result["candidates"], [])


if __name__=="__main__":
    unittest.main()
