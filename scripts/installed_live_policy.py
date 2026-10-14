"""Pure runtime permission validation. No file/network/env lookup or defaults.

The approved artifact pin is an independent operator input from the owner's
approval, not inferred from the grant's own hash. This does not authenticate
GitHub users or certify provider entitlement / global writer participation.
"""
from dataclasses import asdict,dataclass,field
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from ingestion.package_a_contracts import utc

_SEAL=object()
OPERATIONS=frozenset(('read','migrate','ingest','report_private'))
ORIGINS={'binance':'https://fapi.binance.com','bybit':'https://api.bybit.com',
 'fred':'https://api.stlouisfed.org','yahoo':'https://query1.finance.yahoo.com',
 'researchbitcoin':'https://api.researchbitcoin.net','bitview':'https://bitview.space'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _hex(value,n):return type(value) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}',value) is not None


@dataclass(frozen=True)
class SourceWindow:
    provider:str
    origin:str
    path:str
    start:str
    end:str
    request_sha:str
    max_dp:int

    def __post_init__(self):
        if (self.provider not in ORIGINS or self.origin!=ORIGINS[self.provider] or
            type(self.path) is not str or not self.path.startswith('/') or
            any(c in self.path for c in ('?','#','\n','\r','..')) or
            utc(self.start)>=utc(self.end) or not _hex(self.request_sha,64) or
            type(self.max_dp) is not int or not 1<=self.max_dp<=1000000):
            raise ValueError('SOURCE_WINDOW_CONTRACT_REQUIRED')


@dataclass(frozen=True)
class RuntimeGrant:
    code_sha:str
    plan_sha:str
    approval_reference:str
    target_kind:str
    root:str
    db:str
    lock:str
    root_identity:tuple
    db_identity:tuple
    lock_identity:tuple
    owner_uid:int
    allowed_operations:tuple
    source_windows:tuple
    issued_utc:str
    expires_utc:str
    max_requests:int
    max_attempts:int
    max_raw_bytes:int
    max_dp:int
    socket_timeout:int
    deadline_seconds:int
    approval_artifact_sha:str
    installed_schema_sha:str|None=None
    writer_window_sha:str|None=None
    backup_manifest_sha:str|None=None
    creation_parent_identity:tuple=()

    @property
    def public(self):
        value=asdict(self);value.pop('approval_artifact_sha');return value

    @property
    def artifact_sha(self):return digest(self.public)


@dataclass(frozen=True)
class ValidatedGrant:
    grant:RuntimeGrant
    validated_utc:str
    _seal:object=field(repr=False,compare=False)

    def verify(self,operation,*,now=None):
        if self._seal is not _SEAL:raise ValueError('RUNTIME_CAPABILITY_REQUIRED')
        current=(utc(self.validated_utc) if self.grant.target_kind=='FIXTURE_TEST' else
                 dt.datetime.now(dt.timezone.utc).replace(microsecond=0)) if now is None else utc(now)
        if operation not in self.grant.allowed_operations or operation not in OPERATIONS:
            raise ValueError('RUNTIME_OPERATION_NOT_APPROVED')
        if not utc(self.grant.issued_utc)<=current<utc(self.grant.expires_utc):
            raise ValueError('RUNTIME_GRANT_EXPIRED')
        if self.grant.artifact_sha!=self.grant.approval_artifact_sha:
            raise ValueError('RUNTIME_APPROVAL_DRIFT')
        return current


