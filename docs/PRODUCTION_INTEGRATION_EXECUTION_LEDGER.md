# SDD ledger — plan: docs/superpowers/plans/2026-10-14-production-integration.md

Spec: docs/superpowers/specs/2026-10-14-production-integration-design.md
Base: ab576a8c179edf97a379976a0c9b8aefed398b6e; proposal 3a2f341.
Owner G0 2026-10-13 20:52 America/Bogota; #60/#6159129314. Native execution,
one integrated PR, no live/installed DB/cron/publication. All old worktrees kept.

Pre-flight: Task 1 ValidatedGrant/RuntimeSession -> Tasks 2–4; Task 2 typed
request/result/parsers -> Task 3 reservation/RAW/cursor -> Task 4 snapshot/stages.
Task 3 complete fingerprint -> migration/legacy reader guard. Task 4 pure PLAN
and six test modules -> Task 5 runner/CI. Interfaces aligned; spec is authority.

Ruling: cloud skill helper shell scripts are not local executable resources;
use this durable Git-backed ledger with task BASE/tests/rulings — equivalent
tracking survives compaction — cost if wrong: regenerate task briefs from plan.

Ruling: G0 accepts the written design/plan and native method; no repeated
approval between engineering tasks. Shared Git/PR operations already authorized.
G2/G4 operational approvals remain separate — cost if wrong: hold runtime blocked.

Ruling: validate_runtime_grant needs a separately supplied approved artifact
SHA pin; a self-hashed grant alone cannot authorize production. Add optional
approved_artifact_sha keyword defaulting to denial — cost if wrong: adjust the
operator intake contract before a pilot, preserving default fail-closed.

Baseline code tree equals accepted PR82 (CI and Hermes 82/82), proposal adds
only three docs. No duplicate baseline runner or dependency installation.
Task 1: in progress; tests first. Final complete runner on final code remains due.

Task 1: complete (BASE 3a2f341; 11 methods/18 initial missing-policy failures RED -> 11/11 GREEN, RC0). Pure grant pins and source windows, UID/path/physical MAIN under parent lease; readonly close, contender zeroSQL and released session blocked.
Ruling: new private targets bind newly created identities in an owned fsynced receipt linked to the approved artifact; no existing unmarked target is reused. Installed targets require separately approved schema/writer/backup receipt hashes. No receipt is emitted by this offline engineering — cost if wrong: harden operational intake before G2/G4.

Task 2: complete (BASE 4f3e3e3; 11 missing-request RED tests plus pilot page-limit RED -> policy/transport 23/23 PASS RC0). Six native provider contracts, pure parsers, source/time/unit validation, error RAW, bounded bodies/deadline, redirect rejection and deterministic response close. Tests use only explicit mock opener or frozen custody.
Ruling: explicit mock opener is allowed ONLY for FIXTURE_TEST and yields SIMULATED_HTTP; live targets reject injected opener and FrozenLiveTransport — no simulated request is called real — cost if wrong: revise test/runtime transport seam before pilot.
Ruling: native request adds bounded page_limit bound into identity for the exact private pilot (limit=100); saturated funding page is rejected without cursor credit — cost if wrong: add source-verified pagination contract rather than assume an event grid.
Ruling: unknown dimension/provider-live shapes remain EXTERNAL_UNVERIFIED and fail contract; existing pure parser support is the scope, not entitlement/all Tier2 metrics — cost if wrong: add specific verified shape contracts following G3 observations.

Task 3: complete (BASE 616a54e; 16 absent-module RED plus nullable-PK, real-clock, credential-import and migration-kernel RED -> 43/43 core methods PASS RC0). Eight live tables, per-grant durable budgets, RAW/rows/cursors/coverage atomicity, no-refetch replay, corruption/deleted history/cursors block. Whole preservation scanned 100.007 daily rows, incremental 4MiB BLOB under 8MiB Python peak, typed/null/schema checks, WAL online copy/restore and additive rollback/idempotence.
Ruling: audits stream grants and keep at most 128 attempts per grant; per-page audits current grant, full corpus audit occurs at pipeline validation/final gates. Latest cursor references are queried rather than materializing all historical cursors — cost if wrong: strengthen cross-grant validation without rereading whole history before every page.
Ruling: ordinary nullable PK rows additionally bind physical rowid; WITHOUT ROWID uses bounded key/substr fallback and rejects a large value without a verified incremental route. Cancellation rolls back an owned read snapshot and never returns partial PASS — cost if wrong: add a native value stream path for that particular installed table.
Ruling: pure Bitview epoch extracted to bitview_contract; live parser reuses package_a_contracts without importing collectors/config. Fresh-process regression proves no source credential read at import. Legacy cross-parser checks remain in A's suites — cost if wrong: refine shared pure source parsers, not enable collector imports.
Ruling: installed acquisition refuses an uninitialized live schema; only explicit migrate_installed with granted schema/writer/backup receipts may create it. Migration compares full before/after legacy fingerprints in one transaction — cost if wrong: operational receipt schema must be revised before G4, no automatic production initialization.
Task 4: in progress. Hard wall-clock HTTP supervisor, SQL-filtered evidence, seven stages/recovery/readiness, full corpus gate and final acceptance remain due.
