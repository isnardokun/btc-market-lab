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

## Independent whole-branch review and consolidated fix pass

Reviewer /root/installed_final_review, fresh-context gpt-6-astra, base
1a224b0..e61e03f: four Important findings, zero Critical. Six targeted regressions
failed locally (seven failures including subcases), recorded before fixes:
future naive/date capture, swapped/foreign actual SQLite descriptor, deleted
cursor set, deleted reserved attempt/budget refund. Initial candidate final
runner then completed 82/82 offline PASS, RC=0; this did not resolve review P1s.

Fix pass:
- Reject ambiguous naive/date capture instead of substituting snapshot time.
- Pin O_NOFOLLOW and independently attest actual Linux SQLite MAIN descriptor
  before SQL; repeat path/descriptor checks through transactions. Reader,
  acquisition, pipeline and both backup/restore ends use the same boundary.
  Fifteen targeted tests passed locally before the execution service disconnected.
- Reconstruct COMPLETE cursor set/frontier and continuity from reconciled
  successful receipts; absent cursors are corruption, not fresh streams.
- Durable independent run reservation total/per-request high-water counters
  are committed with every reservation. Audit requires exact complete receipt
  history and contiguous attempts; deleting even all reserved rows cannot refund.
Final consolidated fix verification is assigned to current-SHA GitHub CI because
exec-server transport disconnected and recovery timed out. No fake local PASS.

Final: minor (deferred): legacy matching-link evidence does not check
market_request_acquisitions.acquired_utc. It remains explicitly
LEGACY_REQUEST_LINK_PRESENT_UNRECONCILED, never native verified acquisition.
A future production/as-of lineage adapter must validate that timestamp before
crediting links; no productive approval inferred.

Final: Ruling: live source contracts/entitlement, complete installed DDL,
productive migration/restore, scheduler and global production writer exclusion
remain unjudged and blocked by separate owner permissions. Cost if wrong:
new pilot evidence and operational integration before productive acceptance.

Ruling: actual-descriptor verification requires Linux /proc/self/fd and fails
closed when descriptor identity is unavailable. Cost if wrong: a separate
platform implementation is required on non-Linux hosts.

Ruling: keep recovery/backups fully descriptor-attested using SQLite online
backup directly in a new private fixture folder; do not alter the historical
trusted backup helper used by the already-closed production diagnostic order.
Cost if wrong: revise only this offline rehearsal before productive migration.

## Hermes QA failure and deterministic fixture lifecycle correction

Initial final candidate 65ebed2 had current-SHA CI 82/82 PASS, retention and
structural visual checks PASS. Hermes report #6152857496 nevertheless returned
81/82 PASS, runner RC=1, SQLITE_ACTUAL_IDENTITY_MISMATCH in acquisition/WAL
restore. The report also used stale local master e0a3be9 as merge-base instead
of the ordered 1a224b0; it is not acceptance evidence for the frozen base.

Reproduced the existing uncheckpointed-WAL test on Python 3.14.8: failure at
the rehearsal's final writer open, with no newly opened SQLite MAIN descriptor.
The synthetic fixture initializer used Connection's transaction context manager,
which does not close the connection. Later garbage collection can leave its
descriptor in SQLite's retained cache while another WAL connection holds a lock.
The attestation correctly refuses an ambiguous reused descriptor before SQL.

Two new lifecycle regressions reproduced RED: initializer leaves MAIN open
with GC disabled; schema audit leaves its private in-memory reference open.
GREEN fix closes the initializer, direct connections in the four installed
test modules, and the audit reference deterministically. Transaction contexts
retain their commit/rollback behavior. Actual-descriptor attestation is unchanged;
cached/ambiguous descriptors, foreign connections and swap/restore still fail
closed. No synthetic test was skipped and WAL backup assertions remain intact.

Local verification: Python 3.14.7 / SQLite 3.53.1, 23 acquisition/lifecycle tests
plus 33 profile/evidence/pipeline tests PASS, original RC=0 for both commands.
Python 3.12.3 / SQLite 3.45.1: 23 acquisition/WAL tests PASS, RC=0. These are two
different version combinations, not a claim to have reproduced Hermes's exact
Python/SQLite build. CI adds a Python 3.14 job for all four installed modules;
the existing complete runner/retention/visual job remains.

Next: verify the complete runner and current-SHA CI, then publish FINAL_2,
explicitly superseding FINAL_1. Require fetching origin/master and verifying the
literal pinned base and its ancestry; never compare to stale local master.
Preserve pr-82-qa and use a distinct new QA worktree. PR #82 remains unmerged.
