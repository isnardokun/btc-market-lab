# SDD ledger — plan: docs/superpowers/plans/2026-10-14-production-integration.md

Spec: docs/superpowers/specs/2026-10-14-production-integration-design.md
Base: ab576a8c179edf97a379976a0c9b8aefed398b6e; proposal 3a2f341.
Owner G0 2026-10-13 20:52 America/Bogota; #60/#6159129314. Native execution,
one integrated PR, no live/installed DB/cron/publication. All old worktrees kept.

Pre-flight: Task 1 ValidatedGrant/RuntimeSession -> Tasks 2–4; Task 2 typed
request/result/parsers -> Task 3 reservation/RAW/cursor -> Task 4 snapshot/stages.
Task 3 complete fingerprint -> migration/legacy reader guard. Task 4 pure PLAN
and six test modules -> Task 5 runner/CI. Interfaces aligned; spec is authority.

Ruling: cloud skill helper shell scripts are not local executable resources;
use this durable Git-backed ledger with task BASE/tests/rulings — equivalent
tracking survives compaction — cost if wrong: regenerate task briefs from plan.

Ruling: G0 accepts the written design/plan and native method; no repeated
approval between engineering tasks. Shared Git/PR operations already authorized.
G2/G4 operational approvals remain separate — cost if wrong: hold runtime blocked.

Ruling: validate_runtime_grant needs a separately supplied approved artifact
SHA pin; a self-hashed grant alone cannot authorize production. Add optional
approved_artifact_sha keyword defaulting to denial — cost if wrong: adjust the
operator intake contract before a pilot, preserving default fail-closed.

Baseline code tree equals accepted PR82 (CI and Hermes 82/82), proposal adds
only three docs. No duplicate baseline runner or dependency installation.
Task 1: in progress; tests first. Final complete runner on final code remains due.

Task 1: complete (BASE 3a2f341; 11 methods/18 initial missing-policy failures RED -> 11/11 GREEN, RC0). Pure grant pins and source windows, UID/path/physical MAIN under parent lease; readonly close, contender zeroSQL and released session blocked.
Ruling: new private targets bind newly created identities in an owned fsynced receipt linked to the approved artifact; no existing unmarked target is reused. Installed targets require separately approved schema/writer/backup receipt hashes. No receipt is emitted by this offline engineering — cost if wrong: harden operational intake before G2/G4.
