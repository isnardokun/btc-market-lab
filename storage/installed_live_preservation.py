"""Whole-history SHA proofs using bounded metadata and incremental large values.

Caller owns one anchored snapshot. Never merges chunks from moving snapshots,
never truncates a table and calls it complete. No path/network/DB defaults.
"""
from dataclasses import dataclass
import hashlib
import json
from storage.installed_schema import quote
from scripts.installed_live_policy import digest
from storage.installed_live_ledger import LIVE_TABLES

class IncompleteScan(ValueError):pass

@dataclass(frozen=True)
class CompleteFingerprint:
    schema_sha:str
    table_counts:dict
    table_shas:dict

def _scan(conn,*,batch_rows,batch_bytes,value_chunk_bytes,cancel):
    if (type(batch_rows) is not int or not 1<=batch_rows<=2048 or type(batch_bytes) is not int or not 1024<=batch_bytes<=8388608 or
        type(value_chunk_bytes) is not int or not 1<=value_chunk_bytes<=65536 or (cancel is not None and not callable(cancel))):
        raise ValueError('BOUNDED_FINGERPRINT_REQUIRED')
    def check():
        if cancel is not None and cancel():raise IncompleteScan('LEGACY_SCAN_INCOMPLETE')
    layout=[tuple(r) for r in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name") if r[2] not in LIVE_TABLES]
    tables=sorted(r[1] for r in layout if r[0]=='table');counts={};shas={}
    for name in tables:
        check();cols=list(conn.execute('PRAGMA table_info('+quote(name)+')'))
        pk=[r[1] for r in sorted(cols,key=lambda r:r[5]) if r[5]]
        rowid_name=next((alias for alias in ('_rowid_','rowid','oid') if alias not in {c[1] for c in cols}),None)
        if rowid_name is not None:
            try:conn.execute('SELECT '+quote(rowid_name)+' FROM '+quote(name)+' LIMIT 0')
            except Exception:rowid_name=None
        if rowid_name is not None:pk.append(rowid_name)
        if not pk:raise ValueError('LEGACY_PHYSICAL_KEY_UNAVAILABLE')
        if conn.execute('SELECT 1 FROM '+quote(name)+' WHERE '+ ' OR '.join('length(CAST('+quote(k)+' AS BLOB))>4096' for k in pk)+' LIMIT 1').fetchone():
            raise ValueError('LEGACY_KEY_BYTE_BUDGET')
        projection=','.join(quote(k) for k in pk)
        for col in cols:
            q=quote(col[1]);projection+=',typeof('+q+'),CASE WHEN typeof('+q+") IN ('blob','text') THEN length(CAST("+q+' AS BLOB)) ELSE '+q+' END'
        cursor=conn.execute('SELECT '+projection+' FROM '+quote(name)+' ORDER BY '+','.join(quote(k) for k in pk))
        size=min(batch_rows,max(1,batch_bytes//(len(pk)*8192+len(cols)*512+256)))
        sha=hashlib.sha256();count=0
        while True:
            check();batch=cursor.fetchmany(size)
            if not batch:break
            for row in batch:
                check();keys=row[:len(pk)];values=[]
                where=' AND '.join(quote(k)+' IS ?' for k in pk)
                for i,col in enumerate(cols):
                    kind,value=row[len(pk)+2*i:len(pk)+2*i+2]
                    if kind in ('blob','text'):
                        large=hashlib.sha256()
                        if rowid_name is not None:
                            with conn.blobopen(name,col[1],keys[-1],readonly=True) as blob:
                                if len(blob)!=value:raise IncompleteScan('LEGACY_VALUE_DRIFT')
                                while blob.tell()<value:
                                    check();large.update(blob.read(value_chunk_bytes))
                        else:
                            if value>batch_bytes:raise IncompleteScan('LEGACY_INCREMENTAL_VALUE_UNAVAILABLE')
                            for offset in range(1,value+1,value_chunk_bytes):
                                check();chunk=conn.execute('SELECT substr(CAST('+quote(col[1])+' AS BLOB),?,?) FROM '+quote(name)+' WHERE '+where,(offset,value_chunk_bytes,*keys)).fetchone()
                                if chunk is None or type(chunk[0]) is not bytes:raise IncompleteScan('LEGACY_VALUE_DRIFT')
                                large.update(chunk[0])
                        values.append({'type':kind,'bytes':value,'sha256':large.hexdigest()})
                    else:values.append({'type':kind,'value':value.hex() if type(value) is float else value})
                sha.update(json.dumps(values,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n');count+=1
        counts[name]=count;shas[name]=sha.hexdigest()
    check();return CompleteFingerprint(digest(layout),counts,shas)


def stream_legacy_fingerprint(conn,*,batch_rows=2048,batch_bytes=8388608,value_chunk_bytes=65536,cancel=None):
    owns_snapshot=not conn.in_transaction
    if owns_snapshot:conn.execute('BEGIN')
    try:return _scan(conn,batch_rows=batch_rows,batch_bytes=batch_bytes,value_chunk_bytes=value_chunk_bytes,cancel=cancel)
    finally:
        if owns_snapshot:conn.rollback()
