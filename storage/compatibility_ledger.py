"""Additive evidence ledger; caller owns explicit transaction and permissions.

Real transport is a distinct FUTURE storage contract, never a runtime binding.
This offline integration audits FAKE_HTTP only and never labels it real.
"""
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import sqlite3

from analysis.package_b_evidence import EvidencePoint, EvidenceSeries, canonical_digest
from ingestion.package_a_contracts import utc
from ingestion.package_a_legacy_bridge import reconciled_fixture_points
from storage.installed_schema import (ADDON_TABLES, columns, indexes, foreign_keys,
    inspect_installed_schema, legacy_fingerprint)

DDL=(
'''CREATE TABLE compat_schema_migrations(version INTEGER PRIMARY KEY,applied_utc TEXT NOT NULL)''',
'''CREATE TABLE compat_raw_payloads(sha256 TEXT PRIMARY KEY,body BLOB NOT NULL)''',
'''CREATE TABLE compat_runs(run_id TEXT PRIMARY KEY,plan_sha256 TEXT NOT NULL,policy_json TEXT NOT NULL)''',
'''CREATE TABLE compat_requests(
 receipt_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES compat_runs(run_id),
 request_sha256 TEXT NOT NULL,request_json TEXT NOT NULL,attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 3),
 transport TEXT NOT NULL CHECK(transport IN ('FAKE_HTTP','HTTP')),
 mode TEXT NOT NULL CHECK(mode IN ('OFFLINE_PROVIDER_CONTRACT','REAL_ACQUISITION')),
 status TEXT NOT NULL CHECK(status IN ('reserved','ok','empty','failed','rejected')),
 http_status INTEGER,captured_utc TEXT NOT NULL,raw_sha256 TEXT REFERENCES compat_raw_payloads(sha256),
 error_class TEXT,UNIQUE(run_id,request_sha256,attempt),
 CHECK((transport='FAKE_HTTP' AND mode='OFFLINE_PROVIDER_CONTRACT') OR (transport='HTTP' AND mode='REAL_ACQUISITION')),
 CHECK(status NOT IN ('ok','empty') OR (raw_sha256 IS NOT NULL AND http_status=200)))''',
'''CREATE TABLE compat_observations(
 receipt_id TEXT NOT NULL REFERENCES compat_requests(receipt_id),provider TEXT NOT NULL,
 metric TEXT NOT NULL,symbol TEXT NOT NULL,frequency TEXT NOT NULL,observed_utc TEXT NOT NULL,
 dimension_key TEXT NOT NULL,value REAL NOT NULL,unit TEXT NOT NULL,raw_scale TEXT NOT NULL,row_sha256 TEXT NOT NULL,
 PRIMARY KEY(receipt_id,provider,metric,symbol,frequency,observed_utc,dimension_key))''',
'''CREATE TABLE compat_cursors(
 provider TEXT NOT NULL,metric TEXT NOT NULL,symbol TEXT NOT NULL,frequency TEXT NOT NULL,
 direction TEXT NOT NULL CHECK(direction='forward'),end_exclusive_utc TEXT NOT NULL,
 advanced_by_receipt TEXT NOT NULL REFERENCES compat_requests(receipt_id),
 PRIMARY KEY(provider,metric,symbol,frequency,direction))''',
'''CREATE INDEX idx_compat_observations_asof ON compat_observations(provider,metric,symbol,frequency,observed_utc)''')


