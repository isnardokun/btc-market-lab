"""Self-contained Research Studio candidate: model in, HTML out, no I/O."""
from dataclasses import asdict
import datetime as dt
from html import escape
import hashlib
import math

from analysis.package_b_analytics import analyze_snapshot
from analysis.package_b_evidence import canonical_digest
from analysis.research_studio import rsi_zone
from ingestion.package_a_contracts import utc
from ingestion.researchbitcoin_catalog import CATALOG
from rendering.market_design import RESEARCH_CSS
from rendering.portable_report import make_portable
from validation.content_checks import audit_report_html

STYLE='mercados-research-studio-package-b-v1'

CSS=RESEARCH_CSS+r'''
*{box-sizing:border-box}body{margin:0}.report{max-width:1120px;margin:auto;padding:28px}
.page{background:white;border:1px solid var(--line);border-radius:12px;padding:36px;margin:0 0 24px}
.cover{border-top:7px solid var(--deep)}.brand{font:700 12px var(--mono);letter-spacing:.14em;color:var(--sea)}
h1{font:700 44px/1.12 var(--serif);letter-spacing:-.04em;color:var(--deep);margin:14px 0}
h2{font:700 27px/1.25 var(--serif);color:var(--deep);margin:10px 0 18px}
h3{font:700 18px var(--serif);margin:0 0 12px;color:var(--deep)}
.sub{color:var(--muted)}.demo{font:700 11px var(--mono);background:#EDF2F8;padding:8px 12px;border-radius:4px;display:inline-block}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;margin:22px 0}
.metric,.panel{border:1px solid var(--line);padding:17px;border-radius:8px;background:var(--paper);min-width:0}
.label{color:var(--muted);font-size:12px}.value,.big-price,.tk-price{font:700 25px var(--mono);color:var(--deep);display:block;line-height:1.4}
.change{font:600 13px var(--mono)}.up{color:var(--up)}.dn{color:var(--dn)}
.fact{border-left:3px solid var(--sea);padding:12px 18px;background:#F3F6FA;margin:20px 0}
.chart{margin:20px 0;padding:14px;border:1px solid var(--line);border-radius:8px;overflow-x:auto}
.chart svg{display:block;width:100%;min-width:600px;height:auto}.caption{font-size:11px;color:var(--muted);margin:9px 0 0}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.kpis .value{font-size:18px}
.scenarios{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:20px}
.scenario{padding:16px;background:#F3F6FA;border-radius:6px;font-size:12px}.scenario p{margin:0}
.note{color:var(--muted);font-size:12px}.quality{font:11px var(--mono);color:var(--sea)}
.sources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;background:#F3F6FA;color:var(--deep);font-weight:600}
th.num{white-space:nowrap}
th,td{padding:8px 9px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.num{text-align:right;font-family:var(--mono)}
.table-wrap{overflow-x:auto;margin:12px 0}.table-wrap table{min-width:570px}.table-wrap .num{white-space:nowrap}.table-help{font-size:11px;color:var(--muted)}
.digest{font:10px var(--mono);overflow-wrap:anywhere;word-break:break-all}.footer{border-top:1px solid var(--line);padding-top:13px;margin-top:22px;font-size:10px;color:var(--muted)}
.methods{display:grid;grid-template-columns:65px minmax(0,1fr);gap:4px 10px}.methods dd{margin:0}
.blocked{border:2px solid var(--dn);padding:18px;color:var(--dn)}a{color:var(--sea)}
tfoot td{border-bottom:0}tfoot .footer{margin-top:6px;padding-top:6px}
@media(max-width:600px){.report{padding:10px}.page{padding:22px 16px;margin-bottom:14px}h1{font-size:33px}h2{font-size:24px}.grid,.scenarios,.sources{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.kpis .metric{padding:10px}.value{font-size:23px}th,td{padding:6px;font-size:11px}}
@page{size:A4;margin:13mm 12mm}
@media print{body{background:white;font-size:11px}.report{padding:0;max-width:none}.page{border:0;border-radius:0;padding:0;margin:0;break-after:page;box-shadow:none}.page:last-child{break-after:auto}.chart{padding:8px;overflow:visible;break-inside:avoid}.chart svg{min-width:0}h1{font-size:37px}.grid{margin:17px 0}.metric{padding:12px;break-inside:avoid}.panel{padding:8px;break-inside:avoid}.panel h3{margin-bottom:6px}.scenarios{gap:10px}.scenario{padding:12px;font-size:10px}th,td{padding:5px 7px;font-size:10px}.footer{font-size:9px;break-before:avoid;break-inside:avoid}.digest{font-size:8px}.table-wrap{overflow:visible;margin:6px 0}.table-wrap table{min-width:0}thead{display:table-header-group}tr{break-inside:avoid}h2,h3{break-after:avoid}.table-help{display:none}}
'''


