"""Deterministic, source-keyed calculations for the offline B candidate."""
from dataclasses import dataclass
import datetime as dt
import math

from analysis.package_b_evidence import ReportSnapshot, snapshot_digest
from ingestion.package_a_contracts import utc
from quant_engine.indicators import (compute_rsi,compute_ma,compute_macd,atr,
                                     find_supp_res,compute_scenarios)

VERSION='package-b-analytics-v1'

@dataclass(frozen=True)
class AssetAnalysis:
    symbol: str
    instrument: str
    input_sha256: str
    observed_utc: str|None
    closes: tuple[tuple[str,float],...]
    close: float|None
    change_pct: float|None
    rsi: float|None
    sma20: float|None
    sma50: float|None
    sma200: float|None
    macd_histogram: float|None
    atr14: float|None
    scenarios: tuple[str,...]
    quality: str
    errors: tuple[str,...]
    rsi_method: str='Wilder(14), first-period seed; zero-loss legacy=100'
    atr_method: str='Mean true range, 14 observations'

@dataclass(frozen=True)
class SourceQuality:
    key: tuple[str,str,str,str]
    latest_observed_utc: str
    age_hours: float
    maximum_age_hours: float
    freshness: str

@dataclass(frozen=True)
class ReportAnalysis:
    snapshot: ReportSnapshot
    assets: tuple[AssetAnalysis,...]
    critical_errors: tuple[str,...]
    source_quality: tuple[SourceQuality,...]=()
    calculation_version: str=VERSION
    external_verification: str='NOT_VERIFIED'
    calculation_parameters: tuple[tuple[str,str],...]=(
        ('RSI','period=14; Wilder; first 14 changes seed; zero-loss=100'),
        ('SMA','periods=20,50,200; arithmetic mean; no padding'),
        ('MACD','EMA periods=12,26,9; arithmetic initial EMA seed'),
        ('ATR','period=14; simple mean of true range; not Wilder ATR'),
        ('PIVOTS','lookback=5; confirmed historical pivots; price-relative levels'),
        ('HORIZON','completed UTC daily observations; conditional daily scenarios'),
    )

PROFILES=(('BTC-USD','Bitcoin (BTC)',72,'BTC'),
          ('SPY','SPDR S&P 500 ETF (SPY)',120,'SPY'),
          ('GLD','Oro ETF SPDR Gold Shares (GLD)',120,'GLD'))


