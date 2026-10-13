"""Synthetic structural stand-in for exported metadata, NOT production DDL.

Unknown CHECK/default/trigger expressions are deliberately not reconstructed.
All values below are invented test inputs; no production rows or RAW are used.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from scripts.package_a_ingest_fixture import MARKER, MARKER_VALUE
from scripts import package_c_state as state

ASOF = '2026-10-12T00:05:00Z'
CAPTURE = '2026-10-12T00:00:00Z'


def metadata():
    path = Path(__file__).resolve().parents[2] / 'storage/installed_schema_profile.json'
    return json.loads(path.read_text())


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def create_schema(db):
    profile = metadata()
    for name, table in profile['tables'].items():
        columns = []
        pk = sorted((c['pk_position'], c['name']) for c in table['columns'] if c['pk_position'])
        integer_pk = len(pk) == 1 and next(c['type'] for c in table['columns'] if c['name'] == pk[0][1]) == 'INTEGER'
        for column in table['columns']:
            text = quote(column['name']) + ' ' + column['type']
            if integer_pk and column['pk_position']: text += ' PRIMARY KEY'
            if column['notnull']: text += ' NOT NULL'
            if column['has_default']: text += ' DEFAULT NULL'
            columns.append(text)
        if pk and not integer_pk: columns.append('PRIMARY KEY(' + ','.join(quote(n) for _, n in pk) + ')')
        for index in sorted(table['indexes'], key=lambda i:i['name']):
            if index['origin'] == 'u': columns.append('UNIQUE(' + ','.join(quote(n) for n in index['columns']) + ')')
        for fk in table['foreign_keys']:
            columns.append('FOREIGN KEY(' + quote(fk['from']) + ') REFERENCES ' + quote(fk['table']) + '(' + quote(fk['to']) + ')')
        db.execute('CREATE TABLE ' + quote(name) + '(' + ','.join(columns) + ')')
        for index in table['indexes']:
            if index['origin'] == 'c':
                db.execute('CREATE ' + ('UNIQUE ' if index['unique'] else '') + 'INDEX ' + quote(index['name']) + ' ON ' + quote(name) + '(' + ','.join(quote(n) for n in index['columns']) + ')')
    for obj in profile['objects']:
        if obj['type'] == 'trigger':
            db.execute('CREATE TRIGGER ' + quote(obj['name']) + ' AFTER UPDATE ON ' + quote(obj['table']) + ' BEGIN SELECT 1; END')
    db.execute("INSERT INTO archive_schema_migrations VALUES(1,?, 'SYNTHETIC')", (CAPTURE,))
    db.executemany('INSERT INTO market_context_migrations VALUES(?,?)', [(v, CAPTURE) for v in (1,2,3)])


def seed_legacy(db):
    db.execute("INSERT INTO series VALUES(1,'mvrv','1d','float','SYNTHETIC')")
    for day, value in [('2026-10-09', 80000), ('2026-10-10', 81000), ('2026-10-11', 80500)]:
        stamp = int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp())
        db.execute('INSERT INTO price_btc(ts,price) VALUES(?,?)', (stamp,value))
        db.execute('INSERT INTO daily(series_id,ts,value) VALUES(1,?,?)', (stamp,1.2))
    db.executemany('INSERT INTO archive_fred_vintages VALUES(?,?,?,?,?,?)', [
        ('DGS10','2026-10-08','2026-10-09',None,4.1,'2026-10-10T00:00:00Z'),
        ('DGS10','2026-10-08','2026-10-11',None,4.2,'2026-10-11T00:00:00Z'),
        ('DGS10','2026-10-08','2026-10-13',None,9.9,'2026-10-13T00:00:00Z')])
    db.execute("INSERT INTO macro_fred(series_id,date,value,quality_status,fetched_at) VALUES('DGS10','2026-10-08',4.3,'SYNTHETIC','2026-10-10T00:00:00Z')")
    db.execute("INSERT INTO onchain_external_observations VALUES('researchbitcoin','sopr_sth','2026-10-11','2026-10-11T00:00:00Z',1.01,'ratio','/SYNTHETIC','2026-10-12T00:00:00Z')")
    raw = b'[{"fundingRate":"0.0001","fundingTime":1791676800000,"symbol":"BTCUSDT"}]'
    sha = hashlib.sha256(raw).hexdigest()
    db.execute('INSERT INTO market_raw_payloads VALUES(?,?,?,?,?,?)', (sha,'binance','/fapi/v1/fundingRate',CAPTURE,'application/json',raw))
    db.execute('INSERT INTO market_derivatives VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('binance','BTCUSDT','funding_settled','2026-10-11T16:00:00Z','event',.0001,'fraction',None,'/fapi/v1/fundingRate',sha,CAPTURE))
    db.execute("INSERT INTO meta VALUES('fixture','SYNTHETIC_KEEP')")


def new_fixture(testcase, *, seed=True):
    temp = tempfile.TemporaryDirectory(prefix='package-a-fixture-installed-')
    testcase.addCleanup(temp.cleanup)
    root = Path(temp.name); root.chmod(0o700)
    locks = root/'locks'; locks.mkdir(mode=0o700)
    (root/'artifacts').mkdir(mode=0o700)
    (root/MARKER).write_text(MARKER_VALUE)
    from scripts.package_a_migration_rehearsal import MARKER as MIGRATION_MARKER, MARKER_TEXT
    (root/MIGRATION_MARKER).write_text(MARKER_TEXT)
    path = root/'package_a_synthetic.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        create_schema(db)
        if seed: seed_legacy(db)
    path.chmod(0o600)
    r=root.stat(); d=path.stat()
    (root/state.FLAG).write_bytes(state.encode({'version':1,'run_id':uuid.uuid4().hex,
        'root_identity':[r.st_dev,r.st_ino],'db_identity':[d.st_dev,d.st_ino]}))
    (root/state.STATE).write_bytes(state.encode({'body':{},'sha256':state.digest(state.encode({}))}))
    return root, path, locks
