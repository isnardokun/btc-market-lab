#!/usr/bin/env python3
"""Recoverable seven-stage installed-schema compatibility, OFFLINE only.

PLAN_ONLY is pure and never reads the supplied path. Apply operates only on
C-attested marked fixtures. Report acceptance is distinct from successful
private diagnostics and is ALWAYS distinct from publication authorization.
"""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from analysis.installed_evidence import LegacySelection, read_installed_snapshot
from analysis.package_b_analytics import analyze_snapshot
from analysis.package_b_evidence import canonical_digest
from ingestion.package_a_contracts import SeriesSpec, utc
from ingestion import compatibility_acquisition as acquisition
from ingestion.compatibility_acquisition import AcquisitionRequest, FakeTransport, InjectedCrash, request_from_dict
from rendering.package_b_report import render_report, audit_candidate_report
from scripts import package_c_state as state
from scripts.package_c_pipeline import STAGES, STAGE_OUTPUTS, OrchestrationPolicy
from scripts.package_a_parent_lock import hold_offline_parent_lease, verify_offline_parent_lease
from scripts.market_r10e11_writer_exclusion import WriterLockBusy
from storage import compatibility_ledger as ledger
from storage.installed_schema import inspect_installed_schema, legacy_fingerprint
from validation.installed_report_gate import evaluate_installed_fixture

VERSION='installed-compatibility-offline-v1'


def plan_installed_pipeline(requests=(),policy=OrchestrationPolicy()):
    if (type(requests) is not tuple or any(not isinstance(r,AcquisitionRequest) for r in requests) or
        len({r.sha256 for r in requests})!=len(requests) or not isinstance(policy,OrchestrationPolicy)):
        raise ValueError('TYPED_UNIQUE_REQUEST_PLAN_REQUIRED')
    counts=Counter(r.specs[0].provider for r in requests)
    if len(requests)>policy.max_fake_requests or any(n>dict(policy.provider_budgets)[p] for p,n in counts.items()):
        raise ValueError('PLAN_REQUEST_BUDGET_INSUFFICIENT')
    return {'state':'PLAN_ONLY_NO_IO','version':VERSION,'mode':'OFFLINE_INSTALLED_SCHEMA',
        'stages':list(STAGES),'planned_fake_requests':len(requests),'providers':dict(counts),
        'real_http_requests':0,'production_authorized':False,'production_publication_authorized':False}


def _outcomes(transport):
    if type(transport) is not FakeTransport: raise ValueError('FAKE_HTTP_ONLY')
    return {key:[{'status':code,'raw_sha256':state.digest(raw),'bytes':len(raw)} for code,raw in values]
        for key,values in sorted(transport.outcomes.items())}


def _manifest(asof,capture,selections,requests,policy,transport):
    plan_installed_pipeline(requests,policy);utc(asof);utc(capture)
    if type(selections) is not tuple or any(not isinstance(s,LegacySelection) for s in selections):
        raise ValueError('TYPED_LEGACY_SELECTIONS_REQUIRED')
    outcomes=_outcomes(transport)
    if set(outcomes)!={r.sha256 for r in requests}: raise ValueError('FROZEN_PLAN_RESPONSE_DRIFT')
    return {'asof':asof,'snapshot_captured_utc':capture,'selections':[asdict(s) for s in selections],
        'requests':[asdict(r) for r in requests],'policy':asdict(policy),'outcomes':outcomes}


def _decode(plan):
    if type(plan) is not dict or set(plan)!={'asof','snapshot_captured_utc','selections','requests','policy','outcomes'}:
        raise ValueError('PLAN_MANIFEST_DRIFT')
    selections=tuple(LegacySelection(SeriesSpec(**s['spec']),s['table'],s['lookup']) for s in plan['selections'])
    requests=tuple(request_from_dict(r) for r in plan['requests'])
    p=plan['policy'];policy=OrchestrationPolicy(p['max_attempts'],p['max_fake_requests'],tuple(tuple(pair) for pair in p['provider_budgets']))
    plan_installed_pipeline(requests,policy);utc(plan['asof']);utc(plan['snapshot_captured_utc'])
    return selections,requests,policy


