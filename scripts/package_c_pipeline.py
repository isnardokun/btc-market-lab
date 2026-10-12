"""Fixed OFFLINE DAG over actual A/B contracts; no live or publication handler."""
from collections import Counter
from contextlib import closing
from dataclasses import asdict,dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from scripts import package_b_report_fixture as source
from scripts import package_c_state as state
from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE,inspect_fixture
from scripts.package_a_parent_lock import hold_offline_parent_lease,verify_offline_parent_lease
from scripts.market_r10e11_writer_exclusion import WriterLockBusy
from storage import package_a_ledger as ledger
from analysis.package_b_evidence import read_report_snapshot
from analysis.package_b_analytics import analyze_snapshot
from rendering.package_b_report import render_report,audit_candidate_report
from validation.publication_gate import evaluate_package_b_fixture
from scripts.package_a_offline_retry_policy import OfflineRetryPolicy,plan_offline_retry_advice

STAGES=('ingest','validate','analytics','charts','report','archive','gate')
STAGE_OUTPUTS={'ingest':(), 'validate':('audit.json',), 'analytics':('evidence.json',),
    'charts':('charts.json',), 'report':('report.html',), 'archive':('archive.json',), 'gate':('gate.json',)}
VERSION='package-c-offline-v1'
DEFAULT_BUDGETS=(('yahoo',9),('researchbitcoin',39),('bitview',69),('fred',6),('binance',6),('bybit',6))
SAFE_CODES=frozenset('POLICY_BUDGET_INVALID TYPED_POLICY_REQUIRED PLAN_RESOURCE_BUDGET_INSUFFICIENT '
    'FAKE_FAILURE_MANIFEST_INVALID FAKE_FAILURE_STATUS_INVALID NEW_MARKED_FIXTURE_ROOT_REQUIRED '
    'STATE_SCHEMA_OR_PLAN_DRIFT STATE_JOB_COUNTER_INVALID ARTIFACT_CHECKPOINT_MISMATCH '
    'DATABASE_CHECKPOINT_MISMATCH PARTIAL_NATIVE_JOB_RECEIPTS MIXED_NATIVE_JOB_RECEIPTS '
    'RAW_INTEGRITY_BLOCKED UNRESERVED_SOURCE_RECEIPTS DUPLICATED_SUCCESSFUL_SOURCE_JOB '
    'SUCCESS_CHECKPOINT_WITHOUT_RECEIPT PERMANENT_SOURCE_FAILURE SOURCE_CIRCUIT_OPEN '
    'PERSISTENT_REQUEST_BUDGET_EXHAUSTED CRITICAL_CHART_INPUTS CRITICAL_DATA_BLOCKED '
    'CHART_EVIDENCE_MISMATCH UNKNOWN_PIPELINE_STAGE ARCHIVE_EVIDENCE_MISMATCH '
    'PUBLICATION_GATE_BLOCKED EXPLICIT_APPLY_BOOLEAN_REQUIRED FINAL_GATE_EVIDENCE_MISMATCH '
    'UNSAFE_FIXTURE_PATH OVERSIZE_OR_LINKED_FILE OVERSIZE_FILE UNMARKED_FIXTURE_ROOT '
    'FIXTURE_IDENTITY_DRIFT BOUNDED_ARTIFACT_REQUIRED STATE_SIZE_BUDGET STATE_CHECKSUM_MISMATCH '
    'UNKNOWN_ARTIFACT_NAME UNKNOWN_FAULT_PARAMETER STATE_EVENT_SCHEMA_INVALID'.split())
ERROR_CLASSES=frozenset('ValueError OSError TypeError KeyError RuntimeError FileNotFoundError '
    'PermissionError IsADirectoryError NotADirectoryError JSONDecodeError UnicodeDecodeError '
    'Error DatabaseError IntegrityError OperationalError ProgrammingError InterfaceError '
    'DataError InternalError NotSupportedError'.split())
FAILURE_CLASSES=frozenset('SYNTHETIC_TRANSPORT_TIMEOUT SYNTHETIC_HTTP_429 SYNTHETIC_HTTP_500 '
    'SYNTHETIC_HTTP_503 ValueError KeyError TypeError IndexError OverflowError AttributeError'.split())

