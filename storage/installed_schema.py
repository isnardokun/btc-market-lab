"""Observed installed structural contract. No DB path, connection or writes.

The exported profile is intentionally NOT a complete production DDL dump.
Caller owns the connection and snapshot. Fingerprints include actual schema
SQL so preservation checks also cover unexported CHECKs/defaults/triggers.
"""
import hashlib
import json
from pathlib import Path

ADDON_TABLES = frozenset(('compat_schema_migrations','compat_raw_payloads',
    'compat_requests','compat_observations','compat_cursors','compat_runs'))
ADDON_INDEXES = frozenset(('idx_compat_observations_asof',))
MAX_ROWS = 100000


def profile():
    return json.loads(Path(__file__).with_name('installed_schema_profile.json').read_text())


def quote(name):
    if not isinstance(name,str) or not name: raise ValueError('IDENTIFIER_REQUIRED')
    return '"'+name.replace('"','""')+'"'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def columns(conn,name):
    return [{'name':r[1],'type':r[2],'notnull':bool(r[3]),
             'has_default':r[4] is not None,'pk_position':r[5]}
            for r in conn.execute('PRAGMA table_info('+quote(name)+')')]


def indexes(conn,name):
    return sorted([{'name':r[1],'unique':bool(r[2]),'origin':r[3],'partial':bool(r[4]),
        'columns':[c[2] for c in conn.execute('PRAGMA index_info('+quote(r[1])+')')]}
        for r in conn.execute('PRAGMA index_list('+quote(name)+')')],key=lambda r:r['name'])


def foreign_keys(conn,name):
    # SQLite's FK IDs are enumeration order, not constraint identity.
    return sorted([(r[2],r[3],r[4],r[5],r[6]) for r in
        conn.execute('PRAGMA foreign_key_list('+quote(name)+')')])


def inspect_installed_schema(conn):
    expected=profile()
    actual=[{'type':r[0],'name':r[1],'table':r[2]} for r in conn.execute(
        "SELECT type,name,tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")]
    known={o['name']:o for o in expected['objects']}
    legacy=[o for o in actual if o['name'] in known]
    extra=[o for o in actual if o['name'] not in known]
    if (sorted(legacy,key=lambda o:o['name'])!=sorted(known.values(),key=lambda o:o['name']) or
        any(not ((o['type']=='table' and o['name'] in ADDON_TABLES) or
                 (o['type']=='index' and o['name'] in ADDON_INDEXES and o['table']=='compat_observations')) for o in extra)):
        raise ValueError('INSTALLED_OBJECT_DRIFT')
    for name,table in expected['tables'].items():
        if columns(conn,name)!=table['columns']: raise ValueError('INSTALLED_SCHEMA_DRIFT:'+name)
        if indexes(conn,name)!=sorted(table['indexes'],key=lambda i:i['name']):
            raise ValueError('INSTALLED_INDEX_DRIFT:'+name)
        wanted=sorted((f['table'],f['from'],f['to'],f['on_update'],f['on_delete']) for f in table['foreign_keys'])
        if foreign_keys(conn,name)!=wanted: raise ValueError('INSTALLED_FK_DRIFT:'+name)
        if 'integer_versions' in table:
            versions=[r[0] for r in conn.execute('SELECT version FROM '+quote(name)+' ORDER BY version')]
            if versions!=table['integer_versions']: raise ValueError('INSTALLED_MIGRATION_VERSION_DRIFT:'+name)
    return {'state':'INSTALLED_STRUCTURE_MATCH','legacy_tables':len(expected['tables']),
        'objects':len(known),'addon_tables':sorted(o['name'] for o in extra if o['type']=='table'),
        'scope':expected['scope'],'profile_snapshot_sha256':expected['snapshot_sha256']}


def legacy_fingerprint(conn):
    names=sorted(profile()['tables'])
    layout=[tuple(r) for r in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")
        if r[2] in names]
    tables={}
    for name in names:
        pk=[c['name'] for c in sorted(columns(conn,name),key=lambda c:c['pk_position']) if c['pk_position']]
        order=','.join(quote(n) for n in pk) if pk else 'rowid'
        cursor=conn.execute('SELECT * FROM '+quote(name)+' ORDER BY '+order+' LIMIT ?', (MAX_ROWS+1,))
        sha=hashlib.sha256();count=0
        for row in cursor:
            count+=1
            if count>MAX_ROWS: raise ValueError('OFFLINE_LEGACY_FINGERPRINT_ROW_BUDGET')
            value=[{'bytes_sha256':hashlib.sha256(x).hexdigest(),'size':len(x)} if isinstance(x,bytes) else x for x in row]
            sha.update(canonical(value)+b'\n')
        tables[name]={'rows':count,'sha256':sha.hexdigest()}
    return {'schema_sha256':hashlib.sha256(canonical(layout)).hexdigest(),'tables':tables}
