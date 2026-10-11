# Package A — Engineering execution ledger (living document in ONE PR)

**Status:** `A_IN_PROGRESS / QA_NOT_REQUESTED / PRODUCTION_NOT_AUTHORIZED`  
**Working branch:** `feat/package-a-integrated-historical-ingestion`.  
**Source of truth:** [Program A/B/C](INTEGRATED_DELIVERY_PROGRAM_ABC.md), [Issue #76](https://github.com/isnardokun/btc-market-lab/issues/76), [Agent roles](../AGENTS.md).  
**Baseline master:** `8ca80e0f221ad2b0b276f6557d16ee00fe0ee381` (R10-E.14 integrated; 52 prior suites).  
**Rule:** update THIS draft PR with meaningful commits until A is integrated; **do not create micro-PRs** for the following workstreams.

## Workstreams and objective acceptance

| Workstream | Required deliverable | Objective tests / evidence | State |
|---|---|---|---|
| A1 Writer coordination | A **single** global lock file/path for active writers, wrapper/launcher strategy compatible with legacy daily and manual jobs; no double-lock deadlock; uncovered writers visibly blocked | Child-process contention, process crash, HTTP/SQLite untouched on busy, real call-site coverage inventory; fail closed on uncooperative processes | PARTIAL — 20/24 known source-level opt-in CLI candidates plus guarded report/news library; host/daily.sh, unknown writers, library callsites remain unverified |
| A2 Schema and history ledger | Versioned schema/indices/PK/units/vintages, migration plan and immutable legacy safety; coverage table/contract distinguishing observed/available/unverified | SQLite ephemeral migration/reconciliation, FK/integrity, duplicate insert, rollback and downgrade/restore plan | PARTIAL — WAL-safe synthetic legacy coexistence migration+backup/rollback and strict PK/FK/index validation; productive DB migration still unauthorized |
| A3 Provider adapters | Binance/Bybit OI+funding, ResearchBitcoin Tier2 catalog, Bitview, Yahoo, FRED with independent unit/frequency/shape/cutoff policies | Contract fixtures per source, fake HTTP failures, endpoint limits, missing/empty/restricted states, no fabricated historical claims | PARTIAL — 8 fixture streams (6 prior + Binance/Bybit funding events); source-native checks RBN/Bitview/Yahoo; live shapes unverified |
| A4 Historical engine | One integrated backfill/forward engine with RAW+SHA, HTTP receipt, lineage, idempotent upsert/cursors, checkpoint/retry/rate limit and bounded batches | Full synthetic multi-provider forward and backward, 429, crash-before/after-commit, cursor monotonicity, replay idempotence, PK SHA and source audits | PARTIAL — synthetic bounded batch, 429 stop, backward/forward replay, crash/rollback, cursor tail validation |
| A5 Acceptance | Full offline E2E + migration/regression + design/operational evidence, one final CI and one pinned Hermes QA | 52 original suites + new package suites, all RC0; zero P0/P1; terminal `A_OFFLINE_ACCEPTED` or explicit BLOCKED; do not claim external coverage | PARTIAL — full CI 60/60 offline suites PASS, one live test quarantined; package integration, real-source verification and final Hermes QA pending |

## Required interface shape

Every provider adapter specifies `provider, metric, symbol, frequency, event/granularity, unit, raw_scale, dimensionality, endpoint`, whether upstream data are day bars/event/vintage, and `earliest_requested` separate from `earliest_observed` and `earliest_provider_verified`. The engine records `request_attempt`, `raw_payload_digest`, `parsed_rows`, `source_run`, `checkpoint` and `audit_outcome` with foreign-key linkage. Retried pages must not create duplicates. A zero-row `empty` response cannot imply provider completeness.

**No speculative FULL_HISTORY or ENTITLEMENT_VERIFIED.** Provider limits/tier remain `NOT_CHECKED` in synthetic tests. FRED release calendars and vintages must be treated differently from Yahoo sessions; Funding timestamps are event-settled, not fixed daily 5m bars.

## Cross-process lock adoption acceptance

Only one lock namespace AND one absolute lock-directory per SQLite resource; a real unlocked process can bypass `flock`, and therefore an opt-in option in one file is **not** proof of global coverage. Production-reachable manual and scheduler entrypoints must be covered through an agreed launcher and/or explicit guard, and the central inventory must identify remaining unprotected paths. Do not change cron or activate the new launcher in this development PR.

Avoid holding SQLite write transactions across network calls. Plan and fetch externally, persist short batches under cooperative lock with recoverable consistency. Test lock-holder crashed at several cut points.

## DB migration and reconciliation acceptance

Migrations should be additive when possible, with explicit `user_version` / compatibility behavior, rollback or forward restore plan; test old schema to new in a **copied fixture**. For every page, pre-existing rows preserve their PK/hash, new RAW receipts match normalized entries, and checkpoint advancement occurs only after successful atomic persistence. Never fabricate old R10-D.2 / Funding P1 provenance.

## Integration scenarios — no QA pass without these

1. Start from an empty synthetic DB and build the schema.
2. Fetch fake history for each supported source and ingest forward/backward with both event and periodic semantics.
3. Restart from a saved checkpoint and prove idempotency and zero duplicate PK rows.
4. Fake 429/5xx/network timeout, enforce limited retry, no cursor advancement and retain traceable attempted request.
5. Two independent processes compete for a shared writer lock; the loser does not request HTTP or modify SQLite.
6. Crash at source run creation, RAW insertion, normalized rows, and cursor boundary, then replay without corruption.
7. Verify legacy data and 1,035 Funding P1 rows remain untouched and explicitly labeled unreceipted where applicable.
8. Reconcile RAW/SQL/TEMPORAL/LINEAGE/FK/integrity and compare row-level digests before/after.
9. Validate FRED frequency/realtime vintage, Yahoo session days, Bitview daily, and Binance/Bybit OI funding.
10. Demonstrate requested/observed/provider-verified ranges remain different, and entitlement remains unverified without authorized real proof.
11. Ensure default automation/run scripts and all production flags are unchanged.
12. Print a precise offline coverage manifest: tests, providers, metric counts and unresolved external blockers.

## Implementation and review rules

- ChatGPT writes the code and tests and runs GH CI on each meaningful commit. Use issues and this ledger for implementation notes.
- Maintain ONE draft PR for entire package. No Hermes order until A1–A5 are complete; exceptions only on explicit user request.
- At final review pin SHA, verify current master, CI, full runner, cross-process and migration tests; Hermes performs independent final QA only and STOP.
- During development, do **not** read production DB, call live provider APIs, install services, run cron, migrate DB or publish reports.
- `A_OFFLINE_ACCEPTED` and `A_EXTERNAL_VERIFIED` are different decisions. A can be accepted offline without external credentials/HTTP; report those unmet real-data requirements honestly.


## 2026-10-11 — implemented changes, evidence, remaining gates

The SINGLE package PR #79 now includes **real code**, not only the contract:

- A1 (PARTIAL): opt-in `--global-lock-dir` wired to `ingestion/ingest.py` (Bitview daily), `ingestion/ingest_fred.py`, and `scripts/market_context_ingest.py`; Yahoo daily price had the earlier E14 opt-in. Same namespace `btc-research-global-writer-v1`. Default script commands and `daily.sh` are unchanged **and still NOT operationally protected**. The remaining historical/manual/cron writers have not been proven to adopt the global lock.
- A2 (PARTIAL): `storage/package_a_ledger.py` adds versioned fixture schema for BLOB RAW receipts+SHA256, immutable observations+row digests, FK-linked cursors, observed coverage and fail-closed external `NOT_VERIFIED` states. All writes are performed only on explicitly created synthetic SQLite.
- A3 (PARTIAL): `ingestion/package_a_contracts.py` validates synthetic source payload shapes for RBN/Bitview/FRED/Yahoo/Binance/Bybit. `package_a_legacy_bridge.py` cross-checks RBN/Bitview/Yahoo values against the existing repo parsers, blocking disagreements and rejecting missing final 5m OI candles. **Real entitlement, historical availability, quota and API response compatibility remain UNVERIFIED.**
- A4 (PARTIAL): `scripts/package_a_ingest_fixture.py` and `scripts/package_a_batch_fixture.py` integrate lock → parser → RAW receipt → immutable observations → cursor → integrity audit. Synthetic batch stops at 429, resumes, supports backward history without regressing the forward cursor and rolls back atomic failures.
- A5 (NOT STARTED): no final Hermes QA order. Complete active writer adoption, safe legacy data migration, real adapter/network injection contracts (offline), broader crash/fault modes, full-runner regression and final QA.

**Latest verified CI evidence before this documentation update:** on commit `6ea7e922133fee37c91ad4b3586efe36fbad5387`, GitHub Actions [run 38148242880](https://github.com/isnardokun/btc-market-lab/actions/runs/38148242880/job/114496024820) PASS, including **315 earlier CI tests + 32 integrated package tests + 16 entrypoint lock tests**. CI is not equivalent to the complete `tests/run_all.py` runner, which now lists **54 suites**, or an acceptance verdict. Package remains `A_IN_PROGRESS`.

**No Hermes order, no live APIs, no production DB access, no cron modification or activation.**


## 2026-10-11 — further implementation under this SAME PR

- Shared `scripts/package_a_writer_guard.py` now protects **six existing historical apply entrypoints** with explicit `--global-lock-dir` and consistent `btc-research-global-writer-v1`: `ingestion/bitview_history.py`, `ingestion/yahoo_history.py`, `ingestion/researchbitcoin_archive.py`, `scripts/market_history_forward.py`, `scripts/market_history_pilot.py`, `scripts/market_history_batch.py`. Lock is acquired **before starting the apply worker**; occupied lock returns RC=3, zero DB/HTTP in tests. Plan/default commands are unchanged. Opt-in is NOT production adoption; live host jobs/daily.sh still do not pass the option. Non-cooperative writers are a known blocker.
- A1 tested via `tests/unit/test_package_a_historical_writer_locks.py` (**17 tests**) with a real independent child `flock` holder, contention across all six, release/crash, unsafe path, plan no-lock and unchanged default behavior. Combined with prior daily/context tests (**16**), CI has **33 targeted writer-lock tests**.
- A3 extended `ingestion/package_a_contracts.py` to support **event-settled funding** on Binance and Bybit, separate from OI 5m interval candles. Supports signed raw fraction units, provider timestamp/retcode validation, no invented periodic funding candles. Integrated batch now tests **eight source×metric streams** (Bitview, ResearchBitcoin, FRED, Yahoo, Binance OI+funding, Bybit OI+funding). These are offline contractual fixtures, not proof of the live API's current shape.
- A2/A4 extended `storage/package_a_ledger.py:read_history_evidence` and `scripts/package_a_ingest_fixture.py:inspect_fixture` to differentiate **requested windows from archived UTC ranges** and count ok/empty/failed/rejected attempts, including streams with zero observed rows. `entitlement=NOT_VERIFIED` and `historical_completeness=NOT_VERIFIED` never change as a result of fake responses.
- The package integrated suite now has **45 tests**, and `tests/run_all.py` has **55 registered suites**. CI workflow runs the package suites during development. **Do not claim 55/55 PASS until full runner completes**.
- The production SQLite, WAL, cron/scheduler, API credentials, requests, backups and publish jobs were not touched. Funding P1 1,035 legacy observations missing per-request receipts remain unchanged.

**CLI backwards compatibility review:** Yahoo historic archive retains its original `argparse` error code 2 for malformed parameters, with a dedicated offline regression. When `--global-lock-dir` is NOT present, every previously existing CLI executes unchanged and remains **UNLOCKED**; that is a production adoption blocker, not a test PASS of host safety.


## 2026-10-11 — Additive legacy migration validation and schema hardening

**Implementation:** `scripts/package_a_migration_rehearsal.py` runs only on a specially marked disposable `package_a_synthetic.db` in `TemporaryDirectory`, under the E11 **global lock**. Its WAL-compatible backup is created using the repository's existing `scripts.backup_db.create_backup`, then the **actual** `storage.package_a_ledger.initialize` installs `package_a_*` tables additively inside `BEGIN IMMEDIATE`. Integrity, FK checks, before/after legacy table content hashes and version preservation are checked. Synthetic crashes after first DDL or just before commit MUST leave **no partial Package A DDL**, preserve all original data and retain a restorable original backup.

**Authenticity boundary:** the migration tests seed a synthetic replica with the archive metadata schema and market-context v3, including **1,035 artificial legacy Funding rows** (635 Binance + 400 Bybit) *without per-request provenance*. They verify none is changed and no new receipt is fabricated. This is NOT evidence that the real production database currently has exactly the fixture's schema or valid WAL state.

**Schema hardening:** even a table containing the correct names of every column is REJECTED if PK, FK, indexes or schema version are wrong. The real fixture page writer now calls the shared `storage.package_a_ledger.validate_schema` on every initialization; `initialize` rolls back partial DDL using a SAVEPOINT. Tests deliberately construct forged primary keys, missing foreign keys, mismatched migration markers and a syntactically broken DDL.

**Latest verified CI:** commit `606641d7fc2c98f906e38fd1e2cbe7a4a1af3b8e`, [GitHub Actions #38150583781](https://github.com/isnardokun/btc-market-lab/actions/runs/38150583781/job/114503026604), **2/2 checks SUCCESS**, suites: **315 portable regressions + 48 Package A integrated + 16 daily/context lock + 17 historical CLI lock + 17 migration = 413 directed tests PASS**. An earlier CI commit had **3 test-assertion wording failures** (not a safety bypass); ChatGPT corrected the tests and both checks on the new pinned SHA are green. Full registered runner now has **56 suites**, **not yet certified 56/56**, and there is **NO Hermes final QA order**.

**Open:** real host writer/cron adoption, exact existing production schema compatibility, live API entitlement/retention and provider response verification, full runner, final CI/QA. Owner authorization remains mandatory before actual DB/API/backup/pilot work. `A_IN_PROGRESS` / `R10_E_PRODUCTION_NOT_AUTHORIZED`.


## 2026-10-11 — Full offline runner safety and concurrent Bitview bulk writer

**Safety finding:** `tests/integration/test_pipeline.py` previously dereferenced the hard-coded `/home/ignotus/btc-research/db/btc_research.db` *at import time*. Launching the original `tests/run_all.py` from a QA worktree on the user host could open production SQLite. **The default runner is now strictly offline**: it retains all 57 declared entries, executes the 56 offline suites and visibly marks the single legacy live integration as `SKIPPED_LIVE_NOT_AUTHORIZED`. There is no opt-in bypass in the runner; the live suite must be handled by a separate explicitly authorized operation. A static regression test `test_package_a_runner_offline_gate.py` inspects AST ordering to ensure the skip happens before subprocess invocation. Historical R10-E.3 failure-propagation regression has been adapted to preserve the designated live entry in its synthetic AST fixture. This did NOT weaken the runner's live-suite guard.

**Execution time and CI quality:** replaced duplicate per-subsystem GitHub CI commands with **one complete default offline runner step**, retaining independent runtime-retention tests. `tests/run_all.py` now reports independent `OFFLINE_SUITES_EXECUTED`, `OFFLINE_SUITES_PASSED`, `LIVE_SUITES_SKIPPED`, `REGISTERED_SUITES` and `LIVE_STATUS=NOT_TESTED_NOT_AUTHORIZED`. GitHub workflow asserts expected values and fails if a suite is missing or fails, rather than presenting 57/57 PASS.

**Additional real writer opt-in:** `ingestion/ingest_all.py` (legacy bulk Bitview, 20 concurrent API workers) now accepts an explicit `--global-lock-dir` and acquires the SAME E11 global `flock` before schema setup, SQLite and threaded ingestion; existing `--backfill`/`--dry-run` safety errors and unflagged behavior remain unchanged. It also closes its startup DB connection after schema setup. Seven additional offline tests in `test_package_a_entrypoint_locks.py` verify external child-process lock contention, holding lock while worker runs, no DB/HTTP under contention, invalid lock directory, rejected legacy commands and unchanged default.

**Latest independently observed GitHub Actions:** [CI job 114505402484](https://github.com/isnardokun/btc-market-lab/actions/runs/38151386307/job/114505402484) on commit `d3903fe466226944f61a6a990d4e7317650ed36a`: **2/2 GitHub checks SUCCESS**, `REGISTERED_SUITES=57`, `OFFLINE_SUITES_EXECUTED=56`, `OFFLINE_SUITES_PASSED=56`, `LIVE_SUITES_SKIPPED=1`, production pipeline `NOT_TESTED_NOT_AUTHORIZED`. No fake pass on the live integration test.

**Remaining A1/A2/A3/A4 blockers:** all writers must actually coordinate on the same physical lockfile; current opt-ins are **not used by default cron**, and archive/metrics/other write paths are still to be accounted for. Real host process inspection, source API rights/coverage, SQLite operational schema/migration, production backup, historical backfill and scheduling require explicit user authorization. `A_IN_PROGRESS`, Hermes `STOP`, `historical_completeness=NOT_VERIFIED`.


## 2026-10-11 — Daily archive/backfill writer coordination and live-safe inventory (single PR #79)

**Six new opt-in entrypoints (17 total static candidates):**
- `validation/archive_metrics.py`: archived technical/market metrics writer; now explicit `--global-lock-dir` before its SQLite and potential Yahoo read.
- `rendering/archive_report.py`: CLI report versioning and market-level writes under the same shared lock. Direct library calls without the CLI flag remain **UNPROTECTED**.
- `rendering/export_dashboard.py`: shared lock around SQLite read and dashboard file export.
- `ingestion/snapshot.py`: shared lock before snapshot DB reads and file output; standalone script resolves repository root before import.
- `ingestion/backfill_metrics.py` and `ingestion/backfill_onchain_metrics.py`: legacy manual backfill writes gain the explicit opt-in lock; original unflagged CLI behavior retained.

**Static adoption discovery:** `scripts/package_a_writer_adoption_review.py` wraps original E13 source inventory and uses AST, not string searches, for an actual opt-in CLI argument and a guard call. Result on tracked source: **24 inventoried writer contracts, 17 static opt-in candidates, 7 unprotected or not independently verified contracts**. It also reports the actual `daily.sh` command references and explicitly classifies their runtime lock adoption **UNVERIFIED/UNGUARDED** because the daily shell does not pass the option. Library-level callers and unknown writers are not covered by merely protecting a command-line entrypoint. This is **NOT global exclusion certification**.

**Regression tests:** `tests/unit/test_package_a_daily_archive_locks.py` tests all six new entrypoints against same lock, including independent OS child-holder contention, crash/release, unsafe directories, default unlocked behavior and absence of unexpected DB/HTTP under blocked-lock mocked delegates. `tests/unit/test_package_a_writer_adoption_review.py` verifies 17/24 candidate boundary without incorrectly promoting its status, and rejects comments/docstrings that mention guards without actually calling them.

**Verified GitHub CI:** commit `616ebb1abffc5617b6e3821d6ec760b269135e59`, [workflow 38152921833 / job 114509911547](https://github.com/isnardokun/btc-market-lab/actions/runs/38152921833/job/114509911547), **2/2 checks SUCCESS**, runner **59 registered = 58/58 offline PASS + 1 live integration deliberately SKIPPED**. Hermes QA has **NOT** run on this package.

**Critical remaining safety gaps:** E13 enumerates 7 registered contracts not covered by CLI opt-in, and possible unknown writers. The same lock filename must be shared by ALL real processes, not just offered as an optional flag. The currently installed `daily.sh` remains untouched and by default unprotected. Dedicated migration CLI calls require backups, schema preflight, owner authorization and a separate operational rollout plan. No actual APIs, production SQLite, live backups, cron or publication operations have been executed; `historical_completeness=NOT_VERIFIED` and external ResearchBitcoin Tier2 entitlement remain unverified. `A_IN_PROGRESS`, **Hermes STOP**.


## 2026-10-11 — RBN V2, migrations, report/news library and nested-lock isolation

**A1 effective source-level opt-ins:** `ingestion/researchbitcoin_v2.py` now gates explicit `--init-db` and `--sync` under `--global-lock-dir`; `--catalog` and `--inventory` remain unmodified non-write paths and `--sample` is a deliberate inspect-only API call requiring its own real-API authorization outside QA. Both schema migration CLIs, `scripts/archive_db_migrate.py` and `scripts/market_context_migrate.py`, hold the same global file lock **before backups and DDL** only for `--apply`, leaving `PLAN` unaffected. The E13 **24 known contracts** now comprise **20 source-level opt-in candidates**, plus **4 library-only/non-CLI contracts** or remaining uncovered paths; AST evidence is NOT operational host protection.

**Direct-library write safety:** `ingestion/news_archive.archive_news` accepts an explicit `lock_directory` to acquire the same lock before opening SQLite. On contention the library raises `WriterLockBusy` (never returns a valid-looking count); without the argument it retains the legacy behavior. `analysis/daily_report.py` now has an opt-in `--global-lock-dir` parent boundary before HTTP, SQLite and report output. Inside the report, the existing `archive_news` library call runs *without a second nested flock*, because its parent owns the shared lock throughout. This is critical to avoid self-deadlock. `scripts/daily.sh` remains unchanged and its normal report invocation is still unguarded. Direct callers of archival utilities without the lock option remain uncovered and must not be claimed safe.

**Tests added:** 14 cases in `tests/unit/test_package_a_migration_and_rbn_locks.py` verify direct subprocess flock contention, RC3, migrations never running under occupied lock, legacy RC2 path errors, plan/catalog no-lock, unsafe directories and crash release. 15 cases in `tests/unit/test_package_a_report_library_lock.py` exercise a real **temporary** news SQLite fixture, replay deduplication, a parent report lock around a library insert, no nested lock acquisition, child-process contention and opt-in defaults.

**Complete CI gate:** on commit `1a8fd28a1c541db8b2079e27a1bb788275ccb8f6`, [Actions 38153979469/job 114513023349](https://github.com/isnardokun/btc-market-lab/actions/runs/38153979469/job/114513023349) shows **2/2 checks SUCCESS** and **61 suites registered: 60/60 offline PASS, one live DB integration SKIPPED** as required. This is NOT Hermes independent QA; no final QA order has been issued.

**Safe production cutover architecture — not activated:** Either (1) explicitly provide the *same existing directory+namespace* to every mutually exclusive writer stage, including any direct library writer, or (2) let a single parent job wrapper acquire the global lock for a complete job then execute only legacy **unflagged** child CLIs while it owns that lock. **Never combine a parent global flock and child opt-in flock on the same pathname**, because that can self-deadlock/fail busy. Parent orchestration should be designed in Package C and only deployed after the owner authorizes host preflight, WAL-safe backup, actual lock file identity and scheduler cutover. Static 20/24 ≠ runtime full protection; the original daily shell still does not use any lock option, and unknown writers/indirect imports have not been fully assessed.

**Residual A blockers:** a working source adapter-to-original-provider pathway beyond fixtures; genuinely obtainable historical windows, actual ResearchBitcoin Tier2 entitlement, coverage by provider and cadence, safe production-host writer enumeration, migration preflight and rollout permissions. Legacy Funding P1 1,035 per-request receipts remain missing and must never be fabricated. `historical_completeness=NOT_VERIFIED`, `A_IN_PROGRESS`, `PRODUCTION_NOT_AUTHORIZED`, Hermes `STOP`.


## 2026-10-11 — Bounded, source-aware local gap discovery (no API fetch)

After reconciling the current branch's newer implementation (20/24 static opt-in writers and 60/60 offline CI), Package A adds **`scripts/package_a_local_gap_candidates.py`** and **11 source-native offline tests**. This read-only function receives a caller-owned SQLite connection and typed source contract; it has no CLI, no database default path, no credential, no HTTP and cannot enqueue a real request. It models half-open UTC daily BTC and 5m OI grids under strict observation/window budgets, identifies internal and tail gaps, and explicitly labels them `CANDIDATE_NOT_PROVIDER_VERIFIED` with `request_ready=false`. Event-based settled funding and FRED monthly/weekly are **not** treated as periodic fixed-day gaps. Yahoo symbols requiring market calendars are not silently inferred.

The full offline runner now expects **62 registered suites**, of which **61 are offline** and **one legacy live-DB integration remains blocked/skipped**. GitHub CI validates these gates; **do not assert PASS of the latest HEAD until its checks complete**. This is discovery of local gaps, not evidence the exchange/API historically offers those missing intervals, nor verification of ResearchBitcoin Tier2 entitlement, quotas or a provider's earliest available date. No production DB/API/cron operation was performed, and Hermes remains STOP.