def _sql(conn):
    return {r[0]:r[1] for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'compat_%' OR name='idx_compat_observations_asof'")}


def assert_schema(conn):
    with sqlite3.connect(':memory:') as expected:
        for sql in DDL: expected.execute(sql)
        if _sql(conn)!=_sql(expected): raise ValueError('ADDON_SCHEMA_CONTRACT_DRIFT')
        for name in ADDON_TABLES:
            if (columns(conn,name)!=columns(expected,name) or indexes(conn,name)!=indexes(expected,name) or
                foreign_keys(conn,name)!=foreign_keys(expected,name)): raise ValueError('ADDON_SCHEMA_CONTRACT_DRIFT')
    if conn.execute('SELECT version FROM compat_schema_migrations ORDER BY version').fetchall()!=[(1,)]:
        raise ValueError('ADDON_SCHEMA_VERSION_DRIFT')


def initialize(conn,*,now,fault=None):
    if not conn.in_transaction: raise ValueError('EXPLICIT_MIGRATION_TRANSACTION_REQUIRED')
    utc(now);structure=inspect_installed_schema(conn)
    if structure['addon_tables']:
        assert_schema(conn); return
    before=legacy_fingerprint(conn)
    for i,sql in enumerate(DDL):
        conn.execute(sql)
        if fault=='after_first_ddl' and i==0: raise ValueError('INJECTED_MIGRATION_CRASH')
    conn.execute('INSERT INTO compat_schema_migrations VALUES(1,?)',(now,))
    assert_schema(conn)
    if legacy_fingerprint(conn)!=before: raise ValueError('LEGACY_CHANGED_DURING_MIGRATION')


def observation(spec,point,receipt_id):
    core={'provider':spec.provider,'metric':spec.metric,'symbol':spec.symbol,'frequency':spec.frequency,
        'observed_utc':point.timestamp,'dimension_key':point.dimension,'value':point.value,
        'unit':spec.unit,'raw_scale':spec.raw_scale}
    return (receipt_id,*core.values(),canonical_digest(core))


def validated_points(request,spec,payload,*,now):
    points=reconciled_fixture_points(spec,payload,start=request.start,end=request.end,now=now)
    if points and (spec.provider in ('researchbitcoin','bitview') or
                   spec.provider=='yahoo' and spec.symbol=='BTC-USD'):
        begin,end=utc(request.start),utc(request.end);step=dt.timedelta(days=1)
        count=(end-begin)/step
        if count!=int(count) or count>10000: raise ValueError('DAILY_GRID_WINDOW_INVALID')
        if [utc(p.timestamp) for p in points]!=[begin+i*step for i in range(int(count))]:
            raise ValueError('DAILY_GRID_GAP_OR_PARTIAL_PAGE')
    return points


def audit(conn):
    assert_schema(conn)
    if conn.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('ADDON_FOREIGN_KEY_FAILURE')
    from ingestion.compatibility_acquisition import request_from_dict
    count=0
    for rid,run_id,request_sha,body,attempt,transport,mode,status,http,captured,raw_sha,error in conn.execute('SELECT * FROM compat_requests'):
        count+=1
        if count>256: raise ValueError('OFFLINE_RECEIPT_AUDIT_BUDGET')
        request=request_from_dict(json.loads(body))
        if request.sha256!=request_sha or rid!=canonical_digest([run_id,request_sha,attempt]):
            raise ValueError('REQUEST_IDENTITY_MISMATCH')
        utc(captured)
        if transport!='FAKE_HTTP' or mode!='OFFLINE_PROVIDER_CONTRACT':
            raise ValueError('REAL_TRANSPORT_NOT_AUTHORIZED')
        actual=[tuple(row) for row in conn.execute('SELECT * FROM compat_observations WHERE receipt_id=? ORDER BY provider,metric,symbol,frequency,observed_utc,dimension_key',(rid,))]
        if status=='reserved':
            if raw_sha is not None or http is not None or error is not None or actual:
                raise ValueError('RESERVED_ATTEMPT_EVIDENCE_DRIFT')
            continue
        raw=None
        if raw_sha is not None:
            row=conn.execute('SELECT body FROM compat_raw_payloads WHERE sha256=?',(raw_sha,)).fetchone()
            if not row or type(row[0]) is not bytes or hashlib.sha256(row[0]).hexdigest()!=raw_sha:
                raise ValueError('ADDON_RAW_SHA_MISMATCH')
            raw=row[0]
        if status in ('ok','empty'):
            expected=[]
            payload=json.loads(raw)
            for spec in request.specs:
                points=validated_points(request,spec,payload,now=captured)
                expected.extend(observation(spec,p,rid) for p in points)
            if (actual!=sorted(expected,key=lambda r:r[1:7]) or
                status!=('ok' if expected else 'empty') or http!=200 or error is not None):
                raise ValueError('OBSERVATION_REQUEST_RAW_RECONCILIATION')
        elif status in ('failed','rejected'):
            if actual or not error or (status=='failed' and http==200) or (status=='rejected' and http!=200):
                raise ValueError('FAILED_RECEIPT_EVIDENCE_DRIFT')
        else: raise ValueError('RECEIPT_STATUS_INVALID')
    # Cursor truth is checked against receipt identity/window and stream data.
    for provider,metric,symbol,freq,direction,end,rid in conn.execute('SELECT * FROM compat_cursors'):
        row=conn.execute('SELECT request_json,status FROM compat_requests WHERE receipt_id=?',(rid,)).fetchone()
        request=request_from_dict(json.loads(row[0])) if row else None
        if (not row or row[1]!='ok' or request.end!=end or direction!='forward' or
            (provider,metric,symbol,freq) not in {(s.provider,s.metric,s.symbol,s.frequency) for s in request.specs} or
            not conn.execute('SELECT 1 FROM compat_observations WHERE receipt_id=? AND provider=? AND metric=? AND symbol=? AND frequency=?',
                (rid,provider,metric,symbol,freq)).fetchone()): raise ValueError('CURSOR_RECEIPT_MISMATCH')
    return {'state':'SIMULATED_ACQUISITIONS_RECONCILED','receipts':count,
        'transport':'FAKE_HTTP','production_authorized':False,'external_history_verified':False}


def read_evidence(conn,legacy,*,asof,cutoff):
    audit(conn)
    # Each revision retains its own receipt; choose the latest eligible capture.
    grouped={s.key:{p.observed_utc:p for p in s.points} for s in legacy}
    configs={s.key:(s.unit,s.raw_scale) for s in legacy}
    rows=conn.execute('SELECT o.*,r.request_sha256,r.captured_utc FROM compat_observations o JOIN compat_requests r '
        'ON o.receipt_id=r.receipt_id WHERE r.status=\'ok\' ORDER BY o.provider,o.metric,o.symbol,o.frequency,o.observed_utc,o.dimension_key,r.captured_utc,r.receipt_id').fetchall()
    if len(rows)>50000: raise ValueError('OFFLINE_ADDITIVE_EVIDENCE_BUDGET')
    for rid,provider,metric,symbol,freq,stamp,dimension,value,unit,scale,row_sha,request_sha,captured in rows:
        if utc(stamp)>=cutoff or utc(captured)>asof: continue
        if provider=='fred' and dt_date(dimension)>asof.date(): continue
        key=(provider,metric,symbol,freq)
        if key in configs and configs[key]!=(unit,scale): raise ValueError('MIXED_LEGACY_ADDITIVE_SOURCE_UNITS')
        configs[key]=(unit,scale)
        raw_sha=conn.execute('SELECT raw_sha256 FROM compat_requests WHERE receipt_id=?',(rid,)).fetchone()[0]
        point=EvidencePoint(stamp,dimension,value,row_sha,rid,raw_sha,request_sha,captured,
            'SIMULATED_REQUEST_VERIFIED','compat_observations','RECORDED_CAPTURE')
        previous=grouped.setdefault(key,{}).get(stamp)
        if previous is None or previous.provenance.startswith('LEGACY') or (dimension,captured)>=(previous.dimension,previous.captured_utc):
            grouped[key][stamp]=point
    return [EvidenceSeries(*key,*configs[key],tuple(points[t] for t in sorted(points)),False)
        for key,points in sorted(grouped.items()) if points]


def dt_date(value):
    import datetime as dt
    return dt.date.fromisoformat(value)
