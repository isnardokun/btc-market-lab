# Package A — Engineering execution ledger (living document in ONE PR)

**Status:** `A_IN_PROGRESS / QA_NOT_REQUESTED / PRODUCTION_NOT_AUTHORIZED`  
**Working branch:** `feat/package-a-integrated-historical-ingestion`.  
**Source of truth:** [Program A/B/C](INTEGRATED_DELIVERY_PROGRAM_ABC.md), [Issue #76](https://github.com/isnardokun/btc-market-lab/issues/76), [Agent roles](../AGENTS.md).  
**Baseline master:** `8ca80e0f221ad2b0b276f6557d16ee00fe0ee381` (R10-E.14 integrated; 52 prior suites).  
**Rule:** update THIS draft PR with meaningful commits until A is integrated; **do not create micro-PRs** for the following workstreams.

## Workstreams and objective acceptance

| Workstream | Required deliverable | Objective tests / evidence | State |
|---|---|---|---|
| A1 Writer coordination | A **single** global lock file/path for active writers, wrapper/launcher strategy compatible with legacy daily and manual jobs; no double-lock deadlock; uncovered writers visibly blocked | Child-process contention, process crash, HTTP/SQLite untouched on busy, real call-site coverage inventory; fail closed on uncooperative processes | PARTIAL — 10 opt-in entrypoints (4 daily/context +6 historical), OS contention CI; daily.sh/host and other writers remain unguarded |
| A2 Schema and history ledger | Versioned schema/indices/PK/units/vintages, migration plan and immutable legacy safety; coverage table/contract distinguishing observed/available/unverified | SQLite ephemeral migration/reconciliation, FK/integrity, duplicate insert, rollback and downgrade/restore plan | PARTIAL — fixture RAW/PK/SHA/cursor plus explicit attempted-vs-archived window evidence; legacy schema migration pending |
| A3 Provider adapters | Binance/Bybit OI+funding, ResearchBitcoin Tier2 catalog, Bitview, Yahoo, FRED with independent unit/frequency/shape/cutoff policies | Contract fixtures per source, fake HTTP failures, endpoint limits, missing/empty/restricted states, no fabricated historical claims | PARTIAL — 8 fixture streams (6 prior + Binance/Bybit funding events); source-native checks RBN/Bitview/Yahoo; live shapes unverified |
| A4 Historical engine | One integrated backfill/forward engine with RAW+SHA, HTTP receipt, lineage, idempotent upsert/cursors, checkpoint/retry/rate limit and bounded batches | Full synthetic multi-provider forward and backward, 429, crash-before/after-commit, cursor monotonicity, replay idempotence, PK SHA and source audits | PARTIAL — synthetic bounded batch, 429 stop, backward/forward replay, crash/rollback, cursor tail validation |
| A5 Acceptance | Full offline E2E + migration/regression + design/operational evidence, one final CI and one pinned Hermes QA | 52 original suites + new package suites, all RC0; zero P0/P1; terminal `A_OFFLINE_ACCEPTED` or explicit BLOCKED; do not claim external coverage | TODO — runner 55 suites not completed, comprehensive migration/fault tests and Hermes final QA pending |

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
