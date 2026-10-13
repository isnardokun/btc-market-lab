"""One query-only installed-schema fixture snapshot; no productive reader.

Mappings are explicit declarations, not certification of units/entitlement.
Unknown historical acquisition times use a snapshot capture UPPER BOUND;
they cannot support a report as-of before that snapshot existed.
"""
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
import datetime as dt
import hashlib
import sqlite3

from analysis.package_b_evidence import EvidencePoint, EvidenceSeries, ReportSnapshot, canonical_digest, snapshot_digest
from ingestion.package_a_contracts import SeriesSpec, utc, daystamp, scalar, epoch
from scripts import package_c_state as state
from storage.installed_schema import inspect_installed_schema

MAX_POINTS=10000


@dataclass(frozen=True)
class LegacySelection:
    spec: SeriesSpec
    table: str
    lookup: str

    def __post_init__(self):
        s=self.spec
        if not isinstance(s,SeriesSpec) or not isinstance(self.lookup,str) or not self.lookup:
            raise ValueError('EXPLICIT_LEGACY_SELECTION_REQUIRED')
        valid=(self.table=='price_btc' and s.provider=='yahoo' and s.symbol=='BTC-USD' and s.metric=='close' and self.lookup=='BTC-USD' or
            self.table=='daily' and s.provider=='bitview' and self.lookup==s.metric or
            self.table in ('macro_fred','macro_fred_old','archive_fred_vintages') and s.provider=='fred' and self.lookup==s.metric or
            self.table=='onchain_external_observations' and s.provider=='researchbitcoin' and self.lookup==s.metric or
            self.table=='market_derivatives' and s.provider in ('binance','bybit') and self.lookup==s.symbol)
        if not valid: raise ValueError('UNSUPPORTED_OR_IMPLICIT_LEGACY_MAPPING')


def _text(stamp): return stamp.isoformat().replace('+00:00','Z')


def _rows(conn,selection):
    s=selection.spec; table=selection.table
    if table=='price_btc':
        sql='SELECT id,ts,price FROM price_btc ORDER BY ts'; params=()
    elif table=='daily':
        sql='SELECT d.id,d.ts,d.value FROM daily d JOIN series s ON d.series_id=s.id WHERE s.name=? ORDER BY d.ts'; params=(selection.lookup,)
    elif table in ('macro_fred','macro_fred_old'):
        sql='SELECT * FROM '+table+' WHERE series_id=? ORDER BY date'; params=(selection.lookup,)
    elif table=='archive_fred_vintages':
        sql='SELECT * FROM archive_fred_vintages WHERE series_id=? ORDER BY observed_date,realtime_start'; params=(selection.lookup,)
    elif table=='onchain_external_observations':
        sql='SELECT * FROM onchain_external_observations WHERE provider=? AND metric=? ORDER BY observed_at_utc';params=(s.provider,s.metric)
    else:
        sql='SELECT * FROM market_derivatives WHERE provider=? AND symbol=? AND metric=? AND interval_label=? ORDER BY observed_utc';params=(s.provider,s.symbol,s.metric,s.frequency)
    cursor=conn.execute(sql+' LIMIT ?',params+(MAX_POINTS+1,))
    names=[c[0] for c in cursor.description]; rows=[dict(zip(names,r)) for r in cursor]
    if len(rows)>MAX_POINTS: raise ValueError('OFFLINE_SELECTED_SERIES_ROW_BUDGET')
    return rows


