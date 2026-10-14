"""Typed live namespace. Audits reconstruct custody and durable global budgets."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import sqlite3
from ingestion.package_a_contracts import utc
from ingestion.installed_live_sources import request_from_dict,normalize_live_page
from scripts.installed_live_policy import digest

DDL=(
 'CREATE TABLE live_schema_migrations(version INTEGER PRIMARY KEY, applied_utc TEXT NOT NULL)',
 '''CREATE TABLE live_authorizations(grant_sha TEXT PRIMARY KEY,grant_json TEXT NOT NULL,
 reserved_requests INTEGER NOT NULL CHECK(reserved_requests>=0),reserved_dp INTEGER NOT NULL CHECK(reserved_dp>=0),attempts_json TEXT NOT NULL)''',
 '''CREATE TABLE live_runs(run_id TEXT PRIMARY KEY,grant_sha TEXT NOT NULL REFERENCES live_authorizations(grant_sha),plan_sha TEXT NOT NULL)''',
 'CREATE TABLE live_raw_payloads(sha256 TEXT PRIMARY KEY,body BLOB NOT NULL)',
 '''CREATE TABLE live_requests(receipt_id TEXT PRIMARY KEY,grant_sha TEXT NOT NULL REFERENCES live_authorizations(grant_sha),run_id TEXT NOT NULL REFERENCES live_runs(run_id),
 request_sha TEXT NOT NULL,request_json TEXT NOT NULL,attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 3),reserved_dp INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('reserved','ok','empty','failed','rejected')),custody_mode TEXT NOT NULL CHECK(custody_mode IN ('HTTP','SIMULATED_HTTP')),
 http_status INTEGER,attempted_utc TEXT NOT NULL,received_utc TEXT,raw_sha TEXT REFERENCES live_raw_payloads(sha256),error_class TEXT,metadata_json TEXT NOT NULL,
 UNIQUE(grant_sha,request_sha,attempt),CHECK(status NOT IN ('ok','empty') OR (http_status=200 AND raw_sha IS NOT NULL)))''',
 '''CREATE TABLE live_observations(receipt_id TEXT NOT NULL REFERENCES live_requests(receipt_id),provider TEXT NOT NULL,metric TEXT NOT NULL,symbol TEXT NOT NULL,
 frequency TEXT NOT NULL,observed_utc TEXT NOT NULL,dimension_key TEXT NOT NULL,value REAL NOT NULL,unit TEXT NOT NULL,raw_scale TEXT NOT NULL,row_sha TEXT NOT NULL,
 PRIMARY KEY(receipt_id,provider,metric,symbol,frequency,observed_utc,dimension_key))''',
 '''CREATE TABLE live_cursors(provider TEXT NOT NULL,metric TEXT NOT NULL,symbol TEXT NOT NULL,frequency TEXT NOT NULL,direction TEXT NOT NULL,
 scope TEXT NOT NULL,frontier_utc TEXT NOT NULL,receipt_id TEXT NOT NULL REFERENCES live_requests(receipt_id),
 PRIMARY KEY(provider,metric,symbol,frequency,direction,scope))''',
 '''CREATE TABLE live_coverage(receipt_id TEXT PRIMARY KEY REFERENCES live_requests(receipt_id),requested_start TEXT NOT NULL,requested_end TEXT NOT NULL,
 observed_first TEXT,observed_last TEXT,state TEXT NOT NULL,provider_availability TEXT NOT NULL)''',
 'CREATE INDEX idx_live_observations_asof ON live_observations(provider,metric,symbol,frequency,observed_utc)',
 'CREATE INDEX idx_live_requests_grant ON live_requests(grant_sha,request_sha,attempt)')

LIVE_TABLES=frozenset(('live_schema_migrations','live_authorizations','live_runs',
 'live_raw_payloads','live_requests','live_observations','live_cursors','live_coverage'))

def _layout(conn):return dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'live_%' OR name LIKE 'idx_live_%'"))

def assert_live_schema(conn):
    with closing(sqlite3.connect(':memory:')) as ref:
        for sql in DDL:ref.execute(sql)
        if _layout(conn)!=_layout(ref):raise ValueError('LIVE_ADDON_SCHEMA_DRIFT')
    if conn.execute('SELECT version FROM live_schema_migrations').fetchall()!=[(1,)]:raise ValueError('LIVE_SCHEMA_VERSION_DRIFT')

def initialize_live(conn,*,session,now,fault=None):
    session.verify('migrate')
    if not conn.in_transaction:raise ValueError('LIVE_EXPLICIT_TRANSACTION_REQUIRED')
    if _layout(conn):assert_live_schema(conn);return
    for i,sql in enumerate(DDL):
        conn.execute(sql)
        if i==0 and fault=='after_first_ddl':raise ValueError('INJECTED_LIVE_MIGRATION_CRASH')
    conn.execute('INSERT INTO live_schema_migrations VALUES(1,?)',(now,));assert_live_schema(conn)

def observation(p,rid):
    s=p.spec;core=(s.provider,s.metric,s.symbol,s.frequency,p.timestamp,p.dimension,p.value,s.unit,s.raw_scale)
    return (rid,*core,digest(core))

def cursor_rows(request,points,rid):
    output=[]
    for s in request.specs:
        series=[p for p in points if p.spec==s]
        if not series:continue
        scope=request.sha256 if request.direction=='bounded_window' else 'continuation'
        frontier=request.start if request.direction=='backward' else request.end
        output.append((s.provider,s.metric,s.symbol,s.frequency,request.direction,scope,frontier,rid))
    return output

def _related_receipt(conn,request,s,*,before=None):
    if request.direction=='bounded_window':
        row=conn.execute("SELECT receipt_id,request_json FROM live_requests WHERE request_sha=? AND status='ok' ORDER BY rowid DESC LIMIT 1",(request.sha256,)).fetchone()
        return (row[0],request_from_dict(json.loads(row[1]))) if row else None
    extra=' AND r.rowid<?' if before is not None else ''
    values=(s.provider,s.metric,s.symbol,s.frequency)+((before,) if before is not None else ())
    rows=conn.execute("SELECT DISTINCT r.rowid,r.receipt_id,r.request_json FROM live_requests r JOIN live_observations o ON o.receipt_id=r.receipt_id WHERE o.provider=? AND o.metric=? AND o.symbol=? AND o.frequency=? AND r.status='ok'"+extra+' ORDER BY r.rowid DESC',values)
    for _,rid,body in rows:
        candidate=request_from_dict(json.loads(body))
        if candidate.direction==request.direction:return rid,candidate
    return None


def _audit_live(conn,*,session,grant_sha=None):
    session.verify('read');assert_live_schema(conn)
    if conn.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('LIVE_FK_VIOLATION')
    receipts=0;cursor_count=0;mode=None
    auth_rows=conn.execute('SELECT * FROM live_authorizations'+(' WHERE grant_sha=?' if grant_sha else '')+' ORDER BY grant_sha',((grant_sha,) if grant_sha else ()))
    for gsha,grant_body,n,dp,hist in auth_rows:
        grant=json.loads(grant_body);history=json.loads(hist)
        if digest(grant)!=gsha:raise ValueError('LIVE_GRANT_JSON_DRIFT')
        if grant['target_kind']!=session.target_kind or grant['db']!=str(session.db) or grant['owner_uid']!=session.capability.grant.owner_uid:
            raise ValueError('LIVE_GRANT_TARGET_DRIFT')
        if n>grant['max_requests'] or n>128 or dp>grant['max_dp']:raise ValueError('LIVE_DURABLE_HISTORY_OR_BUDGET_DRIFT')
        count=total_dp=0;attempts={}
        rows=conn.execute('SELECT rowid,* FROM live_requests WHERE grant_sha=? ORDER BY rowid',(gsha,))
        for row in rows:
            session.verify('read');receipts+=1;count+=1
            rowid,rid,_,run,rsha,body,attempt,reserved,status,custody,code,started,captured,rawsha,error,metadata=row
            if count>128:raise ValueError('LIVE_DURABLE_HISTORY_OR_BUDGET_DRIFT')
            request=request_from_dict(json.loads(body))
            if rsha!=request.sha256 or rid!=digest([gsha,run,rsha,attempt]) or reserved!=request.max_dp:raise ValueError('LIVE_REQUEST_IDENTITY_DRIFT')
            expected='SIMULATED_HTTP' if grant['target_kind']=='FIXTURE_TEST' else 'HTTP'
            if custody!=expected:raise ValueError('LIVE_CUSTODY_MODE_DRIFT')
            if rsha not in {w['request_sha'] for w in grant['source_windows']}:raise ValueError('LIVE_UNAPPROVED_REQUEST')
            if conn.execute('SELECT grant_sha,plan_sha FROM live_runs WHERE run_id=?',(run,)).fetchone()!=(gsha,grant['plan_sha']):raise ValueError('LIVE_RUN_POLICY_DRIFT')
            total_dp+=reserved;previous=attempts.get(rsha,0)
            if attempt!=previous+1:raise ValueError('LIVE_ATTEMPT_HISTORY_DRIFT')
            attempts[rsha]=attempt
            if not utc(grant['issued_utc'])<=utc(started)<utc(grant['expires_utc']):raise ValueError('LIVE_RECEIPT_TIME_DRIFT')
            if captured is not None and (utc(captured)<utc(started) or utc(captured)>=utc(grant['expires_utc'])):raise ValueError('LIVE_CAPTURE_TIME_DRIFT')
            raw=None
            if rawsha is not None:
                found=conn.execute('SELECT body FROM live_raw_payloads WHERE sha256=?',(rawsha,)).fetchone()
                if not found or type(found[0]) is not bytes or hashlib.sha256(found[0]).hexdigest()!=rawsha:raise ValueError('LIVE_RAW_SHA_DRIFT')
                raw=found[0]
                if len(raw)>grant['max_raw_bytes']:raise ValueError('LIVE_RAW_BODY_BUDGET')
            actual=[tuple(r) for r in conn.execute('SELECT * FROM live_observations WHERE receipt_id=? ORDER BY provider,metric,symbol,frequency,observed_utc,dimension_key',(rid,))]
            coverage=conn.execute('SELECT * FROM live_coverage WHERE receipt_id=?',(rid,)).fetchone()
            if status in ('ok','empty'):
                if code!=200 or raw is None or captured is None:raise ValueError('LIVE_SUCCESS_CUSTODY_INCOMPLETE')
                points=normalize_live_page(request,raw,captured_utc=captured)
                if actual!=sorted(observation(p,rid) for p in points) or (status=='ok')!=bool(points):raise ValueError('LIVE_RAW_OBSERVATION_DRIFT')
                stamps=[p.timestamp for p in points]
                wanted=(rid,request.start,request.end,min(stamps) if stamps else None,max(stamps) if stamps else None,'RECEIVED_WINDOW' if stamps else 'EMPTY_NOT_COMPLETENESS','NOT_CHECKED')
                if coverage!=wanted:raise ValueError('LIVE_COVERAGE_DRIFT')
                for cursor in cursor_rows(request,points,rid):
                    spec=next(s for s in request.specs if (s.provider,s.metric,s.symbol,s.frequency)==cursor[:4])
                    if request.direction!='bounded_window':
                        prior=_related_receipt(conn,request,spec,before=rowid)
                        required=request.end if request.direction=='backward' else request.start
                        if prior and (prior[1].start if request.direction=='backward' else prior[1].end)!=required:raise ValueError('LIVE_CURSOR_CONTINUITY_DRIFT')
                    latest=_related_receipt(conn,request,spec)
                    if latest and latest[0]==rid:
                        cursor_count+=1
                        stored=conn.execute('SELECT * FROM live_cursors WHERE provider=? AND metric=? AND symbol=? AND frequency=? AND direction=? AND scope=?',cursor[:6]).fetchone()
                        if stored!=cursor:raise ValueError('LIVE_COMPLETE_CURSOR_SET_DRIFT')
            elif actual or coverage:raise ValueError('LIVE_FAILED_REQUEST_HAS_ROWS')
            if status=='reserved' and (code is not None or captured is not None or raw is not None):raise ValueError('LIVE_RESERVED_CUSTODY_DRIFT')
            mode=custody if mode is None else mode if mode==custody else 'MIXED_EXPLICIT_CUSTODY'
        if count!=n or total_dp!=dp or attempts!=history or any(a>grant['max_attempts'] for a in attempts.values()):raise ValueError('LIVE_DURABLE_HISTORY_OR_BUDGET_DRIFT')
    if grant_sha is None and conn.execute('SELECT COUNT(*) FROM live_cursors').fetchone()[0]!=cursor_count:raise ValueError('LIVE_COMPLETE_CURSOR_SET_DRIFT')
    return {'state':'LIVE_PRIVATE_ACQUISITION_RECONCILED' if mode=='HTTP' else 'SIMULATED_LIVE_BINDING_RECONCILED',
      'receipts':receipts,'custody_mode':mode,'historical_completeness':'NOT_VERIFIED','production_publication_authorized':False}


def audit_live(conn,*,session,grant_sha=None):
    own=not conn.in_transaction
    if own:conn.execute('BEGIN')
    try:return _audit_live(conn,session=session,grant_sha=grant_sha)
    finally:
        if own:conn.rollback()


def plan_source_backfill(coverage,capability,*,source_contract):
    if coverage.get('provider_availability') not in ('AVAILABLE_VERIFIED','PARTIAL_VERIFIED'):
        return {'state':'BLOCKED_EXTERNAL_AVAILABILITY','requests':[],'historical_completeness':'NOT_VERIFIED'}
    return {'state':'BOUNDED_BACKFILL_REQUIRES_APPROVED_WINDOWS','requests':[asdict(w) for w in capability.grant.source_windows],
            'historical_completeness':'NOT_VERIFIED','source_contract':source_contract}
