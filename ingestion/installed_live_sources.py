"""Typed native requests and pure parsers; availability remains unverified."""
from dataclasses import asdict,dataclass
import datetime as dt
import json
import math
from urllib.parse import quote
from ingestion.package_a_contracts import SeriesSpec,utc,parse_fixture
from ingestion.researchbitcoin_catalog import CATALOG
from scripts.installed_live_policy import ORIGINS,digest

CONTRACT_VERSION='installed-live-source-v1'


def _native(specs,start,end,page_limit=None):
    s=specs[0];a,b=utc(start),utc(end);p=s.provider
    ms=lambda t:int(t.timestamp()*1000)
    if p in ('binance','bybit'):
        funding=s.metric=='funding_settled'
        path=('/fapi/v1/fundingRate' if funding else '/futures/data/openInterestHist') if p=='binance' else ('/v5/market/funding/history' if funding else '/v5/market/open-interest')
        params={'symbol':s.symbol,'startTime':ms(a),'endTime':ms(b)-1,'limit':1000 if funding and p=='binance' else 500 if p=='binance' else 200}
        if p=='binance' and not funding:params['period']='5m'
        if p=='bybit':
            params['category']='linear'
            if not funding:params['intervalTime']='5min'
    elif p=='yahoo':
        path='/v8/finance/chart/'+quote(s.symbol,safe='-');params={'period1':int(a.timestamp()),'period2':int(b.timestamp()),'interval':'1d'}
    elif p=='researchbitcoin':
        path=CATALOG[s.metric].endpoint+'/'+s.metric
        params={'from_time':a.date().isoformat(),'to_time':b.date().isoformat(),'resolution':'d1','output_format':'json'}
    elif p=='bitview':
        path='/api/series/'+quote(s.metric,safe='_')+'/1d'
        params={'start':a.date().isoformat(),'end':(b-dt.timedelta(days=1)).date().isoformat()}
    else:
        path='/fred/series/observations';params={'series_id':s.metric,'observation_start':a.date().isoformat(),
          'observation_end':(b-dt.timedelta(days=1)).date().isoformat(),'file_type':'json','units':'lin',
          'sort_order':'asc','output_type':2,'realtime_start':'1776-07-04','realtime_end':b.date().isoformat(),'limit':100000,'offset':0}
    if page_limit is not None:
        if 'limit' not in params or type(page_limit) is not int or not 1<=page_limit<=params['limit']:
            raise ValueError('NATIVE_PAGE_LIMIT_INVALID')
        params['limit']=page_limit
    return ORIGINS[p],path,tuple(sorted(params.items()))


@dataclass(frozen=True)
class LiveRequest:
    specs:tuple
    native_origin:str
    path:str
    params:tuple
    start:str
    end:str
    direction:str='bounded_window'
    contract_sha:str=''
    max_dp:int=10000
    page_limit:int|None=None

    def __post_init__(self):
        if (type(self.specs) is not tuple or not 1<=len(self.specs)<=5 or any(type(s) is not SeriesSpec for s in self.specs) or
            len({s.provider for s in self.specs})!=1 or len({s.metric for s in self.specs})!=len(self.specs) or
            self.direction not in ('backward','forward','bounded_window') or utc(self.start)>=utc(self.end) or
            type(self.max_dp) is not int or not 1<=self.max_dp<=1000000):raise ValueError('NATIVE_REQUEST_REQUIRED')
        if len(self.specs)>1 and (self.specs[0].provider!='yahoo' or {s.metric for s in self.specs}!={'open','high','low','close','volume'} or len({s.symbol for s in self.specs})!=1):
            raise ValueError('NATIVE_SINGLE_SOURCE_REQUIRED')
        expected=_native(self.specs,self.start,self.end,self.page_limit)
        if (self.native_origin,self.path,self.params)!=expected:raise ValueError('NATIVE_ENDPOINT_OR_PARAMS_DRIFT')
        if self.contract_sha!=digest([CONTRACT_VERSION,[asdict(s) for s in self.specs],expected]):raise ValueError('NATIVE_CONTRACT_SHA_DRIFT')

    @property
    def sha256(self):return digest(asdict(self))


