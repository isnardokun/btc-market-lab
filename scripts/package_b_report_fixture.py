#!/usr/bin/env python3
"""Offline-only example: real A adapters/fake HTTP -> SQLite -> B report.

Every numeric input below is synthetic. No provider client may open a real
socket; A frozen transport rejects request drift. No production path exists.
"""
import argparse
from dataclasses import asdict
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.package_a_contracts import SeriesSpec
from ingestion.researchbitcoin_catalog import CATALOG
from ingestion.bitview_history import EPOCH
from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE,_fixture_guard
from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
from scripts.package_a_yahoo_fanout import run_offline_yahoo_ohlcv_fanout
from scripts.package_a_onchain_catalog_batch import configured_onchain_series
from analysis.package_b_evidence import read_report_snapshot
from analysis.package_b_analytics import analyze_snapshot
from rendering.package_b_report import render_report,audit_candidate_report

ASOF='2026-10-12T00:05:00Z'
END='2026-10-12T00:00:00Z'


def raw(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def create_example_fixture(db_path,lock_dir):
    _fixture_guard(db_path,lock_dir)
    stop=dt.date(2026,10,12)
    start=stop-dt.timedelta(days=320)
    for symbol,base,trend,amplitude in [('BTC-USD',79000,8,2400),('SPY',610,.15,18),('GLD',370,.12,9)]:
        ticks=[];quotes={key:[] for key in ('open','high','low','close','volume')}
        for i in range(320):
            day=start+dt.timedelta(days=i)
            if symbol!='BTC-USD' and day.weekday()>=5:continue
            close=round(base+i*trend+amplitude*math.sin(i*.21),2)
            opened=round(close*(1+.001*math.cos(i)),2)
            high=round(max(close,opened)*1.009,2)
            low=round(min(close,opened)*.991,2)
            ticks.append(int(dt.datetime.combine(day,dt.time(),dt.timezone.utc).timestamp()))
            for key,value in zip(quotes,(opened,high,low,close,100000+i*130)):
                quotes[key].append(value)
        result=run_offline_yahoo_ohlcv_fanout(db_path,lock_dir,symbol,
            start.isoformat()+'T00:00:00Z',END,
            raw_bytes=raw({'chart':{'result':[{'timestamp':ticks,
                'indicators':{'quote':[quotes]}}],'error':None}}),now=ASOF)
        if result['status']!='ok':raise ValueError('Synthetic Yahoo example rejected')
    rbn_values={'realized_price_sth':76500,'realized_price_lth':39000,
        'true_market_meanprice':61000,'mvrv_sth':1.1,'mvrv_z_sth':1.3,
        'sopr_sth':1.015,'sopr_lth':1.03,'realized_loss_sth':42000000,
        'realized_profit_sth':44000000,'net_realized_profit_loss_sth':2000000,
        'supply_in_profit_sth_percent':.684,'supply_in_profit_percent':.709,
        'liveliness':.6}
    for spec in configured_onchain_series():
        if spec.provider=='researchbitcoin':
            payload={'data':[{'date':(dt.date(2026,10,8)+dt.timedelta(days=i)).isoformat(),
                              'value':rbn_values[spec.metric]} for i in range(4)]}
        else:
            payload={'index':'1d','start':(dt.date(2026,10,8)-EPOCH).days,
                     'data':[1.0+i*.02 for i in range(4)]}
        result=run_mocked_legacy_page(db_path,lock_dir,spec,
            '2026-10-08T00:00:00Z',END,raw_bytes=raw(payload),now=ASOF)
        if result['status']!='ok':raise ValueError('Synthetic onchain example rejected')
    for metric,frequency,unit,rows,start_day in [
        ('DGS10','d1','percent',[
            {'date':'2026-10-08','value':'4.10','realtime_start':'2026-10-10'},
            {'date':'2026-10-09','value':'4.12','realtime_start':'2026-10-10'}],'2026-10-08'),
        ('CPIAUCSL','monthly','index',[
            {'date':'2026-08-01','value':'322.4','realtime_start':'2026-10-10'},
            {'date':'2026-09-01','value':'323.1','realtime_start':'2026-10-10'}],'2026-08-01')]:
        result=run_mocked_legacy_page(db_path,lock_dir,
            SeriesSpec('fred',metric,'USA',frequency,unit),start_day+'T00:00:00Z',END,
            raw_bytes=raw({'observations':rows}),now=ASOF)
        if result['status']!='ok':raise ValueError('Synthetic FRED example rejected')
    a='2026-10-11T23:50:00Z'
    ms=lambda text:int(dt.datetime.fromisoformat(text.replace('Z','+00:00')).timestamp()*1000)
    for provider in ('binance','bybit'):
        if provider=='binance':
            oi=[{'symbol':'BTCUSDT','timestamp':ms(a),'sumOpenInterest':'49000'},
                {'symbol':'BTCUSDT','timestamp':ms('2026-10-11T23:55:00Z'),'sumOpenInterest':'49200'}]
            funding=[{'symbol':'BTCUSDT','fundingTime':ms('2026-10-11T16:00:00Z'),'fundingRate':'-0.00012'}]
        else:
            oi={'retCode':0,'result':{'symbol':'BTCUSDT','category':'linear','list':[
                {'timestamp':str(ms(a)),'openInterest':'47000'},
                {'timestamp':str(ms('2026-10-11T23:55:00Z')),'openInterest':'47200'}]}}
            funding={'retCode':0,'result':{'symbol':'BTCUSDT','category':'linear','list':[
                {'fundingRateTimestamp':str(ms('2026-10-11T16:00:00Z')),'fundingRate':'0.00010'}]}}
        for metric,freq,unit,payload,begin in [
            ('open_interest','5m','BTC',oi,a),
            ('funding_settled','event','fraction',funding,'2026-10-11T00:00:00Z')]:
            result=run_mocked_legacy_page(db_path,lock_dir,
                SeriesSpec(provider,metric,'BTCUSDT',freq,unit),begin,END,
                raw_bytes=raw(payload),now=ASOF)
            if result['status']!='ok':raise ValueError('Synthetic derivatives example rejected')


def main(argv=None):
    parser=argparse.ArgumentParser(description='Render marked synthetic B report; NO live data')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output_dir.is_symlink():parser.error('No symlink output directory')
    for name in ('package_b_synthetic_report.html','package_b_synthetic_evidence.json',
                 'package_b_synthetic_gate.json'):
        target=args.output_dir/name
        if target.is_symlink() or (target.exists() and target.stat().st_nlink>1):
            parser.error('No symlink or hardlink output files')
    with tempfile.TemporaryDirectory(prefix='package-a-fixture-') as folder:
        root=Path(folder);locks=root/'locks';locks.mkdir(mode=0o700)
        (root/MARKER).write_text(MARKER_VALUE)
        path=root/'package_a_synthetic.db'
        with sqlite3.connect(path) as db:db.execute('CREATE TABLE retained_example(id INTEGER)')
        create_example_fixture(path,locks)
        snapshot=read_report_snapshot(path,locks,asof=ASOF)
        model=analyze_snapshot(snapshot)
        report=render_report(model)
        gate=audit_candidate_report(model,report)
        if gate['state']!='OFFLINE_REPORT_LOCAL_RECONCILIATION_PASS':
            print(json.dumps(gate,sort_keys=True),file=sys.stderr)
            return 3
        args.output_dir.mkdir(parents=True,exist_ok=True)
        from rendering.portable_report import atomic_write
        atomic_write(args.output_dir/'package_b_synthetic_report.html',report.encode())
        atomic_write(args.output_dir/'package_b_synthetic_evidence.json',
            json.dumps(asdict(model),sort_keys=True,indent=2,allow_nan=False).encode())
        atomic_write(args.output_dir/'package_b_synthetic_gate.json',json.dumps(gate,sort_keys=True,indent=2).encode())
        print(json.dumps({'state':gate['state'],'html_sha256':gate['html_sha256'],
            'series':len(snapshot.series),'output_dir':str(args.output_dir),
            'production_publication_authorized':False},sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
