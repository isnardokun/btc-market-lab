"""Guarded, window-filtered native/legacy evidence in one read snapshot."""
from collections import defaultdict
from dataclasses import replace
import datetime as dt
from analysis.package_b_evidence import EvidencePoint,EvidenceSeries,ReportSnapshot,snapshot_digest
from analysis.installed_evidence import LegacySelection,_point,_text
from ingestion.package_a_contracts import utc
from storage.installed_live_ledger import audit_live

BATCH_ROWS=2048
BATCH_BYTES=8388608
MAX_REPORT_POINTS=100000


def _bounded_rows(conn,sql,params):
    description=conn.execute(sql+' LIMIT 0',params)
    names=[c[0] for c in description.description];description.close()
    quote=lambda n:'"'+n.replace('"','""')+'"'
    sizes=['COALESCE(length(CAST('+quote(n)+' AS BLOB)),0)' for n in names]
    # SQL rejects large metadata without first returning it into Python.
    if conn.execute('SELECT 1 FROM ('+sql+') WHERE '+' OR '.join(x+'>4096' for x in sizes)+' LIMIT 1',params).fetchone():
        raise ValueError('EVIDENCE_SCALAR_FIELD_BUDGET')
    worst=conn.execute('SELECT COALESCE(MAX('+ '+'.join(sizes)+'),0) FROM ('+sql+')',params).fetchone()[0]+64*len(names)
    batch=min(BATCH_ROWS,max(1,BATCH_BYTES//max(1,worst)))
    cursor=conn.execute(sql,params)
    while True:
        rows=cursor.fetchmany(batch)
        if not rows:return
        if sum(len(str(v).encode()) for row in rows for v in row)>BATCH_BYTES:
            raise ValueError('EVIDENCE_BATCH_BYTE_BUDGET')
        for row in rows:yield dict(zip(names,row))


def _legacy_query(selection,start,end,asof):
    s=selection.spec;t=selection.table
    if t=='price_btc':
        return 'SELECT id,ts,price FROM price_btc WHERE ts>=? AND ts<? ORDER BY ts',(int(start.timestamp()),int(end.timestamp()))
    if t=='daily':
        return 'SELECT d.id,d.ts,d.value FROM daily d JOIN series s ON d.series_id=s.id WHERE s.name=? AND d.ts>=? AND d.ts<? ORDER BY d.ts',(selection.lookup,int(start.timestamp()),int(end.timestamp()))
    if t in ('macro_fred','macro_fred_old','archive_fred_vintages'):
        column='observed_date' if t=='archive_fred_vintages' else 'date'
        extra=' AND julianday(realtime_start)<=julianday(?)' if t=='archive_fred_vintages' else ''
        params=(selection.lookup,_text(start),_text(end))+((_text(asof),) if extra else ())
        return 'SELECT * FROM '+t+' WHERE series_id=? AND julianday('+column+')>=julianday(?) AND julianday('+column+')<julianday(?)'+extra+' ORDER BY '+column,params
    if t=='onchain_external_observations':
        return 'SELECT * FROM '+t+' WHERE provider=? AND metric=? AND julianday(observed_at_utc)>=julianday(?) AND julianday(observed_at_utc)<julianday(?) ORDER BY observed_at_utc',(s.provider,s.metric,_text(start),_text(end))
    return 'SELECT * FROM market_derivatives WHERE provider=? AND symbol=? AND metric=? AND interval_label=? AND julianday(observed_utc)>=julianday(?) AND julianday(observed_utc)<julianday(?) ORDER BY observed_utc',(s.provider,s.symbol,s.metric,s.frequency,_text(start),_text(end))


def read_live_snapshot(session,*,asof,snapshot_captured_utc,selections,start_utc,end_exclusive_utc):
    session.verify('read');generated=utc(asof);capture=utc(snapshot_captured_utc)
    start=utc(start_utc);end=utc(end_exclusive_utc);cutoff=generated.replace(hour=0,minute=0,second=0,microsecond=0)
    if not start<end<=cutoff:raise ValueError('COMPLETED_EVIDENCE_WINDOW_REQUIRED')
    if type(selections) is not tuple or any(type(s) is not LegacySelection for s in selections):raise ValueError('TYPED_LEGACY_SELECTIONS_REQUIRED')
    keys=[(s.spec.provider,s.spec.metric,s.spec.symbol,s.spec.frequency) for s in selections]
    if len(set(keys))!=len(keys):raise ValueError('AMBIGUOUS_LEGACY_STREAM_SELECTION')
    selected=defaultdict(dict);units=defaultdict(set);count=0
    def add(key,unit,scale,point,*,native=False):
        nonlocal count
        count+=1
        if count>MAX_REPORT_POINTS:raise ValueError('SELECTED_REPORT_POINT_BUDGET_NOT_COMPLETE')
        if utc(point.observed_utc)>=end or utc(point.observed_utc)<start or utc(point.captured_utc)>generated:return
        if key[0]=='fred' and point.dimension and dt.date.fromisoformat(point.dimension)>generated.date():return
        units[key].add((unit,scale));old=selected[key].get(point.observed_utc)
        # A reconciled native receipt beats a legacy point at equal vintage.
        rank=lambda p:(p.dimension,p.provenance.endswith('REQUEST_VERIFIED'),p.captured_utc,p.receipt_id or '')
        if old is None or rank(point)>rank(old):selected[key][point.observed_utc]=point
    with session.connect(readonly=True) as (conn,check):
        conn.set_progress_handler(lambda:(session.verify('read'),0)[1],5000)
        conn.execute('BEGIN')
        for selection in selections:
            query,params=_legacy_query(selection,start,end,generated)
            for row in _bounded_rows(conn,query,params):
                # These explicitly selected tables contain scalar metadata,
                # not payload BLOBs. Reject pathological fields before reports.
                if any(len(str(v).encode())>4096 for v in row.values()):raise ValueError('EVIDENCE_SCALAR_FIELD_BUDGET')
                p=_point(conn,selection,row,asof=generated,cutoff=end,snapshot_capture=capture)
                if p is not None:
                    s=selection.spec;add((s.provider,s.metric,s.symbol,s.frequency),s.unit,s.raw_scale,p)
            session.verify('read')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='live_schema_migrations'").fetchone():
            audit_live(conn,session=session)
            sql="SELECT o.*,r.raw_sha,r.request_sha,r.received_utc,r.custody_mode FROM live_observations o JOIN live_requests r ON r.receipt_id=o.receipt_id WHERE r.status='ok' AND o.observed_utc>=? AND o.observed_utc<? AND r.received_utc<=? AND (o.provider!='fred' OR o.dimension_key<=?) ORDER BY o.provider,o.metric,o.symbol,o.frequency,o.observed_utc,o.dimension_key,r.received_utc"
            for row in _bounded_rows(conn,sql,(_text(start),_text(end),_text(generated),generated.date().isoformat())):
                key=tuple(row[k] for k in ('provider','metric','symbol','frequency'))
                tag='SIMULATED_HTTP_REQUEST_VERIFIED' if row['custody_mode']=='SIMULATED_HTTP' else 'HTTP_REQUEST_VERIFIED'
                if session.target_kind!='FIXTURE_TEST' and tag!='HTTP_REQUEST_VERIFIED':raise ValueError('SIMULATED_NOT_REAL_EVIDENCE')
                p=EvidencePoint(row['observed_utc'],row['dimension_key'],row['value'],row['row_sha'],row['receipt_id'],row['raw_sha'],row['request_sha'],row['received_utc'],tag,'live_observations')
                add(key,row['unit'],row['raw_scale'],p,native=True)
        check()
    output=[]
    for key in sorted(selected):
        if len(units[key])!=1:raise ValueError('MIXED_SOURCE_EVIDENCE_UNITS')
        unit,scale=next(iter(units[key]));output.append(EvidenceSeries(*key,unit,scale,tuple(selected[key][t] for t in sorted(selected[key])),False))
    fixture=session.target_kind=='FIXTURE_TEST'
    snap=ReportSnapshot(_text(generated),_text(end),tuple(output),mode='OFFLINE_LIVE_BINDING' if fixture else 'PRIVATE_LIVE_EVIDENCE',dataset_origin='SYNTHETIC' if fixture else 'LIVE_PRIVATE' if session.target_kind=='LIVE_PRIVATE_PILOT' else 'INSTALLED')
    return replace(snap,sha256=snapshot_digest(snap))