def native_request(specs,start,end,*,direction='bounded_window',max_dp=10000,page_limit=None):
    endpoint=_native(specs,start,end,page_limit)
    return LiveRequest(specs,*endpoint,start,end,direction,digest([CONTRACT_VERSION,[asdict(s) for s in specs],endpoint]),max_dp,page_limit)


def request_from_example(job):return native_request(job.specs,job.start,job.end)


def request_from_dict(value):
    data=dict(value);data['specs']=tuple(SeriesSpec(**s) for s in data['specs']);data['params']=tuple(tuple(x) for x in data['params'])
    return LiveRequest(**data)


def numeric_upper_bound(value):
    if type(value) in (int,float):
        if not math.isfinite(value):raise ValueError('NONFINITE_SOURCE_JSON')
        return 1
    if type(value) is str:
        try:return int(math.isfinite(float(value)))
        except ValueError:return 0
    if type(value) is list:return sum(numeric_upper_bound(x) for x in value)
    if type(value) is dict:return sum(numeric_upper_bound(x) for x in value.values())
    return 0


@dataclass(frozen=True)
class NormalizedPoint:
    spec:SeriesSpec
    timestamp:str
    dimension:str
    value:float


def normalize_live_page(request,body,*,captured_utc):
    if type(request) is not LiveRequest or type(body) is not bytes or len(body)>4000000:raise ValueError('BOUNDED_NATIVE_BODY_REQUIRED')
    payload=json.loads(body);captured=utc(captured_utc)
    if numeric_upper_bound(payload)>request.max_dp:raise ValueError('SOURCE_DP_BOUND_EXCEEDED')
    output=[]
    for spec in request.specs:
        if spec.metric=='funding_settled':
            rows=payload if spec.provider=='binance' else payload.get('result',{}).get('list',[])
            if len(rows)>=dict(request.params)['limit']:raise ValueError('NATIVE_RESULT_SATURATED')
        # Require actual source identifiers where the older fixture parser
        # intentionally allowed omissions. Do not certify future live shapes.
        if spec.provider=='bybit':
            if type(payload) is not dict or payload.get('retCode')!=0 or payload.get('result',{}).get('symbol')!=spec.symbol or payload.get('result',{}).get('category')!='linear':
                raise ValueError('NATIVE_BYBIT_SCOPE_REQUIRED')
        if spec.provider=='binance' and (type(payload) is not list or any(row.get('symbol')!=spec.symbol for row in payload)):
            raise ValueError('NATIVE_BINANCE_SCOPE_REQUIRED')
        points=parse_fixture(spec,payload,start=request.start,end=request.end)
        if any(utc(p.timestamp)>captured or (spec.provider=='fred' and utc(p.dimension+'T00:00:00Z')>captured) for p in points):
            raise ValueError('FUTURE_SOURCE_OR_VINTAGE')
        if points and ((spec.provider in ('researchbitcoin','bitview')) or (spec.provider=='yahoo' and spec.symbol=='BTC-USD')):
            dates={p.timestamp for p in points};stamp=utc(request.start)
            while stamp<utc(request.end):
                if stamp.isoformat().replace('+00:00','Z') not in dates:raise ValueError('NATIVE_DAILY_GAP')
                stamp+=dt.timedelta(days=1)
        output.extend(NormalizedPoint(spec,p.timestamp,p.dimension,p.value) for p in points)
    return tuple(output)


class SourceRegistry:
    version=CONTRACT_VERSION
    availability='EXTERNAL_UNVERIFIED'
    providers=tuple(sorted(ORIGINS))
    native_request=staticmethod(native_request)