def prepare_installed_run(root,*,asof,snapshot_captured_utc,selections=(),requests=(),
                          policy=OrchestrationPolicy(),transport):
    plan=_manifest(asof,snapshot_captured_utc,selections,requests,policy,transport)
    root,db,locks,flag=state.guard(root)
    with hold_offline_parent_lease(locks,identity_check=lambda:state.guard(root)):
        if state.read_state(root)!={}: raise ValueError('NEW_EMPTY_INSTALLED_RUN_REQUIRED')
        with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
            inspect_installed_schema(conn);before=legacy_fingerprint(conn)
        body={'version':VERSION,'run_id':flag['run_id'],'plan':plan,'plan_sha256':canonical_digest(plan),
            'status':'PREPARED','completed':[],'outputs':{},'legacy_fingerprint':before,'events':[]}
        state.save_state(root,body)
    return {'state':'PREPARED_OFFLINE_INSTALLED_SCHEMA','plan_sha256':body['plan_sha256'],'production_authorized':False}


def _checked(root,flag,transport):
    body=state.read_state(root)
    if (set(body)!={'version','run_id','plan','plan_sha256','status','completed','outputs','legacy_fingerprint','events'} or
        body['version']!=VERSION or body['run_id']!=flag['run_id'] or
        body['status'] not in ('PREPARED','RUNNING','BLOCKED','COMPLETE') or
        canonical_digest(body['plan'])!=body['plan_sha256'] or type(body['completed']) is not list or
        body['completed']!=list(STAGES[:len(body['completed'])]) or
        type(body['outputs']) is not dict or set(body['outputs'])!={f for s in body['completed'] for f in STAGE_OUTPUTS[s]} or
        type(body['events']) is not list): raise ValueError('INSTALLED_STATE_OR_PLAN_DRIFT')
    selections,requests,policy=_decode(body['plan'])
    if _outcomes(transport)!=body['plan']['outcomes']: raise ValueError('FROZEN_PLAN_RESPONSE_DRIFT')
    for event in body['events']:
        if (type(event) is not dict or set(event)!={'stage','state'} or event['stage'] not in STAGES or
            event['state'] not in ('DONE','BLOCKED','RETRY_ADVISORY_ONLY')): raise ValueError('INSTALLED_EVENT_DRIFT')
    for name,sha in body['outputs'].items():
        if sha!=state.artifact_sha(root,name): raise ValueError('ARTIFACT_CHECKPOINT_MISMATCH')
    if body['status']=='COMPLETE' and body['completed']!=list(STAGES): raise ValueError('INCOMPLETE_STAGE_STATE')
    return body,selections,requests,policy


def _audit(db,body,requests,policy):
    with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        structure=inspect_installed_schema(conn);retained=legacy_fingerprint(conn)
        if retained!=body['legacy_fingerprint']: raise ValueError('LEGACY_FINGERPRINT_DRIFT')
        proof=ledger.audit(conn) if structure['addon_tables'] else {'state':'NO_ADDITIVE_ACQUISITIONS','receipts':0}
        if requests:
            if not structure['addon_tables']: raise ValueError('MISSING_RUN_RECEIPTS')
            expected_policy=json.dumps({'max_requests':policy.max_fake_requests,'max_attempts':policy.max_attempts,
                'provider_budgets':dict(policy.provider_budgets)},sort_keys=True,separators=(',',':'))
            if conn.execute('SELECT plan_sha256,policy_json FROM compat_runs WHERE run_id=?',(body['run_id'],)).fetchone()!=(body['plan_sha256'],expected_policy):
                raise ValueError('RUN_PLAN_OR_POLICY_DRIFT')
            rows=conn.execute('SELECT request_sha256,attempt,status,request_json FROM compat_requests WHERE run_id=?',(body['run_id'],)).fetchall()
            known={r.sha256 for r in requests}
            counts=Counter(row[0] for row in rows);providers=Counter(json.loads(row[3])['specs'][0]['provider'] for row in rows)
            successes={row[0] for row in rows if row[2] in ('ok','empty')}
            if (not set(counts)<=known or successes!=known or len(rows)>policy.max_fake_requests or
                any(n>policy.max_attempts for n in counts.values()) or
                any(n>dict(policy.provider_budgets)[p] for p,n in providers.items())):
                raise ValueError('RUN_RECEIPT_PLAN_OR_BUDGET_DRIFT')
        return {'structure':structure,'legacy_fingerprint':retained,'acquisitions':proof,
            'external_history_verified':False,'global_writer_exclusion':'NOT_VERIFIED','production_authorized':False}


