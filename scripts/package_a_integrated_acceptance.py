"""Strict integrated six-provider Package A OFFLINE release-evidence audit.

A normal 'LOCAL_FIXTURE_INTEGRITY_PASS' means the *existing* SQLite bytes
and receipts are consistent. It emphatically does NOT mean every source is
represented, all requests have native HTTP metadata, or history is complete.

This independent read-only gate requires the entire locally configured
13-RBN + 23-Bitview source catalog, 5 Yahoo OHLCV streams, 1 FRED stream
and 4 exchange derivative streams to be present *together*, with successful
RAW provenance and original source-run custody of all catalog streams.

The result is evidence for an independent Hermes review, NEVER authorization
to run APIs, migrate production SQLite, schedule, merge or publish.
"""
from collections import Counter
import sqlite3

from scripts.package_a_ingest_fixture import _inspect_fixture_snapshot, _fixture_guard
from scripts.package_a_onchain_catalog_batch import configured_onchain_series


_FIXED={
    ("yahoo",metric,"SPY","d1")
    for metric in ("open","high","low","close","volume")
} | {
    ("fred","CPIAUCSL","USA","monthly"),
    ("binance","open_interest","BTCUSDT","5m"),
    ("binance","funding_settled","BTCUSDT","event"),
    ("bybit","open_interest","BTCUSDT","5m"),
    ("bybit","funding_settled","BTCUSDT","event"),
}
_YAHOO=set(x for x in _FIXED if x[0]=="yahoo")
_ONCHAIN=set((s.provider,s.metric,s.symbol,s.frequency)
             for s in configured_onchain_series())
EXPECTED_STREAMS=frozenset(_ONCHAIN | _FIXED)