def _reason(error):
    if isinstance(error,ValueError) and str(error) in SAFE_CODES:return str(error)
    return type(error).__name__ if type(error).__name__ in ERROR_CLASSES else 'RuntimeError'

@dataclass(frozen=True)
class OrchestrationPolicy:
    max_attempts:int=3
    max_fake_requests:int=128
    provider_budgets:tuple[tuple[str,int],...]=DEFAULT_BUDGETS

    def __post_init__(self):
        if (type(self.max_attempts) is not int or not 1<=self.max_attempts<=3 or
            type(self.max_fake_requests) is not int or not 1<=self.max_fake_requests<=128 or
            type(self.provider_budgets) is not tuple or
            any(type(pair) is not tuple or len(pair)!=2 or type(pair[1]) is not int or not 1<=pair[1]<=128 for pair in self.provider_budgets) or
            len(self.provider_budgets)!=6 or {p for p,_ in self.provider_budgets}!={p for p,_ in DEFAULT_BUDGETS}):
            raise ValueError('POLICY_BUDGET_INVALID')

class InjectedCrash(BaseException):pass

def _maybe_fault(fault,position):
    if fault==position:raise InjectedCrash(position)

def _plan(policy,http_failures):
    if not isinstance(policy,OrchestrationPolicy):raise ValueError('TYPED_POLICY_REQUIRED')
    jobs=source.example_jobs();counts=Counter(j.specs[0].provider for j in jobs)
    if len(jobs)>policy.max_fake_requests or any(counts[p]>dict(policy.provider_budgets)[p] for p in counts):
        raise ValueError('PLAN_RESOURCE_BUDGET_INSUFFICIENT')
    failures={} if http_failures is None else http_failures
    if type(failures) is not dict or any(key not in {j.key for j in jobs} for key in failures):
        raise ValueError('FAKE_FAILURE_MANIFEST_INVALID')
    normalized={}
    for key,values in failures.items():
        if (type(values) not in (tuple,list) or not 1<=len(values)<=policy.max_attempts or
            any(v is not None and (type(v) is not int or v not in (200,429,500,503)) for v in values)):
            raise ValueError('FAKE_FAILURE_STATUS_INVALID')
        normalized[key]=list(values)
    identity={'version':VERSION,'asof':source.ASOF,'cutoff':source.END,'policy':asdict(policy),
              'jobs':[{ 'key':j.key,'sha256':j.sha256} for j in jobs],'http_failures':normalized}
    return jobs,normalized,state.digest(state.encode(identity))

def plan_fixture_pipeline(*,policy=OrchestrationPolicy(),http_failures=None):
    jobs,_,sha=_plan(policy,http_failures)
    return {'state':'PLAN_ONLY_NO_IO','mode':'OFFLINE_SYNTHETIC','version':VERSION,
        'plan_sha256':sha,'stages':list(STAGES),'planned_fake_requests':len(jobs),
        'planned_streams':sum(len(j.specs) for j in jobs),'policy':asdict(policy),
        'production_authorized':False,'real_http_requests':0,'host_writers_verified':False}

def prepare_fixture(root,*,policy=OrchestrationPolicy(),http_failures=None):
    jobs,_,sha=_plan(policy,http_failures)
    root=Path(root)
    if (not root.name.startswith('package-a-fixture-') or root.is_symlink() or root.exists()):
        raise ValueError('NEW_MARKED_FIXTURE_ROOT_REQUIRED')
    root.mkdir(mode=0o700)
    locks=root/'locks';locks.mkdir(mode=0o700)
    (root/'artifacts').mkdir(mode=0o700)
    (root/MARKER).write_text(MARKER_VALUE)
    db=root/'package_a_synthetic.db';run_id=uuid.uuid4().hex
    with hold_offline_parent_lease(locks):
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('CREATE TABLE package_c_synthetic_retained(value TEXT)')
            ledger.initialize(conn,now=source.ASOF)
        r=root.stat();d=db.stat()
        state.atomic_bytes(root/state.FLAG,state.encode({'version':1,'run_id':run_id,
            'root_identity':[r.st_dev,r.st_ino],'db_identity':[d.st_dev,d.st_ino]}))
        body={'version':VERSION,'run_id':run_id,'plan_sha256':sha,'status':'PREPARED',
            'completed':[],'outputs':{},'jobs':{j.key:{'reserved':0,'success':False} for j in jobs},
            'events':[],'db_sha256':None}
        state.save_state(root,body)
    return {'state':'PREPARED_OFFLINE_FIXTURE','run_id':run_id,'plan_sha256':sha,'production_authorized':False}