def _asset(snapshot,symbol,instrument,max_age,tag):
    streams={s.metric:s for s in snapshot.series
             if s.provider=='yahoo' and s.symbol==symbol and s.frequency=='d1'}
    fields=('open','high','low','close','volume')
    errors=[]
    if any(field not in streams for field in fields):
        errors.append(symbol+':OHLCV_MISSING')
    elif any(s.unit!=('native_volume' if m=='volume' else 'USD') or
             s.raw_scale!='native' for m,s in streams.items() if m in fields):
        errors.append(symbol+':OHLCV_UNIT_MISMATCH')
    elif any(tuple(p.observed_utc for p in streams[field].points)!=
             tuple(p.observed_utc for p in streams['close'].points) for field in fields):
        errors.append(symbol+':OHLCV_DATES_MISALIGNED')
    closes=tuple((p.observed_utc,p.value) for p in streams.get('close',()).points) if 'close' in streams else ()
    stamp=closes[-1][0] if closes else None
    if stamp:
        age=(utc(snapshot.asof_utc)-utc(stamp)).total_seconds()/3600
        if age<0 or age>max_age: errors.append(symbol+':STALE_OR_FUTURE_CLOSE')
    else:
        errors.append(symbol+':CLOSE_NO_DATA')
    if len(closes)<2: errors.append(symbol+':COMPARABLE_CLOSE_NO_DATA')
    if not errors:
        for i in range(len(closes)):
            values={k:streams[k].points[i].value for k in fields}
            if (not all(math.isfinite(v) for v in values.values()) or
                values['volume']<0 or values['low']<=0 or
                not values['low']<=values['open']<=values['high'] or
                not values['low']<=values['close']<=values['high']):
                errors.append(symbol+':INVALID_OHLCV_BAR')
                break
    if errors:
        if snapshot.mode=='OFFLINE_INSTALLED_SCHEMA' and closes and 'close' in streams:
            stream=streams['close']
            if (stream.unit=='USD' and stream.raw_scale=='native' and len(closes)>=2 and
                all(math.isfinite(v) and v>0 for _,v in closes) and
                0<=(utc(snapshot.asof_utc)-utc(stamp)).total_seconds()/3600<=max_age):
                numbers=[v for _,v in closes]
                return AssetAnalysis(symbol,instrument,snapshot.sha256,stamp,closes,
                    numbers[-1],(numbers[-1]/numbers[-2]-1)*100,compute_rsi(numbers),
                    compute_ma(numbers,20),compute_ma(numbers,50),compute_ma(numbers,200),
                    compute_macd(numbers)[2],None,(),'PARTIAL_CLOSE_ONLY',tuple(sorted(set(errors))))
        return AssetAnalysis(symbol,instrument,snapshot.sha256,stamp,(),
                             None,None,None,None,None,None,None,None,(),
                             'BLOCKED',tuple(sorted(set(errors))))
    numbers=[v for _,v in closes]
    highs=[p.value for p in streams['high'].points]
    lows=[p.value for p in streams['low'].points]
    latest=numbers[-1]
    rsi=compute_rsi(numbers)
    macd=compute_macd(numbers)[2]
    atr_value=atr(highs,lows,numbers)
    support,resistance=find_supp_res(numbers,highs,lows,lookback=5)
    scenarios=compute_scenarios(latest,numbers,highs,lows,support,resistance,
                                rsi,atr_value,macd,tag)
    if symbol!='BTC-USD':
        scenarios=tuple(text.replace('(spot ','(cotización ').replace('demanda spot','demanda del instrumento')
                        for text in scenarios)
    return AssetAnalysis(symbol,instrument,snapshot.sha256,stamp,closes,
        latest,(latest/numbers[-2]-1)*100,rsi,compute_ma(numbers,20),
        compute_ma(numbers,50),compute_ma(numbers,200),macd,atr_value,
        scenarios,('LOCAL_SYNTHETIC_EVIDENCE' if snapshot.mode=='OFFLINE_SYNTHETIC' else
                   'LOCAL_SIMULATED_ACQUISITION'),())


def analyze_snapshot(snapshot):
    if not isinstance(snapshot,ReportSnapshot) or snapshot.sha256!=snapshot_digest(snapshot):
        raise ValueError('BLOCKED_REPORT_SNAPSHOT_DIGEST_MISMATCH')
    if (snapshot.mode not in ('OFFLINE_SYNTHETIC','OFFLINE_INSTALLED_SCHEMA') or
        snapshot.dataset_origin!='SYNTHETIC' or snapshot.production_authorized or snapshot.real_history_verified):
        raise ValueError('BLOCKED_UNAUTHORIZED_REPORT_PROFILE')
    assets=tuple(_asset(snapshot,*profile) for profile in PROFILES)
    qualities=[]
    for series in snapshot.series:
        if not series.points: raise ValueError('BLOCKED_EMPTY_EVIDENCE_SERIES')
        # Conservative operational freshness budgets, not externally proven
        # source calendars. FRED holidays/release timing remain unverified.
        limit=({'5m':1,'event':36,'d1':120,'weekly':14*24,'monthly':62*24}
               .get(series.frequency,24))
        if series.provider=='researchbitcoin':limit=10*24
        if series.provider=='bitview':limit=10*24
        latest=series.points[-1].observed_utc
        age=(utc(snapshot.asof_utc)-utc(latest)).total_seconds()/3600
        state='STALE' if age>limit else 'FUTURE' if age<0 else 'WITHIN_LOCAL_FRESHNESS_BUDGET'
        qualities.append(SourceQuality(series.key,latest,age,limit,state))
    return ReportAnalysis(snapshot,assets,tuple(e for a in assets for e in a.errors),
                          tuple(qualities))
