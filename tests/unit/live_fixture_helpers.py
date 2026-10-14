"""Only test-owned installed fixtures and explicitly simulated authority."""
from dataclasses import replace
from tests.unit.installed_fixture_helpers import new_fixture, ASOF

CODE_SHA='a'*40
PLAN_SHA='b'*64


def grant_for(root,path,locks,*,requests=(),max_requests=16,max_dp=10000):
    from scripts.installed_live_policy import RuntimeGrant, SourceWindow
    windows=tuple(SourceWindow(r.specs[0].provider,r.native_origin,r.path,r.start,r.end,
        r.sha256,r.max_dp) for r in requests)
    identity=lambda p:(p.stat().st_dev,p.stat().st_ino)
    g=RuntimeGrant(code_sha=CODE_SHA,plan_sha=PLAN_SHA,approval_reference='fixture-test:owner',
        target_kind='FIXTURE_TEST',root=str(root),db=str(path),lock=str(locks),
        root_identity=identity(root),db_identity=identity(path),lock_identity=identity(locks),
        owner_uid=root.stat().st_uid,allowed_operations=('read','migrate','ingest','report_private'),
        source_windows=windows,issued_utc='2026-10-12T00:00:00Z',expires_utc='2026-10-12T00:20:00Z',
        max_requests=max_requests,max_attempts=3,max_raw_bytes=4000000,max_dp=max_dp,
        socket_timeout=15,deadline_seconds=900,approval_artifact_sha='')
    return replace(g,approval_artifact_sha=g.artifact_sha)


def capability(grant):
    from scripts.installed_live_policy import validate_runtime_grant
    return validate_runtime_grant(grant,code_sha=CODE_SHA,plan_sha=PLAN_SHA,
        now=ASOF,operation='read',approved_artifact_sha=grant.artifact_sha)
