# Installed compatibility — offline runbook and productive limits

Owner-authorized one integrated compatibility PR; proposal #6137030693 and
authorization #6137088170 on Issue #60. ChatGPT engineers; Hermes only final
pinned QA. Source metadata comes from snapshot #6136919779, backed up in
preflight-20261012T212432Z-ba310f6f, SHA256
643f256c3907d4ea081907f5cf7bdfcd3cf5d2420ccdb50302c8471b0cd143bc.

## What the operator can verify offline

`python3 scripts/installed_schema_pipeline.py` returns PLAN_ONLY_NO_IO without
reading any DB, root path or provider. It exposes no apply/live CLI.

`python3 -m unittest -v tests.unit.test_installed_schema
tests.unit.test_installed_evidence tests.unit.test_compatibility_acquisition
tests.unit.test_installed_pipeline` (one shell command) creates only marked
owned TemporaryDirectory SQLite fixtures. The complete runner registers these
four suites and always skips the existing live integration suite.

`python3 tests/fixtures/installed_report_example.py --output-dir NEW_PRIVATE_DIR`
exports a synthetic private HTML and gate for local visual QA. This test helper
uses a partial structural stand-in for the 34 installed tables, synthetic rows
and existing six-provider fake payloads: 45 requests, 57 streams. It is never
production schema installation or evidence of provider-live response formats.
Run the existing file-only `scripts/package_b_visual_qa.cjs` against its HTML
and gate for 320/390/768/1440 and A4 checks; no market requests or upload.

## State, stages and recovery

Preparation pins C root/DB inode attestation, explicit mappings/capture/as-of,
request/response manifests, limits and legacy schema/content fingerprints.
Seven stages: ingest, validate, analytics, charts, report, archive, gate.
Parent OS flock uses btc-research-global-writer-v1; descendants verify its
live pathname before SQL/transport/commit and bounded atomic artifacts.

The SQLite reservation is durable before FAKE_HTTP and outside the write
transaction. A crash at reservation leaves an unknown attempt that consumes
budget. Receipt/RAW/observations/cursors commit atomically. Resume reuses an
already successful matching request without transport, even if the JSON stage
checkpoint was never saved. No empty/failed/rejected page advances a cursor.
OI uses A's exact five-minute continuity contract; RBN/Bitview/BTC daily gaps
are rejected. Equity/FRED/funding calendars are not invented.

Repeated 429/503/timeouts have bounded attempts/provider/global budgets and
virtual retry advisories only, with no real wait or requests. Permanent source
contract rejection stops. Budget exhaustion persists BLOCKED while the original
guard/lease is valid. Guard/state/identity failures are returned BLOCKED and
never overwritten through an unsafe path. Fixes require a new frozen QA SHA.

## Evidence and report acceptance

Legacy tables are read query-only in one SQLite snapshot. Explicit typed
selections declare source/metric/symbol/cadence/unit; mismatches fail closed.
Unknown legacy capture times use an explicit snapshot upper bound and cannot
support an earlier as-of. Offset timestamps are normalized without replacing
future capture evidence. FRED vintages exclude later releases and captures;
legacy latest-only FRED is marked VINTAGE_UNKNOWN.

Absent receipts/RAW/request hashes remain absent. Native market RAW bytes are
hash-checked; matching request links remain UNRECONCILED until row/request
reconstruction, and missing acquisitions are labeled. Funding P1 (1,035 rows)
and R10-D.2 limitations are preserved. Additive fake acquisitions are labeled
SIMULATED_REQUEST_VERIFIED and FAKE_HTTP, never FROZEN_OFFLINE or real.
Storage distinguishes future HTTP/REAL_ACQUISITION; runtime/audit reject it.
Schema names/types/default presence do not certify full CHECK/default/trigger
DDL. Actual SQL is fingerprinted for preservation, not reconstructed.

price_btc contains closes, not OHLCV. A legacy close chart and close-only
indicators may be shown; ATR/scenarios that need bars remain NO_DATA. Missing
critical inputs block the five-category >=95 gate. COMPLETE_PRIVATE_DIAGNOSTIC
means a useful private report with a BLOCKED data gate, not report acceptance.
COMPLETE_OFFLINE_COMPATIBILITY requires recomputed report/gate, immutable
evidence and artifacts, exact plan/policy/receipts and unchanged legacy rows.
Both states always carry production_publication_authorized=false and
external_history_verified=false. Completed runs are revalidated on resume.

## Migration and rollback rehearsal

Known compat_* tables only; exact additive DDL/version checked. Installation
uses an explicit transaction and compares legacy row/schema fingerprints;
partial/drifted addons fail rather than being silently repaired. Fingerprints
are bounded to 100,000 rows/table for offline fixtures: these routines are NOT
a production million-row migration tool.

The rehearsal also requires the dedicated migration fixture marker and original
C inode. A single SQLite online backup goes into a new private folder with
retain=1, preserving all existing backup directories. Restore is to a new
private fixture file, never over the source. Integrity and all legacy table
fingerprints must match. WAL-only rows are explicitly tested. Injected DDL or
precommit failure rolls back all additions and retains the private backup.

## Productive blockers and future permissions

Do not chmod or run daily.sh: it independently calls Yahoo/Bitview/FRED and may
fetch news/publish. Optional flags OFF do not disable the base API jobs.
Operational checkout SHA/dirty changes, effective environment, full DDL and
writer inventory/exclusion still need reconciled evidence before a cutover.

No productive migration, restoration, new source DB read, credential handling,
live HTTP, provider quota spending, entitlement/history assertion, flag,
cron/systemd, deployment or publication is authorized by this PR. A future
pilot requires source/endpoint/range/request/cost limits, DB identity, fresh
backup/rollback and uncooperative-writer control specified for that operation.
Neither the existing backup nor offline QA grants those permissions.
