"""Explicit FAKE_HTTP acquisition on an owned installed-schema fixture.

Durable reservation precedes transport; no SQL write transaction spans a
transport operation. New evidence NEVER uses A's FROZEN_OFFLINE label.
No live transport implementation, callback, credentials or default DB path.
"""
from contextlib import closing, nullcontext
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from analysis.package_b_evidence import canonical_digest
from ingestion.package_a_contracts import SeriesSpec, utc
from ingestion.package_a_legacy_bridge import reconciled_fixture_points
from scripts import package_c_state as state
from scripts.package_a_parent_lock import hold_offline_parent_lease, verify_offline_parent_lease
from storage import compatibility_ledger as ledger
from storage.installed_schema import inspect_installed_schema

MAX_RAW=4_000_000


class InjectedCrash(BaseException): pass


@dataclass(frozen=True)
class AcquisitionRequest:
    specs: tuple[SeriesSpec,...]
    start: str
    end: str
    endpoint_origin: str
    endpoint_path: str
    params: tuple[tuple[str,str|int],...]
    direction: str = 'forward'

    def __post_init__(self):
        if (type(self.specs) is not tuple or not 1<=len(self.specs)<=5 or
            any(not isinstance(s,SeriesSpec) for s in self.specs) or
            len({s.provider for s in self.specs})!=1 or len({s.metric for s in self.specs})!=len(self.specs) or
            utc(self.start)>=utc(self.end) or self.direction!='forward'):
            raise ValueError('TYPED_FORWARD_REQUEST_REQUIRED')
        provider=self.specs[0].provider
        if len(self.specs)>1 and (provider!='yahoo' or {s.metric for s in self.specs}!={'open','high','low','close','volume'} or len({s.symbol for s in self.specs})!=1):
            raise ValueError('ONE_SOURCE_CONTRACT_PER_REQUEST')
        origin=urlsplit(self.endpoint_origin)
        if (origin.scheme!='https' or origin.netloc!=provider+'.invalid' or origin.path or origin.query or origin.fragment or
            not isinstance(self.endpoint_path,str) or not self.endpoint_path.startswith('/offline/') or
            any(c in self.endpoint_path for c in ('?','#','\n','\r'))):
            raise ValueError('FROZEN_OFFLINE_ENDPOINT_REQUIRED')
        allowed={'symbol','interval','start','end','startTime','endTime','period1','period2','limit','series_id','start_utc','end_exclusive_utc','metric'}
        if (type(self.params) is not tuple or len(self.params)>16 or any(type(p) is not tuple or len(p)!=2 or p[0] not in allowed or
            type(p[1]) not in (str,int) or len(str(p[1]))>120 for p in self.params) or len({p[0] for p in self.params})!=len(self.params)):
            raise ValueError('BOUNDED_SECRET_FREE_REQUEST_PARAMS_REQUIRED')

    @property
    def sha256(self): return canonical_digest(asdict(self))


def request_from_dict(value):
    if type(value) is not dict or set(value)!={'specs','start','end','endpoint_origin','endpoint_path','params','direction'}:
        raise ValueError('REQUEST_MANIFEST_DRIFT')
    return AcquisitionRequest(tuple(SeriesSpec(**s) for s in value['specs']),value['start'],value['end'],
        value['endpoint_origin'],value['endpoint_path'],tuple(tuple(p) for p in value['params']),value['direction'])


def request_from_job(job):
    from scripts.package_b_report_fixture import ExampleJob
    if not isinstance(job,ExampleJob): raise ValueError('TYPED_SOURCE_JOB_REQUIRED')
    s=job.specs[0]
    return AcquisitionRequest(job.specs,job.start,job.end,'https://'+s.provider+'.invalid',
        '/offline/'+s.symbol+'/'+('ohlcv' if len(job.specs)>1 else s.metric),
        (('start_utc',job.start),('end_exclusive_utc',job.end)))


