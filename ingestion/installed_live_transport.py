"""Bounded HTTP client; only an active pinned grant/lease can start transport."""
from dataclasses import dataclass
import datetime as dt
import os
import socket
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from ingestion.installed_live_sources import LiveRequest
from scripts.installed_live_policy import ValidatedGrant
from scripts.package_a_parent_lock import verify_offline_parent_lease


@dataclass(frozen=True)
class TransportResult:
    body:bytes|None
    http_status:int|None
    attempted_utc:str
    received_utc:str
    response_metadata:tuple
    custody_mode:str


def validate_request_capability(request,capability,*,deadline):
    if type(request) is not LiveRequest or type(capability) is not ValidatedGrant:raise ValueError('TYPED_HTTP_CAPABILITY_REQUIRED')
    capability.verify('ingest');verify_offline_parent_lease(capability.grant.lock)
    if type(deadline) not in (int,float) or time.monotonic()>=deadline:raise ValueError('HTTP_DEADLINE_EXCEEDED')
    wanted=(request.specs[0].provider,request.native_origin,request.path,request.start,request.end,request.sha256,request.max_dp)
    if not any((w.provider,w.origin,w.path,w.start,w.end,w.request_sha,w.max_dp)==wanted for w in capability.grant.source_windows):
        raise ValueError('HTTP_REQUEST_NOT_APPROVED')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class BoundedHttpTransport:
    def __init__(self,*,opener=None):self._opener=opener

    def fetch(self,request,capability,*,deadline):
        validate_request_capability(request,capability,deadline=deadline)
        g=capability.grant
        if g.target_kind!='FIXTURE_TEST' and self._opener is not None:raise ValueError('SIMULATED_TRANSPORT_NOT_REAL_CUSTODY')
        if g.target_kind=='FIXTURE_TEST' and self._opener is None:raise ValueError('FIXTURE_HTTP_OPENER_REQUIRED')
        params=dict(request.params);headers={'Accept':'application/json','User-Agent':'BTCMarketLab/installed-live-v1'}
        if g.target_kind!='FIXTURE_TEST':
            key_name={'researchbitcoin':'RESEARCHBITCOIN_API_TOKEN','fred':'FRED_API_KEY'}.get(request.specs[0].provider)
            if key_name:
                secret=os.getenv(key_name)
                if not secret:raise ValueError('APPROVED_SOURCE_CREDENTIAL_UNAVAILABLE')
                if request.specs[0].provider=='fred':params['api_key']=secret
                else:headers['X-API-Token']=secret
        url=request.native_origin+request.path+'?'+urlencode(params)
        req=Request(url,headers=headers,method='GET');opened=None
        now=lambda:capability.verify('ingest').isoformat().replace('+00:00','Z')
        started=now();mode='SIMULATED_HTTP' if g.target_kind=='FIXTURE_TEST' else 'HTTP'
        try:
            opener=self._opener if self._opener is not None else build_opener(_NoRedirect()).open
            try:opened=opener(req,timeout=min(g.socket_timeout,max(.001,deadline-time.monotonic())))
            except HTTPError as error:opened=error
            final=urlsplit(getattr(opened,'url',url));expected=urlsplit(url)
            if (final.scheme,final.netloc,final.path,final.query)!=(expected.scheme,expected.netloc,expected.path,expected.query):
                raise ValueError('HTTP_REDIRECT_NOT_APPROVED')
            code=getattr(opened,'status',None) or getattr(opened,'code',None)
            if type(code) is not int or not 100<=code<600:raise ValueError('HTTP_STATUS_INVALID')
            if 300<=code<400:raise ValueError('HTTP_REDIRECT_NOT_APPROVED')
            body=opened.read(g.max_raw_bytes+1)
            if type(body) is not bytes or len(body)>g.max_raw_bytes:raise ValueError('HTTP_PAYLOAD_BUDGET_EXCEEDED')
            validate_request_capability(request,capability,deadline=deadline)
            meta=tuple((k,str(opened.headers[k])[:1024]) for k in ('Content-Type','Retry-After') if k in opened.headers)
            return TransportResult(body,code,started,now(),meta,mode)
        except (TimeoutError,socket.timeout,URLError):
            return TransportResult(None,None,started,now(),(('error_class','TRANSPORT_TIMEOUT_OR_UNAVAILABLE'),),mode)
        finally:
            if opened is not None:opened.close()


class FrozenLiveTransport:
    """Explicit test byte custody; never claims that HTTP was executed."""
    def __init__(self,outcomes):
        self.outcomes=dict(outcomes);self.calls=0
        for values in self.outcomes.values():
            if type(values) is not tuple or not 1<=len(values)<=3 or any(type(x) is not tuple or len(x)!=2 or type(x[1]) is not bytes or len(x[1])>4000000 for x in values):
                raise ValueError('BOUNDED_TEST_OUTCOMES_REQUIRED')
    def fetch(self,request,capability,*,deadline):
        validate_request_capability(request,capability,deadline=deadline)
        if capability.grant.target_kind!='FIXTURE_TEST':raise ValueError('SIMULATED_TRANSPORT_NOT_REAL_CUSTODY')
        values=self.outcomes.get(request.sha256)
        if not values:raise ValueError('FROZEN_REQUEST_MISSING')
        item=values[min(self.calls,len(values)-1)];self.calls+=1
        now=capability.verify('ingest').isoformat().replace('+00:00','Z')
        return TransportResult(item[1] if item[0] is not None else None,item[0],now,now,(),'SIMULATED_HTTP')
