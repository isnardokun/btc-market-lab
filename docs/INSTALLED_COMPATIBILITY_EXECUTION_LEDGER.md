# Installed compatibility execution ledger

Plan: docs/superpowers/plans/2026-10-13-installed-compatibility.md
Spec: docs/superpowers/specs/2026-10-13-installed-compatibility-design.md
Base: 1a224b0db5d71200efe379aad58accd2e1d99e49; isolated linked worktree,
branch feat/installed-schema-compatibility. Owner exception #6137088170.
Baseline: 19 B evidence/analytics/C source tests PASS, Python 3.12.14.

Pre-flight: Task 1 profile/fingerprint consumed by Tasks 2–4; Task 2 immutable
B evidence consumed by Task 3 addon reads and Task 4 gates; Task 3 receipt
state/budgets consumed by Task 4 recovery. Signatures aligned.

Ruling: continue implementation under the approved concrete #6137030693
design and standing offline authority; consolidate written spec/plan here,
without repeating design approval stages. No productive authority inferred.
Cost if wrong: revise the offline implementation before final QA.

Ruling: keep FAKE_HTTP and future HTTP contracts separate; runtime accepts
only FAKE_HTTP. Real transport wiring awaits a source-specific authorized pilot.
Cost if wrong: an additional transport integration before production.

Task 1: complete; five profile/fingerprint tests RED (missing validator) →
GREEN. 34 tables, 54 named objects, exact columns/PK/indexes/FK and versions;
content and actual trigger SQL fingerprints. Tests use partial synthetic DDL,
explicitly not a production schema installer. Task 2 in progress.

Task 2: complete; eight missing-adapter failures and reproduced legacy SQL
OperationalError (close absent) → GREEN. Nine installed-reader tests plus
B evidence/analytics/report regressions: 37 tests PASS, 10.332s. Read-only
fixture guard, snapshot upper-bound/no lookahead, explicit unit mappings,
vintages, native RAW and limited provenance; partial close report stays blocked.
Task 3 in progress.

Task 3: complete; eleven missing-route failures → GREEN; added daily gap
regression reproduced false ok/cursor advance → rejected with no cursor.
18 acquisition/rehearsal tests plus profile/reader/B regressions: 60 tests PASS.
Atomic additive migration, one-RAW/five Yahoo streams, six providers, explicit
FAKE_HTTP provenance, reserved/crash budgets, no transport under transaction,
RAW corruption, mode mismatches, existing backup retention, and uncheckpointed
WAL inclusion/restore fingerprints tested. Task 4 in progress.

Ruling: receipt provider is canonical request metadata, not a duplicated
column; the test inspects persisted request JSON. Cost if wrong: add a indexed
provider column for productive query performance; semantic evidence unchanged.

Task 4: implementation tests complete; 14 pipeline methods plus 21 stage
before/after/after-output subcases, real child SIGKILL, no-refetch receipt
recovery, retries/budgets, links/inode/response drift and weighted gate pass.
Three regressions were reproduced RED→GREEN: nonzero-offset future capture,
persisted BLOCKED state on budget exhaustion, and changed run policy at final
resume. New installed profile suites registered: 83 total / 82 offline / 1
mandatory live skip. Updated the exact-count guard after reproduced 83 != 79.

Initial full runner: 81/82 PASS, RC=1, sole failure was the already-corrected
registry count (old guard loaded before update); complete final run pending.
Initial A4 QA failed with a footer-only page (89 chars). Evidence footer moved
into native tfoot, preserving rows/footer together: automated visual QA PASS,
Chromium 143.0.7499.0, 320/390/768/1440, 12 A4 pages, zero remote resources,
minimum page text 516 chars. Manual cover/final-page review passed; nowrap
numeric headers added after observing split Puntos header. Final visual pending.

No live APIs, productive SQL, cron, flags, source checkout or old worktrees
modified. Next: independent whole-branch review and final verification.