def number(value,precision=2,prefix=''):
    return 'NO_DATA' if value is None else prefix+format(value,f',.{precision}f')


def _metric(label,value,unit='',precision=2):
    return '<div class="metric"><div class="label">'+escape(label)+'</div><span class="value">'+escape(number(value,precision))+'</span><div class="label">'+escape(unit)+'</div></div>'


def _chart(asset):
    if not asset.closes:return '<div class="blocked">NO_DATA · serie de cierres no utilizable</div>'
    points=asset.closes[-60:]
    x0=utc(points[0][0]).timestamp();x1=utc(points[-1][0]).timestamp()
    low=min(v for _,v in points);high=max(v for _,v in points)
    pad=max((high-low)*.1,abs(high)*.001,1e-6)
    ymin=low-pad;ymax=high+pad
    coords=[]
    for stamp,value in points:
        x=78+(utc(stamp).timestamp()-x0)/max(1,x1-x0)*754
        y=20+(1-(value-ymin)/(ymax-ymin))*184
        coords.append(f'{x:.4f},{y:.4f}')
    marks=[]
    for i in range(5):
        y=20+i*46;value=ymax-i*(ymax-ymin)/4
        marks.append(f'<line x1="78" y1="{y}" x2="832" y2="{y}" stroke="#D9E0E8"/><text x="68" y="{y+5}" text-anchor="end" font-size="13">{number(value)}</text>')
    for i in range(3):
        x=78+i*377
        stamp=dt.datetime.fromtimestamp(x0+i*(x1-x0)/2,dt.timezone.utc).strftime('%d %b')
        marks.append(f'<text x="{x}" y="230" text-anchor="middle" font-size="13">{stamp}</text>')
    digest=canonical_digest(points)
    return ('<div class="chart"><svg viewBox="0 0 860 260" role="img" '
        f'aria-label="Cierres {escape(asset.symbol)} en USD, fechas UTC" data-series-sha256="{digest}">'
        '<g fill="#526275" font-family="Arial,sans-serif">'+''.join(marks)+
        '<text x="80" y="254" font-size="12">USD · fecha UTC</text></g>'
        '<polyline fill="none" stroke="#1D5C73" stroke-width="2.6" points="'+' '.join(coords)+'"/>'
        f'</svg><p class="caption">{len(points)} observaciones archivadas · '
        f'{escape(points[0][0][:10])} a {escape(points[-1][0][:10])}. '
        'El trazo une observaciones disponibles; no rellena sesiones ausentes.</p></div>')


def _source_row(series,quality):
    p=series.points[-1]
    label=CATALOG[series.metric].title if series.provider=='researchbitcoin' else series.metric
    unit='fracción RAW 0..1' if series.raw_scale=='fraction_0_1' else series.unit
    raw_value=format(p.value,'.12g') if series.unit=='fraction' else number(p.value,4)
    state=quality.get(series.key,'UNVERIFIED')
    label_quality={'WITHIN_LOCAL_FRESHNESS_BUDGET':'Dentro del umbral local',
                   'STALE':'STALE · dato antiguo','FUTURE':'FUTURE · dato posterior'}.get(state,state)
    vintage=('<br><span class="note">'+('Vintage '+escape(p.dimension) if p.dimension else 'VINTAGE_UNKNOWN')+'</span>') if series.provider=='fred' else ''
    return (f'<tr data-series-sha256="{series.sha256}"><td><a href="#evidence-{series.sha256}">{escape(label)}</a></td>'
            f'<td class="num">{escape(raw_value)}</td><td>{escape(unit)}</td>'
            f'<td>{escape(p.observed_utc[:16].replace("T"," "))} UTC{vintage}<br><span class="quality" data-freshness="{escape(state)}">{escape(label_quality)}</span></td></tr>')


