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
'''CREATE TABLE compat_runs(run_id TEXT PRIMARY KEY,plan_sha256 TEXT NOT NULL,policy_json TEXT NOT NULL,
 reserved_total INTEGER NOT NULL DEFAULT 0 CHECK(reserved_total BETWEEN 0 AND 128),
 attempts_json TEXT NOT NULL DEFAULT '{}')''',
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
    count=0;histories={};providers={};frontiers={}
    for rid,run_id,request_sha,body,attempt,transport,mode,status,http,captured,raw_sha,error in conn.execute('SELECT * FROM compat_requests'):
        count+=1
        if count>256: raise ValueError('OFFLINE_RECEIPT_AUDIT_BUDGET')
        request=request_from_dict(json.loads(body))
        if request.sha256!=request_sha or rid!=canonical_digest([run_id,request_sha,attempt]):
            raise ValueError('REQUEST_IDENTITY_MISMATCH')
        utc(captured)
        histories.setdefault(run_id,{}).setdefault(request_sha,[]).append((attempt,status))
        provider=request.specs[0].provider
        providers.setdefault(run_id,{}).setdefault(provider,0)
        providers[run_id][provider]+=1
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
                if points:
                    key=(spec.provider,spec.metric,spec.symbol,spec.frequency,'forward')
                    frontiers.setdefault(key,[]).append((utc(request.start),utc(request.end),request.end,rid))
            if (actual!=sorted(expected,key=lambda r:r[1:7]) or
                status!=('ok' if expected else 'empty') or http!=200 or error is not None):
                raise ValueError('OBSERVATION_REQUEST_RAW_RECONCILIATION')
        elif status in ('failed','rejected'):
            if actual or not error or (status=='failed' and http==200) or (status=='rejected' and http!=200):
                raise ValueError('FAILED_RECEIPT_EVIDENCE_DRIFT')
        else: raise ValueError('RECEIPT_STATUS_INVALID')
    # Durable high-water counters are independent of surviving receipt rows.
    # Missing even the LAST/all reserved attempts must not refund quota.
    for run_id,plan_sha,policy_text,reserved_total,counters in conn.execute('SELECT * FROM compat_runs'):
        policy=json.loads(policy_text);recorded=json.loads(counters)
        if (type(policy) is not dict or set(policy)!={'max_requests','max_attempts','provider_budgets'} or
            type(policy['max_requests']) is not int or not 1<=policy['max_requests']<=128 or
            type(policy['max_attempts']) is not int or not 1<=policy['max_attempts']<=3 or
            type(policy['provider_budgets']) is not dict or
            any(p not in ('yahoo','fred','bitview','researchbitcoin','binance','bybit') or
                type(n) is not int or not 1<=n<=128 for p,n in policy['provider_budgets'].items()) or
            type(recorded) is not dict or type(reserved_total) is not int or
            not isinstance(plan_sha,str) or len(plan_sha)!=64):
            raise ValueError('RUN_ATTEMPT_POLICY_DRIFT')
        observed=histories.get(run_id,{})
        if (any(type(k) is not str or len(k)!=64 or type(n) is not int or
                not 1<=n<=policy['max_attempts'] for k,n in recorded.items()) or
            set(recorded)!=set(observed) or reserved_total!=sum(recorded.values()) or
            reserved_total!=sum(len(rows) for rows in observed.values()) or
            reserved_total>policy['max_requests']):
            raise ValueError('DURABLE_ATTEMPT_HISTORY_OR_BUDGET_DRIFT')
        for sha,n in recorded.items():
            attempts=sorted(observed[sha])
            if ([a for a,_ in attempts]!=list(range(1,n+1)) or
                sum(status in ('ok','empty') for _,status in attempts)>1):
                raise ValueError('DURABLE_ATTEMPT_SEQUENCE_DRIFT')
        if any(n>policy['provider_budgets'].get(p,128) for p,n in providers.get(run_id,{}).items()):
            raise ValueError('DURABLE_PROVIDER_BUDGET_DRIFT')
    # Reconstruct the COMPLETE cursor set and final frontier, including
    # continuity, from successful raw-reconciled receipts, not existing rows.
    expected_cursors={}
    for key,windows in frontiers.items():
        windows.sort()
        for previous,current in zip(windows,windows[1:]):
            if current[0]!=previous[1]: raise ValueError('CURSOR_RECEIPT_CONTINUITY_DRIFT')
        expected_cursors[key]=(windows[-1][2],windows[-1][3])
    actual_cursors={tuple(row[:5]):tuple(row[5:]) for row in conn.execute('SELECT * FROM compat_cursors')}
    if actual_cursors!=expected_cursors:
        raise ValueError('CURSOR_SET_OR_FRONTIER_DRIFT')
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