def _check_events(events,job_keys,policy):
    for event in events:
        valid=False
        if type(event) is dict and event.get('stage') in STAGES and type(event.get('state')) is str:
            keys=set(event);kind=event['state']
            if kind=='DONE':valid=keys=={'stage','state'}
            elif kind=='BLOCKED':
                valid=keys=={'stage','state','reason'} and type(event['reason']) is str and event['reason'] in SAFE_CODES|ERROR_CLASSES
            elif event['stage']=='ingest' and type(event.get('job')) is str and event['job'] in job_keys:
                if kind=='RESERVED' or kind in ('ok','failed','empty','rejected','blocked'):
                    expected={'stage','job','attempt','state'}|({'failure_class'} if kind!='RESERVED' else set())
                    valid=keys==expected and type(event.get('attempt')) is int and 1<=event['attempt']<=policy.max_attempts
                    if valid and kind!='RESERVED':
                        failure=event['failure_class']
                        valid=failure is None or type(failure) is str and failure in FAILURE_CLASSES
                elif kind in ('RETRY_ADVISORY_ONLY','RETRY_BUDGET_EXHAUSTED','RETRY_DENIED_PERMANENT_OR_LOCK'):
                    delay=event.get('virtual_delay_seconds')
                    valid=(keys=={'stage','job','state','virtual_delay_seconds','clock_wait_executed'} and
                        event['clock_wait_executed'] is False and
                        (type(delay) is int and 1<=delay<=60 if kind=='RETRY_ADVISORY_ONLY' else delay is None))
        if not valid:raise ValueError('STATE_EVENT_SCHEMA_INVALID')

def _checked_state(root,flag,jobs,sha,policy):
    body=state.read_state(root)
    if (set(body)!={'version','run_id','plan_sha256','status','completed','outputs','jobs','events','db_sha256'} or
        body['version']!=VERSION or body['run_id']!=flag['run_id'] or body['plan_sha256']!=sha or
        body['status'] not in ('PREPARED','RUNNING','BLOCKED','COMPLETE') or
        type(body['completed']) is not list or body['completed']!=list(STAGES[:len(body['completed'])]) or
        type(body['outputs']) is not dict or not set(body['outputs'])<=state.ARTIFACTS or
        type(body['events']) is not list or type(body['jobs']) is not dict or set(body['jobs'])!={j.key for j in jobs}):
        raise ValueError('STATE_SCHEMA_OR_PLAN_DRIFT')
    _check_events(body['events'],{j.key for j in jobs},policy)
    for item in body['jobs'].values():
        if (type(item) is not dict or set(item)!={'reserved','success'} or
            type(item['reserved']) is not int or not 0<=item['reserved']<=policy.max_attempts or
            type(item['success']) is not bool):raise ValueError('STATE_JOB_COUNTER_INVALID')
    if (body['status']=='COMPLETE' and body['completed']!=list(STAGES) or
        set(body['outputs'])!={file for stage in body['completed'] for file in STAGE_OUTPUTS[stage]} or
        ('ingest' in body['completed'] and (not all(item['success'] and item['reserved']>0 for item in body['jobs'].values()) or
         type(body['db_sha256']) is not str or len(body['db_sha256'])!=64))):
        raise ValueError('STATE_SCHEMA_OR_PLAN_DRIFT')
    for name,digest in body['outputs'].items():
        if type(digest) is not str or digest!=state.artifact_sha(root,name):raise ValueError('ARTIFACT_CHECKPOINT_MISMATCH')
    if body['db_sha256'] is not None and body['db_sha256']!=state.digest(Path(root/'package_a_synthetic.db').read_bytes()):
        raise ValueError('DATABASE_CHECKPOINT_MISMATCH')
    return body

