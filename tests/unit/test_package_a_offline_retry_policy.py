"""Package A bounded retry E2E with ONLY marked temporary SQLite and fake HTTP."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ingestion.package_a_contracts import SeriesSpec
from scripts.package_a_historical_window_planner import (
    FrozenPageBody,plan_offline_historical_windows)
from scripts.package_a_offline_retry_policy import (
    OfflineRetryPolicy,plan_offline_retry_advice,run_offline_retry_rehearsal)
from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE,inspect_fixture
from tests.unit.test_package_a_historical_window_planner import (
    RBN,SEPT,OCT,NOW,native_bodies)
from scripts.market_r10e11_writer_exclusion import hold_writer_lock


class OfflineRetryRehearsalTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.db=self.root/"package_a_synthetic.db"
        self.locks=self.root/"locks"
        self.locks.mkdir()
        (self.root/MARKER).write_text(MARKER_VALUE,encoding="utf-8")
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE old_protected (id INTEGER PRIMARY KEY, payload TEXT)")
            db.execute("INSERT INTO old_protected VALUES (1, 'preserved')")
        self.plan=plan_offline_historical_windows(RBN,SEPT,OCT)
        self.good=native_bodies(self.plan,RBN)

    def sql(self,sql):
        with sqlite3.connect(self.db) as db:
            return db.execute(sql).fetchall()

    def manifest(self,*,failure_at=None,status=None):
        a=list(self.good)
        if failure_at is not None:
            if status is None:
                a[failure_at]=FrozenPageBody(b"",None)
            else:
                a[failure_at]=FrozenPageBody(
                    ("<mock %d>"%status).encode(),status)
        return a

    def apply(self,attempts,policy=OfflineRetryPolicy()):
        return run_offline_retry_rehearsal(
            self.db,self.locks,RBN,SEPT,OCT,attempts,now=NOW,policy=policy)

    def test_two_distinct_failures_429_then_503_recover_3_source_runs_with_no_duplicate(self):
        out=self.apply([self.manifest(failure_at=1,status=429),
                        self.manifest(failure_at=2,status=503),
                        self.manifest()])
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_ACCEPTED")
        self.assertEqual(out["attempts_executed"],3)
        self.assertEqual(out["mock_requests_executed"],5)
        self.assertEqual([x["source_window_base_index"] for x in out["attempts"]],
                         [0,1,2])
        self.assertEqual([x["last_page"]["original_window_index"]
                          for x in out["attempts"]],[1,2,2])
        self.assertEqual([x["last_page"]["state"] for x in out["attempts"]],
                         ["PAGE_NOT_COMMITTED","PAGE_NOT_COMMITTED",
                          "PAGE_COMMITTED_OFFLINE"])
        self.assertEqual([x["last_page"]["failure_class"]
                          for x in out["attempts"][:2]],
                         ["SYNTHETIC_HTTP_429","SYNTHETIC_HTTP_503"])
        self.assertEqual(self.sql(
            "SELECT status,COUNT(*) FROM package_a_source_runs "
            "GROUP BY status ORDER BY status"),[("blocked",2),("complete",1)])
        self.assertEqual(self.sql("SELECT status,COUNT(*) FROM package_a_receipts "
                                  "GROUP BY status ORDER BY status"),
                         [("failed",2),("ok",3)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(30,)])
        self.assertEqual(self.sql("SELECT end_exclusive_utc FROM package_a_cursors"),
                         [(OCT,)])
        self.assertEqual(self.sql("SELECT payload FROM old_protected"),[("preserved",)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertFalse(out["production_authorized"])
        self.assertTrue(out["virtual_backoff_only"])
        self.assertFalse(out["clock_wait_executed"])

    def test_repeated_same_ordinal_timeout_then_500_is_replayed_from_same_boundary(self):
        out=self.apply([self.manifest(failure_at=1),
                        self.manifest(failure_at=1,status=500),
                        self.manifest()])
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_ACCEPTED")
        self.assertEqual(out["mock_requests_executed"],5)
        self.assertEqual([x["source_window_base_index"] for x in out["attempts"]],
                         [0,1,1])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(30,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(5,)])
        self.assertEqual(self.sql(
            "SELECT http_status,raw_payload IS NOT NULL FROM package_a_receipts "
            "WHERE status='failed' ORDER BY rowid"),
            [(None,0),(500,1)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_success_on_first_attempt_skips_staged_future_fixtures(self):
        out=self.apply([self.manifest(),self.manifest(failure_at=0,status=503)])
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_ACCEPTED")
        self.assertEqual(out["attempts_executed"],1)
        self.assertEqual(out["mock_requests_executed"],3)
        self.assertEqual(out["final_retry_advice"]["state"],"RETRY_NOT_REQUIRED")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(3,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])

    def test_retry_429_without_submitted_next_manifest_awaits_operator(self):
        out=self.apply([self.manifest(failure_at=1,status=429)])
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_AWAITING_OPERATOR")
        self.assertEqual(out["attempts_executed"],1)
        self.assertTrue(out["final_retry_advice"]["retry_allowed"])
        self.assertEqual(out["final_retry_advice"]["reason"],"SYNTHETIC_HTTP_429")
        self.assertTrue(5 <= out["final_retry_advice"]["virtual_delay_seconds"] <= 8)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(14,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])
        self.assertFalse(out["final_retry_advice"]["clock_wait_executed"])

    def test_retry_policy_exhausted_preserves_each_failed_receipt_and_cursor(self):
        policy=OfflineRetryPolicy(max_attempts=2)
        out=self.apply([self.manifest(failure_at=1,status=429),
                        self.manifest(failure_at=1,status=503)],policy)
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_EXHAUSTED")
        self.assertEqual(out["attempts_executed"],2)
        self.assertEqual(out["final_retry_advice"]["state"],"RETRY_BUDGET_EXHAUSTED")
        self.assertFalse(out["final_retry_advice"]["retry_allowed"])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(14,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(3,)])
        self.assertEqual(self.sql("SELECT status,COUNT(*) FROM package_a_source_runs "
                                  "GROUP BY status"),[("blocked",2)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_invalid_http_200_is_permanent_rejection_not_retried(self):
        bad=self.manifest()
        bad[1]=FrozenPageBody(b'{"data":[{"date":"bad-date","value":1}]}')
        out=self.apply([bad,self.manifest()])
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_STOPPED")
        self.assertEqual(out["attempts_executed"],1)
        self.assertEqual(out["mock_requests_executed"],2)
        self.assertEqual(out["final_retry_advice"]["state"],
                         "RETRY_DENIED_PERMANENT_OR_LOCK")
        self.assertEqual(out["attempts"][0]["last_page"]["status"],"rejected")
        self.assertEqual(self.sql("SELECT status FROM package_a_receipts "
                                  "ORDER BY rowid"),[("ok",),("rejected",)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])

    def test_empty_http_200_is_not_retried_or_counted_as_available_history(self):
        empty=self.manifest()
        empty[1]=FrozenPageBody(b'{"data":[]}')
        out=self.apply([empty,self.manifest()])
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_STOPPED")
        self.assertEqual(out["attempts_executed"],1)
        self.assertEqual(out["attempts"][0]["last_page"]["status"],"empty")
        self.assertEqual(out["final_retry_advice"]["state"],
                         "RETRY_DENIED_PERMANENT_OR_LOCK")
        self.assertEqual(out["mock_requests_executed"],2)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(14,)])

    def test_lock_contention_fails_before_sqlite_or_frozen_http(self):
        with hold_writer_lock(self.locks,namespace="btc-research-global-writer-v1"):
            out=self.apply([self.manifest(failure_at=0,status=429),
                            self.manifest()])
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_STOPPED")
        self.assertEqual(out["mock_requests_executed"],0)
        self.assertEqual(out["attempts_executed"],1)
        self.assertEqual(out["final_retry_advice"]["state"],
                         "RETRY_DENIED_PERMANENT_OR_LOCK")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])
        self.assertEqual(self.sql("SELECT payload FROM old_protected"),[("preserved",)])

    def test_mismatched_success_prefix_refuses_second_attempt_without_new_receipts(self):
        changed=self.manifest()
        blob=json.loads(changed[0].body)
        blob["data"][0]["value"]=0.42
        changed[0]=FrozenPageBody(json.dumps(blob).encode())
        with self.assertRaisesRegex(ValueError,"SUCCESS_RAW_DRIFT"):
            self.apply([self.manifest(failure_at=1,status=503),changed])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(14,)])

    def test_manifest_count_type_and_status_budgets_rejected_before_sqlite(self):
        for attempt in ([],[self.manifest()]*5,[self.manifest()[:2]],
                        [[b"not-a-frozen-response"]*3]):
            with self.subTest(attempt=type(attempt).__name__):
                with self.assertRaises(ValueError):
                    self.apply(attempt)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])

    def test_policy_rejects_boolean_negative_unbounded_and_float_values(self):
        cases=(
            {"max_attempts":0},{"max_attempts":5},{"max_attempts":True},
            {"max_attempts":1.5},{"initial_backoff_seconds":0},
            {"max_backoff_seconds":True},{"max_backoff_seconds":601},
            {"initial_backoff_seconds":15,"max_backoff_seconds":10},
            {"max_fake_requests":129},{"max_fake_requests":0},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError,"RETRY_POLICY_BUDGET"):
                    OfflineRetryPolicy(**kwargs)
        self.assertEqual(self.sql("SELECT payload FROM old_protected"),[("preserved",)])

    def test_total_fake_budget_rejection_occurs_before_first_attempt(self):
        policy=OfflineRetryPolicy(max_fake_requests=5)
        with self.assertRaisesRegex(ValueError,"RETRY_TOTAL_MOCK_RESOURCE_BUDGET"):
            self.apply([self.manifest(failure_at=0,status=429),
                        self.manifest()],policy)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])

    def test_deterministic_advice_jitter_stable_capped_and_no_sleep(self):
        policy=OfflineRetryPolicy(max_attempts=4,
                                  initial_backoff_seconds=7,
                                  max_backoff_seconds=12)
        result={"state":"BATCH_BLOCKED","pages":[{
            "index":1,"state":"PAGE_NOT_COMMITTED","status":"failed",
            "failure_class":"SYNTHETIC_HTTP_429"}]}
        adv=[plan_offline_retry_advice(
            result,policy=policy,completed_attempts=n,
            window_plan_sha256=self.plan["window_plan_sha256"])
             for n in range(1,5)]
        self.assertEqual(adv[0],plan_offline_retry_advice(
            result,policy=policy,completed_attempts=1,
            window_plan_sha256=self.plan["window_plan_sha256"]))
        self.assertTrue(7 <= adv[0]["virtual_delay_seconds"] <= 10)
        self.assertTrue(all(x["virtual_delay_seconds"]==12 for x in adv[1:3]))
        self.assertEqual(adv[3]["state"],"RETRY_BUDGET_EXHAUSTED")
        self.assertFalse(adv[3]["retry_allowed"])
        self.assertTrue(all(x["clock_wait_executed"] is False for x in adv))

    def test_retry_classifier_disallows_rejected_empty_busy_and_malformed(self):
        base={"index":0,"state":"PAGE_NOT_COMMITTED","status":"failed"}
        def classify(p):
            return plan_offline_retry_advice(
                {"state":"BATCH_BLOCKED","pages":[p]},
                policy=OfflineRetryPolicy(),completed_attempts=1,
                window_plan_sha256=self.plan["window_plan_sha256"])
        for altered in (
            dict(base,failure_class="SYNTHETIC_HTTP_418"),
            dict(base,failure_class="SYNTHETIC_HTTP_429",status="rejected"),
            dict(base,failure_class="SYNTHETIC_HTTP_429",state="PAGE_EMPTY_UNVERIFIED"),
            dict(base,failure_class="BLOCKED_FUNDING_EVENT_RESULT_SATURATED"),
            dict(base,failure_class="SYNTHETIC_HTTP_429",state="BLOCKED_WRITER_BUSY"),
        ):
            self.assertEqual(classify(altered)["state"],"RETRY_DENIED_PERMANENT_OR_LOCK")

    def test_retry_policy_full_4_attempts_and_3_failures_preserves_each_run(self):
        policy=OfflineRetryPolicy(max_attempts=4)
        out=self.apply([
            self.manifest(failure_at=0,status=503),
            self.manifest(failure_at=0,status=429),
            self.manifest(failure_at=0),
            self.manifest()],policy)
        self.assertEqual(out["state"],"OFFLINE_RETRY_REHEARSAL_ACCEPTED")
        self.assertEqual(out["attempts_executed"],4)
        self.assertEqual(out["mock_requests_executed"],6)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_source_runs"),[(4,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(6,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(30,)])

    def test_retry_classification_includes_never_live_transport_500_503_timeout(self):
        for failure in ("SYNTHETIC_HTTP_429","SYNTHETIC_HTTP_500",
                        "SYNTHETIC_HTTP_503","SYNTHETIC_TRANSPORT_TIMEOUT"):
            out=plan_offline_retry_advice({
                "state":"BATCH_BLOCKED","pages":[{
                    "index":0,"status":"failed","state":"PAGE_NOT_COMMITTED",
                    "failure_class":failure}]},policy=OfflineRetryPolicy(),
                    completed_attempts=1,
                    window_plan_sha256=self.plan["window_plan_sha256"])
            self.assertEqual(out["state"],"RETRY_ADVISORY_ONLY")
            self.assertEqual(out["reason"],failure)
            self.assertFalse(out["real_http_authorized"])

    def test_empty_or_busy_without_page_never_fabricates_retry(self):
        for data in ({"state":"BLOCKED_WRITER_BUSY"},
                     {"state":"BATCH_BLOCKED","pages":[]},
                     {"state":"BATCH_BLOCKED","pages":"not-a-list"}):
            result=plan_offline_retry_advice(
                data,policy=OfflineRetryPolicy(),completed_attempts=1,
                window_plan_sha256=self.plan["window_plan_sha256"])
            self.assertFalse(result["retry_allowed"])
            self.assertEqual(result["state"],"RETRY_DENIED_NO_PERSISTED_PAGE")

    def test_unsupported_real_source_or_interval_plan_preflight_never_writes(self):
        spec=SeriesSpec("yahoo","close","BTC-USD","d1","USD")
        with self.assertRaisesRegex(ValueError,"BLOCKED_SOURCE_NATIVE_PAGINATION"):
            run_offline_retry_rehearsal(
                self.db,self.locks,spec,SEPT,OCT,[self.manifest()],
                now=NOW)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])


if __name__=="__main__":
    unittest.main()