def _model(db,locks,body,selections):
    plan=body['plan']
    return analyze_snapshot(read_installed_snapshot(db,locks,asof=plan['asof'],
        snapshot_captured_utc=plan['snapshot_captured_utc'],selections=selections))


def _charts(model):
    return {a.symbol:{'series_sha256':canonical_digest(a.closes),'observations':len(a.closes)} for a in model.assets}


def _archive(body):
    return {'mode':'PRIVATE_OFFLINE_INSTALLED_SCHEMA','run_id':body['run_id'],'plan_sha256':body['plan_sha256'],
        'inputs':{name:body['outputs'][name] for name in ('audit.json','evidence.json','charts.json','report.html')},
        'production_publication_authorized':False}


def _gate(model,document,db,locks,body,selections):
    report=audit_candidate_report(model,document)
    scored=evaluate_installed_fixture(document,db_path=db,lock_dir=locks,asof=body['plan']['asof'],
        snapshot_captured_utc=body['plan']['snapshot_captured_utc'],selections=selections)
    if scored['snapshot_sha256']!=model.snapshot.sha256: raise ValueError('GATE_SNAPSHOT_DRIFT')
    return {'state':'OFFLINE_COMPATIBILITY_REPORT_PASS' if scored['pass'] else 'BLOCKED_PRIVATE_REPORT_DATA',
        'pass':scored['pass'],'report':report,'production_publication_authorized':False,
        'publication_gate':scored,'html_sha256':report['html_sha256'],
        'external_history_verified':False,'mode':'OFFLINE_INSTALLED_SCHEMA'}


def _final(root,db,locks,body,selections,requests,policy):
    model=_model(db,locks,body,selections);document=render_report(model)
    checks={'audit.json':_audit(db,body,requests,policy),'evidence.json':asdict(model),
        'charts.json':_charts(model),'archive.json':_archive(body),'gate.json':_gate(model,document,db,locks,body,selections)}
    for name,value in checks.items():
        if state.bounded_read(state.artifact(root,name),state.MAX_ARTIFACT)!=state.encode(value):
            raise ValueError('FINAL_EVIDENCE_ARTIFACT_MISMATCH')
    if state.bounded_read(state.artifact(root,'report.html'),state.MAX_ARTIFACT)!=document.encode():
        raise ValueError('FINAL_REPORT_EVIDENCE_MISMATCH')
    gate=checks['gate.json']
    return {'state':'COMPLETE_OFFLINE_COMPATIBILITY' if gate['pass'] else 'COMPLETE_PRIVATE_DIAGNOSTIC',
        'report_gate_pass':gate['pass'],'snapshot_sha256':model.snapshot.sha256,
        'plan_sha256':body['plan_sha256'],'run_id':body['run_id'],'real_http_requests':0,
        'production_publication_authorized':False,'external_history_verified':False}


def _fault(fault,position):
    if fault==position: raise InjectedCrash(position)


@contextmanager
def _record_block(root,holder):
    try: yield
    except (ValueError,OSError,TypeError,KeyError,sqlite3.Error):
        body=holder.get('body')
        if body is not None:
            try:
                # Persist only a validated state while the original lease and
                # inode remain valid. Never write after guard/identity drift.
                _,_,locks,_=state.guard(root);verify_offline_parent_lease(locks)
                body['status']='BLOCKED'
                body['events'].append({'stage':holder['stage'],'state':'BLOCKED'})
                state.save_state(root,body)
            except (ValueError,OSError,TypeError,KeyError): pass
        raise


