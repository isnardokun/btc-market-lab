"""Native seven-stage private pipeline. PLAN_ONLY is pure and is the default."""
from dataclasses import asdict,dataclass
import json
import re
from ingestion.installed_live_sources import LiveRequest,request_from_dict
from ingestion.package_a_contracts import utc
from scripts.installed_live_policy import digest

STAGES=('ingest','validate','analytics','charts','report','archive','gate')
FILES=('audit.json','evidence.json','charts.json','report.html','archive.json','gate.json')
MAX_ARTIFACT=16000000

class PipelineCrash(BaseException):pass

@dataclass(frozen=True)
class LivePlan:
    run_id:str
    requests:tuple
    start_utc:str
    end_exclusive_utc:str
    asof_utc:str
    snapshot_captured_utc:str
    selections:tuple=()
    def __post_init__(self):
        if type(self.run_id) is not str or not re.fullmatch('[a-zA-Z0-9_-]{1,64}',self.run_id):raise ValueError('BOUNDED_PLAN_RUN_ID_REQUIRED')
        if type(self.requests) is not tuple or len(self.requests)>128 or any(type(r) is not LiveRequest for r in self.requests):raise ValueError('TYPED_PLAN_REQUESTS_REQUIRED')
        if len({r.sha256 for r in self.requests})!=len(self.requests):raise ValueError('DUPLICATE_PLAN_REQUEST')
        cutoff=utc(self.asof_utc).replace(hour=0,minute=0,second=0,microsecond=0)
        if not utc(self.start_utc)<utc(self.end_exclusive_utc)<=cutoff:raise ValueError('COMPLETED_PLAN_WINDOW_REQUIRED')
        utc(self.snapshot_captured_utc)
        if type(self.selections) is not tuple:raise ValueError('TYPED_PLAN_SELECTIONS_REQUIRED')
    @property
    def sha256(self):return digest(asdict(self))


def plan_live_run(plan):
    if type(plan) is not LivePlan:raise ValueError('TYPED_LIVE_PLAN_REQUIRED')
    return {'state':'PLAN_ONLY_NO_IO','plan_sha256':plan.sha256,'requests':len(plan.requests),'stages':list(STAGES),'runtime_apply_authorized':False,'production_publication_authorized':False}


