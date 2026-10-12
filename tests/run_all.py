#!/usr/bin/env python3
"""
tests/run_all.py
================
Runner centralizado: unit + integration tests.
Ejecutar con: python3 tests/run_all.py
"""
import subprocess, sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)

TESTS = [
    ("Unit: Indicators", "python3 tests/unit/test_indicators.py"),
    ("Unit: Metric Registry", "python3 tests/unit/test_metric_registry.py"),
    ("Unit: Portable HTML / Telegram", "python3 tests/unit/test_portable_report.py"),
    ("Unit: Hermes Skill", "python3 tests/unit/test_hermes_skill.py"),
    ("Unit: Hermes Bridge", "python3 tests/unit/test_hermes_bridge.py"),
    ("Unit: Financial Integrity", "python3 -m unittest -v tests.unit.test_financial_integrity"),
    ("Unit: Editorial Integrity", "python3 -m unittest -v tests.unit.test_editorial_integrity"),
    ("Unit: Ingest/News Quality", "python3 -m unittest -v tests.unit.test_ingest_and_news_quality"),
    ("Unit: Completed Daily Candles", "python3 -m unittest -v tests.unit.test_daily_close_cutoff"),
    ("Unit: 52-week/Price Archive", "python3 -m unittest -v tests.unit.test_52week_and_partial_archive"),
    ("Unit: Archive/UTC Reconciliation", "python3 -m unittest -v tests.unit.test_archive_reconciliation"),
    ("Unit: ResearchBitcoin On-chain Complement", "python3 -m unittest -v tests.unit.test_researchbitcoin_complement"),
    ("Unit: Specialist News and Knowledge RAG", "python3 -m unittest -v tests.unit.test_specialized_news_rag"),
    ("Unit: Versioned Knowledge Corpus v2", "python3 -m unittest -v tests.unit.test_knowledge_corpus_v2"),
    ("Unit: Research Studio v2", "python3 -m unittest -v tests.unit.test_research_studio_v2"),
    ("Unit: FRED Entry & Market SQLite Evidence", "python3 -m unittest -v tests.unit.test_fred_cli_and_market_evidence"),
    ("Unit: Independent Market Request Lineage Audit", "python3 -m unittest -v tests.unit.test_market_request_lineage_audit"),
    ("Unit: Market Request Lineage V2 Migration", "python3 -m unittest -v tests.unit.test_market_request_lineage_v2"),
    ("Unit: One-shot Binance Historical OI Endpoint", "python3 -m unittest -v tests.unit.test_binance_oi_endpoint_probe"),
    ("Unit: One-shot Binance HTTPS Connectivity", "python3 -m unittest -v tests.unit.test_binance_https_probe"),
    ("Unit: Market Network Diagnostics", "python3 -m unittest -v tests.unit.test_market_network_diagnostics"),
    ("Unit: Bounded Market Historical Batches", "python3 -m unittest -v tests.unit.test_market_history_batch"),
    ("Unit: Bounded Derivatives Historical Pilot", "python3 -m unittest -v tests.unit.test_market_history_pilot"),
    ("Unit: R10-C.6 Shared SQLite Identity", "python3 -m unittest -v tests.unit.test_market_readonly_snapshot_identity"),
    ("Unit: R10-C.5 Shared Read Snapshot", "python3 -m unittest -v tests.unit.test_market_r10c_shared_snapshot"),
    ("Unit: R10-C.4 Real Auditors E2E PRE Gate", "python3 -m unittest -v tests.unit.test_market_r10c_real_auditors_e2e"),
    ("Unit: R10-C.3 Read-only PRE Quality Gate", "python3 -m unittest -v tests.unit.test_market_r10c_quality_gate"),
    ("Unit: R10-C v3 Migrator Fail-closed Atomicity", "python3 -m unittest -v tests.unit.test_market_v3_migrator_atomicity"),
    ("Unit: R10-C WAL-safe v2-v3 Migration Rehearsal", "python3 -m unittest -v tests.unit.test_market_v3_migration_rehearsal"),
    ("Unit: R10-C SQLite Read-only Preflight", "python3 -m unittest -v tests.unit.test_market_v3_preflight"),
    ("Unit: Forward OI Atomic Pilot v3", "python3 -m unittest -v tests.unit.test_market_history_forward"),
    ("Unit: R10-E Offline OI Coverage Planner", "python3 -m unittest -v tests.unit.test_market_r10e_plan_only"),
    ("Unit: R10-E.1 Offline OI Dispatch", "python3 -m unittest -v tests.unit.test_market_r10e_dispatch_offline"),
    ("Unit: R10-E.2 Synthetic SHADOW Safety Gate", "python3 -m unittest -v tests.unit.test_market_r10e2_shadow_gate"),
    ("Unit: R10-E.3 Synthetic v3 Mock E2E", "python3 -m unittest -v tests.unit.test_market_r10e3_mock_e2e"),
    ("Unit: R10-E.4 Synthetic Full Cycle", "python3 -m unittest -v tests.unit.test_market_r10e4_synthetic_cycle"),
    ("Unit: R10-E.5 Synthetic Backward Coverage", "python3 -m unittest -v tests.unit.test_market_r10e5_backward_fixture_plan"),
    ("Unit: R10-E.6 ResearchBitcoin Tier 2 Synthetic Entitlement", "python3 -m unittest -v tests.unit.test_market_r10e6_rbn_tier2_shadow"),
    ("Unit: R10-E.7 Read-Only Multi-Provider Archive Manifest", "python3 -m unittest -v tests.unit.test_market_r10e7_ro_archive_manifest"),
    ("Unit: R10-E.8 Source-Aware Internal Gap Audit RO", "python3 -m unittest -v tests.unit.test_market_r10e8_ro_internal_gap_audit"),
    ("Unit: R10-E.9 Cross-Report Evidence Reconciliation", "python3 -m unittest -v tests.unit.test_market_r10e9_evidence_review_queue"),
    ("Unit: R10-E.10 Atomic Read-Only SQLite Evidence", "python3 -m unittest -v tests.unit.test_market_r10e10_atomic_ro_evidence"),
    ("Unit: R10-E.11 Cross-Process Writer Lock Offline", "python3 -m unittest -v tests.unit.test_market_r10e11_writer_exclusion"),
    ("Unit: R10-E.12 Locked Real Forward Mock Offline", "python3 -m unittest -v tests.unit.test_market_r10e12_locked_mock_forward"),
    ("Unit: R10-E.13 Global Writer Adoption Inventory", "python3 -m unittest -v tests.unit.test_market_r10e13_writer_adoption_inventory"),
    ("Unit: R10-E.14 Opt-in Yahoo Price Writer Lock", "python3 -m unittest -v tests.unit.test_market_r10e14_optin_price_lock"),
    ("Unit: Package A Integrated Multi-Provider Ingestion", "python3 -m unittest -v tests.unit.test_package_a_integrated_ingestion"),
    ("Unit: Package A Global Lock Real Entrypoints Offline", "python3 -m unittest -v tests.unit.test_package_a_entrypoint_locks"),
    ("Unit: Package A Historical Writer Global Lock Offline", "python3 -m unittest -v tests.unit.test_package_a_historical_writer_locks"),
    ("Unit: Package A Legacy Schema WAL Migration Rehearsal", "python3 -m unittest -v tests.unit.test_package_a_legacy_migration"),
    ("Unit: Package A Offline Runner Production Guard", "python3 -m unittest -v tests.unit.test_package_a_runner_offline_gate"),
    ("Unit: Package A Daily Archive Writer Shared Lock", "python3 -m unittest -v tests.unit.test_package_a_daily_archive_locks"),
    ("Unit: Package A Writer Adoption Source-Only Audit", "python3 -m unittest -v tests.unit.test_package_a_writer_adoption_review"),
    ("Unit: Package A Writer Route SHA Attestation and Drift Gate", "python3 -m unittest -v tests.unit.test_package_a_writer_source_attestation"),
    ("Unit: Package A Parent-Lock Cutover (Synthetic Process Only)", "python3 -m unittest -v tests.unit.test_package_a_writer_cutover"),
    ("Unit: Package A Parent-Lock Descendant Process Supervision", "python3 -m unittest -v tests.unit.test_package_a_parent_lock_process_supervision"),
    ("Unit: Package A Physical Lock Inode Guard and PRECOMMIT", "python3 -m unittest -v tests.unit.test_package_a_writer_lock_inode_guard"),
    ("Unit: Package A RBN and Schema Migration Global Lock", "python3 -m unittest -v tests.unit.test_package_a_migration_and_rbn_locks"),
    ("Unit: Package A Daily Report and News Library Shared Lock", "python3 -m unittest -v tests.unit.test_package_a_report_library_lock"),
    ("Unit: Package A Source-Aware Offline Gap Candidates", "python3 -m unittest -v tests.unit.test_package_a_local_gap_candidates"),
    ("Unit: Package A Frozen Legacy HTTP Request Integration", "python3 -m unittest -v tests.unit.test_package_a_frozen_legacy_transport"),
    ("Unit: Package A Native Request Receipt Identity Audit", "python3 -m unittest -v tests.unit.test_package_a_native_request_audit"),
    ("Unit: Package A Yahoo Single-Fetch Five-Metric Fanout", "python3 -m unittest -v tests.unit.test_package_a_yahoo_fanout"),
    ("Unit: Package A RBN-Bitview Full Onchain Catalog Batch", "python3 -m unittest -v tests.unit.test_package_a_onchain_catalog_batch"),
    ("Unit: Package A Source-Native Historical Pagination E2E", "python3 -m unittest -v tests.unit.test_package_a_historical_window_planner"),
    ("Unit: Package A Bounded Offline Multi-Retry Recovery", "python3 -m unittest -v tests.unit.test_package_a_offline_retry_policy"),
    ("Unit: Market Temporal Coverage Audit", "python3 -m unittest -v tests.unit.test_market_temporal_quality"),
    ("Unit: Raw Market Blob Reconciliation", "python3 -m unittest -v tests.unit.test_market_raw_reconcile"),
    ("Unit: Market Context SQLite", "python3 -m unittest -v tests.unit.test_market_context_sqlite"),
    ("Unit: Historical API Archive + Research Design", "python3 -m unittest -v tests.unit.test_historical_archive_and_design"),
    ("Unit: Archive Schema, Lineage & Tier 2 Migration", "python3 -m unittest -v tests.unit.test_archive_schema_v1"),
    ("Integration: Pipeline", "python3 tests/integration/test_pipeline.py"),
]

