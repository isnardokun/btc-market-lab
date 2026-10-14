# Installed-schema compatibility for A/B/C

Status: authorized offline engineering, owner approval 2026-10-12 21:45
America/Bogota; Issue #60 #6137030693 proposal and #6137088170 authorization.
Base: 1a224b0db5d71200efe379aad58accd2e1d99e49.

## Outcome and constraints

Read a synthetic representative of the installed 34-table schema, preserve
legacy data and provenance limitations, acquire new source pages with explicit
simulated request evidence, and produce a reconciled private Research Studio
report through a recoverable pipeline. Existing A/B/C fixture contracts stay
valid. No network client, production default or productive permission exists.
One integrated PR, followed by one frozen-SHA Hermes order; Hermes STOP now.

The profile comes from #6136919779 and backup SHA
643f256c3907d4ea081907f5cf7bdfcd3cf5d2420ccdb50302c8471b0cd143bc.
It covers columns/types/nullability/default presence, PK/FK, indexes and object
names/types, plus migration versions. It is NOT complete SQL DDL: default
expressions, CHECK clauses and trigger bodies were not exported. Counts/ranges
are snapshot evidence, not fixture expectations or provider availability.

## Architecture

1. `storage.installed_schema`: exact observed structural profile, drift gate and
   streaming legacy content/schema fingerprints. Additive `compat_*` tables
   are the only permitted additions. Existing 34 tables and schema SQL remain
   unchanged. Fixtures have synthetic rows, not exported production payloads.
2. `analysis.installed_evidence`: one query-only read transaction validates
   structure and RAW/request evidence, reads explicit typed legacy selections
   and additive acquisitions, and emits B's immutable ReportSnapshot. No
   implicit series-ID/unit mapping. Unsupported selections fail closed.
3. `storage.compatibility_ledger` and `ingestion.compatibility_acquisition`:
   additive request/RAW/observation/cursor/run tables, fake transport only,
   durable attempt reservation before transport, atomic page commit and cursor,
   bounded budgets, crash recovery, and deterministic request identity.
4. `scripts.installed_schema_pipeline`: PLAN_ONLY without any path access by
   default; apply requires the existing owned, marked TemporaryDirectory guard
   plus C inode attestation. Reuse C's seven stages, atomic bounded artifacts
   and parent global flock. Maintain a separate installed-schema run state.
   Never run daily.sh or the legacy scheduler.

## Evidence rules

EvidencePoint gains explicit provenance/source-table/availability fields with
defaults preserving existing constructors. Legacy receipt/request/RAW hashes
are absent when absent; a row checksum is not request provenance. Native
market RAW is checked against bytes; legacy request joins are only credited
when uniquely matching request, acquisition, provider, endpoint and timestamp.
Missing acquisition remains an explicit limited-evidence status. Funding P1
and R10-D.2 reservations remain.

Legacy values lacking a recorded acquisition time are usable only at/after an
explicit snapshot capture upper bound, never for an earlier as-of. Recorded
late fetches are excluded. FRED chooses only vintages known by as-of; records
without vintage metadata remain marked unknown. Units/cadences come from
explicit SeriesSpec selections and are checked against any stored unit.
No local range, declared tier or checksum proves entitlement/completeness.

New requests distinguish FAKE_HTTP/OFFLINE_PROVIDER_CONTRACT from
HTTP/REAL_ACQUISITION at the storage-contract level; runtime accepts only the
former. Successful source-native parsing uses the existing A strict contracts
and legacy parser reconciliation. Thus provider-live schema remains unverified.
One Yahoo response supplies five streams; retries have distinct attempt IDs.
Same bytes across providers never merge request provenance.

Receipt replay must match request metadata, RAW and parsed observations.
Reserved uncertain attempts consume budget after a crash and are not reused.
Empty/failed/rejected pages never advance cursors. No silent revision: versions
of additive observations retain their own receipts and as-of capture time.
Forward and backward namespaces stay separate; this route supports forward.

## Analytics and report behavior

Modes OFFLINE_SYNTHETIC and OFFLINE_INSTALLED_SCHEMA are visibly distinct,
both use SYNTHETIC dataset origin and production authorization false.
price_btc maps only to BTC close (explicit source/unit declaration); no invented
OHLCV. A usable close may show a chart and close-only indicators in the
installed profile, while OHLCV-dependent ATR/scenarios remain NO_DATA and
critical report gates BLOCKED. Correct old SQL helper price/close contract
without fabricating bars. Unknown or mixed units block the selected stream.

Renderer uses the same immutable B model and dynamic provenance labels, keeps
synthetic fixture labeling, explains missing receipts/vintages, and never
promotes local reconciliation to publication permission. The final gate
recomputes the model and exact HTML, checks critical fields and fingerprints,
and rejects provenance mutations and artifact tampering. Rendering a blocked
private diagnostic is successful pipeline work, not a publication PASS.

## Preservation, migration and rollback

Profile validation occurs before SQL mutation/transport and before commit.
Migration is an explicit transaction, creates only the known addon tables and
indexes, validates their exact contract/version, and compares all legacy
schema/content fingerprints before committing. Reject partial/foreign addon
schemas. Rehearsal uses SQLite backup on a marked fixture into a new private
directory, verifies backup/restore integrity and fingerprints, and never
restores the source in place. Existing backups are preserved. Test a failure
after DDL and after observation insert to establish rollback and no cursor
movement. Production restore and migration are outside authorization.

## Acceptance

Structural drift, mixed units, missing RAW, wrong acquisitions, lookahead,
close-only price, future FRED revision, idempotent replay, failed/empty pages,
429/503/timeout, budget/crash recovery, lock contender with no SQL/transport,
inode/path drift and private artifact tampering are tested against actual
temporary SQLite. All 34 legacy tables/PK/values remain intact. A/B/C regression
runner and current-SHA CI pass; one independent whole-branch review and one
final pinned Hermes QA order. Production/external acceptance stays pending.

## Deferred operational gates

Host Git reconciliation; effective environment; all writers sharing the physical
lock; complete DDL attestation; real source contract/entitlement/quota checks;
separate pilot with exact source/endpoint/range/budget; shadow report; scheduler
cutover and rollback. None is inferred from this offline acceptance.
