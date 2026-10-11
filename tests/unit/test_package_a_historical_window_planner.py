"""Historical paging A4: source-specific bounded MOCK requests, never live."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ingestion.package_a_contracts import SeriesSpec
from scripts.package_a_historical_window_planner import (
    FrozenPageBody, plan_offline_historical_windows,
    bind_offline_frozen_history, run_offline_planned_history,
    recover_offline_planned_history,
)
from scripts.package_a_ingest_fixture import MARKER, MARKER_VALUE, inspect_fixture
from scripts.market_r10e11_writer_exclusion import hold_writer_lock

UTC=dt.timezone.utc
NOW="2026-10-11T00:00:00Z"
RBN=SeriesSpec("researchbitcoin","sopr_sth","BTC","d1","ratio")
BINANCE=SeriesSpec("binance","open_interest","BTCUSDT","5m","BTC")
BYBIT=SeriesSpec("bybit","open_interest","BTCUSDT","5m","BTC")
BITVIEW=SeriesSpec("bitview","mvrv","BTC","d1","ratio")
SEPT="2026-09-01T00:00:00Z"
OCT="2026-10-01T00:00:00Z"
OISTART="2026-10-07T00:00:00Z"


def stamp(t):
    return t.isoformat().replace("+00:00","Z")


def epoch(t):
    return int(dt.datetime.fromisoformat(t.replace("Z","+00:00")).timestamp()*1000)


def native_bodies(plan,spec,*,failed=None):
    """Fresh in-memory provider-shaped responses, one per actual plan page."""
    result=[]
    for w in plan["windows"]:
        start=dt.datetime.fromisoformat(w["start_utc"].replace("Z","+00:00"))
        end=dt.datetime.fromisoformat(w["end_exclusive_utc"].replace("Z","+00:00"))
        if failed is not None and w["ordinal"]==failed:
            result.append(FrozenPageBody(b"<html>429 simulated</html>",429))
            continue
        if spec.provider=="researchbitcoin":
            d=start
            rows=[]
            while d<end:
                rows.append({"date":d.date().isoformat(),"value":0.98})
                d+=dt.timedelta(days=1)
            payload={"data":rows}
        elif spec.provider=="bitview":
            payload={"index":"1d","start":(start.date()-dt.date(2009,1,1)).days,
                     "data":[1.1]*w["native_slots"]}
        elif spec.provider=="binance":
            payload=[{"timestamp":epoch(stamp(start+dt.timedelta(minutes=5*i))),
                      "sumOpenInterest":str(30000+i)}
                     for i in range(w["native_slots"])]
        elif spec.provider=="bybit":
            payload={"result":{"list":[
                {"timestamp":str(epoch(stamp(start+dt.timedelta(minutes=5*i)))),
                 "openInterest":str(45000+i)}
                for i in range(w["native_slots"])]}}
        else:
            raise AssertionError("Not in supported synthetic calendar policy")
        result.append(FrozenPageBody(
            json.dumps(payload,separators=(",",":"),sort_keys=True).encode()))
    return result


class HistoricalWindowPlannerTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.db=self.root/"package_a_synthetic.db"
        self.locks=self.root/"locks"
        self.locks.mkdir()
        (self.root/MARKER).write_text(MARKER_VALUE,encoding="utf-8")
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE old_archive(id INTEGER PRIMARY KEY,payload TEXT)")
            db.execute("INSERT INTO old_archive VALUES(1,'protected')")
    def sql(self,q):
        with sqlite3.connect(self.db) as db:
            return db.execute(q).fetchall()

    def test_rbn_30_day_plan_has_exact_14_14_2_windows_and_stable_digest(self):
        a=plan_offline_historical_windows(RBN,SEPT,OCT)
        self.assertEqual(a["state"],"OFFLINE_SOURCE_WINDOWS_PLANNED")
        self.assertEqual(a["candidate_pages"],3)
        self.assertEqual([x["native_slots"] for x in a["windows"]],[14,14,2])
        self.assertEqual([x["ordinal"] for x in a["windows"]],[0,1,2])
        self.assertEqual(a["windows"][0]["start_utc"],SEPT)
        self.assertEqual(a["windows"][-1]["end_exclusive_utc"],OCT)
        self.assertEqual(a["requested_native_slots"],30)
        self.assertTrue(a["page_limit_policy"].startswith("RBN_SYNTHETIC"))
        self.assertEqual(a["window_plan_sha256"],
            plan_offline_historical_windows(RBN,SEPT,OCT)["window_plan_sha256"])
        self.assertFalse(a["external_history_verified"])
        self.assertFalse(a["request_ready"])

    def test_bitview_local_policy_is_not_misrepresented_as_retention(self):
        end="2026-09-30T00:00:00Z"
        p=plan_offline_historical_windows(BITVIEW,SEPT,end)
        self.assertEqual([w["native_slots"] for w in p["windows"]],[14,14,1])
        self.assertIn("NOT_UPSTREAM_VERIFIED",p["page_limit_policy"])
        self.assertFalse(p["provider_entitlement_verified"])

    def test_binance_native_500_bar_boundary_is_exact_no_overlap(self):
        end=stamp(dt.datetime(2026,10,7,tzinfo=UTC)+dt.timedelta(minutes=5*501))
        p=plan_offline_historical_windows(BINANCE,OISTART,end)
        self.assertEqual([w["native_slots"] for w in p["windows"]],[500,1])
        self.assertEqual(p["windows"][0]["end_exclusive_utc"],
                         p["windows"][1]["start_utc"])
        self.assertEqual(p["calendar_step_seconds"],300)

    def test_bybit_native_200_bar_boundary_is_exact_no_overlap(self):
        end=stamp(dt.datetime(2026,10,7,tzinfo=UTC)+dt.timedelta(minutes=5*201))
        p=plan_offline_historical_windows(BYBIT,OISTART,end)
        self.assertEqual([w["native_slots"] for w in p["windows"]],[200,1])
        self.assertEqual(p["synthetic_max_rows_per_window"],200)

    def test_fred_yahoo_and_funding_are_explicitly_not_autopaginated(self):
        cases=(
            SeriesSpec("fred","CPIAUCSL","USA","monthly","index"),
            SeriesSpec("fred","DGS10","USA","d1","percent"),
            SeriesSpec("yahoo","close","BTC-USD","d1","USD"),
            SeriesSpec("binance","funding_settled","BTCUSDT","event","fraction"),
            SeriesSpec("bybit","funding_settled","BTCUSDT","event","fraction"),
        )
        for spec in cases:
            with self.subTest(provider=spec.provider,metric=spec.metric):
                r=plan_offline_historical_windows(spec,SEPT,OCT)
                self.assertEqual(r["state"],
                                 "BLOCKED_SOURCE_NATIVE_PAGINATION_UNVERIFIED")
                self.assertEqual(r["windows"],[])
                self.assertFalse(r["request_ready"])

    def test_limit_32_fails_without_partial_plan_or_truncated_coverage(self):
        end=stamp(dt.datetime(2025,1,1,tzinfo=UTC)+dt.timedelta(days=14*33))
        r=plan_offline_historical_windows(RBN,"2025-01-01T00:00:00Z",end)
        self.assertEqual(r["state"],"BLOCKED_HISTORICAL_PAGE_BUDGET")
        self.assertEqual(r["candidate_pages"],33)
        self.assertEqual(r["windows"],[])
        self.assertFalse(r["production_authorized"])

    def test_request_budget_must_be_explicit_positive_int_with_max_32(self):
        for budget in (0,-1,33,True,1.5):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    plan_offline_historical_windows(RBN,SEPT,OCT,max_pages=budget)
        r=plan_offline_historical_windows(RBN,SEPT,OCT,max_pages=2)
        self.assertEqual(r["state"],"BLOCKED_HISTORICAL_PAGE_BUDGET")

    def test_reject_unaligned_daily_and_oi_five_minute_boundaries(self):
        for spec,start,end in (
            (RBN,"2026-09-01T12:00:00Z",OCT),
            (RBN,SEPT,"2026-09-30T12:00:00Z"),
            (BINANCE,OISTART,"2026-10-07T00:07:00Z"),
            (BYBIT,"2026-10-07T00:01:00Z","2026-10-07T00:11:00Z"),
        ):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    plan_offline_historical_windows(spec,start,end)

    def test_fractional_and_nonutc_timestamp_is_denied(self):
        for start in ("2026-09-01T00:00:00.500Z",
                      "2026-09-01T00:00:00+01:00",
                      "2026-09-01T00:00:00"):
            with self.subTest(start=start):
                with self.assertRaises(ValueError):
                    plan_offline_historical_windows(RBN,start,OCT)

    def test_source_catalog_and_exact_symbol_validation_happen_preplanning(self):
        false_bitview=SeriesSpec("bitview","mvrv","ETH","d1","ratio")
        with self.assertRaises(ValueError):
            plan_offline_historical_windows(false_bitview,SEPT,OCT)

    def test_binding_incomplete_or_wrong_typed_responses_fails_before_db(self):
        p=plan_offline_historical_windows(RBN,SEPT,OCT)
        replies=native_bodies(p,RBN)
        for bad in (replies[:2],replies+[replies[0]],[b"bytes"]*3):
            with self.subTest(n=len(bad)):
                with self.assertRaisesRegex(ValueError,"BLOCKED_HISTORICAL_"):
                    bind_offline_frozen_history(RBN,SEPT,OCT,bad)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM old_archive"),[(1,)])
        self.assertEqual(self.sql(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_fake_body_size_empty_error_and_status_rejected_before_db(self):
        p=plan_offline_historical_windows(RBN,SEPT,OCT)
        rows=native_bodies(p,RBN)
        cases=(FrozenPageBody(b""),FrozenPageBody(b"a"*2000001),
               FrozenPageBody(b"oops",418),FrozenPageBody(b"bad",True),
               FrozenPageBody(b"nonempty",None))
        for bad in cases:
            with self.subTest(size=len(bad.body),status=bad.status_code):
                new=[bad]+rows[1:]
                with self.assertRaisesRegex(ValueError,"FROZEN_RESPONSE_CONTRACT"):
                    bind_offline_frozen_history(RBN,SEPT,OCT,new)
        self.assertEqual(self.sql(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_full_rbn_30_days_runs_three_native_http_requests_into_sqlite(self):
        plan=plan_offline_historical_windows(RBN,SEPT,OCT)
        output=run_offline_planned_history(
            self.db,self.locks,RBN,SEPT,OCT,native_bodies(plan,RBN),now=NOW)
        self.assertEqual(output["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(output["requests_attempted"],3)
        self.assertEqual(output["frozen_http_requests"],3)
        self.assertEqual(output["new_observations"],30)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(3,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(30,)])
        self.assertEqual(self.sql(
            "SELECT end_exclusive_utc FROM package_a_cursors"),[(OCT,)])
        self.assertEqual(output["window_plan_sha256"],plan["window_plan_sha256"])
        self.assertEqual(output["audit"]["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(self.sql("SELECT payload FROM old_archive"),[("protected",)])
        self.assertFalse(output["real_provider_pagination_verified"])

    def test_real_mock_binance_501_bars_paginate_500_plus_1(self):
        end=stamp(dt.datetime(2026,10,7,tzinfo=UTC)+dt.timedelta(minutes=5*501))
        plan=plan_offline_historical_windows(BINANCE,OISTART,end)
        out=run_offline_planned_history(
            self.db,self.locks,BINANCE,OISTART,end,
            native_bodies(plan,BINANCE),now=NOW)
        self.assertEqual(out["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(out["new_observations"],501)
        self.assertEqual(out["frozen_http_requests"],2)
        self.assertEqual(self.sql(
            "SELECT end_exclusive_utc FROM package_a_cursors"),[(end,)])
        self.assertEqual(out["audit"]["source_manifest"]["streams"][0][
            "local_gap_evidence"]["local_missing_candidate_slots"],0)

    def test_real_mock_bybit_201_bars_paginate_200_plus_1(self):
        end=stamp(dt.datetime(2026,10,7,tzinfo=UTC)+dt.timedelta(minutes=5*201))
        plan=plan_offline_historical_windows(BYBIT,OISTART,end)
        out=run_offline_planned_history(
            self.db,self.locks,BYBIT,OISTART,end,native_bodies(plan,BYBIT),now=NOW)
        self.assertEqual(out["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(out["new_observations"],201)
        self.assertEqual(out["frozen_http_requests"],2)

    def test_rbn_second_page_429_retry_preserves_good_14_day_prefix(self):
        p=plan_offline_historical_windows(RBN,SEPT,OCT)
        errors=native_bodies(p,RBN,failed=1)
        first=run_offline_planned_history(
            self.db,self.locks,RBN,SEPT,OCT,errors,now=NOW)
        self.assertEqual(first["state"],"BATCH_BLOCKED")
        self.assertEqual(first["requests_attempted"],2)
        self.assertEqual(first["new_observations"],14)
        self.assertEqual(first["remaining_requests_not_run"],1)
        recovered=recover_offline_planned_history(
            self.db,self.locks,RBN,SEPT,OCT,native_bodies(p,RBN),
            prior_run_id=first["run_id"],now=NOW)
        self.assertEqual(recovered["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(recovered["recovery_start_index"],1)
        self.assertEqual(recovered["frozen_http_requests"],2)
        self.assertEqual(recovered["new_observations"],16)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(4,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(30,)])
        self.assertEqual(self.sql(
            "SELECT status FROM package_a_source_runs ORDER BY status"),
            [("blocked",),("complete",)])
        self.assertEqual(recovered["source_window_plan"]["candidate_pages"],3)
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_recovery_with_changed_successful_prefix_is_refused(self):
        p=plan_offline_historical_windows(RBN,SEPT,OCT)
        first=run_offline_planned_history(
            self.db,self.locks,RBN,SEPT,OCT,native_bodies(p,RBN,failed=1),now=NOW)
        good=native_bodies(p,RBN)
        raw=json.loads(good[0].body)
        raw["data"][0]["value"]=0.777
        good[0]=FrozenPageBody(json.dumps(raw).encode())
        with self.assertRaisesRegex(ValueError,"SUCCESS_RAW_DRIFT"):
            recover_offline_planned_history(self.db,self.locks,RBN,SEPT,OCT,good,
                prior_run_id=first["run_id"],now=NOW)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])

    def test_planned_batch_lock_busy_denies_before_sql_or_mock_http(self):
        p=plan_offline_historical_windows(RBN,SEPT,OCT)
        with hold_writer_lock(self.locks,namespace="btc-research-global-writer-v1"):
            blocked=run_offline_planned_history(
                self.db,self.locks,RBN,SEPT,OCT,native_bodies(p,RBN),now=NOW)
        self.assertEqual(blocked["state"],"BATCH_BLOCKED")
        self.assertEqual(blocked["pages"][0]["state"],"BLOCKED_WRITER_BUSY")
        self.assertEqual(blocked["frozen_http_requests"],0)
        self.assertEqual(self.sql(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='package_a_receipts'"),[(0,)])

    def test_complete_source_plan_recovery_returns_zero_new_http_and_sql(self):
        p=plan_offline_historical_windows(RBN,SEPT,OCT)
        blobs=native_bodies(p,RBN)
        completed=run_offline_planned_history(
            self.db,self.locks,RBN,SEPT,OCT,blobs,now=NOW)
        out=recover_offline_planned_history(
            self.db,self.locks,RBN,SEPT,OCT,blobs,
            prior_run_id=completed["run_id"],now=NOW)
        self.assertEqual(out["state"],"RESUME_ALREADY_COMPLETE")
        self.assertEqual(out["frozen_http_requests"],0)
        self.assertEqual(out["requests_attempted"],0)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_source_runs"),[(1,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(3,)])


if __name__=="__main__":
    unittest.main()
