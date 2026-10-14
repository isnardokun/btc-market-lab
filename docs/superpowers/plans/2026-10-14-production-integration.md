# Production integration — Implementation Plan (proposal)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> after owner approval. Native implementation by ChatGPT; Hermes final QA only.
> Preserve original dirty and every prior worktree. No live execution in this plan.

**Goal:** Deliver one tested production-capable A/B/C route with explicit grants,
scaled preservation and an isolated first-pilot contract, without activating it.

**Architecture:** Keep fixture entrypoints intact. New installed-live boundaries
share pure parsing/evidence/render kernels and the physical parent lease.
New live_* ledger records custody and budgets across runs; installed deployment
and a private live pilot have separate targets and permissions.

**Tech Stack:** Python >=3.11 stdlib, SQLite, Linux FD attestation/flock;
existing HTML/PDF visual QA and GitHub CI on Python 3.11/3.14.

**Spec:** docs/superpowers/specs/2026-10-14-production-integration-design.md

## Global Constraints

- CLI by default PLAN_ONLY_NO_IO: zero DB, network, credentials and scheduler.
- Maximum technical limits: 128 attempts/run, 3/request, body 4.000.000 bytes,
  socket timeout 15s, total deadline <=900s; grants can only reduce them.
- Reads in batches <=2.048 rows and <=8 MiB materialized values, large BLOB/TEXT
  chunks <=64 KiB; complete fingerprint, not a sampled PASS.
- Preserve 34 legacy tables/rows/PK/RAW/SQL; live_* namespace only, no fake
  receipts for Funding P1/R10-D.2, no widening fixture guards for production.
- Linux /proc/self/fd required; exact UID/dev/inode/nlink/path/MAIN identity;
  new dirs 0700/files 0600, namespace btc-research-global-writer-v1.
- No production/API/backup/cron/deploy/publication execution under G0/G1.

## Review Focus

- Wrong DB, stale approval, duplicate run/grant reuse ⇒ blocked before SQL/HTTP.
- Cached FD/GC, wrong UID/symlink, dead parent ⇒ fail closed, deterministic close.
- Cancellation/timeout after reserve and commit before checkpoint ⇒ no quota
  refund, no repeat successful HTTP, typed uncertain attempt retained.
- Million-row snapshots, BLOB/null/type distinctions and changed legacy SQL ⇒
  bounded memory and whole-history preservation, never partial PASS.
- Provider calendar/revisions/dimensions and future acquired_utc ⇒ honest
  temporal evidence, no fabricated bars/completeness or real tag on fixture.

---

## Task 1: Runtime grant, DB boundary and shared lease

**Files:** Create scripts/installed_live_policy.py,
scripts/installed_live_guard.py; test tests/unit/test_installed_live_policy.py.
Reuse storage/installed_connection.py and scripts/package_a_parent_lock.py.

**Interfaces:**
- RuntimeGrant frozen dataclass: code_sha, plan_sha, approval_artifact_sha,
  approval_reference, target_kind, db/root/lock absolute paths and expected
  identities, allowed_operations/source_windows, expires_utc, request/body/DP
  budgets. target_kind = FIXTURE_TEST/LIVE_PRIVATE_PILOT/INSTALLED_DB.
- validate_runtime_grant(grant, *, code_sha, plan_sha, now, operation) ->
  ValidatedGrant; distinct typed capability, not a boolean.
- hold_runtime_session(validated_grant) -> context manager yielding
  RuntimeSession(db, lock, target_kind, verify, connect). connect(readonly)
  wraps attested_connection and requires the active parent lease.
- plan_live_run(requests, policy) -> dict; pure/no I/O.

- [ ] Write RED tests for missing/expired/tampered grants, wrong target/symlink,
  unknown operation/host/range, mismatched SHA, contention before SQL/HTTP,
  cached/foreign FD and parent death. Use temp files only; assertion: no
  connection/transport started on denied path. Verify PLAN_ONLY has zero I/O.
- [ ] Run python3 -m unittest -v tests.unit.test_installed_live_policy; inspect
  intended RED failures, implement exact interfaces, rerun GREEN.
- [ ] Commit this slice with fixture contract tests; no CLI installation/live.