def _point(conn,selection,row,*,asof,cutoff,snapshot_capture):
    table=selection.table; s=selection.spec
    dimension=''; raw_sha=None; receipt=None; request_sha=None; provenance='LEGACY_NO_REQUEST'
    capture=row.get('fetched_utc',row.get('fetched_at_utc',row.get('captured_at_utc',row.get('fetched_at'))))
    availability='RECORDED_CAPTURE'
    if table in ('price_btc','daily'):
        stamp=epoch(row['ts'],millis=False); value=row['price'] if table=='price_btc' else row['value']
    elif table in ('macro_fred','macro_fred_old'):
        stamp=daystamp(row['date']);value=row['value'];provenance='LEGACY_NO_REQUEST_VINTAGE_UNKNOWN'
    elif table=='archive_fred_vintages':
        stamp=daystamp(row['observed_date']);value=row['value'];dimension=row['realtime_start']
        if daystamp(dimension).date()>asof.date(): return None
    else:
        stamp=utc(row.get('observed_utc',row.get('observed_at_utc')))
        value=row.get('raw_value',row.get('value'))
        if row.get('raw_unit',row.get('unit'))!=s.unit: raise ValueError('LEGACY_SOURCE_UNIT_MISMATCH')
    if value is None: return None
    value=scalar(value)
    if stamp>=cutoff: return None
    if capture is None:
        captured=snapshot_capture;availability='SNAPSHOT_UPPER_BOUND'
    else:
        if not isinstance(capture,str): raise ValueError('LEGACY_CAPTURE_TIME_INVALID')
        try: captured=dt.datetime.fromisoformat(capture.replace('Z','+00:00'))
        except ValueError as error: raise ValueError('LEGACY_CAPTURE_TIME_INVALID') from error
        if captured.tzinfo is None:
            captured=snapshot_capture;availability='SNAPSHOT_UPPER_BOUND'
        else: captured=captured.astimezone(dt.timezone.utc)
    if captured>asof: return None
    if table=='market_derivatives':
        raw_sha=row['raw_sha256']
        raw=conn.execute('SELECT body FROM market_raw_payloads WHERE sha256=?',(raw_sha,)).fetchone()
        if not raw or type(raw[0]) is not bytes or hashlib.sha256(raw[0]).hexdigest()!=raw_sha:
            raise ValueError('LEGACY_RAW_SHA_MISMATCH')
        provenance='LEGACY_RAW_WITHOUT_ACQUISITION'
        # Link presence is weaker than a request/row reconstruction audit.
        matches=conn.execute('SELECT l.request_id,l.attempted_utc,l.ended_utc FROM market_request_lineage l '
            'JOIN market_request_acquisitions a ON a.request_id=l.request_id '
            'WHERE l.provider=? AND a.provider=l.provider AND l.stream=? AND l.symbol=? '
            'AND l.interval_label=? AND l.raw_sha256=? AND a.raw_sha256=l.raw_sha256 '
            'AND l.endpoint_path=? AND a.endpoint_path=l.endpoint_path AND l.status=\'success\' '
            'AND l.first_observed_utc<=? AND l.last_observed_utc>=?',
            (s.provider,s.metric,s.symbol,s.frequency,raw_sha,row['source_endpoint'],_text(stamp),_text(stamp))).fetchall()
        if len(matches)==1 and utc(matches[0][2])<=asof:
            receipt=matches[0][0];request_sha=canonical_digest(list(matches[0]))
            provenance='LEGACY_REQUEST_LINK_PRESENT_UNRECONCILED'
    return EvidencePoint(_text(stamp),dimension,value,canonical_digest({'table':table,'row':row}),
        receipt,raw_sha,request_sha,_text(captured),provenance,table,availability)


def read_installed_snapshot(db_path,lock_dir,*,asof,snapshot_captured_utc,selections=()):
    generated=utc(asof);capture=utc(snapshot_captured_utc)
    cutoff=generated.replace(hour=0,minute=0,second=0,microsecond=0)
    if type(selections) is not tuple or any(not isinstance(x,LegacySelection) for x in selections):
        raise ValueError('TYPED_LEGACY_SELECTIONS_REQUIRED')
    keys=[(x.spec.provider,x.spec.metric,x.spec.symbol,x.spec.frequency) for x in selections]
    if len(set(keys))!=len(keys): raise ValueError('AMBIGUOUS_LEGACY_STREAM_SELECTION')
    from pathlib import Path
    root,path,locks,_=state.guard(Path(db_path).parent)
    if path!=Path(db_path) or locks!=Path(lock_dir): raise ValueError('EXPLICIT_FIXTURE_PATH_MISMATCH')
    output=[]
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        state.guard(root);structure=inspect_installed_schema(conn)
        for selection in selections:
            selected={}
            for row in _rows(conn,selection):
                point=_point(conn,selection,row,asof=generated,cutoff=cutoff,snapshot_capture=capture)
                if point is None: continue
                previous=selected.get(point.observed_utc)
                if previous is None or (point.dimension,point.captured_utc)>(previous.dimension,previous.captured_utc):
                    selected[point.observed_utc]=point
            if selected:
                s=selection.spec
                output.append(EvidenceSeries(s.provider,s.metric,s.symbol,s.frequency,s.unit,s.raw_scale,
                    tuple(selected[t] for t in sorted(selected)),False))
        if structure['addon_tables']:
            from storage.compatibility_ledger import read_evidence
            output=read_evidence(conn,output,asof=generated,cutoff=cutoff)
        state.guard(root)
    snapshot=ReportSnapshot(_text(generated),_text(cutoff),tuple(sorted(output,key=lambda s:s.key)),mode='OFFLINE_INSTALLED_SCHEMA')
    return replace(snapshot,sha256=snapshot_digest(snapshot))