def _model(db,locks):return analyze_snapshot(read_report_snapshot(db,locks,asof=source.ASOF))

def _receipt_groups(db,job):
    groups=[]
    with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        for spec in job.specs:
            groups.append(conn.execute('SELECT receipt_id,status,http_status,raw_sha256 FROM package_a_receipts '
                'WHERE provider=? AND metric=? AND symbol=? AND frequency=? AND start_utc=? AND end_exclusive_utc=? '
                'ORDER BY rowid',(spec.provider,spec.metric,spec.symbol,spec.frequency,job.start,job.end)).fetchall())
    if any(len(rows)!=len(groups[0]) for rows in groups):raise ValueError('PARTIAL_NATIVE_JOB_RECEIPTS')
    for index,row in enumerate(groups[0]):
        if any(rows[index][1:]!=row[1:] for rows in groups):raise ValueError('MIXED_NATIVE_JOB_RECEIPTS')
    return groups

def _run_ingest(root,db,locks,body,jobs,policy,failures,fault,counters):
    invoked=0
    verify_offline_parent_lease(locks)
    quality=inspect_fixture(db,locks)
    if quality['state']!='LOCAL_FIXTURE_INTEGRITY_PASS':raise ValueError('RAW_INTEGRITY_BLOCKED')
    for index,job in enumerate(jobs):
        verify_offline_parent_lease(locks)
        item=body['jobs'][job.key]
        groups=_receipt_groups(db,job);rows=groups[0]
        if len(rows)>item['reserved']:raise ValueError('UNRESERVED_SOURCE_RECEIPTS')
        success=[row for row in rows if row[1]=='ok' and row[3]==state.digest(job.raw_bytes)]
        if success:
            if len(success)!=1:raise ValueError('DUPLICATED_SUCCESSFUL_SOURCE_JOB')
            item['success']=True;state.save_state(root,body);continue
        if item['success']:raise ValueError('SUCCESS_CHECKPOINT_WITHOUT_RECEIPT')
        if rows and rows[-1][1] in ('empty','rejected'):raise ValueError('PERMANENT_SOURCE_FAILURE')
        while not item['success']:
            if item['reserved']>=policy.max_attempts:raise ValueError('SOURCE_CIRCUIT_OPEN')
            reserved=sum(v['reserved'] for v in body['jobs'].values())
            source_reserved=sum(body['jobs'][j.key]['reserved'] for j in jobs if j.specs[0].provider==job.specs[0].provider)
            if reserved>=policy.max_fake_requests or source_reserved>=dict(policy.provider_budgets)[job.specs[0].provider]:
                raise ValueError('PERSISTENT_REQUEST_BUDGET_EXHAUSTED')
            item['reserved']+=1
            body['events'].append({'stage':'ingest','job':job.key,'attempt':item['reserved'],'state':'RESERVED'})
            verify_offline_parent_lease(locks);state.save_state(root,body)
            _maybe_fault(fault,'after:reserve:'+job.key)
            statuses=failures.get(job.key,[]);status=statuses[item['reserved']-1] if item['reserved']<=len(statuses) else 200
            result=source.apply_example_job(db,locks,job,now=source.ASOF,status_code=status,_parent_lock_held=True)
            invoked+=result.get('frozen_http_requests',0)
            counters['fake_requests']+=result.get('frozen_http_requests',0)
            _maybe_fault(fault,'after:job:'+job.key)
            body['events'].append({'stage':'ingest','job':job.key,'attempt':item['reserved'],
                'state':result.get('status','blocked'),'failure_class':result.get('failure_class')})
            if result.get('status')=='ok':
                item['success']=True;state.save_state(root,body)
            else:
                state.save_state(root,body)
                if result.get('status')!='failed':raise ValueError('PERMANENT_SOURCE_FAILURE')
                advice=plan_offline_retry_advice({'pages':[{'index':index,
                    'state':'PAGE_NOT_COMMITTED','status':'failed','failure_class':result.get('failure_class')}]},
                    policy=OfflineRetryPolicy(max_attempts=policy.max_attempts,max_fake_requests=policy.max_fake_requests),
                    completed_attempts=item['reserved'],window_plan_sha256=body['plan_sha256'])
                body['events'].append({'stage':'ingest','job':job.key,'state':advice['state'],
                    'virtual_delay_seconds':advice['virtual_delay_seconds'],'clock_wait_executed':False})
                state.save_state(root,body)
                if not advice['retry_allowed']:
                    raise ValueError('SOURCE_CIRCUIT_OPEN' if advice['state']=='RETRY_BUDGET_EXHAUSTED' else 'PERMANENT_SOURCE_FAILURE')
    return invoked