## Task 2: Native request and bounded HTTP custody

**Files:** Create ingestion/installed_live_sources.py,
ingestion/installed_live_transport.py; tests/unit/test_installed_live_transport.py.

**Interfaces:**
- LiveRequest frozen dataclass: specs tuple[SeriesSpec,...], native_origin,
  path, params (secret-free ordered pairs), start/end UTC, direction and
  contract_sha; sha256 property over its canonical public request.
- TransportResult frozen dataclass: body bytes|None, http_status int|None,
  attempted_utc, received_utc, response_metadata, custody_mode.
- BoundedHttpTransport.fetch(request, capability, *, deadline) ->
  TransportResult. Constructor does not read credentials/open network.
- normalize_live_page(request, body, *, captured_utc) -> tuple[NormalizedPoint,...];
  reuse pure native parsers and preserve units/dimensions/vintages.
- SourceRegistry supplies six provider contracts from existing adapter files;
  live status defaults EXTERNAL_UNVERIFIED. Endpoint/units/symbol explicitly
  declared; unknown shape/catalog/DP bound blocks rather than guesses.

- [ ] RED: unexpected redirects/host/path/params, oversized/empty/malformed
  body, HTTPError body retained, close on every exit, 429 circuit, 5xx/deadline,
  keys outside allowed execution, RBN DP/cardinality bound, native currencies,
  FRED latest versus vintage, Yahoo one body/five fields, funding events.
  Block real market sockets in all tests; simulated custody never REAL.
- [ ] Run python3 -m unittest -v tests.unit.test_installed_live_transport;
  implement interfaces and GREEN. Test grant rejection occurs before opener.
- [ ] Commit; no request to any market endpoint during development.

## Task 3: Live ledger, acquisition, scalable migration and coverage

**Files:** Create storage/installed_live_ledger.py,
storage/installed_live_preservation.py, ingestion/installed_live_acquisition.py,
scripts/installed_live_migration.py; tests/unit/test_installed_live_storage.py,
tests/unit/test_installed_live_recovery.py.

**Interfaces:**
- initialize_live(conn, *, session, now) -> dict: exact eight live_* tables
  named in spec; caller owns explicit transaction and capability.
- stream_legacy_fingerprint(conn, *, batch_rows=2048, batch_bytes=8388608,
  value_chunk_bytes=65536, cancel=None) ->
  CompleteFingerprint(schema_sha, table_counts, table_shas); cancellation
  raises IncompleteScan, never returns a complete object.
- acquire_live_page(session, request, transport, *, run_id, now) ->
  AcquisitionResult(status, receipt_id, replayed, custody_mode).
- audit_live(conn, *, session) -> AuditResult; reconstruct all request/RAW/
  observation/cursor and grant high-water counters with bounded iterators.
- plan_source_backfill(coverage, capability, *, source_contract) -> dict:
  pure bounded plan, NOT live requests or provider availability oracle.
- rehearse_live_migration(fixture_session, *, fault=None) -> dict; fixture only.

- [ ] RED: 100.001+ actual SQLite legacy rows preserved without old row limit,
  type/BLOB/null digests, changed DDL, incomplete scan, WAL row in copy/restore,
  DDL/precommit crash rollback, partial addon schema drift, bounded fetchmany;
  oversized BLOB/TEXT hashed incrementally, not 2.048 full large values at once.
- [ ] RED acquisition/recovery: reserve before HTTP and outside SQL write
  transaction; grant budget shared across two run IDs/processes; deletion of
  any/all history/cursors blocks; unknown timeout consumes reservation; same
  RAW/different requests retained; crash at reserve/RAW/rows/commit; successful
  resume calls zero transport without comparing to fresh live response.
- [ ] RED coverage: local min/max/empty not completeness; FRED/Yahoo/event
  calendars, RBN dimensions/DP estimate, directional cursors and source limit
  remain distinct. Legacy tables untouched by acquisitions.
- [ ] Run python3 -m unittest -v tests.unit.test_installed_live_storage
  tests.unit.test_installed_live_recovery; implement interfaces then GREEN.
  Measure peak batch/memory and include timings in the engineering ledger.