def audit_integrated_offline_acceptance(db_path,lock_dir):
    """No mutation, no sockets; incomplete/missing provenance always blocks.

    One pinned SQLite reader transaction produces a complete aggregate view,
    in addition to the independent row-level reconstruction and native HTTP
    request attestation. Reading the same connection avoids inconsistent
    snapshots of receipts, cursors and native request lineage.
    """
    path=_fixture_guard(db_path,lock_dir)
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        audit=_inspect_fixture_snapshot(db)
        rows=db.execute(
            "SELECT provider,metric,symbol,frequency,COUNT(*) "
            "FROM package_a_observations GROUP BY provider,metric,symbol,frequency"
        ).fetchall()
        recorded={(a,b,c,d):n for a,b,c,d,n in rows}
        source_status=Counter(dict(
            db.execute("SELECT status,COUNT(*) FROM package_a_receipts "
                       "GROUP BY status").fetchall()
        ))
        pages=db.execute(
            "SELECT r.provider,r.metric,r.symbol,r.frequency,"
            "COUNT(*) receipts,"
            "SUM(CASE WHEN r.status='ok' THEN 1 ELSE 0 END) successes,"
            "SUM(CASE WHEN p.receipt_id IS NULL THEN 1 ELSE 0 END) missing_http "
            "FROM package_a_receipts r LEFT JOIN package_a_request_provenance p "
            "ON r.receipt_id=p.receipt_id "
            "GROUP BY r.provider,r.metric,r.symbol,r.frequency"
        ).fetchall()
        evidence={(a,b,c,d):(total,ok,missing)
                  for a,b,c,d,total,ok,missing in pages}
        onchain_receipts=db.execute(
            "SELECT r.provider,r.metric,r.symbol,r.frequency,r.receipt_id,"
            "p.run_id FROM package_a_receipts r "
            "LEFT JOIN package_a_run_pages p ON r.receipt_id=p.receipt_id "
            "WHERE r.provider IN ('researchbitcoin','bitview')"
        ).fetchall()
        cursors={(a,b,c,d) for a,b,c,d in db.execute(
            "SELECT provider,metric,symbol,frequency FROM package_a_cursors"
        )}
        yahoo_rows=db.execute(
            "SELECT r.metric,r.start_utc,r.end_exclusive_utc,"
            "r.raw_sha256,p.request_sha256,r.status "
            "FROM package_a_receipts r "
            "LEFT JOIN package_a_request_provenance p "
            "ON p.receipt_id=r.receipt_id "
            "WHERE r.provider='yahoo' AND r.symbol='SPY' "
            "AND r.frequency='d1'"
        ).fetchall()
        run_states=dict(db.execute(
            "SELECT status,COUNT(*) FROM package_a_source_runs "
            "GROUP BY status"
        ).fetchall())
        sql_state=db.execute("PRAGMA query_only").fetchone()[0]

    actual=set(recorded)|set(evidence)
    missing=sorted(EXPECTED_STREAMS-set(recorded))
    unexpected=sorted(actual-EXPECTED_STREAMS)
    absent_cursors=sorted(EXPECTED_STREAMS-cursors)
    absent_requests=sorted(EXPECTED_STREAMS-set(evidence))
    no_success=sorted(k for k in EXPECTED_STREAMS
                      if evidence.get(k,(0,0,1))[1]<1)
    unproven=sorted(k for k in EXPECTED_STREAMS
                    if evidence.get(k,(0,0,1))[2]>0)
    missing_catalog_run=sorted(set(
        (provider,metric,symbol,freq)
        for provider,metric,symbol,freq,rid,run_id in onchain_receipts
        if run_id is None
    ))
    # A shared RAW digest alone does NOT prove only one provider fetch. This
    # is a local one-shot source-correlation check, NOT external attestation.
    expected_five=sorted((x[1] for x in _YAHOO))
    yahoo_one_envelope=(
        len(yahoo_rows)==5 and
        sorted(x[0] for x in yahoo_rows)==expected_five and
        len({(x[1],x[2],x[3],x[4]) for x in yahoo_rows})==1 and
        all(x[5]=="ok" for x in yahoo_rows)
    )
    counts=Counter(k[0] for k in recorded)
    observation_counts=Counter()
    for key,number in recorded.items():
        observation_counts[key[0]]+=number
    failures=[]
    if audit["state"]!="LOCAL_FIXTURE_INTEGRITY_PASS":
        failures.append("RAW_SQL_AUDITS_NOT_PASS")
    if missing:failures.append("EXPECTED_STREAMS_MISSING")
    if unexpected:failures.append("UNCATALOGUED_STREAM_IN_FIXTURE")
    if absent_requests:failures.append("EXPECTED_SOURCE_REQUEST_MISSING")
    if absent_cursors:failures.append("EXPECTED_SOURCE_CURSOR_MISSING")
    if no_success:failures.append("SOURCE_MISSING_SUCCESSFUL_RECEIPT")
    if unproven or audit["native_request_lineage"][
        "historical_receipts_without_frozen_provenance"]:
        failures.append("RECEIPTS_WITHOUT_FROZEN_NATIVE_REQUEST_PROVENANCE")
    if source_status.get("failed",0) or source_status.get("rejected",0) or source_status.get("empty",0):
        failures.append("FAILED_REJECTED_OR_EMPTY_PAGE_UNRESOLVED")
    if missing_catalog_run:failures.append("ONCHAIN_RECEIPT_WITHOUT_SOURCE_RUN")
    if run_states.get("blocked",0) or run_states.get("running",0) or not run_states.get("complete",0):
        failures.append("SOURCE_RUN_NOT_FINALIZED_SUCCESSFULLY")
    if not yahoo_one_envelope:failures.append("YAHOO_FIVE_METRIC_SINGLE_ENVELOPE_UNVERIFIED")
    if sql_state!=1:failures.append("READ_ONLY_SQLITE_NOT_ENFORCED")
    if audit["receipt_reconstruction"]["state"]!="RAW_SQL_LINEAGE_PASS":
        failures.append("RECEIPT_RECONSTRUCTION_NOT_PASS")
    if audit["native_request_lineage"]["state"]!="NATIVE_FROZEN_REQUEST_LINEAGE_PASS":
        failures.append("SOURCE_NATIVE_HTTP_IDENTITY_NOT_PASS")

    return {
        "state":("OFFLINE_INTEGRATED_EVIDENCE_READY_FOR_INDEPENDENT_QA"
                 if not failures else "BLOCKED_INCOMPLETE_OFFLINE_ACCEPTANCE_EVIDENCE"),
        "failures":sorted(set(failures)),
        "expected_streams":len(EXPECTED_STREAMS),
        "observed_streams":len(recorded),
        "expected_source_counts":{
            name:sum(1 for s in EXPECTED_STREAMS if s[0]==name)
            for name in ("researchbitcoin","bitview","yahoo","fred","binance","bybit")
        },
        "stream_counts_by_provider":dict(sorted(counts.items())),
        "archived_observation_counts_by_provider":dict(sorted(observation_counts.items())),
        "source_run_states":dict(sorted(run_states.items())),
        "missing_expected_streams":[list(x) for x in missing],
        "unexpected_streams":[list(x) for x in unexpected],
        "streams_without_successful_cursors":[list(x) for x in absent_cursors],
        "streams_without_successful_receipts":[list(x) for x in no_success],
        "streams_without_native_http_receipts":[list(x) for x in unproven],
        "catalog_receipts_missing_source_run":[list(x) for x in missing_catalog_run],
        "yahoo_five_metric_local_shared_request_envelope":yahoo_one_envelope,
        "native_http_requests_verified_as_REAL":False,
        "bitview_source_units_verified":False,
        "researchbitcoin_tier2_entitlement_verified":False,
        "source_retention_limits_verified":False,
        "entire_real_history_downloaded":False,
        "production_global_writer_exclusion_verified":False,
        "production_authorized":False,
        "hermes_independent_qa_completed":False,
        "historical_completeness":"NOT_VERIFIED",
        "readonly_sqlite_connection":True,
        "existing_fixture_audit_state":audit["state"],
    }
