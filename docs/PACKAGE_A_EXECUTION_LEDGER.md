# Package A — Engineering execution ledger (living document in ONE PR)

**Status:** `A_IN_PROGRESS / QA_NOT_REQUESTED / PRODUCTION_NOT_AUTHORIZED`  
**Working branch:** `feat/package-a-integrated-historical-ingestion`.  
**Source of truth:** [Program A/B/C](INTEGRATED_DELIVERY_PROGRAM_ABC.md), [Issue #76](https://github.com/isnardokun/btc-market-lab/issues/76), [Agent roles](../AGENTS.md).  
**Baseline master:** `8ca80e0f221ad2b0b276f6557d16ee00fe0ee381` (R10-E.14 integrated; 52 prior suites).  
**Rule:** update THIS draft PR with meaningful commits until A is integrated; **do not create micro-PRs** for the following workstreams.

## Workstreams and objective acceptance

| Workstream | Required deliverable | Objective tests / evidence | State |
|---|---|---|---|
| A1 Writer coordination | A **single** global lock file/path for active writers, wrapper/launcher strategy compatible with legacy daily and manual jobs; no double-lock deadlock; uncovered writers visibly blocked | Child-process contention, process crash, HTTP/SQLite untouched on busy, real call-site coverage inventory; fail closed on uncooperative processes | TODO |
| A2 Schema and history ledger | Versioned schema/indices/PK/units/vintages, migration plan and immutable legacy safety; coverage table/contract distinguishing observed/available/unverified | SQLite ephemeral migration/reconciliation, FK/integrity, duplicate insert, rollback and downgrade/restore plan | TODO |
| A3 Provider adapters | Binance/Bybit OI+funding, ResearchBitcoin Tier2 catalog, Bitview, Yahoo, FRED with independent unit/frequency/shape/cutoff policies | Contract fixtures per source, fake HTTP failures, endpoint limits, missing/empty/restricted states, no fabricated historical claims | TODO |
| A4 Historical engine | One integrated backfill/forward engine with RAW+SHA, HTTP receipt, lineage, idempotent upsert/cursors, checkpoint/retry/rate limit and bounded batches | Full synthetic multi-provider forward and backward, 429, crash-before/after-commit, cursor monotonicity, replay idempotence, PK SHA and source audits | TODO |
| A5 Acceptance | Full offline E2E + migration/regression + design/operational evidence, one final CI and one pinned Hermes QA | 52 original suites + new package suites, all RC0; zero P0/P1; terminal `A_OFFLINE_ACCEPTED` or explicit BLOCKED; do not claim external coverage | TODO |

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
