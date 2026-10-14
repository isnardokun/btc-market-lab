# Production binding operation — explicit gates

G0 authorized offline engineering only, #60/#6159129314. This runbook is a
reviewable procedure, not an instruction to execute live operations. The
current deployment, all historical worktrees and installed DB remain intact.

G1 requires final frozen-SHA CI, independent Hermes QA, no P0/P1 and matching
merge TREE. It means PRODUCTION_BINDING_OFFLINE_ACCEPTED, not live acceptance.
G2 separately approves the exact private pilot; G3 records its real observations.
G4 separately approves installed DB/writer control/backup/migration/deployment.

## Default and operator intake

`python3 -m scripts.installed_live_pipeline --plan` prints PLAN_ONLY_NO_IO.
It imports pure contracts, does not read DB, environment keys or call HTTP.
No args means the same. No scheduler, Telegram or upload hooks exist here.

An approved runtime uses a clean checkout at the owner's accepted code SHA.
Plan JSON is the dataclass LivePlan: run_id, requests (typed native definitions),
start_utc, end_exclusive_utc, asof_utc, snapshot_captured_utc, selections=[] for
CLI. The canonical SHA of all fields, including request params/contract/DP,
is the grant's plan_sha. Typed LegacySelection is available only to an explicit
Python operator; arbitrary SQL/table mappings are never accepted by CLI.

Grant JSON has every RuntimeGrant field in installed_live_policy.py, including
exact approved source/window/request SHA, UID, root/db/lock and physical
identities, code_sha, plan_sha, allowed_operations, issue #60 approval URL,
issued/expires UTC and bounded budgets. Its artifact excludes the self SHA;
approval_artifact_sha is that canonical artifact SHA. The operator separately
passes the SHA explicitly approved by the owner, rather than trusting the file.
The code validates the pin/reference, not the GitHub author's identity.

All grant/plan/receipts are private owned files, no symlinks/hardlinks or group/
world write. Never put keys, real RAW or these operational artifacts in Git.
Keys are resolved only inside approved real HTTP: FRED_API_KEY query secret,
RESEARCHBITCOIN_API_TOKEN header. No secret value is included in request identity,
logs, stored HTTP metadata or child error text. Fixture transport is always
SIMULATED_HTTP; actual HTTP custody cannot use an injected opener/frozen client.

After G2/G4 only, the reviewed invocation is:

```
python3 -m scripts.installed_live_pipeline --apply \
  --plan-file /EXPLICIT_PRIVATE/plan.json \
  --grant-file /EXPLICIT_PRIVATE/grant.json \
  --approved-artifact-sha OWNER_APPROVED_64_HEX_SHA
```

Placeholders are deliberately non-executable until individually pinned.
Default DB_PATH or inherited BTC_RESEARCH_HOME never chooses the target.

## Private pilot (G2 pending)

See PRODUCTION_PRIVATE_PILOT_PROPOSAL.md for the exact bounded Binance funding
proposal. No keys required. The owner must still approve code SHA, plan/artifact
SHA, expiry, source/endpoints/window/budget and NEW isolated target.
Use target_kind LIVE_PRIVATE_PILOT, a verified owned creation parent identity,
empty root/db/lock identities for the newly created target, all four operations
for initial namespace creation. The process creates 0700 root/lock, 0600 DB
and a fsynced receipt binding newly assigned physical identities to the pin.
An existing unmarked directory/database is never reused. One source produces
PRIVATE_DIAGNOSTIC_INCOMPLETE; that is an honest limited pilot, no full report PASS.

No installed DB, backup, legacy data, flags, cron, news or publication is part
of that private pilot. Inspect real custody/RAW/rows/budgets/window observations
under the same grant and lease, record a redacted report once, then STOP.
Unknown entitlement/history/availability or rejected native shape stays blocked.

## Installed target (G4 pending)

Never run merely because fixture tests pass. Explicit INSTALLED_DB grants require
owned receipt files at the target root with SHA256 pins approved independently:
.live-schema-receipt.json, .live-writer-window.json, .live-backup-receipt.json.
Every receipt contains code_sha, db_identity [dev,inode] and expires_utc.
Schema receipt additionally has legacy_schema_sha from a COMPLETE full schema
fingerprint. No fixture metadata substitutes for actual DDL. Backup receipt must
be produced by separately authorized WAL-safe backup/integrity/restore evidence.
Current engineering does not manufacture any of these operational receipts.

Writer receipt additionally contains manifest and observations as consumed by
assess_writer_readiness. Manifest: namespace btc-research-global-writer-v1,
exact lock path, code_sha, window_start/window_end UTC, writers=[{id,sha256}].
Observations: observed_utc, inventory_complete=true, processes=[{id,sha256,
state:STOPPED_OBSERVED, namespace, lock, code_sha}]. Every known writer must be
present once; unknown/uncooperative writers block. Observed window must still be
current on every runtime path check. This records operator evidence; the code
cannot discover or authenticate all host writers from a boolean or fixture PASS.

With owner-approved maintenance scope, the operator must actually identify and
stop/control daily.py/daily.sh, Yahoo/Bitview/FRED/RBN/archive/migrators/report
library writers and any other host writers, verify cron/flags/environment and
physical lock namespace. Do not chmod daily.sh: its currently broken cron would
reactivate additional work. No automatic stop/restart/deploy command is embedded.
Any unknown process or changed source/receipt expires the readiness assessment.

Only explicit migrate_installed(session) may initialize the installed namespace:
BEGIN IMMEDIATE, COMPLETE bounded-stream legacy fingerprint, schema receipt
match, exact eight live tables, full legacy after-fingerprint, precommit checks.
Failure/cancellation rolls back without partial PASS. Existing installed ingestion
refuses an uninitialized/drifted live namespace. Do not reconstruct legacy
Funding P1 receipts. Preserve every row, PK/type/BLOB/RAW and actual SQL object.

## Recovery, ceilings and evidence

One actual parent flock guards all stages; MAIN FD and paths/UID/dev/inode are
attested on connection and before commit. This is advisory exclusion, not
protection against an uncontrolled writer. Real HTTP runs in a Linux child with
hard total deadline and SIGKILL-on-parent-death, bounded body/IPC and no SQL write
transaction. 403/429 opens provider circuit; unknown timeout consumes a durable
reserve. Grant budgets are shared across run IDs, <=128 total attempts, <=3 per
request, <=4.000.000 RAW bytes, <=15s socket and <=900s run wall ceiling.
Reservations committed before HTTP cannot be refunded by deletion/restart.

Successful/empty custody replays without refetch. Seven stages recompute model,
charts, HTML, archive and gate from a read snapshot; immutable artifacts and
hash-bound checkpoint are checked on resume. Never silently repair corruption.
Expiry, source drift, replaced DB/lock, deleted receipts/cursors, changed grant/
plan/RAW/output => BLOCKED. Preserve run directory/consumed reserves for review.

Full legacy preservation has no 100.000-row ceiling; metadata batches <=2.048
rows/8MiB and BLOB/TEXT chunks <=64KiB. WITHOUT ROWID values lacking a bounded
stream route block rather than claim partial success. Report reading filters
series/time/vintage in SQL before bounded batches; explicit 100.000 selected
point report budget blocks rather than truncates. It is not a corpus scan cap.

Legacy acquired_utc and attempted/ended/capture UTC are assessed before even
UNRECONCILED request links. Receipt absence/unknown vintage stays explicit.
Native units remain source-specific. Local ranges/empty/freshness are not
provider history, entitlement or session-calendar proof. Every output remains
private and production_publication_authorized=false.