class FakeTransport:
    """Frozen outcomes only, no user fetch function or HTTP client."""
    def __init__(self,outcomes):
        if type(outcomes) is not dict or len(outcomes)>64: raise ValueError('FROZEN_OUTCOMES_REQUIRED')
        for key,values in outcomes.items():
            if type(key) is not str or len(key)!=64 or type(values) is not tuple or not 1<=len(values)<=3:
                raise ValueError('FROZEN_OUTCOMES_REQUIRED')
            for item in values:
                if (type(item) is not tuple or len(item)!=2 or item[0] not in (None,200,429,500,503) or
                    type(item[1]) is not bytes or len(item[1])>MAX_RAW): raise ValueError('BOUNDED_FAKE_RESPONSE_REQUIRED')
        self.outcomes=dict(outcomes);self.calls=0

    def outcome(self,request,attempt):
        values=self.outcomes.get(request.sha256)
        if not values: raise ValueError('REQUEST_NOT_FROZEN')
        return values[min(attempt-1,len(values)-1)]

    def fetch(self,request,attempt):
        self.calls+=1
        return self.outcome(request,attempt)


def _fault(fault,position):
    if fault==position: raise InjectedCrash(position)


def acquire_fake_page(db_path,lock_dir,request,transport,*,run_id,now,max_requests,max_attempts,
                      fault=None,plan_sha=None,provider_budgets=None,_parent_lock_held=False):
    if not isinstance(request,AcquisitionRequest) or type(transport) is not FakeTransport:
        raise ValueError('FAKE_HTTP_ONLY')
    if (not isinstance(run_id,str) or not 1<=len(run_id)<=64 or
        type(max_requests) is not int or not 1<=max_requests<=128 or type(max_attempts) is not int or not 1<=max_attempts<=3 or
        fault not in (None,'after_reserve','after_raw','after_observations','after_commit') or type(_parent_lock_held) is not bool):
        raise ValueError('BOUNDED_ACQUISITION_POLICY_REQUIRED')
    stamp=utc(now).isoformat().replace('+00:00','Z')
    root,path,locks,_=state.guard(Path(db_path).parent)
    if path!=Path(db_path) or locks!=Path(lock_dir): raise ValueError('EXPLICIT_FIXTURE_PATH_MISMATCH')
    budgets={} if provider_budgets is None else dict(provider_budgets)
    if any(p not in ('yahoo','fred','bitview','researchbitcoin','binance','bybit') or type(n) is not int or not 1<=n<=128 for p,n in budgets.items()):
        raise ValueError('PROVIDER_POLICY_INVALID')
    policy=json.dumps({'max_requests':max_requests,'max_attempts':max_attempts,'provider_budgets':budgets},sort_keys=True,separators=(',',':'))
    plan=request.sha256 if plan_sha is None else plan_sha
    if not isinstance(plan,str) or len(plan)!=64: raise ValueError('PLAN_SHA_REQUIRED')
    if _parent_lock_held: verify_offline_parent_lease(locks)
    lease=nullcontext() if _parent_lock_held else hold_offline_parent_lease(locks,identity_check=lambda:state.guard(root))
    with lease:
        verify_offline_parent_lease(locks); state.guard(root)
        with closing(sqlite3.connect(path,isolation_level=None)) as conn:
            conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
            try:
                ledger.initialize(conn,now=stamp)
                row=conn.execute('SELECT plan_sha256,policy_json FROM compat_runs WHERE run_id=?',(run_id,)).fetchone()
                if row and row!=(plan,policy): raise ValueError('RUN_PLAN_OR_POLICY_DRIFT')
                if not row: conn.execute('INSERT INTO compat_runs VALUES(?,?,?)',(run_id,plan,policy))
                ledger.audit(conn)
                existing=conn.execute('SELECT receipt_id,attempt,raw_sha256 FROM compat_requests WHERE run_id=? AND request_sha256=? AND status IN (\'ok\',\'empty\') ORDER BY attempt DESC',(run_id,request.sha256)).fetchone()
                if existing:
                    status,body=transport.outcome(request,existing[1])
                    if status!=200 or hashlib.sha256(body).hexdigest()!=existing[2]: raise ValueError('FROZEN_RESPONSE_DRIFT')
                    verify_offline_parent_lease(locks);state.guard(root);conn.rollback()
                    return {'status':'ok' if conn.execute('SELECT COUNT(*) FROM compat_observations WHERE receipt_id=?',(existing[0],)).fetchone()[0] else 'empty','receipt_id':existing[0],'replayed':True}
                attempts=conn.execute('SELECT COUNT(*) FROM compat_requests WHERE run_id=? AND request_sha256=?',(run_id,request.sha256)).fetchone()[0]
                total=conn.execute('SELECT COUNT(*) FROM compat_requests WHERE run_id=?',(run_id,)).fetchone()[0]
                provider=request.specs[0].provider
                used=sum(1 for (text,) in conn.execute('SELECT request_json FROM compat_requests WHERE run_id=?',(run_id,)) if json.loads(text)['specs'][0]['provider']==provider)
                if attempts>=max_attempts or total>=max_requests or used>=budgets.get(provider,128):
                    raise ValueError('REQUEST_BUDGET_EXHAUSTED')
                # A successful new page must follow the existing cursor exactly.
                for s in request.specs:
                    cursor=conn.execute('SELECT end_exclusive_utc FROM compat_cursors WHERE provider=? AND metric=? AND symbol=? AND frequency=? AND direction=\'forward\'',(s.provider,s.metric,s.symbol,s.frequency)).fetchone()
                    if cursor and utc(cursor[0])!=utc(request.start): raise ValueError('FORWARD_CURSOR_CONTINUITY_REQUIRED')
                attempt=attempts+1; rid=canonical_digest([run_id,request.sha256,attempt])
                body=json.dumps(asdict(request),sort_keys=True,separators=(',',':'))
                conn.execute('INSERT INTO compat_requests VALUES(?,?,?,?,?,\'FAKE_HTTP\',\'OFFLINE_PROVIDER_CONTRACT\',\'reserved\',NULL,?,NULL,NULL)',(rid,run_id,request.sha256,body,attempt,stamp))
                verify_offline_parent_lease(locks);state.guard(root);conn.commit()
            except BaseException: conn.rollback();raise
            _fault(fault,'after_reserve')
            verify_offline_parent_lease(locks);state.guard(root)
            if conn.in_transaction: raise ValueError('HTTP_UNDER_WRITE_TRANSACTION')
            code,raw=transport.fetch(request,attempt)
            raw_sha=hashlib.sha256(raw).hexdigest() if code is not None else None
            parsed=[];error=None
            if code!=200: status='failed';error='SIMULATED_TIMEOUT' if code is None else 'SIMULATED_HTTP_'+str(code)
            else:
                try:
                    payload=json.loads(raw)
                    for s in request.specs:
                        parsed.extend(ledger.observation(s,p,rid) for p in ledger.validated_points(request,s,payload,now=stamp))
                    status='ok' if parsed else 'empty'
                except (ValueError,TypeError,KeyError,IndexError,OverflowError): status='rejected';error='SOURCE_CONTRACT_REJECTED'
            conn.execute('BEGIN IMMEDIATE')
            try:
                inspect_installed_schema(conn);ledger.assert_schema(conn)
                if raw_sha:
                    conn.execute('INSERT OR IGNORE INTO compat_raw_payloads VALUES(?,?)',(raw_sha,raw))
                    if conn.execute('SELECT body FROM compat_raw_payloads WHERE sha256=?',(raw_sha,)).fetchone()[0]!=raw:
                        raise ValueError('RAW_COLLISION')
                _fault(fault,'after_raw')
                if parsed: conn.executemany('INSERT INTO compat_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',parsed)
                _fault(fault,'after_observations')
                conn.execute('UPDATE compat_requests SET status=?,http_status=?,raw_sha256=?,error_class=? WHERE receipt_id=? AND status=\'reserved\'',(status,code,raw_sha,error,rid))
                if status=='ok':
                    for s in request.specs:
                        if not any(r[1:5]==(s.provider,s.metric,s.symbol,s.frequency) for r in parsed): continue
                        conn.execute('INSERT INTO compat_cursors VALUES(?,?,?,?,\'forward\',?,?) ON CONFLICT(provider,metric,symbol,frequency,direction) DO UPDATE SET end_exclusive_utc=excluded.end_exclusive_utc,advanced_by_receipt=excluded.advanced_by_receipt',
                            (s.provider,s.metric,s.symbol,s.frequency,request.end,rid))
                ledger.audit(conn);verify_offline_parent_lease(locks);state.guard(root);conn.commit()
            except BaseException: conn.rollback();raise
            _fault(fault,'after_commit')
            return {'status':status,'receipt_id':rid,'replayed':False,'failure_class':error}