def _chart_bundle(model):
    document=render_report(model)
    charts=re.findall(r'<svg\b.*?</svg>',document,re.DOTALL)
    if len(charts)!=3:raise ValueError('CRITICAL_CHART_INPUTS')
    return [{'symbol':asset.symbol,'snapshot_sha256':model.snapshot.sha256,
             'svg_sha256':state.digest(svg.encode()),'svg':svg} for asset,svg in zip(model.assets,charts)]

def _stage(name,root,db,locks):
    verify_offline_parent_lease(locks)
    if name=='validate':
        quality=inspect_fixture(db,locks)
        if quality['state']!='LOCAL_FIXTURE_INTEGRITY_PASS':raise ValueError('RAW_INTEGRITY_BLOCKED')
        model=_model(db,locks)
        if model.critical_errors or len(model.snapshot.series)!=57:raise ValueError('CRITICAL_DATA_BLOCKED')
        return {'audit.json':state.write_json(root,'audit.json',quality)}
    model=_model(db,locks)
    if model.critical_errors:raise ValueError('CRITICAL_DATA_BLOCKED')
    if name=='analytics':return {'evidence.json':state.write_json(root,'evidence.json',asdict(model))}
    if name=='charts':return {'charts.json':state.write_json(root,'charts.json',_chart_bundle(model))}
    if name=='report':
        if json.loads(state.bounded_read(state.artifact(root,'charts.json'),state.MAX_ARTIFACT))!=_chart_bundle(model):
            raise ValueError('CHART_EVIDENCE_MISMATCH')
        state.atomic_bytes(state.artifact(root,'report.html'),render_report(model).encode())
        return {'report.html':state.artifact_sha(root,'report.html')}
    if name=='archive':
        bundle={'version':VERSION,'snapshot_sha256':model.snapshot.sha256,
            'files':{file:state.artifact_sha(root,file) for file in ('audit.json','evidence.json','charts.json','report.html')},
            'production_publication_authorized':False}
        return {'archive.json':state.write_json(root,'archive.json',bundle)}
    if name=='gate':
        gate=_evaluate_artifacts(root,db,locks,model)
        return {'gate.json':state.write_json(root,'gate.json',gate)}
    raise ValueError('UNKNOWN_PIPELINE_STAGE')

def _evaluate_artifacts(root,db,locks,model):
    archive=json.loads(state.bounded_read(state.artifact(root,'archive.json'),state.MAX_ARTIFACT))
    expected={file:state.artifact_sha(root,file) for file in ('audit.json','evidence.json','charts.json','report.html')}
    if (type(archive) is not dict or set(archive)!={'version','snapshot_sha256','files','production_publication_authorized'} or
        archive.get('version')!=VERSION or archive.get('files')!=expected or archive.get('snapshot_sha256')!=model.snapshot.sha256 or
        archive.get('production_publication_authorized') is not False or
        json.loads(state.bounded_read(state.artifact(root,'evidence.json'),state.MAX_ARTIFACT))!=json.loads(state.encode(asdict(model))) or
        json.loads(state.bounded_read(state.artifact(root,'charts.json'),state.MAX_ARTIFACT))!=_chart_bundle(model)):
        raise ValueError('ARCHIVE_EVIDENCE_MISMATCH')
    document=state.bounded_read(state.artifact(root,'report.html'),state.MAX_ARTIFACT).decode()
    gate=audit_candidate_report(model,document)
    pub=evaluate_package_b_fixture(document,db_path=db,lock_dir=locks,asof=source.ASOF)
    if gate['state']!='OFFLINE_REPORT_LOCAL_RECONCILIATION_PASS' or not pub['pass']:
        raise ValueError('PUBLICATION_GATE_BLOCKED')
    return {**gate,'publication_gate':pub,'archive_sha256':state.artifact_sha(root,'archive.json'),
        'orchestration_mode':'OFFLINE_SYNTHETIC','production_publication_authorized':False}

