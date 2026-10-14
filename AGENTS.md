# Working instructions — BTC Market Lab

This repository is managed under the [Integrated Delivery Program A/B/C](docs/INTEGRATED_DELIVERY_PROGRAM_ABC.md), superseding sequential R10-E microphase development after the validated R10-E.14 merge (PR #75).

## Authority and roles

- **ChatGPT — engineering owner**: designs architecture, implements/refactors code, writes and fixes tests, commits, creates and updates branches/PRs/issues, reviews QA evidence, merges when gates pass.
- **Hermes/OpenClaw — independent QA ONLY**: may run exactly the tests and static inspections authorized by the current pinned GitHub PR QA order; produces one review report on that PR and **STOP**. **Never** change source, tests, docs, worktrees, commits, branches, PR status or merges; never install packages, fetch market APIs, read production DB, touch cron/scheduler or handle credentials.
- **User/property owner**: exclusively authorizes live API traffic, quota expenditure, production SQLite access, backup/migrations, scheduler/cron, enabling feature flags, deployment and external publications.

## Delivery rule: integrated capabilities, not micro-PRs

Deliver only three substantial packages unless the owner explicitly changes this policy:

Owner exception, 2026-10-12 21:45 America/Bogota: ONE additional integrated
offline compatibility PR for the installed schema and A/B/C, authorized after
Issue #60 comment #6137030693; authorization recorded in #6137088170.
Preserve the same roles and final SHA/CI/QA gates. This does not authorize
API traffic, production DB access/migration/restore, cron, flags or deployment.

Owner exception, 2026-10-13 20:52 America/Bogota: ONE further integrated
offline engineering PR for production bindings, approved proposal #6159072770
and authorization #6159129314. Design/plan: 2026-10-14-production-integration.
ChatGPT implements natively; Hermes final pinned QA only. No live pilot,
API quota, installed DB/backup/migration/restore, cron/deploy/publication granted.

- **A — Historical data and integrated ingestion**: [Issue #76](https://github.com/isnardokun/btc-market-lab/issues/76).
- **B — Analytics, design system and professional reporting**: [Issue #77](https://github.com/isnardokun/btc-market-lab/issues/77).
- **C — Orchestration, resilience and controlled rollout**: [Issue #78](https://github.com/isnardokun/btc-market-lab/issues/78).

Each package is developed in ONE long-lived draft integration PR, with multiple meaningful commits and workstreams, and a final independent QA order to Hermes at a frozen SHA. **Do not** open a PR merely for a minor helper, test, documentation tweak or minor design decision. Use commits within the same package PR. Exceptions: genuinely urgent P0 security repairs, explicit owner change, or an incompatible independent hotfix.

## Development checklist (ChatGPT)

1. Read the current master and package acceptance scope, compare the branch to master, and preserve prior worktrees/evidence. Never reset/clean the original dirty worktree.
2. Implement vertical slices that run end-to-end on synthetic data: adapter → RAW+SHA → normalize → SQLite → receipt/lineage → audit → analytics/report as applicable.
3. Add module tests, contract tests, fault injection, idempotency, race/crash cases, source unit/cadence/vintage tests and tests of missing/failed API windows. Avoid tests that only assert mock internals while the integrated behavior is broken.
4. Run targeted affected tests and CI during implementation. Run the complete portable runner for the final PR SHA; do not duplicate all old focused suites manually and again in the runner at every tiny commit.
5. Review diff for schema/backward compatibility, secrets, runtime safety, no real-HTTP surprises, no production path defaults, no false data-completeness claims, and no data corruption.
6. Publish a SINGLE pinned QA order to Hermes only when the entire package is ready for acceptance. Include exact HEAD/base, permitted commands, expected suite counts, E2E assertions, worktree isolation and STOP. On FAIL, ChatGPT changes code in the SAME PR and issues a **new** pinned order that explicitly supersedes the earlier one.
7. Merge only after current-SHA GitHub CI, independent Hermes report, and P0/P1 issues are resolved. Record merge and evidence on Issue #60 and the relevant package Issue.

## Safety baseline / STOP rules

- **No real API calls** (ResearchBitcoin, Bitview, Yahoo, FRED, Binance/Bybit, Exa, etc.) until explicit authorization covers source, endpoint, quantity, scope and cost.
- **No production SQLite access, migration, WAL backup, cron/scheduler change, feature-flag activation or publication** until individually authorized.
- A declared ResearchBitcoin Tier 2 plan is **NOT** independently verified entitlement; local MIN/MAX is not provider availability; empty responses do not prove completeness; FRED/Yahoo sessions and vintages are source-specific.
- Preserve RAW payload hashes, request provenance for new calls, idempotent upsert/cursors and run receipts. **Never fabricate** receipts for 1,035 legacy Funding P1 records or change the R10-D.2 retrospective evidence.
- **All writers must actually share the same lock file and namespace** before productive concurrency is allowed. POSIX flock is advisory and does NOT protect against uncooperative writers. A single opt-in entrypoint is not global safety.
- Missing permission/evidence => `BLOCKED` or `NOT_VERIFIED`, not a fabricated PASS.
- Keep real credentials and confidential payloads out of GitHub discussions, logs, fixtures, or generated artifacts.
- On schema drift, SHA drift, unsigned QA, failed quality gates, or new uncooperative writer => STOP that operation and report; do not bypass safety controls merely to finish a package.

## QA protocol for Hermes

- **No active QA order means STOP.** Neither this file nor the package issue grants permission to test, modify, or deploy on its own.
- Follow ONLY the most recent explicitly designated QA order comment for the current package PR, pinned to exact commit SHA. Reject changed/unknown SHA.
- Create a fresh detached clean worktree; keep the original dirty tree and every historical QA worktree, do not remove them.
- Only run authorized tests against TemporaryDirectory fixtures and fake HTTP; never direct CLIs at production DB or use real market APIs.
- Report Python, HEAD, base, timestamp, worktree, RC, relevant assertions and failures, runner AST registered versus actually executed, and whether tests touched production (must be no).
- Post exactly ONE comment on the package PR with PASS/FAIL/BLOCKED evidence and **STOP**. Do not attempt fixes or merges.

## Acceptance meaning

- `OFFLINE_ACCEPTED`: integrated offline functionality works against synthetic fixtures and passes CI/QA.
- `EXTERNAL_VERIFICATION_PENDING`: entitlement, historical API availability or quotas remain unverified without owner-authorized traffic.
- `PRODUCTION_ACCEPTED`: distinct later approval after explicit user authorization, backups/runbook, controlled pilot and observed gates. Never infer this state from offline QA.

Project tracking: [Issue #60](https://github.com/isnardokun/btc-market-lab/issues/60). Do not quantify completion by total tests or number of PRs.
