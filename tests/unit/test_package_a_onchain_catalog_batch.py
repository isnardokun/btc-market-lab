"""RBN+Bitview full installed-catalogue OFFLINE planning and v3 batch E2E.

Zero provider sockets/keys/cron/production SQLite. Every mutation is made
only inside an explicitly marked TemporaryDirectory fixture.
"""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.package_a_onchain_catalog_batch import (
    _BITVIEW_UNIT,configured_onchain_series,plan_offline_onchain_catalog,
    run_offline_onchain_catalog,
)
from scripts.package_a_historical_window_planner import FrozenPageBody
from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE,inspect_fixture
from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
from ingestion.package_a_contracts import SeriesSpec
from ingestion.researchbitcoin_catalog import CATALOG

START="2026-10-08T00:00:00Z"
END="2026-10-09T00:00:00Z"
NOW="2026-10-11T00:00:00Z"
EPOCH=dt.date(2009,1,1)


def bodies(plan,*,overrides=None):
    overrides=overrides or {}
    responses=[]
    for i,w in enumerate(plan["ordered_requests"]):
        if i in overrides:
            responses.append(overrides[i])
            continue
        a=dt.date.fromisoformat(w["start_utc"][:10])
        b=dt.date.fromisoformat(w["end_exclusive_utc"][:10])
        dates=[a+dt.timedelta(days=i) for i in range((b-a).days)]
        if w["provider"]=="researchbitcoin":
            value=0.75 if w["raw_scale"]=="fraction_0_1" else 1.125
            payload={"data":[
                {"date":day.isoformat(),w["metric"]:value} for day in dates]}
        elif w["provider"]=="bitview":
            payload={"index":"1d","start":(a-EPOCH).days,
                     "data":[float(1+i) for i in range(len(dates))]}
        else:
            raise AssertionError("Not a local onchain series")
        responses.append(FrozenPageBody(json.dumps(
            payload,sort_keys=True,separators=(",",":")).encode()))
    return responses


class OnchainCatalogHistoryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.db=self.root/"package_a_synthetic.db"
        self.lock=self.root/"locks"
        self.lock.mkdir()
        (self.root/MARKER).write_text(MARKER_VALUE,encoding="utf-8")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE old_archive(id INTEGER PRIMARY KEY, payload TEXT)")
            c.execute("INSERT INTO old_archive VALUES(1,'protected')")

    def sql(self,query):
        with sqlite3.connect(self.db) as c:
            return c.execute(query).fetchall()

    def plan(self,selection=None,start=START,end=END,**kwargs):
        return plan_offline_onchain_catalog(
            start,end,selection=selection,**kwargs)

    def run_plan(self,plan,output=None):
        return run_offline_onchain_catalog(
            self.db,self.lock,plan,
            bodies(plan) if output is None else output,now=NOW)

    def test_full_configured_source_catalogue_has_every_metric_not_selected_subset(self):
        specs=configured_onchain_series()
        rbn=[s for s in specs if s.provider=="researchbitcoin"]
        bitview=[s for s in specs if s.provider=="bitview"]
        self.assertEqual(len(rbn),len(CATALOG))
        self.assertEqual(len(bitview),23)
        self.assertEqual(len(rbn)+len(bitview),36)
        self.assertEqual({s.metric for s in rbn},set(CATALOG))
        self.assertTrue(all(s.symbol=="BTC" and s.frequency=="d1" for s in specs))
        self.assertTrue(all(s.unit==_BITVIEW_UNIT for s in bitview))
        self.assertTrue(all(s.raw_scale==CATALOG[s.metric].raw_scale
                            for s in rbn))

    def test_catalog_full_36_series_one_day_spans_two_persisted_v3_runs(self):
        p=self.plan()
        self.assertEqual(p["requested_streams"],36)
        self.assertEqual(p["requested_source_native_pages"],36)
        self.assertEqual(p["shard_count"],2)
        self.assertTrue(p["no_catalog_stream_silently_truncated"])
        self.assertEqual(p["max_persisted_run_pages"],32)
        self.assertEqual(
            p["onchain_plan_sha256"],self.plan()["onchain_plan_sha256"])
        outcome=self.run_plan(p)
        self.assertEqual(outcome["state"],"OFFLINE_ONCHAIN_CATALOG_ACCEPTED")
        self.assertEqual(outcome["frozen_http_requests"],36)
        self.assertEqual(outcome["pages_attempted"],36)
        self.assertEqual(outcome["pages_not_attempted"],0)
        self.assertEqual(outcome["new_observations"],36)
        self.assertEqual([x["requests_attempted"] for x in outcome["source_runs"]],
                         [32,4])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(36,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(36,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(36,)])
        self.assertEqual(self.sql("SELECT status,planned_pages FROM "
                                  "package_a_source_runs ORDER BY planned_pages"),
                         [("complete",4),("complete",32)])
        self.assertEqual(inspect_fixture(self.db,self.lock)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(self.sql("SELECT payload FROM old_archive"),[("protected",)])
        self.assertFalse(outcome["bitview_source_units_verified"])
        self.assertFalse(outcome["tier2_entitlement_verified"])
        self.assertFalse(outcome["real_provider_history_downloaded"])
        self.assertFalse(outcome["production_authorized"])

    def test_round_robin_pages_preserve_each_source_daily_cursor_order(self):
        ids=[("bitview","mvrv"),("researchbitcoin","sopr_sth")]
        end="2026-10-24T00:00:00Z"
        p=self.plan(ids,end=end)
        self.assertEqual(p["requested_source_native_pages"],4)
        self.assertEqual([x["source_page_ordinal"]
                          for x in p["ordered_requests"]],[0,0,1,1])
        self.assertEqual([x["metric"] for x in p["ordered_requests"]],
                         ["mvrv","sopr_sth","mvrv","sopr_sth"])
        # The server-like time range must be in the past relative to NOW;
        # end date is only a synthetic plan until a later test clock is used.
        p2=self.plan(ids,start="2026-09-20T00:00:00Z",
                     end="2026-10-08T00:00:00Z")
        output=self.run_plan(p2)
        self.assertEqual(output["state"],"OFFLINE_ONCHAIN_CATALOG_ACCEPTED")
        self.assertEqual(output["frozen_http_requests"],4)
        self.assertEqual(output["new_observations"],36)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(2,)])

    def test_single_source_30day_has_three_native_windows_no_truncation(self):
        start="2026-09-01T00:00:00Z"
        end="2026-10-01T00:00:00Z"
        p=self.plan([("researchbitcoin","sopr_sth")],start,end)
        self.assertEqual(p["requested_streams"],1)
        self.assertEqual(p["requested_source_native_pages"],3)
        self.assertEqual([w["end_exclusive_utc"] for w in p["ordered_requests"]],
                         ["2026-09-15T00:00:00Z",
                          "2026-09-29T00:00:00Z",end])
        result=self.run_plan(p)
        self.assertEqual(result["new_observations"],30)
        self.assertEqual(result["pages_attempted"],3)
        self.assertEqual(self.sql("SELECT end_exclusive_utc FROM "
                                  "package_a_cursors"),[(end,)])

    def test_request_count_budget_cannot_silently_skip_remaining_source_series(self):
        with self.assertRaisesRegex(ValueError,"TOTAL_REQUEST_BUDGET"):
            self.plan(max_total_requests=32)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])

    def test_15days_two_pages_per_catalog_metric_and_global_budget_fail_closed(self):
        p=self.plan(end="2026-10-23T00:00:00Z")
        self.assertEqual(p["requested_source_native_pages"],72)
        self.assertEqual(p["shard_count"],3)
        self.assertTrue(p["no_catalog_stream_silently_truncated"])
        with self.assertRaisesRegex(ValueError,"TOTAL_REQUEST_BUDGET"):
            self.plan(end="2026-10-23T00:00:00Z",max_total_requests=64)

    def test_early_http_429_global_stop_does_not_start_future_catalog_series(self):
        p=self.plan([("bitview","mvrv"),("researchbitcoin","sopr_sth"),
                     ("researchbitcoin","liveliness")])
        r=self.run_plan(p,bodies(p,overrides={
            1:FrozenPageBody(b"<offline-429>",429)}))
        self.assertEqual(r["state"],"OFFLINE_ONCHAIN_CATALOG_BLOCKED")
        self.assertEqual(r["pages_attempted"],2)
        self.assertEqual(r["pages_not_attempted"],1)
        self.assertEqual(r["frozen_http_requests"],2)
        self.assertEqual(r["source_runs"][0]["state"],"BATCH_BLOCKED")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(2,)])
        self.assertEqual(self.sql("SELECT status,COUNT(*) FROM package_a_receipts "
                                  "GROUP BY status ORDER BY status"),
                         [("failed",1),("ok",1)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(1,)])
        self.assertEqual(self.sql("SELECT status FROM package_a_source_runs"),
                         [("blocked",)])
        self.assertFalse(r["real_provider_history_downloaded"])

    def test_timeout_fails_stop_with_durable_no_raw_receipt(self):
        p=self.plan([("bitview","mvrv"),("researchbitcoin","sopr_sth")])
        r=self.run_plan(p,bodies(p,overrides={
            0:FrozenPageBody(b"",None)}))
        self.assertEqual(r["state"],"OFFLINE_ONCHAIN_CATALOG_BLOCKED")
        self.assertEqual(r["frozen_http_requests"],1)
        self.assertEqual(r["pages_attempted"],1)
        self.assertEqual(self.sql("SELECT status,raw_payload IS NULL "
                                  "FROM package_a_receipts"),[("failed",1)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])

    def test_later_bad_manifest_is_denied_before_first_mock_or_sql(self):
        p=self.plan([("bitview","mvrv"),("researchbitcoin","sopr_sth")])
        invalid=bodies(p)
        invalid[-1]=FrozenPageBody(b"INVALID",418)
        with self.assertRaisesRegex(ValueError,"FROZEN_RESPONSE"):
            self.run_plan(p,invalid)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])

    def test_unsigned_mutated_source_plan_is_denied_before_mock_or_sql(self):
        p=self.plan([("bitview","mvrv")])
        p["ordered_requests"][0]["metric"]="nupl"
        with self.assertRaisesRegex(ValueError,"PLAN_DRIFT"):
            self.run_plan(p,bodies(p))
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])

    def test_busy_writer_global_flock_prevents_any_fake_requests_or_sql(self):
        p=self.plan([("bitview","mvrv"),("researchbitcoin","sopr_sth")])
        with hold_writer_lock(self.lock,namespace="btc-research-global-writer-v1"):
            r=self.run_plan(p)
        self.assertEqual(r["state"],"BLOCKED_WRITER_BUSY")
        self.assertEqual(r["frozen_http_requests"],0)
        self.assertEqual(r["sqlite_connections_started"],0)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])

    def test_invalid_selection_duplicate_or_uncatalogued_metric_denied(self):
        for choice in (
            [],[("bitview","not-a-metric")],
            [("bitview","mvrv"),("bitview","mvrv")],
            [("researchbitcoin","unknown_tier2")],[("yahoo","close")],
            [("bitview","mvrv","BTC")],"all"):
            with self.subTest(choice=choice):
                with self.assertRaisesRegex(ValueError,"ONCHAIN_CATALOG_SELECTION"):
                    self.plan(choice)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master "
                                  "WHERE name='package_a_receipts'"),[(0,)])

    def test_bitview_shorter_array_rejected_not_silently_advanced(self):
        spec=SeriesSpec("bitview","mvrv","BTC","d1","ratio")
        index=(dt.date.fromisoformat(START[:10])-EPOCH).days
        request={"index":"1d","start":index,"data":[]}
        r=run_mocked_legacy_page(self.db,self.lock,spec,START,END,
            raw_bytes=json.dumps(request).encode(),now=NOW)
        self.assertEqual(r["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(r["status"],"rejected")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.db,self.lock)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_bitview_shifted_day_index_with_only_one_value_cannot_advance(self):
        spec=SeriesSpec("bitview","mvrv","BTC","d1","ratio")
        index=(dt.date.fromisoformat(START[:10])-EPOCH).days
        request={"index":"1d","start":index-1,"data":[1.5]}
        r=run_mocked_legacy_page(self.db,self.lock,spec,START,END,
            raw_bytes=json.dumps(request).encode(),now=NOW)
        self.assertEqual(r["state"],"PAGE_NOT_COMMITTED")
        self.assertEqual(r["status"],"rejected")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])

    def test_bitview_full_length_null_placeholder_remains_sparse_not_fake_data(self):
        spec=SeriesSpec("bitview","mvrv","BTC","d1","ratio")
        begin="2026-10-07T00:00:00Z"
        index=(dt.date(2026,10,7)-EPOCH).days
        request={"index":"1d","start":index,"data":[1.4,None]}
        r=run_mocked_legacy_page(self.db,self.lock,spec,begin,END,
            raw_bytes=json.dumps(request).encode(),now=NOW)
        self.assertEqual(r["state"],"PAGE_COMMITTED_OFFLINE")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(1,)])
        self.assertEqual(self.sql("SELECT end_exclusive_utc FROM package_a_cursors"),
                         [(END,)])
        m=inspect_fixture(self.db,self.lock)["source_manifest"]["streams"][0]
        self.assertEqual(m["archived_rows"],1)
        self.assertEqual(m["historical_completeness"],"NOT_VERIFIED")

    def test_rbn_metric_slug_value_key_parses_byte_exact_and_preserves_fraction_scale(self):
        spec=SeriesSpec("researchbitcoin","supply_in_profit_sth_percent",
                        "BTC","d1","percent","fraction_0_1")
        data={"data":[{"date":START[:10],spec.metric:0.75}]}
        response=run_mocked_legacy_page(self.db,self.lock,spec,START,END,
            raw_bytes=json.dumps(data).encode(),now=NOW)
        self.assertEqual(response["state"],"PAGE_COMMITTED_OFFLINE")
        self.assertEqual(self.sql("SELECT value,unit,raw_scale FROM "
                                  "package_a_observations"),
                         [(0.75,"percent","fraction_0_1")])
        self.assertEqual(inspect_fixture(self.db,self.lock)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_rbn_ambiguous_time_keys_rejected_without_observation_or_cursor(self):
        spec=SeriesSpec("researchbitcoin","sopr_sth","BTC","d1","ratio")
        data={"data":[{"date":START[:10],"time":START,"value":1.02}]}
        r=run_mocked_legacy_page(self.db,self.lock,spec,START,END,
            raw_bytes=json.dumps(data).encode(),now=NOW)
        self.assertEqual(r["status"],"rejected")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.db,self.lock)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_rbn_two_different_metric_values_rejects_mislabelled_source(self):
        spec=SeriesSpec("researchbitcoin","sopr_sth","BTC","d1","ratio")
        data={"data":[{"date":START[:10],"sopr_sth":1.02,"value":1.95}]}
        r=run_mocked_legacy_page(self.db,self.lock,spec,START,END,
            raw_bytes=json.dumps(data).encode(),now=NOW)
        self.assertEqual(r["status"],"rejected")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])

    def test_rbn_foreign_catalog_slug_prevents_invented_metric_dimensions(self):
        spec=SeriesSpec("researchbitcoin","sopr_sth","BTC","d1","ratio")
        data={"data":[{"date":START[:10],"sopr_sth":1.02,
                       "mvrv_sth":1.4}]}
        r=run_mocked_legacy_page(self.db,self.lock,spec,START,END,
            raw_bytes=json.dumps(data).encode(),now=NOW)
        self.assertEqual(r["status"],"rejected")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])


if __name__=="__main__":
    unittest.main()