def _section_table(series,quality):
    if not series:return '<p class="quality">NO_DATA · fuente sin observaciones utilizables</p>'
    return '<div class="table-wrap" tabindex="0" role="region" aria-label="Tabla de datos; desplazar horizontalmente en móvil"><table><thead><tr><th>Métrica</th><th class="num">Valor RAW</th><th>Unidad</th><th>Observación UTC</th></tr></thead><tbody>'+''.join(_source_row(s,quality) for s in series)+'</tbody></table></div><p class="table-help">En móvil, desplaza la tabla para consultar unidad y fecha sin cortar las cifras.</p>'


def render_report(model):
    snapshot=model.snapshot
    installed=snapshot.mode in ('OFFLINE_INSTALLED_SCHEMA','OFFLINE_LIVE_BINDING','PRIVATE_LIVE_EVIDENCE')
    real=snapshot.mode=='PRIVATE_LIVE_EVIDENCE'
    closes={s.symbol:s for s in snapshot.series if s.provider=='yahoo' and s.metric=='close' and s.frequency=='d1'}
    quality={q.key:q.freshness for q in model.source_quality}
    generated=utc(snapshot.asof_utc).strftime('%Y-%m-%d %H:%M')
    foot='<div class="footer">Mercados Daily Pro / Research Studio · '+('diagnóstico privado · publicación no autorizada' if real else 'ejemplo sintético')+' · verificación externa pendiente</div>'
    tickers=[]
    for asset in model.assets:
        label='BTC/USD' if asset.symbol=='BTC-USD' else asset.symbol
        color='up' if asset.change_pct is not None and asset.change_pct>=0 else 'dn'
        tickers.append('<div class="metric ticker-item"><div class="tk-pair">'+escape(label)+'</div>'
                       '<div class="tk-price">'+escape(number(asset.close,2,'$'))+'</div>'
                       f'<div class="change {color}">'+escape(number(asset.change_pct,2))+
                       '% vs cierre observado anterior</div><div class="label">'+escape((asset.observed_utc or 'NO_DATA')[:10])+' UTC</div></div>')
    btc=model.assets[0]
    facts='; '.join(a.symbol+' '+number(a.close,2,'$')+' ('+number(a.change_pct,2)+'%)' for a in model.assets)
    reading=('BTC '+('por encima' if btc.close>=btc.sma20 else 'por debajo')+' de SMA 20, '
             +rsi_zone(btc.rsi)+'. Son relaciones descriptivas sobre estas series, no causalidad.') if btc.close is not None and btc.sma20 is not None else 'NO_DATA · no se confirma lectura técnica sin inputs suficientes.'
    pages=['<section class="page cover"><div class="brand">MERCADOS DAILY PRO / RESEARCH STUDIO</div>'
        '<h1>El mercado,<br>con evidencia.</h1><p class="sub">Bitcoin · renta variable · oro · macro · on-chain</p>'+
        ('<div class="demo">DATOS REALES · DIAGNÓSTICO PRIVADO</div>' if real else '<div class="demo">EJEMPLO SINTÉTICO — no representa precios reales</div>')+
        '<p class="note">Generado '+escape(generated)+' UTC · corte de observaciones '+escape(snapshot.cutoff_utc[:10])+' UTC</p>'
        '<div class="grid">'+''.join(tickers)+'</div>'
        '<div id="executive-brief"><h2>Hechos observados</h2><div class="fact">'+escape(facts)+'</div>'
        '<h3>Lectura condicional</h3><p>'+escape(reading)+'</p>'
        '<h3>Riesgos y datos pendientes</h3><p>Unidades y derechos de las fuentes reales pendientes de verificación. '
        'ETF, liquidaciones, noticias y agenda verificables: NO_DATA en este perfil de ensayo.</p></div>'+foot+'</section>']
    for asset in model.assets:
        content='<section class="page"><div class="brand">MERCADO / '+escape(asset.symbol)+'</div><h2 class="section-title">'+escape(asset.instrument)+'</h2>'
        if asset.errors:
            content+='<div class="blocked">BLOCKED · '+escape('; '.join(asset.errors))+'</div>'
            if asset.quality=='PARTIAL_CLOSE_ONLY':
                content+='<p class="note">Solo cierres disponibles; OHLCV, ATR y escenarios: NO_DATA.</p>'+_chart(asset)
                content+='<div class="kpis">'+_metric('RSI(14)',asset.rsi,rsi_zone(asset.rsi),1)+_metric('SMA 20',asset.sma20,'USD')+_metric('SMA 200',asset.sma200,'USD')+_metric('ATR 14',None,'NO_DATA')+'</div>'
        else:
            content+='<div class="price-row"><span class="big-price">'+number(asset.close,2,'$')+'</span><span class="note">USD por '+('BTC' if asset.symbol=='BTC-USD' else 'participación')+' · '+escape(asset.observed_utc[:10])+' UTC</span></div>'
            content+=_chart(asset)
            content+='<div class="kpis">'+_metric('RSI(14)',asset.rsi,rsi_zone(asset.rsi),1)+_metric('SMA 20',asset.sma20,'USD')+_metric('SMA 200',asset.sma200,'USD')+_metric('ATR 14',asset.atr14,'USD · media true range')+'</div>'
            content+='<div class="scenarios">'+''.join('<div class="scenario '+cls+'"><h3>'+title+'</h3><p>'+escape(text)+'</p></div>' for cls,title,text in zip(('s-bull','s-base','s-bear'),('Alcista','Base','Bajista'),asset.scenarios))+'</div>'
        content+='<p class="caption">Fuente local: Yahoo / '+escape(asset.symbol)+' / '+('cierres d1, OHLCV ausente' if asset.quality=='PARTIAL_CLOSE_ONLY' else 'OHLCV d1')+'. Instrumento y sesiones conservados; calendario bursátil externo sin verificar.</p>'
        if asset.symbol in closes:
            content+='<p class="caption"><a href="#evidence-'+closes[asset.symbol].sha256+'">Consultar la serie y su evidencia</a></p>'
        content+=foot+'</section>'
        pages.append(content)
    fred=[s for s in snapshot.series if s.provider=='fred']
    derivatives=[s for s in snapshot.series if s.provider in ('binance','bybit')]
    pages.append('<section class="page"><div class="brand">CONTEXTO / MACRO Y DERIVADOS</div><h2>El contexto disponible</h2>'
        '<h3>FRED · observación y revisión</h3>'+_section_table(fred,quality)+
        '<p class="caption">Cada valor usa la última vintage conocida al momento de generación; el índice CPIAUCSL no se rotula como inflación interanual.</p>'
        '<h3>Derivados · exchanges separados</h3>'+''.join('<div class="panel"><h3>'+escape(s.provider+' / '+s.metric)+'</h3>'+_section_table([s],quality)+'</div>' for s in derivatives)+
        ('' if derivatives else '<p class="quality">NO_DATA · OI y funding</p>')+
        '<p class="note">OI en unidad nativa BTC; funding liquidado en fracción. No hay OI agregado ni annualización inferida.</p>'
        '<div class="grid">'+''.join('<div class="panel"><h3>'+label+'</h3><span class="quality">NO_DATA</span></div>' for label in ('Flujos ETF','Liquidaciones','Noticias y agenda'))+'</div>'+foot+'</section>')
    for provider,title in [('researchbitcoin','ResearchBitcoin · cohortes y costo base'),('bitview','Bitview · series nativas')]:
        series=[s for s in snapshot.series if s.provider==provider]
        pages.append('<section class="page"><div class="brand">ON-CHAIN / '+provider.upper()+'</div><h2>'+title+'</h2>'+
            '<p class="note">Definiciones y denominadores propios de la fuente. Se conservan escala y valores RAW; no se mezclan proveedores ni se crean señales.</p>'+
            _section_table(series,quality)+'<p class="quality">UNVERIFIED · unidades/metodología externas. Disponibilidad local no prueba entitlement ni historia completa.</p>'+foot+'</section>')
    source_rows=[]
    for s in snapshot.series:
        provenance=', '.join(sorted({p.provenance for p in s.points}))
        provenance_label={'SIMULATED_REQUEST_VERIFIED':'Petición simulada',
            'SIMULATED_HTTP_REQUEST_VERIFIED':'Petición HTTP simulada',
            'HTTP_REQUEST_VERIFIED':'Petición HTTP reconciliada',
            'LEGACY_NO_REQUEST':'Receipt ausente',
            'LEGACY_NO_REQUEST_VINTAGE_UNKNOWN':'Receipt/vintage ausentes',
            'LEGACY_RAW_WITHOUT_ACQUISITION':'RAW sin acquisition',
            'LEGACY_REQUEST_LINK_PRESENT_UNRECONCILED':'Link sin reconciliar'}.get(provenance,provenance)
        source_rows.append('<tr id="evidence-'+s.sha256+'" data-evidence-series-sha256="'+s.sha256+'"><td>'+escape(s.provider+' / '+s.metric+' / '+s.symbol)+(('<br><span class="quality" data-provenance="'+escape(provenance)+'">'+escape(provenance_label)+'</span>') if installed else '')+'</td><td class="num">'+str(len(s.points))+'</td><td>'+escape(s.points[0].observed_utc[:10]+' → '+s.points[-1].observed_utc[:10])+'</td><td class="digest">'+s.sha256+'</td></tr>')
    pages.append('<section class="page"><div class="brand">METODOLOGÍA / EVIDENCIA</div><h2>Fuentes y trazabilidad</h2>'
        '<p class="note">'+('Evidencia privada: adquisiciones HTTP reconciliadas, procedencia heredada explícita; permisos externos e historia completa sin verificar. ' if real else 'Fixture de esquema instalado: procedencia heredada explícita; receipts ausentes no se reconstruyen. Adquisiciones nuevas son FAKE_HTTP. Vintages ausentes: VINTAGE_UNKNOWN. ' if installed else 'Un snapshot SQLite read-only reúne RAW/SHA, native request provenance, observaciones, cohortes y revisiones. ')+'Todos los gráficos/tablas usan este mismo conjunto.</p>'
        '<p class="digest">Snapshot SHA-256: '+snapshot.sha256+'</p><p class="note">Cálculos: '+escape(model.calculation_version)+'; RSI '+escape(btc.rsi_method)+'. ATR: '+escape(btc.atr_method)+'.</p>'
        '<dl class="note methods">'+''.join('<dt>'+escape(name)+'</dt><dd>'+escape(parameters)+'</dd>' for name,parameters in model.calculation_parameters)+'</dl>'
        '<table><thead><tr><th>Serie / fuente</th><th class="num">Puntos</th><th>Rango observado</th><th>SHA de serie</th></tr></thead><tbody>'+''.join(source_rows)+'</tbody>'+('<tfoot><tr><td colspan="4">'+foot+'</td></tr></tfoot>' if installed else '')+'</table>'+('' if installed else foot)+'</section>')
    return ('<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="market-design-system" content="'+STYLE+'"><title>Mercados Research Studio · '+('diagnóstico privado' if real else 'ejemplo sintético')+'</title>'
        '<style>'+CSS+'</style></head><body data-b-report-version="v1" data-snapshot-sha256="'+snapshot.sha256+'"><main class="report">'+''.join(pages)+'</main></body></html>')


