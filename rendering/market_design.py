"""Mercados Research Studio — proprietary, offline editorial design language.

CSS-only progressive enhancement of the existing report. No data values,
charts, source labels, validation gates, JS logic, or semantic sections change.
"""
STYLE_VERSION = "mercados-research-studio-v1"

RESEARCH_CSS = r"""
/* MERCADOS RESEARCH STUDIO / editorial design system v1 */
:root{
 --ink:#152436; --deep:#12243A; --ivory:#F3F6FA; --paper:#FFFFFF;
 --sea:#1D5C73; --mint:#CDE4EA; --coral:#B96F58; --gold:#8B6B32;
 --muted:#526275; --line:#D9E0E8; --up:#187454; --dn:#B43C48;
 --serif:Georgia,'Times New Roman',serif;
 --mono:'SFMono-Regular',Consolas,'Liberation Mono',monospace;
 color-scheme:light;
}
html{scroll-behavior:auto}
body{background:var(--ivory);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
 font-size:14px;line-height:1.62;-webkit-font-smoothing:antialiased}
.wrap{max-width:1160px;margin:auto;padding:30px 28px 50px}
.hdr{background:var(--deep);border-radius:14px;padding:38px 40px 34px;
 border:1px solid #2E4B64;box-shadow:0 9px 24px rgba(17,34,54,.09)}
.hdr h1{font-family:var(--serif);font-weight:700;letter-spacing:-.035em;
 font-size:clamp(27px,3.4vw,39px);color:#FFF;line-height:1.12}
.hdr-kicker,.kicker{font-family:var(--mono);letter-spacing:.12em;
 text-transform:uppercase;font-weight:700}
.hdr-kicker{color:#C8DFEA}
.hdr .sub,.hdr-meta{color:#E1EAF0}
.section-title{font-family:var(--serif);font-weight:700;font-size:clamp(21px,2.35vw,28px);
 color:var(--deep);line-height:1.25;border-bottom:1px solid var(--line);
 margin-bottom:20px;padding-bottom:12px}
.kicker{color:var(--sea)}
.card,.chart-box,.expert-box,.news-item,.macro-card,.stat-item{
 box-shadow:0 2px 9px rgba(20,38,60,.035);
 border-color:var(--line)}
.card,.chart-box,.expert-box{border-radius:12px}
.card h3,.chart-box h3,.expert-box h3{
 font-family:var(--serif);font-weight:700;font-size:17px;color:var(--deep);
 letter-spacing:-.012em}
.stats-bar{gap:9px}
.stat-item{background:#FFF;border:1px solid var(--line);border-radius:10px;
 min-width:0;padding:14px 15px}
.stat-item .slbl{font-size:10px;letter-spacing:.065em;font-weight:700;
 color:var(--muted);line-height:1.3}
.stat-item .sval{font-family:var(--mono);font-size:19px;font-weight:700;
 font-variant-numeric:tabular-nums;letter-spacing:-.035em;line-height:1.3}
.stat-item .ssub{font-size:10px;line-height:1.5;color:var(--muted);overflow-wrap:anywhere}
.big-price{font-family:var(--mono);font-weight:700;font-variant-numeric:tabular-nums}
.ticker-item .tk-price,.mc .mc-val,.ma-item .ma-price,.lev-price,.o-val{
 font-family:var(--mono);font-variant-numeric:tabular-nums}
.signal-table,.tbl{font-variant-numeric:tabular-nums}
.signal-table th,.tbl th{background:#F3F6F9;font-weight:700;
 color:var(--deep);letter-spacing:.045em}
.signal-table td,.tbl td{border-bottom:1px solid var(--line)}
a{color:var(--sea);text-underline-offset:3px}
a:hover{text-decoration:underline}
a:focus-visible{outline:3px solid #277E9B;outline-offset:3px;border-radius:3px}
.news-item .source,.expert-box .source{font-family:var(--mono);font-size:10px}
#onchain-complement>.ssub{color:var(--muted)}
#onchain-complement .stat-item{border-top:3px solid var(--sea)}
#onchain-complement .sval{color:var(--deep)}
#onchain-complement .stat-item:has([data-api-raw]){border-color:var(--line)}
.footer{color:var(--muted);line-height:1.6;border-top:1px solid var(--line)}
@media(max-width:760px){
 .wrap{padding:14px 12px 30px}
 .hdr{padding:27px 20px}
 .section-title{font-size:22px}
 .stats-bar{gap:7px}
 .stat-item{padding:10px}
 .stat-item .sval{font-size:16px}
 .chart-box svg{max-width:100%;height:auto}
}
@media(prefers-reduced-motion:reduce){
 *,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important}
}
@page{size:A4;margin:14mm}
@media print{
 :root{--ivory:#fff;--paper:#fff}
 html,body{background:#FFF!important;color:#111!important}
 .wrap{padding:0!important;max-width:none}
 .hdr{background:#fff!important;border:0;border-bottom:2px solid #12243A;
 color:#111!important;border-radius:0;box-shadow:none;padding:0 0 14px!important}
 .hdr h1,.hdr .sub,.hdr .hdr-kicker,.hdr-meta{color:#111!important}
 .card,.chart-box,.expert-box,.stat-item,.news-item{
 box-shadow:none!important;break-inside:avoid;page-break-inside:avoid}
 section{break-before:auto}
 .section-title{break-after:avoid}
 a{color:#111;text-decoration:underline}
 svg{max-width:100%;height:auto}
 .footer{font-size:9px}
}
"""