- [ ] Commit; do not execute migration or copy on the installed DB/backup.

## Task 4: Operational evidence, private report and entrypoint integration

**Files:** Create analysis/installed_live_evidence.py,
scripts/installed_live_pipeline.py, scripts/installed_live_readiness.py;
modify analysis/installed_evidence.py, analysis/package_b_evidence.py,
analysis/package_b_analytics.py, rendering/package_b_report.py only where
needed to share temporal kernels/add explicit real modes;
tests/unit/test_installed_live_pipeline.py and tests/unit/test_installed_live_evidence.py.

**Interfaces:**
- read_live_snapshot(session, *, asof, snapshot_captured_utc, selections,
  start_utc, end_exclusive_utc) -> ReportSnapshot with explicit target/data origin.
- Assess acquired_utc along with request attempted/ended/capture UTC before
  linking even UNRECONCILED legacy identity. Do not promote link to verified.
- run_live_pipeline(plan, *, capability=None, apply=False, transport=None) ->
  PipelineResult; default pure PLAN_ONLY, seven existing stages, no inherited DB.
- assess_writer_readiness(manifest, observations) -> dict: uncooperative/
  unknown writer BLOCKED, global participation cannot come from fixture PASS.
- CLI installed_live_pipeline.py: --plan only by default; explicit --apply
  requires approved grant+plan+target. No scheduler/Telegram/upload hooks.

- [ ] RED: six-source simulated E2E through new boundary, temporal filters in
  SQL before batch loading, legacy acquired_utc future/naive rejection, native
  OHLCV versus close-only, provenance tags, model/HTML/archive/gate recomputed,
  incomplete one-source pilot yields private diagnosis rather than full PASS.
- [ ] RED: all 21 stage crash boundaries, SIGKILL child, corrupted checkpoint/
  RAW/plan/grant, expired grant during run, no-refetch completion, parent lease
  shared among descendants and writer bypass produces BLOCKED readiness.
- [ ] Run python3 -m unittest -v tests.unit.test_installed_live_pipeline
  tests.unit.test_installed_live_evidence and affected B/installed tests;
  implement GREEN and commit. Defaults remain no I/O.

## Task 5: One integrated acceptance and concrete operational handoff

**Files:** Modify tests/run_all.py, tests/unit/test_package_a_receipts.py and
.github/workflows/portable-report-tests.yml counts/registration together;
create docs/PRODUCTION_INTEGRATION_EXECUTION_LEDGER.md and operational runbook;
update this proposal/pilot only to reflect final code and observed limitations.

- [ ] Register six new suite modules from Tasks 1–4: expected 89 registered,
  88 offline, 1 live forcibly skipped. Confirm actual registry AST/execution;
  update tests/workflow atomically if scope changes, never claim guessed counts.
- [ ] Final self-review against spec and all five Review Focus classes; scan
  real-default/secret/provenance/permission hazards. Whole-branch review and
  resolution of P0/P1 before frozen QA. Preserve every worktree.
- [ ] Run complete runner ONCE at final code, retention, PLAN_ONLY, relevant
  existing visual QA for changed reports; original RCs and precise versions.
  CI Python 3.11 full runner + Python 3.14 installed/live module checks.
- [ ] Publish one final Hermes offline order on exact HEAD/base/tree only when
  complete; one report then STOP. No APIs/DB installed during this QA.
- [ ] Merge only after current-SHA CI/QA/review; record
  PRODUCTION_BINDING_OFFLINE_ACCEPTED with identical merge tree on #60/PR.
- [ ] Pin the private pilot proposal to accepted code/plan SHA and prepare an
  exact grant for owner G2 review. No operator starts a pilot solely from G1.

## Review and execution handoff

Proposal prepared together with the design for one reviewable decision; not
implementation approval. Need G0: owner exception for ONE additional integrated
offline engineering PR, acceptance of written scope/plan and native execution.
API/DB installed/deploy remain outside G0. Hermes STOP until final pinned QA.

Self-review: five tasks cover all spec blocks, interfaces have single owners,
Review Focus cases assigned, no unbounded scan or new live default, no unclear
permission or fabricated history state. Six new suites → 89/88/1 planned counts.