def audit_candidate_report(model,document):
    """Local reconciliation only, never a productive publication approval.

    Recompute analytical model from immutable evidence, then compare exact
    deterministic HTML. Mutable/wrong calculations and rendered numbers,
    geometry, units or dates cannot be accepted by a matching header alone.
    """
    failures=[]
    if model.critical_errors:failures.append('CRITICAL_INPUT_DATA')
    try:
        actual=analyze_snapshot(model.snapshot)
        if actual!=model:failures.append('ANALYTICS_DO_NOT_MATCH_EVIDENCE')
        if render_report(actual)!=document:failures.append('HTML_DOES_NOT_MATCH_EVIDENCE')
    except (ValueError,TypeError,AttributeError):
        failures.append('MODEL_EVIDENCE_INVALID')
    try:make_portable(document)
    except ValueError:failures.append('REMOTE_OR_UNSAFE_DEPENDENCY')
    content_errors=audit_report_html(document,report_day=utc(model.snapshot.asof_utc).date())
    if content_errors:failures.append('LEGACY_FINANCIAL_CONTENT_CHECKS')
    stale=[q for q in model.source_quality if q.freshness in ('STALE','FUTURE')]
    return {'warnings':['STALE_SOURCE_SERIES'] if stale else [],
            'stale_source_series':[list(q.key) for q in stale],
            'state':('BLOCKED_OFFLINE_REPORT_EVIDENCE' if failures else
                     'OFFLINE_REPORT_LOCAL_RECONCILIATION_PASS'),
            'failures':sorted(set(failures)),'financial_content_errors':content_errors,
            'html_sha256':hashlib.sha256(document.encode()).hexdigest(),
            'snapshot_sha256':model.snapshot.sha256,
            'score_local':0 if failures else 100,
            'production_publication_authorized':False,
            'external_history_verified':False,
            'legacy_publication_gate_execution':'NOT_EXECUTED_BY_RENDERER'}
