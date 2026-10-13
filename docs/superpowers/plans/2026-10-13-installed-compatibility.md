# Installed compatibility implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this
> plan inline, with one independent whole-branch review at the end.

**Goal:** A preserved installed-schema fixture feeds explicit evidence,
additive simulated acquisitions and a recoverable private report pipeline.

**Architecture:** Structural drift gate and immutable legacy read adapter;
separate additive acquisition ledger; common B evidence/analytics/renderer;
C flock, inode guards, artifact and seven-stage contracts reused.

**Tech Stack:** Python 3.11+, stdlib SQLite/dataclasses/unittest, existing
Research Studio HTML/SVG; no new dependencies or network.

**Spec:** ../specs/2026-10-13-installed-compatibility-design.md

## Global constraints

- One owner-authorized integrated offline PR; base 1a224b0db5d71200efe379aad58accd2e1d99e49.
- No network client, production default or productive permission exists.
- Existing 34 tables and schema SQL remain unchanged.
- Runtime accepts FAKE_HTTP/OFFLINE_PROVIDER_CONTRACT only.
- Existing A/B/C fixture contracts stay valid.
- Hermes STOP until final current-SHA order; preserve historical worktrees.

## Review focus

- Timestamp normalization (Z/offset/naive/date) must not admit future captures.
- Equal BLOB across providers must not imply equal request/acquisition identity.
- Replacement DB/lock/artifact links must fail before SQL/transport/commit.
- Empty, reserved or failed receipts must not permit cursor/budget refunds.
- Legacy closes and unknown vintages must not become OHLCV or verified history.

## Task 1: Installed profile, fingerprint and fixture contract

Files: storage/installed_schema.py, storage/installed_schema_profile.json,
tests/unit/installed_fixture_helpers.py, tests/unit/test_installed_schema.py.
Produces: inspect_installed_schema(conn) -> dict; legacy_fingerprint(conn)
-> dict; profile() -> dict. Tests own the partial DDL fixture generator;
do not export it as production schema installation.

- [ ] Write test_profile_and_all_34_tables, test_missing_or_changed_contract,
  test_fingerprints_include_legacy_rows_and_trigger_sql, test_versions_and_extra_objects.
- [ ] Run python3 -m unittest -v tests.unit.test_installed_schema; expected RED.
- [ ] Implement profile validation and deterministic fingerprints, bounded to
  100000 rows/table for offline preservation checks; reject incomplete reads.
- [ ] Run same command; expected GREEN; commit coherent profile slice.

## Task 2: Legacy evidence, close contract and B provenance rendering

Files: analysis/installed_evidence.py, analysis/package_b_evidence.py,
analysis/package_b_analytics.py, rendering/package_b_report.py,
storage/repositories.py, tests/unit/test_installed_evidence.py.
Consumes Task 1; produces LegacySelection(spec,table,lookup),
read_installed_snapshot(db_path,lock_dir,*,asof,snapshot_captured_utc,selections)
-> ReportSnapshot. Snapshot digest includes explicit provenance/availability.

- [ ] Write tests for price-only BTC without manufactured bars, explicit
  Bitview/FRED/RBN/derivatives selection, absent receipts, RAW mismatch,
  vintage selection, mixed units, late captures, snapshot-bound availability,
  read-only single-snapshot behavior, unmarked DB rejection and legacy SQL.
- [ ] Run python3 -m unittest -v tests.unit.test_installed_evidence; expected RED.
- [ ] Implement guarded reader and provenance fields; installed-profile partial
  close analytics with critical OHLCV errors; preserve old default behavior.
- [ ] Render missing provenance/vintage honestly; old helper uses price and
  raises explicit OHLCV_UNAVAILABLE rather than querying absent columns.
- [ ] Run new tests and B evidence/analytics/report regression; expected GREEN;
  commit coherent read-to-report slice.

## Task 3: Additive page acquisitions, durable budgets and rehearsal

Files: storage/compatibility_ledger.py, ingestion/compatibility_acquisition.py,
scripts/installed_migration_rehearsal.py,
tests/unit/test_compatibility_acquisition.py.
Consumes Task 1 and B immutable evidence; produces initialize(conn,now),
audit(conn), acquire_fake_page(db,locks,request,transport,*,run_id,now,
max_requests,max_attempts,fault=None), and rehearsal(db,locks,*,now).
Request contains source specs, endpoint origin/path, parameters and half-open
window. FakeTransport owns explicit frozen outcomes, no fetch callback.

- [ ] Write tests for migration retention/rollback/schema drift, Yahoo one-RAW
  five-stream fanout, six-provider parsing, idempotency/request drift,
  provider identity, empty/failed/rejected responses, cursor continuity,
  crash reservation/commit, budgets, locked contender, RAW/receipt corruption
  and WAL-safe restore rehearsal on fixtures only.
- [ ] Run python3 -m unittest -v tests.unit.test_compatibility_acquisition; RED.
- [ ] Implement known additive DDL, validate existing addon contracts, atomic
  migration preservation; durable reservation before fake transport outside
  SQL write transactions; atomic RAW/observations/receipt/cursor commit.
- [ ] Add strict addon audit to Task 2 read transaction; preserve versions and
  select latest eligible receipt by capture/as-of without silent revision.
- [ ] Run new tests plus Tasks 1/2; GREEN; commit acquisition/rehearsal slice.

## Task 4: Recoverable private pipeline and final delivery

Files: scripts/installed_schema_pipeline.py,
tests/unit/test_installed_pipeline.py, tests/run_all.py,
docs/INSTALLED_COMPATIBILITY_RUNBOOK.md, execution ledger.
Consumes all prior tasks; produces plan_installed_pipeline(requests,policy)
pure dict, prepare_installed_run(root,*,asof,selections,requests,policy),
run_installed_pipeline(root,*,apply=False,transport=None,fault=None) -> dict.
Artifacts reuse C's audit/evidence/charts/report/archive/gate names.

- [ ] Write pure PLAN_ONLY no-path-I/O tests and end-to-end legacy-only blocked
  diagnostic/full acquired report, seven-stage before/after crash recovery,
  commit-before-checkpoint resume, 429/503/timeout budgets, plan/inode drift,
  contender zero SQL/transport, artifact tampering, private 0600 outputs and
  retained legacy fingerprints. Tests use real child SIGKILL where relevant.
- [ ] Run python3 -m unittest -v tests.unit.test_installed_pipeline; RED.
- [ ] Implement guarded preparation and parent-flock seven-stage execution;
  resume recomputes final evidence/artifacts/gate. Gate distinguishes private
  diagnostic success from report acceptance and never permits publication.
- [ ] Register four suites; write runbook with limitations and future exact
  permission gates. Run python3 tests/run_all.py, expected all offline PASS,
  one mandatory live skip. Run retention and compatible visual checks.
- [ ] Commit, fresh independent whole-branch review, resolve P0/P1 with a
  reproduced RED/GREEN fix, final runner after SHA-changing fixes.
- [ ] Publish selected GitHub tree/commit/ref, compare trees, one draft PR;
  current-SHA CI, finalize body and issue one pinned Hermes order. No merge
  before current-SHA independent QA/CI gates. No productive action.
