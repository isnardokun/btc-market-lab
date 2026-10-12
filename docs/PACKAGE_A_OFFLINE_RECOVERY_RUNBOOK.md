# Package A — Offline crash and rate-limit recovery protocol

**Scope:** Integration rehearsal for marked temporary `package_a_synthetic.db`
only. **No production database**, real HTTP, API tokens, cron, or deployment.
This protocol is not the production recovery runbook.

## Recovery contract

The caller must provide the same complete, ordered, bounded original batch
plan (maximum 32 typed `FixturePage` or `FrozenLegacyPage` entries) plus
the exact **persisted** old `run_id`. Never infer a missing page from a
provider's alleged historical retention or a zero-row response.

The read-only `plan_synthetic_resume(db_path,lock_dir,pages,prior_run_id=…)`
reconciles the current SQLite RAW, request receipts, SHA-256, source-run
ledger, FK, interval and ownership using `inspect_fixture`. It matches
every already-successful prefix page by provider/metric/symbol/frequency,
canonical UTC request window, exact expected RAW hash and archived unit/scale.
It also checks that previously stored frozen HTTP provenance matches the
original page kind. The last failed page is **retried**, not counted as
complete. For an interrupted `running` run, recovery begins at the first
uncommitted ordinal. For a `complete` run there is no remaining work.

A successful local audit means **internal consistency only**. This read-only
preflight by itself is advisory; another cooperative writer might write
between an independent plan call and a subsequent execution.

## End-to-end offline recovery

`recover_synthetic_batch(db_path,lock_dir,pages,prior_run_id=…,now=…)`
holds the **same global POSIX parent flock for the full preflight and all
remaining synthetic HTTP + SQLite page operations**, preventing a
cooperating writer from changing the database between the checks and
commits. The internal lease is carried in a `ContextVar` active only
while that OS lock is held: setting `_parent_lock_held=True` alone is
rejected, and reuse with another lock directory is rejected.

The child routines reuse the actual held lease instead of reacquiring the
nonrecursive flock. Source requests use frozen in-memory responses and
writes use the same strict fixture-only paths, atomic page transactions,
PRECOMMIT audits and v3 run→receipt ledger as ordinary synthetic ingestion.

The previous blocked/running run is retained unchanged. A successful
recovery **creates a new run ID**, whose pages start with the first missing
(or failed) original ordinal. A completed original run returns
`RESUME_ALREADY_COMPLETE` with **zero new requests or receipts**.
If lock contention is detected, returns `BLOCKED_WRITER_BUSY` before
opening SQLite or executing mock HTTP.

## Failure model and limits

| Prior run status | Expected planner result | Execution behavior |
|---|---|---|
| `complete` | No remaining pages | No-op; no new run or request |
| `blocked` by 429/503/timeout | Retry failed last original ordinal | New run, preserve old failed receipt |
| `running` after process crash between pages | Resume first absent ordinal | Old run remains visibly interrupted |
| Changed source, time window or RAW for successful prefix | `BLOCKED_RECOVERY_*_DRIFT` | No new request / SQL |
| Corrupt receipt/metadata/PK/FK/schema | `BLOCKED_RECOVERY_DB_INTEGRITY` | No new request / SQL |
| Same logical lock already held | `BLOCKED_WRITER_BUSY` | No new request / SQL |

A failed attempt can have a **different new response** than its prior
429/5xx/timeout (expected); only its original request source and time
window must remain identical. The 32-page budget remains enforced. The
tool does not assert upstream retention, completeness, entitlement, a
production schedule or synchronization of noncooperating host writers.

## Verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.unit.test_package_a_frozen_legacy_transport
PYTHONDONTWRITEBYTECODE=1 python3 tests/run_all.py
```

The default runner explicitly **skips one legacy integration test** that
imports the production DB. Do not invoke that test directly. All other
regression cases use temporary/disposable synthetic SQLite and fake HTTP.

**Next required external work** (separate owner authorization): inspect real
writer process inventory and canonical lock inode, verify each provider's
historical availability/entitlement/quotas, plan WAL-safe production backup
and permitted migration, then authorize a bounded live pilot. Nothing in
this runbook grants that permission. Hermes QA remains STOP until the full
offline Package A gate is ready and pinned to one exact SHA.
