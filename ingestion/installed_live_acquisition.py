"""One granted page: durable reservation -> transport -> atomic custody/rows."""
from dataclasses import asdict,dataclass
import hashlib
import json
import time
from ingestion.installed_live_sources import LiveRequest,normalize_live_page
from ingestion.installed_live_transport import BoundedHttpTransport,FrozenLiveTransport,validate_request_capability
from scripts.installed_live_policy import digest
from storage import installed_live_ledger as ledger
from ingestion.package_a_contracts import utc

class InjectedCrash(BaseException):pass

@dataclass(frozen=True)
class AcquisitionResult:
    status:str
    receipt_id:str
    replayed:bool
    custody_mode:str

def acquire_live_page(session,request,transport,*,run_id,now,fault=None):
    session.verify('ingest');cap=session.capability;g=cap.grant
    deadline=session.started+g.deadline_seconds
    validate_request_capability(request,cap,deadline=deadline)
    if type(transport) not in (BoundedHttpTransport,FrozenLiveTransport):raise ValueError('TYPED_CUSTODY_TRANSPORT_REQUIRED')
    if type(run_id) is not str or not 1<=len(run_id)<=64 or fault not in (None,'after_reserve','after_raw','after_observations','after_commit'):
        raise ValueError('BOUNDED_LIVE_RUN_REQUIRED')
    current=cap.verify('ingest')
    if utc(now)>current or (current-utc(now)).total_seconds()>1:raise ValueError('LIVE_OPERATION_TIME_DRIFT')
    stamp=utc(now).isoformat().replace('+00:00','Z');gsha=g.artifact_sha
    mode='SIMULATED_HTTP' if session.target_kind=='FIXTURE_TEST' else 'HTTP'
    with session.connect(readonly=False,operation='ingest') as (db,verify):
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        try:
            if session.target_kind=='INSTALLED_DB' or db.execute("SELECT 1 FROM sqlite_master WHERE name='live_schema_migrations'").fetchone():ledger.assert_live_schema(db)
            else:ledger.initialize_live(db,session=session,now=stamp)
            grant_body=json.dumps(g.public,sort_keys=True,separators=(',',':'))
            db.execute("INSERT OR IGNORE INTO live_authorizations VALUES(?,?,0,0,'{}')",(gsha,grant_body))
            if db.execute('SELECT grant_json FROM live_authorizations WHERE grant_sha=?',(gsha,)).fetchone()!=(grant_body,):raise ValueError('LIVE_GRANT_DRIFT')
            db.execute('INSERT OR IGNORE INTO live_runs VALUES(?,?,?)',(run_id,gsha,g.plan_sha))
            if db.execute('SELECT grant_sha,plan_sha FROM live_runs WHERE run_id=?',(run_id,)).fetchone()!=(gsha,g.plan_sha):raise ValueError('LIVE_RUN_POLICY_DRIFT')
            ledger.audit_live(db,session=session,grant_sha=gsha)
            previous=db.execute("SELECT receipt_id,status,custody_mode FROM live_requests WHERE grant_sha=? AND request_sha=? AND status IN ('ok','empty') ORDER BY attempt DESC LIMIT 1",(gsha,request.sha256)).fetchone()
            if previous:
                session.verify('ingest');verify();db.commit()
                return AcquisitionResult(previous[1],previous[0],True,previous[2])
            provider=request.specs[0].provider
            prior=db.execute("SELECT request_json,http_status,status FROM live_requests WHERE grant_sha=? ORDER BY rowid DESC",(gsha,))
            failures=0
            for text,code,status in prior:
                if json.loads(text)['specs'][0]['provider']!=provider:continue
                if code in (403,429):raise ValueError('LIVE_PROVIDER_CIRCUIT_OPEN')
                if status in ('ok','empty'):break
                if status=='failed':failures+=1
                if failures>=3:raise ValueError('LIVE_PROVIDER_CIRCUIT_OPEN')
            total,dp,hist=db.execute('SELECT reserved_requests,reserved_dp,attempts_json FROM live_authorizations WHERE grant_sha=?',(gsha,)).fetchone()
            history=json.loads(hist);attempt=history.get(request.sha256,0)+1
            if total>=g.max_requests or dp+request.max_dp>g.max_dp or attempt>g.max_attempts:raise ValueError('LIVE_GRANT_BUDGET_EXHAUSTED')
            for s in request.specs:
                if request.direction=='bounded_window':continue
                previous=db.execute("SELECT frontier_utc FROM live_cursors WHERE provider=? AND metric=? AND symbol=? AND frequency=? AND direction=? AND scope='continuation'",(s.provider,s.metric,s.symbol,s.frequency,request.direction)).fetchone()
                required=request.end if request.direction=='backward' else request.start
                if previous and previous!=(required,):raise ValueError('LIVE_CURSOR_CONTINUITY_REQUIRED')
            rid=digest([gsha,run_id,request.sha256,attempt]);text=json.dumps(asdict(request),sort_keys=True,separators=(',',':'))
            db.execute("INSERT INTO live_requests VALUES(?,?,?,?,?,?,?,'reserved',?,NULL,?,NULL,NULL,NULL,'[]')",(rid,gsha,run_id,request.sha256,text,attempt,request.max_dp,mode,stamp))
            history[request.sha256]=attempt
            db.execute('UPDATE live_authorizations SET reserved_requests=reserved_requests+1,reserved_dp=reserved_dp+?,attempts_json=? WHERE grant_sha=?',(request.max_dp,json.dumps(history,sort_keys=True,separators=(',',':')),gsha))
            session.verify('ingest');verify();db.commit()
        except BaseException:db.rollback();raise
        if fault=='after_reserve':raise InjectedCrash(fault)
        session.verify('ingest');verify()
        if db.in_transaction:raise ValueError('LIVE_HTTP_UNDER_WRITE_TRANSACTION')
        result=transport.fetch(request,cap,deadline=deadline)
        if result.custody_mode!=mode:raise ValueError('LIVE_CUSTODY_MODE_DRIFT')
        rawsha=hashlib.sha256(result.body).hexdigest() if result.body is not None else None
        points=();error=None
        if result.http_status!=200:status='failed';error='HTTP_'+str(result.http_status) if result.http_status is not None else 'TRANSPORT_UNCERTAIN'
        else:
            try:points=normalize_live_page(request,result.body,captured_utc=result.received_utc);status='ok' if points else 'empty'
            except (ValueError,TypeError,KeyError,IndexError,OverflowError):status='rejected';error='SOURCE_CONTRACT_REJECTED'
        session.verify('ingest');verify();db.execute('BEGIN IMMEDIATE')
        try:
            ledger.assert_live_schema(db)
            if rawsha is not None:
                db.execute('INSERT OR IGNORE INTO live_raw_payloads VALUES(?,?)',(rawsha,result.body))
                if db.execute('SELECT body FROM live_raw_payloads WHERE sha256=?',(rawsha,)).fetchone()!=(result.body,):raise ValueError('LIVE_RAW_COLLISION')
            if fault=='after_raw':raise InjectedCrash(fault)
            if points:db.executemany('INSERT INTO live_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ledger.observation(p,rid) for p in points))
            if fault=='after_observations':raise InjectedCrash(fault)
            db.execute('UPDATE live_requests SET status=?,http_status=?,attempted_utc=?,received_utc=?,raw_sha=?,error_class=?,metadata_json=? WHERE receipt_id=? AND status=\'reserved\'',
                (status,result.http_status,result.attempted_utc,result.received_utc,rawsha,error,json.dumps(result.response_metadata),rid))
            if status in ('ok','empty'):
                stamps=[p.timestamp for p in points]
                db.execute('INSERT INTO live_coverage VALUES(?,?,?,?,?,?,?)',(rid,request.start,request.end,min(stamps) if stamps else None,max(stamps) if stamps else None,'RECEIVED_WINDOW' if points else 'EMPTY_NOT_COMPLETENESS','NOT_CHECKED'))
                for row in ledger.cursor_rows(request,points,rid):
                    db.execute('INSERT INTO live_cursors VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(provider,metric,symbol,frequency,direction,scope) DO UPDATE SET frontier_utc=excluded.frontier_utc,receipt_id=excluded.receipt_id',row)
            ledger.audit_live(db,session=session,grant_sha=gsha);session.verify('ingest');verify();db.commit()
        except BaseException:db.rollback();raise
        if fault=='after_commit':raise InjectedCrash(fault)
        return AcquisitionResult(status,rid,False,mode)
