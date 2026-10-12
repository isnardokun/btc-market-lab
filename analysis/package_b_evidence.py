"""Immutable report evidence from ONE read-only synthetic A SQLite snapshot.

No implicit production path, network client, credentials or mutation. RAW
integrity and report selection share the same SQLite read transaction.
"""
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass, replace
import datetime as dt
import hashlib
import json
import sqlite3

from ingestion.package_a_contracts import utc
from scripts.package_a_ingest_fixture import _fixture_guard, _inspect_fixture_snapshot
from storage.package_a_ledger import validate_schema


@dataclass(frozen=True)
class EvidencePoint:
    observed_utc: str
    dimension: str
    value: float
    row_sha256: str
    receipt_id: str
    raw_sha256: str
    request_sha256: str
    captured_utc: str


@dataclass(frozen=True)
class EvidenceSeries:
    provider: str
    metric: str
    symbol: str
    frequency: str
    unit: str
    raw_scale: str
    points: tuple[EvidencePoint, ...]
    source_units_verified: bool

    @property
    def key(self):
        return (self.provider, self.metric, self.symbol, self.frequency)

    @property
    def sha256(self):
        return canonical_digest(asdict(self))


@dataclass(frozen=True)
class ReportSnapshot:
    asof_utc: str
    cutoff_utc: str
    series: tuple[EvidenceSeries, ...]
    sha256: str = ''
    real_history_verified: bool = False
    production_authorized: bool = False
    mode: str = 'OFFLINE_SYNTHETIC'


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                    ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def snapshot_digest(snapshot):
    value=asdict(snapshot)
    value.pop('sha256')
    return canonical_digest(value)


def read_report_snapshot(db_path, lock_dir, *, asof, cutoff=None):
    """Pin audit + rows, reject missing RAW/request lineage and lookahead.

    vintage dates bound a revision's availability independently of its
    observation date. Captures known after generation are excluded too.
    Daily bars dated on the current UTC day are never treated as closed.
    """
    generated=utc(asof)
    stop=utc(cutoff) if cutoff is not None else generated.replace(
        hour=0,minute=0,second=0,microsecond=0)
    if stop > generated or stop.time()!=dt.time():
        raise ValueError('BLOCKED_REPORT_CUTOFF_NOT_COMPLETED_UTC_DAY')
    asof_text=generated.isoformat().replace('+00:00','Z')
    cutoff_text=stop.isoformat().replace('+00:00','Z')
    path=_fixture_guard(db_path,lock_dir)
    selected=defaultdict(dict)
    configs=defaultdict(set)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        validate_schema(db)
        audit=_inspect_fixture_snapshot(db)
        if (audit['state']!='LOCAL_FIXTURE_INTEGRITY_PASS' or
                audit['native_request_lineage']['historical_receipts_without_frozen_provenance']):
            raise ValueError('BLOCKED_REPORT_RAW_OR_NATIVE_REQUEST_EVIDENCE')
        rows=db.execute(
            'SELECT o.provider,o.metric,o.symbol,o.frequency,o.unit,o.raw_scale,'
            'o.observed_utc,o.dimension_key,o.value,o.row_sha256,o.receipt_id,'
            'r.raw_sha256,p.request_sha256,r.captured_utc '
            'FROM package_a_observations o JOIN package_a_receipts r '
            'ON o.receipt_id=r.receipt_id JOIN package_a_request_provenance p '
            'ON p.receipt_id=r.receipt_id WHERE r.status=\'ok\' '
            'ORDER BY o.provider,o.metric,o.symbol,o.frequency,o.observed_utc,o.dimension_key'
        ).fetchall()
        for provider,metric,symbol,freq,unit,scale,stamp,dimension,value,row_sha,rid,raw_sha,request_sha,captured in rows:
            if utc(stamp)>=stop or utc(captured)>generated:
                continue
            if provider=='fred' and dt.date.fromisoformat(dimension)>generated.date():
                continue
            key=(provider,metric,symbol,freq)
            configs[key].add((unit,scale))
            point=EvidencePoint(stamp,dimension,value,row_sha,rid,raw_sha,request_sha,captured)
            old=selected[key].get(stamp)
            # FRED revisions: choose the last vintage actually known as-of.
            if old is None or dimension>old.dimension:
                selected[key][stamp]=point
        result=[]
        for key in sorted(selected):
            if len(configs[key])!=1:
                raise ValueError('BLOCKED_REPORT_MIXED_SOURCE_UNITS')
            unit,scale=next(iter(configs[key]))
            result.append(EvidenceSeries(*key,unit,scale,
                tuple(selected[key][stamp] for stamp in sorted(selected[key])),
                source_units_verified=False))
    snapshot=ReportSnapshot(asof_text,cutoff_text,tuple(result))
    return replace(snapshot,sha256=snapshot_digest(snapshot))