def validate_runtime_grant(grant,*,code_sha,plan_sha,now,operation,approved_artifact_sha=None):
    if type(grant) is not RuntimeGrant:raise ValueError('RUNTIME_GRANT_REQUIRED')
    if (not _hex(grant.code_sha,40) or not _hex(grant.plan_sha,64) or
        grant.code_sha!=code_sha or grant.plan_sha!=plan_sha):raise ValueError('CODE_OR_PLAN_SHA_DRIFT')
    if (not _hex(approved_artifact_sha,64) or approved_artifact_sha!=grant.artifact_sha or
        grant.approval_artifact_sha!=approved_artifact_sha):raise ValueError('INDEPENDENT_APPROVAL_PIN_REQUIRED')
    if grant.target_kind not in ('FIXTURE_TEST','LIVE_PRIVATE_PILOT','INSTALLED_DB'):
        raise ValueError('RUNTIME_TARGET_KIND_REQUIRED')
    if grant.target_kind=='FIXTURE_TEST':
        if not grant.approval_reference.startswith('fixture-test:'):raise ValueError('FIXTURE_APPROVAL_REQUIRED')
    elif not re.fullmatch(r'https://github.com/isnardokun/btc-market-lab/issues/60#issuecomment-\d+',grant.approval_reference):
        raise ValueError('OWNER_APPROVAL_REFERENCE_REQUIRED')
    if grant.target_kind=='INSTALLED_DB' and not all(_hex(v,64) for v in
       (grant.installed_schema_sha,grant.writer_window_sha,grant.backup_manifest_sha)):
        raise ValueError('INSTALLED_OPERATIONAL_RECEIPTS_REQUIRED')
    if type(grant.owner_uid) is not int or grant.owner_uid<0:raise ValueError('OWNER_UID_REQUIRED')
    root,db,lock=(Path(p) for p in (grant.root,grant.db,grant.lock))
    if (any(not p.is_absolute() or '..' in p.parts for p in (root,db,lock)) or
        db.parent!=root or lock.parent!=root or db==lock or len(str(root))>1024):
        raise ValueError('EXPLICIT_RUNTIME_PATHS_REQUIRED')
    for value in (grant.root_identity,grant.db_identity,grant.lock_identity):
        if type(value) is not tuple or (len(value)!=2 and not
            (grant.target_kind=='LIVE_PRIVATE_PILOT' and value==())) or any(type(x) is not int or x<0 for x in value):
            raise ValueError('PHYSICAL_IDENTITIES_REQUIRED')
    if grant.target_kind=='LIVE_PRIVATE_PILOT' and (type(grant.creation_parent_identity) is not tuple or
       len(grant.creation_parent_identity)!=2):raise ValueError('CREATION_PARENT_IDENTITY_REQUIRED')
    if (type(grant.allowed_operations) is not tuple or not grant.allowed_operations or
        not set(grant.allowed_operations)<=OPERATIONS or len(set(grant.allowed_operations))!=len(grant.allowed_operations)):
        raise ValueError('BOUNDED_OPERATIONS_REQUIRED')
    if (type(grant.source_windows) is not tuple or len(grant.source_windows)>128 or
        any(type(w) is not SourceWindow for w in grant.source_windows) or
        len({w.request_sha for w in grant.source_windows})!=len(grant.source_windows)):
        raise ValueError('BOUNDED_SOURCE_WINDOWS_REQUIRED')
    for value,limit in ((grant.max_requests,128),(grant.max_attempts,3),(grant.max_raw_bytes,4000000),
                        (grant.max_dp,10**12),(grant.socket_timeout,15),(grant.deadline_seconds,900)):
        if type(value) is not int or not 1<=value<=limit:raise ValueError('BOUNDED_RUNTIME_LIMIT_REQUIRED')
    if grant.socket_timeout>grant.deadline_seconds:raise ValueError('DEADLINE_SHORTER_THAN_SOCKET_TIMEOUT')
    if utc(grant.expires_utc)<=utc(grant.issued_utc) or (utc(grant.expires_utc)-utc(grant.issued_utc)).total_seconds()>86400:
        raise ValueError('BOUNDED_GRANT_VALIDITY_REQUIRED')
    cap=ValidatedGrant(grant,utc(now).isoformat().replace('+00:00','Z'),_SEAL);cap.verify(operation,now=now)
    return cap


def grant_from_dict(value):
    if type(value) is not dict:raise ValueError('TYPED_GRANT_JSON_REQUIRED')
    data=dict(value)
    for key in ('root_identity','db_identity','lock_identity','creation_parent_identity','allowed_operations'):
        if key in data:data[key]=tuple(data[key])
    data['source_windows']=tuple(SourceWindow(**w) for w in data.get('source_windows',()))
    return RuntimeGrant(**data)