def run_installed_pipeline(root,*,apply=False,transport=None,fault=None):
    if type(apply) is not bool: raise ValueError('EXPLICIT_APPLY_BOOLEAN_REQUIRED')
    if not apply: return plan_installed_pipeline()
    permitted={None}|{p+':'+s for s in STAGES for p in ('before','after','after_output')}|{
        'acquire:'+s for s in ('after_reserve','after_raw','after_observations','after_commit')}
    if fault not in permitted: raise ValueError('UNKNOWN_INSTALLED_PIPELINE_FAULT')
    body=None;holder={}
    try:
        root,db,locks,flag=state.guard(root)
        with hold_offline_parent_lease(locks,identity_check=lambda:state.guard(root)),_record_block(root,holder):
            body,selections,requests,policy=_checked(root,flag,transport)
            holder.update(body=body,stage='gate' if body['status']=='COMPLETE' else STAGES[min(len(body['completed']),6)])
            # Legacy rows/schema must still match even when the previous run
            # ended before producing its validate artifact.
            with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN');inspect_installed_schema(conn)
                if legacy_fingerprint(conn)!=body['legacy_fingerprint']: raise ValueError('LEGACY_FINGERPRINT_DRIFT')
            if body['status']=='COMPLETE': return _final(root,db,locks,body,selections,requests,policy)
            body['status']='RUNNING';state.save_state(root,body)
            for stage in STAGES[len(body['completed']):]:
                holder['stage']=stage
                _fault(fault,'before:'+stage);verify_offline_parent_lease(locks);state.guard(root)
                if stage=='ingest':
                    for request in requests:
                        while True:
                            result=acquisition.acquire_fake_page(db,locks,request,transport,run_id=body['run_id'],now=body['plan']['asof'],
                                max_requests=policy.max_fake_requests,max_attempts=policy.max_attempts,plan_sha=body['plan_sha256'],
                                provider_budgets=policy.provider_budgets,_parent_lock_held=True,
                                fault=fault.split(':',1)[1] if fault and fault.startswith('acquire:') else None)
                            if result['status'] in ('ok','empty'): break
                            if result['status']=='rejected': raise ValueError('SOURCE_CONTRACT_REJECTED')
                            body['events'].append({'stage':stage,'state':'RETRY_ADVISORY_ONLY'});state.save_state(root,body)
                elif stage=='validate': state.write_json(root,'audit.json',_audit(db,body,requests,policy))
                elif stage=='analytics': state.write_json(root,'evidence.json',asdict(_model(db,locks,body,selections)))
                elif stage=='charts': state.write_json(root,'charts.json',_charts(_model(db,locks,body,selections)))
                elif stage=='report': state.atomic_bytes(state.artifact(root,'report.html'),render_report(_model(db,locks,body,selections)).encode())
                elif stage=='archive': state.write_json(root,'archive.json',_archive(body))
                elif stage=='gate':
                    model=_model(db,locks,body,selections)
                    document=state.bounded_read(state.artifact(root,'report.html'),state.MAX_ARTIFACT).decode()
                    state.write_json(root,'gate.json',_gate(model,document,db,locks,body,selections))
                _fault(fault,'after_output:'+stage)
                for name in STAGE_OUTPUTS[stage]: body['outputs'][name]=state.artifact_sha(root,name)
                body['completed'].append(stage);body['events'].append({'stage':stage,'state':'DONE'});state.save_state(root,body)
                _fault(fault,'after:'+stage)
            result=_final(root,db,locks,body,selections,requests,policy)
            body['status']='COMPLETE';state.save_state(root,body)
            return result
    except WriterLockBusy:
        return {'state':'BLOCKED_WRITER_BUSY','real_http_requests':0,'production_publication_authorized':False}
    except (ValueError,OSError,TypeError,KeyError,sqlite3.Error) as error:
        # Do not persist a state loaded before its schema/checksum passed, or
        # reacquire a lock merely to record an already-blocked operation.
        return {'state':'BLOCKED','reason_class':type(error).__name__,'real_http_requests':0,
            'production_publication_authorized':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Installed compatibility PLAN_ONLY; fixtures via Python API only')
    parser.parse_args(argv)
    print(json.dumps(plan_installed_pipeline(),sort_keys=True))
    return 0


if __name__=='__main__': raise SystemExit(main())