def _atomic(session,path,data):
    import os
    import tempfile
    from scripts.installed_live_guard import owned
    session.verify('report_private');uid=session.capability.grant.owner_uid
    owned(path.parent,uid,directory=True)
    if type(data) is not bytes or len(data)>MAX_ARTIFACT:raise ValueError('LIVE_ARTIFACT_BYTE_BUDGET')
    if path.exists() or path.is_symlink():
        owned(path,uid)
        # No silent repair, including output committed before its checkpoint.
        if _read(session,path)!=data:raise ValueError('LIVE_EXISTING_ARTIFACT_DRIFT')
        return
    fd,name=tempfile.mkstemp(prefix='.live-write-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        session.verify('report_private');os.replace(name,path)
        d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if os.path.exists(name):os.unlink(name)


def _read(session,path):
    from scripts.installed_live_guard import _read_owned
    session.verify('read');return _read_owned(path,session.capability.grant.owner_uid,MAX_ARTIFACT)


def _encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def _checkpoint(session,directory,body):
    # Checkpoints alone are mutable; outputs and immutable manifest never are.
    import os
    import tempfile
    from scripts.installed_live_guard import owned
    session.verify('report_private');path=directory/'checkpoint.json'
    if path.exists() or path.is_symlink():owned(path,session.capability.grant.owner_uid)
    data=_encode({'body':body,'sha256':digest(body)})
    if len(data)>1000000:raise ValueError('LIVE_CHECKPOINT_BUDGET')
    fd,name=tempfile.mkstemp(prefix='.live-state-',dir=directory)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        session.verify('report_private');os.replace(name,path)
        d=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if os.path.exists(name):os.unlink(name)


def _initialize(session,plan):
    from scripts.installed_live_guard import owned
    from storage.installed_live_ledger import initialize_live,audit_live,assert_live_schema
    directory=session.root/('live-run-'+plan.run_id)
    manifest={'version':1,'plan_sha':plan.sha256,'grant_sha':session.capability.grant.artifact_sha,'code_sha':session.capability.grant.code_sha,'identities':session.identities}
    new=not directory.exists()
    if new:directory.mkdir(mode=0o700)
    owned(directory,session.capability.grant.owner_uid,directory=True)
    if not new and not (directory/'manifest.json').exists():raise ValueError('LIVE_RUN_MANIFEST_MISSING')
    _atomic(session,directory/'manifest.json',_encode(manifest))
    with session.connect(readonly=False,operation='ingest') as (db,check):
        if session.target_kind=='INSTALLED_DB':assert_live_schema(db)
        elif not db.execute("SELECT 1 FROM sqlite_master WHERE name='live_schema_migrations'").fetchone():
            db.execute('BEGIN IMMEDIATE')
            try:initialize_live(db,session=session,now=plan.asof_utc);check();db.commit()
            except BaseException:db.rollback();raise
        audit_live(db,session=session)
    path=directory/'checkpoint.json'
    if not path.exists():
        if not new and any((directory/name).exists() for name in FILES):raise ValueError('LIVE_CHECKPOINT_MISSING')
        body={'completed':[],'outputs':{},'manifest_sha':digest(manifest)};_checkpoint(session,directory,body)
    else:
        value=json.loads(_read(session,path));body=value.get('body')
        if type(body) is not dict or value.get('sha256')!=digest(body):raise ValueError('LIVE_CHECKPOINT_CHECKSUM_DRIFT')
    if set(body)!={'completed','outputs','manifest_sha'} or body['manifest_sha']!=digest(manifest) or type(body['completed']) is not list or body['completed']!=list(STAGES[:len(body['completed'])]) or type(body['outputs']) is not dict:
        raise ValueError('LIVE_CHECKPOINT_PLAN_DRIFT')
    expected=FILES[:max(0,len(body['completed'])-1)]
    if set(body['outputs'])!=set(expected):raise ValueError('LIVE_CHECKPOINT_OUTPUT_SET_DRIFT')
    import hashlib
    for name,sha in body['outputs'].items():
        if hashlib.sha256(_read(session,directory/name)).hexdigest()!=sha:raise ValueError('LIVE_CHECKPOINT_ARTIFACT_DRIFT')
    return directory,body


def _model(session,plan):
    from analysis.installed_live_evidence import read_live_snapshot
    from analysis.package_b_analytics import analyze_snapshot
    return analyze_snapshot(read_live_snapshot(session,asof=plan.asof_utc,snapshot_captured_utc=plan.snapshot_captured_utc,selections=plan.selections,start_utc=plan.start_utc,end_exclusive_utc=plan.end_exclusive_utc))


def _stage_output(session,plan,stage,directory):
    import hashlib
    from storage.installed_live_ledger import audit_live
    from rendering.package_b_report import render_report,audit_candidate_report
    if stage=='validate':
        with session.connect(readonly=True) as (db,check):result=audit_live(db,session=session);check()
        return 'audit.json',_encode(result)
    model=_model(session,plan);document=render_report(model)
    charts=re.findall(r'<svg\b.*?</svg>',document,re.DOTALL)
    bundle={'snapshot_sha':model.snapshot.sha256,'charts':[{'svg':svg,'sha256':hashlib.sha256(svg.encode()).hexdigest()} for svg in charts]}
    if stage=='analytics':return 'evidence.json',_encode(asdict(model))
    if stage=='charts':return 'charts.json',_encode(bundle)
    if stage=='report':return 'report.html',document.encode()
    if stage in ('archive','gate'):
        expected={'evidence.json':_encode(asdict(model)),'charts.json':_encode(bundle),'report.html':document.encode()}
        for name,data in expected.items():
            if _read(session,directory/name)!=data:raise ValueError('LIVE_RECOMPUTED_EVIDENCE_DRIFT')
        files={name:hashlib.sha256(_read(session,directory/name)).hexdigest() for name in FILES[:4]}
        archive={'version':1,'files':files,'snapshot_sha':model.snapshot.sha256,'production_publication_authorized':False}
        if stage=='archive':return 'archive.json',_encode(archive)
        if _read(session,directory/'archive.json')!=_encode(archive):raise ValueError('LIVE_ARCHIVE_DRIFT')
        gate=audit_candidate_report(model,document)
        providers={s.provider for s in model.snapshot.series}
        full=(not gate['failures'] and providers=={'yahoo','fred','bitview','researchbitcoin','binance','bybit'} and len(model.snapshot.series)==57 and len(charts)==3 and not gate['stale_source_series'])
        gate={**gate,'state':'OFFLINE_LIVE_BINDING_LOCAL_PASS' if full and session.target_kind=='FIXTURE_TEST' else 'PRIVATE_LIVE_LOCAL_RECONCILIATION' if full else 'PRIVATE_DIAGNOSTIC_INCOMPLETE','full_report_ready':full,'score':gate['score_local'] if full else 0,'series_count':len(model.snapshot.series),'historical_completeness':'NOT_VERIFIED','production_publication_authorized':False}
        return 'gate.json',_encode(gate)
    raise ValueError('UNKNOWN_LIVE_STAGE')


def run_live_pipeline(plan,*,capability=None,apply=False,transport=None,fault=None):
    if type(apply) is not bool:raise ValueError('EXPLICIT_APPLY_REQUIRED')
    if not apply:return plan_live_run(plan)
    if type(plan) is not LivePlan:raise ValueError('TYPED_LIVE_PLAN_REQUIRED')
    from scripts.installed_live_policy import ValidatedGrant
    if type(capability) is not ValidatedGrant:raise ValueError('RUNTIME_CAPABILITY_REQUIRED')
    capability.verify('read');capability.verify('ingest');capability.verify('report_private')
    if capability.grant.plan_sha!=plan.sha256:raise ValueError('CODE_OR_PLAN_SHA_DRIFT')
    if {w.request_sha for w in capability.grant.source_windows}!={r.sha256 for r in plan.requests}:raise ValueError('PLAN_APPROVED_WINDOWS_DRIFT')
    if capability.grant.target_kind!='FIXTURE_TEST' and fault is not None:raise ValueError('FIXTURE_FAULT_ONLY')
    allowed={None,'ingest:after_commit'}|{prefix+name for name in STAGES for prefix in ('before:','after-output:','after:')}
    if fault not in allowed:raise ValueError('UNKNOWN_STAGE_FAULT')
    from scripts.installed_live_guard import hold_runtime_session
    from ingestion.installed_live_transport import BoundedHttpTransport,FrozenLiveTransport
    from ingestion.installed_live_acquisition import acquire_live_page
    if transport is None:transport=BoundedHttpTransport()
    if type(transport) not in (BoundedHttpTransport,FrozenLiveTransport):raise ValueError('TYPED_CUSTODY_TRANSPORT_REQUIRED')
    if capability.grant.target_kind!='FIXTURE_TEST' and (type(transport) is FrozenLiveTransport or transport._opener is not None):raise ValueError('SIMULATED_TRANSPORT_NOT_REAL_CUSTODY')
    import hashlib
    with hold_runtime_session(capability) as session:
        directory,body=_initialize(session,plan)
        for stage in STAGES:
            session.verify('report_private')
            if fault=='before:'+stage:raise PipelineCrash(fault)
            if stage=='ingest':
                # Always reconstruct/replay, including completed runs. Any
                # deleted custody/budget blocks before a new request starts.
                for request in plan.requests:
                    result=None
                    for _ in range(capability.grant.max_attempts):
                        now=capability.verify('ingest').isoformat().replace('+00:00','Z')
                        result=acquire_live_page(session,request,transport,run_id=plan.run_id,now=now,fault='after_commit' if fault=='ingest:after_commit' else None)
                        if result.status in ('ok','empty','rejected'):break
                    if result.status not in ('ok','empty'):raise ValueError('LIVE_SOURCE_ACQUISITION_BLOCKED')
            else:
                name,data=_stage_output(session,plan,stage,directory)
                _atomic(session,directory/name,data)
                sha=hashlib.sha256(data).hexdigest()
                if stage in body['completed'] and body['outputs'].get(name)!=sha:raise ValueError('LIVE_RECOMPUTED_CHECKPOINT_DRIFT')
                body['outputs'][name]=sha
            if fault=='after-output:'+stage:raise PipelineCrash(fault)
            if stage not in body['completed']:
                body['completed'].append(stage);_checkpoint(session,directory,body)
            if fault=='after:'+stage:raise PipelineCrash(fault)
        gate=json.loads(_read(session,directory/'gate.json'))
        return {'state':'PRIVATE_PIPELINE_COMPLETE','completed':body['completed'],'gate':gate,'plan_sha256':plan.sha256,'production_publication_authorized':False}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='store_true');parser.add_argument('--apply',action='store_true')
    parser.add_argument('--plan-file');parser.add_argument('--grant-file');parser.add_argument('--approved-artifact-sha')
    args=parser.parse_args(argv)
    if not args.apply:
        print(json.dumps({'state':'PLAN_ONLY_NO_IO','stages':list(STAGES),'runtime_apply_authorized':False}));return 0
    if not all((args.plan_file,args.grant_file,args.approved_artifact_sha)):parser.error('apply requires exact plan, grant and independent owner approval SHA')
    from pathlib import Path
    import subprocess
    import datetime as dt
    from scripts.installed_live_guard import _read_owned
    from scripts.installed_live_policy import grant_from_dict,validate_runtime_grant
    import os
    repo=Path(__file__).absolute().parents[1]
    code=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=repo,text=True).strip():raise ValueError('CLEAN_PINNED_CODE_REQUIRED')
    raw=json.loads(_read_owned(Path(args.plan_file),os.getuid(),1000000))
    if raw.get('selections'):raise ValueError('CLI_LEGACY_SELECTIONS_REQUIRE_EXPLICIT_TYPED_OPERATOR')
    raw['requests']=tuple(request_from_dict(r) for r in raw['requests']);raw['selections']=()
    plan=LivePlan(**raw);grant=grant_from_dict(json.loads(_read_owned(Path(args.grant_file),os.getuid(),1000000)))
    cap=validate_runtime_grant(grant,code_sha=code,plan_sha=plan.sha256,now=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),operation='read',approved_artifact_sha=args.approved_artifact_sha)
    print(json.dumps(run_live_pipeline(plan,capability=cap,apply=True)));return 0

if __name__=='__main__':raise SystemExit(main())