# The historical pipeline integration suite reads a hard-coded
# /home/ignotus/btc-research/db/btc_research.db at import time. It is NOT an
# offline unittest, and must NEVER be run by the generic CI/Hermes runner.
# Running that test requires a separately scoped owner-authorized action.
LIVE_ONLY_SUITES = {"Integration: Pipeline"}

live_rows = [(name, cmd) for name, cmd in TESTS if name in LIVE_ONLY_SUITES]
if len(live_rows) != 1 or live_rows[0][1] != "python3 tests/integration/test_pipeline.py":
    raise SystemExit("BLOCKED: unsafe pipeline suite registry drift")
if len({name for name, _ in TESTS}) != len(TESTS):
    raise SystemExit("BLOCKED: duplicate test suite labels")
if len({cmd for _, cmd in TESTS}) != len(TESTS):
    raise SystemExit("BLOCKED: duplicate suite commands")

results = []
skipped_live = []
for name, cmd in TESTS:
    if name in LIVE_ONLY_SUITES:
        skipped_live.append(name)
        print("SKIPPED_LIVE_NOT_AUTHORIZED: " + name)
        continue
    print(f"\n{'='*60}")
    print(f"  {name}")
    print('='*60)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    ok = r.returncode == 0
    results.append((name, ok))
    if ok:
        print(f"✅ PASS")
    else:
        print(f"❌ FAIL\n{r.stderr[-5000:]}")
        # Print last 20 lines of output
        lines = r.stdout.strip().split('\n')
        print("Output:")
        for l in lines[-20:]:
            print(f"  {l}")

print(f"\n{'='*60}")
print("  RESULTS")
print('='*60)
for name, ok in results:
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {status}  {name}")

all_ok = bool(results) and all(ok for _, ok in results)
print(f"\nOFFLINE_SUITES_EXECUTED={len(results)}")
print(f"OFFLINE_SUITES_PASSED={sum(1 for _,ok in results if ok)}")
print(f"LIVE_SUITES_SKIPPED={len(skipped_live)}")
print(f"REGISTERED_SUITES={len(TESTS)}")
print("LIVE_STATUS=NOT_TESTED_NOT_AUTHORIZED")
print(f"\n{'✅ Offline suites passed; live suite NOT TESTED' if all_ok else '❌ Some tests failed (offline only)'}")
sys.exit(0 if all_ok else 1)