def run_fixture_pipeline(root,*,apply=False,policy=OrchestrationPolicy(),fault=None,http_failures=None):
    if type(apply) is not bool:raise ValueError('EXPLICIT_APPLY_BOOLEAN_REQUIRED')
    if not apply:return plan_fixture_pipeline(policy=policy,http_failures=http_failures)
    current='preflight';invoked=0;counters={'fake_requests':0}
    try:
        jobs,failures,sha=_plan(policy,http_failures)
        allowed={None}|{prefix+stage for stage in STAGES for prefix in ('before:','after:','after-output:')}|{
            prefix+j.key for j in jobs for prefix in ('after:reserve:','after:job:')}
        if type(fault) not in (str,type(None)) or fault not in allowed:raise ValueError('UNKNOWN_FAULT_PARAMETER')
        root,db,locks,flag=state.guard(root)
        # Verify state/plan/files without SQL before attempting the global lock.
        body=_checked_state(root,flag,jobs,sha,policy)
        with hold_offline_parent_lease(locks,identity_check=lambda:state.guard(root)):
            root,db,locks,flag=state.guard(root)
            body=_checked_state(root,flag,jobs,sha,policy)
            try:
                for name in STAGES:
                    current=name;verify_offline_parent_lease(locks)
                    if name in body['completed']:continue
                    _maybe_fault(fault,'before:'+name)
                    body['status']='RUNNING';state.save_state(root,body)
                    if name=='ingest':invoked+=_run_ingest(root,db,locks,body,jobs,policy,failures,fault,counters)
                    else:body['outputs'].update(_stage(name,root,db,locks))
                    _maybe_fault(fault,'after-output:'+name)
                    verify_offline_parent_lease(locks)
                    body['completed'].append(name)
                    if name=='ingest':body['db_sha256']=state.digest(db.read_bytes())
                    body['events'].append({'stage':name,'state':'DONE'})
                    state.save_state(root,body)
                    _maybe_fault(fault,'after:'+name)
                verify_offline_parent_lease(locks)
                model=_model(db,locks);actual=_evaluate_artifacts(root,db,locks,model)
                if json.loads(state.bounded_read(state.artifact(root,'gate.json'),state.MAX_ARTIFACT))!=actual:
                    raise ValueError('FINAL_GATE_EVIDENCE_MISMATCH')
                body['status']='COMPLETE';verify_offline_parent_lease(locks);state.save_state(root,body)
                return {'state':'COMPLETE_OFFLINE_FIXTURE_ONLY','run_id':body['run_id'],
                    'plan_sha256':sha,'stages_completed':body['completed'],
                    'fake_requests_reserved':sum(v['reserved'] for v in body['jobs'].values()),
                    'fake_requests_this_invocation':invoked,'real_http_requests':0,
                    'production_authorized':False,'host_writers_verified':False}
            except (ValueError,OSError,sqlite3.Error,TypeError,KeyError,RuntimeError) as error:
                # Persist failure only while the SAME safe lease is still held;
                # never repair a corrupt checkpoint or a replaced lock pathname.
                try:
                    state.guard(root);verify_offline_parent_lease(locks)
                    persisted=state.read_state(root)
                    persisted['status']='BLOCKED'
                    persisted['events'].append({'stage':current,'state':'BLOCKED','reason':_reason(error)})
                    state.save_state(root,persisted)
                except (ValueError,OSError,TypeError,KeyError):pass
                raise
    except WriterLockBusy:
        return {'state':'BLOCKED_WRITER_BUSY','stage':current,'fake_requests_this_invocation':0,
                'sqlite_connections_started':0,'production_authorized':False}
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError,RuntimeError) as error:
        return {'state':'BLOCKED_OFFLINE_PIPELINE','stage':current,
            'reason':_reason(error),
            'fake_requests_this_invocation':counters['fake_requests'],
            'production_authorized':False,'real_http_requests':0}
