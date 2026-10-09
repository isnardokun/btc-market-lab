#!/usr/bin/env python3
"""
BTC Research — Chart Generator
Pulls data from SQLite DB and produces SVG charts.
"""
import sys, os, sqlite3
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from ingestion import config

def get_db():
    return sqlite3.connect(config.DB_PATH)

def ts_to_date(ts):
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")

def query_series(db, series_name: str, days: int = 365):
    """Returns list of (ts, value) for a series."""
    cur = db.execute("""
        SELECT d.ts, d.value
        FROM daily d
        JOIN series s ON d.series_id = s.id
        WHERE s.name = ?
        ORDER BY d.ts ASC
        LIMIT ?
    """, (series_name, days))
    return [(row[0], row[1]) for row in cur.fetchall() if row[1] is not None]

def query_two(db, s1: str, s2: str, days: int = 365):
    """Returns (ts, v1, v2) for two series."""
    cur = db.execute("""
        SELECT d1.ts, d1.value, d2.value
        FROM daily d1
        JOIN series s1 ON d1.series_id = s1.id
        JOIN daily d2 ON d2.series_id = (
            SELECT id FROM series WHERE name = ?
        ) AND d1.ts = d2.ts
        WHERE s1.name = ?
        ORDER BY d1.ts ASC
        LIMIT ?
    """, (s2, s1, days))
    return [(row[0], row[1], row[2]) for row in cur.fetchall() if row[1] is not None and row[2] is not None]

def minmax(data):
    vals = [v for _, v in data if v is not None]
    return min(vals), max(vals)

def scale(val, lo, hi, chart_lo, chart_hi, invert=False):
    """Scale a value to chart pixel coordinates."""
    if hi == lo:
        return chart_lo
    t = (val - lo) / (hi - lo)
    if invert:
        t = 1 - t
    return chart_lo + t * (chart_hi - chart_lo)

def svg_polyline(points, x_min, x_max, y_min, y_max, w, h, pad_l=70, pad_r=20, pad_t=20, pad_b=40, invert_y=True):
    """Convert (x_ts, y_val) list to SVG polyline points string."""
    if not points:
        return ""
    pts = []
    for ts, val in points:
        if val is None:
            continue
        x = pad_l + (ts - x_min) / (x_max - x_min) * (w - pad_l - pad_r)
        y = pad_t + (val - y_min) / (y_max - y_min) * (h - pad_t - pad_b) if not invert_y else \
            pad_t + (1 - (val - y_min) / (y_max - y_min)) * (h - pad_t - pad_b)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)

# ─── Chart 1: MVRV + Price ─────────────────────────────────────────────────

def chart_mvrv_price(db, output_path: str, days: int = 365*3):
    """MVRV ratio + price overlay — key for cycle tops and bottoms."""
    data_mvrv = query_series(db, "mvrv", days)
    data_price = query_series(db, "price", days)
    data_real  = query_series(db, "realized_price", days)

    if not data_mvrv:
        print(f"[WARN] No MVRV data in DB yet")
        return False

    W, H = 860, 320
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 20, 20, 45

    all_prices = [v for _, v in data_price if v is not None]
    all_mvrv   = [v for _, v in data_mvrv if v is not None]
    all_ts     = [ts for ts, _ in data_mvrv]

    x_min = min(all_ts)
    x_max = max(all_ts)
    price_min = min(all_prices) * 0.85
    price_max = max(all_prices) * 1.05
    mvrv_min  = max(0, min(all_mvrv) * 0.8)
    mvrv_max  = max(all_mvrv) * 1.1

    # Build price polyline
    price_pts = svg_polyline(data_price, x_min, x_max, price_min, price_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)
    mvrv_pts = svg_polyline(data_mvrv, x_min, x_max, mvrv_min, mvrv_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)

    # Realized price
    real_pts = svg_polyline(data_real, x_min, x_max, price_min, price_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)

    # Zones: MVRV > 3.5 = red zone (overvalued), MVRV < 1 = green (undervalued)
    zone_over_x1 = PAD_L
    zone_over_x2 = PAD_L + (x_max - x_min) / (x_max - x_min) * (W - PAD_L - PAD_R)
    zone_over_y1 = PAD_T + (1 - (3.5 - mvrv_min) / (mvrv_max - mvrv_min)) * (H - PAD_T - PAD_B)
    zone_under_y1 = PAD_T + (1 - (1.0 - mvrv_min) / (mvrv_max - mvrv_min)) * (H - PAD_T - PAD_B)

    # Dates for x axis
    def format_date(ts):
        d = datetime.utcfromtimestamp(ts)
        return d.strftime("%b %Y")

    x_labels = []
    step = (x_max - x_min) / 6
    for i in range(7):
        ts = x_min + step * i
        x = PAD_L + step * i / (x_max - x_min) * (W - PAD_L - PAD_R)
        x_labels.append((x, format_date(ts)))

    svg = f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="font-family:Manrope,sans-serif">
  <rect width="{W}" height="{H}" fill="#04201c"/>
  <rect x="{PAD_L}" y="{PAD_T}" width="{{W-PAD_L-PAD_R}}" height="{{H-PAD_T-PAD_B}}" fill="#092b27" opacity="0.4"/>

  <!-- Grid -->
  <line x1="{PAD_L}" y1="{{PAD_T+(H-PAD_T-PAD_B)*0.33}}" x2="{{W-PAD_R}}" y2="{{PAD_T+(H-PAD_T-PAD_B)*0.33}}" stroke="rgba(255,255,255,0.05)" stroke-width="1" stroke-dasharray="4,4"/>
  <line x1="{PAD_L}" y1="{{PAD_T+(H-PAD_T-PAD_B)*0.66}}" x2="{{W-PAD_R}}" y2="{{PAD_T+(H-PAD_T-PAD_B)*0.66}}" stroke="rgba(255,255,255,0.05)" stroke-width="1" stroke-dasharray="4,4"/>

  <!-- Overvalued zone -->
  <rect x="{PAD_L}" y="{int(PAD_T)}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(zone_over_y1-PAD_T)}}" fill="rgba(220,38,38,0.08)"/>
  <line x1="{PAD_L}" y1="{int(zone_over_y1)}" x2="{{W-PAD_R}}" y2="{int(zone_over_y1)}" stroke="rgba(220,38,38,0.4)" stroke-width="1" stroke-dasharray="4,3"/>
  <text x="{{W-PAD_R+4}}" y="{int(zone_over_y1+4)}" font-size="9" fill="#DC2626" font-family="IBM Plex Mono,monospace">3.5</text>

  <!-- Undervalued zone -->
  <rect x="{PAD_L}" y="{int(zone_under_y1)}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(H-PAD_B-zone_under_y1)}}" fill="rgba(22,163,74,0.08)"/>
  <line x1="{PAD_L}" y1="{int(zone_under_y1)}" x2="{{W-PAD_R}}" y2="{int(zone_under_y1)}" stroke="rgba(22,163,74,0.4)" stroke-width="1" stroke-dasharray="4,3"/>
  <text x="{{W-PAD_R+4}}" y="{int(zone_under_y1+4)}" font-size="9" fill="#16A34A" font-family="IBM Plex Mono,monospace">1.0</text>

  <!-- Realized price -->
  <polyline points="{real_pts}" fill="none" stroke="#d5a844" stroke-width="1.5" stroke-dasharray="5,3" opacity="0.7"/>

  <!-- MVRV line (left axis) -->
  <polyline points="{mvrv_pts}" fill="none" stroke="#ef765f" stroke-width="2"/>

  <!-- Price (right axis) -->
  <polyline points="{price_pts}" fill="none" stroke="#8bd5bd" stroke-width="2.5"/>

  <!-- X axis labels -->
  {"".join(f'<text x="{x:.0f}" y="{{H-8}}" text-anchor="middle" font-size="9" fill="#63766f" font-family="IBM Plex Mono,monospace">{label}</text>' for x, label in x_labels)}

  <!-- Left Y axis: MVRV -->
  <text x="8" y="{int((PAD_T+H-PAD_B)/2)}" text-anchor="middle" font-size="9" fill="#ef765f" font-family="IBM Plex Mono,monospace" transform="rotate(-90,8,{int((PAD_T+H-PAD_B)/2)})">MVRV</text>
  <text x="{PAD_L-6}" y="{int(zone_over_y1+4)}" text-anchor="end" font-size="8" fill="#ef765f" font-family="IBM Plex Mono,monospace">3.5</text>
  <text x="{PAD_L-6}" y="{int(zone_under_y1+4)}" text-anchor="end" font-size="8" fill="#ef765f" font-family="IBM Plex Mono,monospace">1.0</text>

  <!-- Right Y axis: Price -->
  <text x="{{W-6}}" y="{int((PAD_T+H-PAD_B)/2)}" text-anchor="middle" font-size="9" fill="#8bd5bd" font-family="IBM Plex Mono,monospace" transform="rotate(90,{{W-6}},{int((PAD_T+H-PAD_B)/2)})">BTC USD</text>

  <!-- Legend -->
  <rect x="{PAD_L+10}" y="{H-32}" width="14" height="3" fill="#8bd5bd"/>
  <text x="{PAD_L+30}" y="{H-27}" font-size="10" fill="#8bd5bd" font-family="Manrope,sans-serif">BTC Price</text>
  <rect x="{PAD_L+130}" y="{H-32}" width="14" height="3" fill="#ef765f"/>
  <text x="{PAD_L+150}" y="{H-27}" font-size="10" fill="#ef765f" font-family="Manrope,sans-serif">MVRV</text>
  <line x1="{PAD_L+220}" y1="{H-30}" x2="{PAD_L+234}" y2="{H-30}" stroke="#d5a844" stroke-width="1.5" stroke-dasharray="5,3"/>
  <text x="{PAD_L+240}" y="{H-27}" font-size="10" fill="#d5a844" font-family="Manrope,sans-serif">Realized Price</text>

  <text x="{W//2}" y="14" text-anchor="middle" font-size="11" fill="#f4eddd" font-family="Fraunces,serif" font-weight="500">MVRV Ratio — Bitcoin Cycle Monitor</text>
</svg>'''

    with open(output_path, "w") as f:
        f.write(svg)
    return True

# ─── Chart 2: SOPR + RSI-like ───────────────────────────────────────────────

def chart_sopr_price(db, output_path: str, days: int = 365*2):
    """SOPR + price. Shows when SOPR > 1 (profit) vs < 1 (loss)."""
    data_sopr  = query_series(db, "sopr_1w", days)
    data_sth   = query_series(db, "sth_sopr", days)
    data_price = query_series(db, "price",    days)

    if not data_sopr:
        print(f"[WARN] No SOPR data in DB yet")
        return False

    W, H = 860, 300
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 20, 20, 45

    all_ts    = [ts for ts, _ in data_sopr]
    all_sopr  = [v for _, v in data_sopr  if v is not None]
    all_sth   = [v for _, v in data_sth   if v is not None]
    all_price = [v for _, v in data_price  if v is not None]

    x_min = min(all_ts)
    x_max = max(all_ts)
    sopr_min  = 0.5
    sopr_max  = max(all_sopr + all_sth) * 1.1
    price_min = min(all_price) * 0.85
    price_max = max(all_price) * 1.05

    sopr_pts = svg_polyline(data_sopr, x_min, x_max, sopr_min, sopr_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)
    sth_pts  = svg_polyline(data_sth,  x_min, x_max, sopr_min, sopr_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)
    price_pts = svg_polyline(data_price, x_min, x_max, price_min, price_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)

    # SOPR = 1 line
    y_1 = PAD_T + (1 - (1.0 - sopr_min) / (sopr_max - sopr_min)) * (H - PAD_T - PAD_B)

    # X axis
    def format_date(ts):
        return datetime.utcfromtimestamp(ts).strftime("%b %Y")
    x_labels = []
    step = (x_max - x_min) / 6
    for i in range(7):
        ts = x_min + step * i
        x = PAD_L + step * i / (x_max - x_min) * (W - PAD_L - PAD_R)
        x_labels.append((x, format_date(ts)))

    svg = f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="font-family:Manrope,sans-serif">
  <rect width="{W}" height="{H}" fill="#04201c"/>
  <rect x="{PAD_L}" y="{PAD_T}" width="{{W-PAD_L-PAD_R}}" height="{{H-PAD_T-PAD_B}}" fill="#092b27" opacity="0.4"/>

  <!-- SOPR = 1 baseline -->
  <line x1="{PAD_L}" y1="{int(y_1)}" x2="{{W-PAD_R}}" y2="{int(y_1)}" stroke="rgba(239,118,95,0.5)" stroke-width="1.5" stroke-dasharray="6,3"/>
  <text x="{{W-PAD_R+4}}" y="{int(y_1+4)}" font-size="9" fill="#ef765f" font-family="IBM Plex Mono,monospace">1.0</text>

  <!-- Area above 1: green tint -->
  <rect x="{PAD_L}" y="{PAD_T}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(y_1-PAD_T)}}" fill="rgba(22,163,74,0.06)"/>
  <rect x="{PAD_L}" y="{int(y_1)}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(H-PAD_B-y_1)}}" fill="rgba(220,38,38,0.06)"/>

  <!-- Price -->
  <polyline points="{price_pts}" fill="none" stroke="#8bd5bd" stroke-width="2.5"/>

  <!-- STH SOPR -->
  <polyline points="{sth_pts}" fill="none" stroke="#d5a844" stroke-width="1.5" opacity="0.8"/>

  <!-- SOPR -->
  <polyline points="{sopr_pts}" fill="none" stroke="#ef765f" stroke-width="2"/>

  <!-- X axis -->
  {"".join(f'<text x="{x:.0f}" y="{{H-8}}" text-anchor="middle" font-size="9" fill="#63766f" font-family="IBM Plex Mono,monospace">{label}</text>' for x, label in x_labels)}

  <!-- Left Y axis -->
  <text x="8" y="{int((PAD_T+H-PAD_B)/2)}" text-anchor="middle" font-size="9" fill="#ef765f" font-family="IBM Plex Mono,monospace" transform="rotate(-90,8,{int((PAD_T+H-PAD_B)/2)})">SOPR</text>

  <!-- Legend -->
  <rect x="{PAD_L+10}" y="{H-32}" width="14" height="3" fill="#8bd5bd"/>
  <text x="{PAD_L+30}" y="{H-27}" font-size="10" fill="#8bd5bd" font-family="Manrope,sans-serif">BTC Price</text>
  <rect x="{PAD_L+140}" y="{H-32}" width="14" height="3" fill="#ef765f"/>
  <text x="{PAD_L+160}" y="{H-27}" font-size="10" fill="#ef765f" font-family="Manrope,sans-serif">SOPR</text>
  <rect x="{PAD_L+210}" y="{H-32}" width="14" height="3" fill="#d5a844"/>
  <text x="{PAD_L+230}" y="{H-27}" font-size="10" fill="#d5a844" font-family="Manrope,sans-serif">STH SOPR</text>

  <text x="{W//2}" y="14" text-anchor="middle" font-size="11" fill="#f4eddd" font-family="Fraunces,serif" font-weight="500">SOPR — Spent Output Profit Ratio</text>
</svg>'''

    with open(output_path, "w") as f:
        f.write(svg)
    return True

# ─── Chart 3: RSI (price-based, 14-day) + MVRV + Bands ───────────────────

def chart_price_rsi(db, output_path: str, days: int = 365*3):
    """Custom RSI computed from price + MVRV overlay."""
    data_price = query_series(db, "price", days)
    data_mvrv  = query_series(db, "mvrv",  days)

    if not data_price:
        return False

    W, H = 860, 300
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 20, 20, 45

    all_ts = [ts for ts, _ in data_price]
    x_min, x_max = min(all_ts), max(all_ts)
    all_prices = [v for _, v in data_price]
    price_min, price_max = min(all_prices)*0.85, max(all_prices)*1.05

    # Compute 14-day RSI from price
    gains = []
    losses = []
    rsi_vals = []
    for i in range(1, len(data_price)):
        delta = data_price[i][1] - data_price[i-1][1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:14]) / 14 if len(gains) >= 14 else 0
    avg_loss = sum(losses[:14]) / 14 if len(losses) >= 14 else 0
    for i in range(14, len(gains)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14
        avg_loss = (avg_loss * 13 + losses[i]) / 14
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        rsi_ts = data_price[i+1][0]
        rsi_vals.append((rsi_ts, rsi))

    price_pts = svg_polyline(data_price, x_min, x_max, price_min, price_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)
    rsi_pts    = svg_polyline(rsi_vals, x_min, x_max, 0, 100, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)

    # MVRV to right axis
    mvrv_min, mvrv_max = 0, 5
    mvrv_data = [(ts, v) for ts, v in data_mvrv if v is not None]
    mvrv_pts = svg_polyline(mvrv_data, x_min, x_max, mvrv_min, mvrv_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)

    def format_date(ts):
        return datetime.utcfromtimestamp(ts).strftime("%b %Y")
    x_labels = []
    step = (x_max - x_min) / 6
    for i in range(7):
        ts = x_min + step * i
        x = PAD_L + step * i / (x_max - x_min) * (W - PAD_L - PAD_R)
        x_labels.append((x, format_date(ts)))

    # RSI 70/30 lines
    y70 = PAD_T + (1 - (70 - 0) / 100) * (H - PAD_T - PAD_B)
    y30 = PAD_T + (1 - (30 - 0) / 100) * (H - PAD_T - PAD_B)
    y50 = PAD_T + (1 - 0.5) * (H - PAD_T - PAD_B)

    svg = f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="font-family:Manrope,sans-serif">
  <rect width="{W}" height="{H}" fill="#04201c"/>
  <rect x="{PAD_L}" y="{PAD_T}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(H-PAD_T-PAD_B)}}" fill="#092b27" opacity="0.4"/>

  <!-- RSI zones -->
  <rect x="{PAD_L}" y="{PAD_T}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(y70-PAD_T)}}" fill="rgba(220,38,38,0.07)"/>
  <rect x="{PAD_L}" y="{int(y30)}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(y70-y30)}}" fill="rgba(22,163,74,0.07)"/>
  <rect x="{PAD_L}" y="{int(y30)}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(H-PAD_B-y30)}}" fill="rgba(220,38,38,0.07)"/>

  <line x1="{PAD_L}" y1="{int(y70)}" x2="{{W-PAD_R}}" y2="{int(y70)}" stroke="rgba(220,38,38,0.4)" stroke-width="1" stroke-dasharray="4,3"/>
  <line x1="{PAD_L}" y1="{int(y30)}" x2="{{W-PAD_R}}" y2="{int(y30)}" stroke="rgba(22,163,74,0.4)" stroke-width="1" stroke-dasharray="4,3"/>
  <line x1="{PAD_L}" y1="{int(y50)}" x2="{{W-PAD_R}}" y2="{int(y50)}" stroke="rgba(213,168,68,0.3)" stroke-width="1" stroke-dasharray="4,3"/>
  <text x="{{W-PAD_R+4}}" y="{int(y70+4)}" font-size="9" fill="#DC2626" font-family="IBM Plex Mono,monospace">70</text>
  <text x="{{W-PAD_R+4}}" y="{int(y30+4)}" font-size="9" fill="#16A34A" font-family="IBM Plex Mono,monospace">30</text>
  <text x="{{W-PAD_R+4}}" y="{int(y50+4)}" font-size="9" fill="#d5a844" font-family="IBM Plex Mono,monospace">50</text>

  <!-- Price -->
  <polyline points="{price_pts}" fill="none" stroke="#8bd5bd" stroke-width="2"/>

  <!-- RSI line (left axis) -->
  <polyline points="{rsi_pts}" fill="none" stroke="#ef765f" stroke-width="2"/>

  <!-- MVRV (right, simplified as thin line) -->
  <polyline points="{mvrv_pts}" fill="none" stroke="#d5a844" stroke-width="1.5" opacity="0.6"/>

  <!-- X axis -->
  {"".join(f'<text x="{x:.0f}" y="{{H-8}}" text-anchor="middle" font-size="9" fill="#63766f" font-family="IBM Plex Mono,monospace">{label}</text>' for x, label in x_labels)}

  <text x="8" y="{int((PAD_T+H-PAD_B)/2)}" text-anchor="middle" font-size="9" fill="#ef765f" font-family="IBM Plex Mono,monospace" transform="rotate(-90,8,{int((PAD_T+H-PAD_B)/2)})">RSI(14)</text>
  <text x="{{W-6}}" y="{int((PAD_T+H-PAD_B)/2)}" text-anchor="middle" font-size="9" fill="#d5a844" font-family="IBM Plex Mono,monospace" transform="rotate(90,{{W-6}},{int((PAD_T+H-PAD_B)/2)})">MVRV</text>

  <!-- Legend -->
  <rect x="{PAD_L+10}" y="{H-32}" width="14" height="3" fill="#8bd5bd"/>
  <text x="{PAD_L+30}" y="{H-27}" font-size="10" fill="#8bd5bd" font-family="Manrope,sans-serif">BTC Price</text>
  <rect x="{PAD_L+140}" y="{H-32}" width="14" height="3" fill="#ef765f"/>
  <text x="{PAD_L+160}" y="{H-27}" font-size="10" fill="#ef765f" font-family="Manrope,sans-serif">RSI(14)</text>
  <rect x="{PAD_L+220}" y="{H-32}" width="14" height="3" fill="#d5a844"/>
  <text x="{PAD_L+240}" y="{H-27}" font-size="10" fill="#d5a844" font-family="Manrope,sans-serif">MVRV</text>

  <text x="{W//2}" y="14" text-anchor="middle" font-size="11" fill="#f4eddd" font-family="Fraunces,serif" font-weight="500">Price + RSI(14) + MVRV</text>
</svg>'''

    with open(output_path, "w") as f:
        f.write(svg)
    return True

# ─── Chart 4: Realized Price vs Price (delta) ────────────────────────────

def chart_realized_delta(db, output_path: str, days: int = 365*3):
    """Price minus realized price: shows over/undervaluation vs cost basis."""
    data_price = query_series(db, "price",         days)
    data_real  = query_series(db, "realized_price", days)

    if not data_price or not data_real:
        return False

    W, H = 860, 280
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 20, 20, 45

    # Merge on ts
    price_dict = dict(data_price)
    delta_data = [(ts, price_dict.get(ts, 0) - real)
                   for ts, real in data_real if ts in price_dict]

    all_ts    = [ts for ts, _ in delta_data]
    all_delta = [v for _, v in delta_data]
    x_min, x_max = min(all_ts), max(all_ts)
    d_min = min(all_delta) * 1.2
    d_max = max(all_delta) * 1.2

    delta_pts = svg_polyline(delta_data, x_min, x_max, d_min, d_max, W, H, PAD_L, PAD_R, PAD_T, PAD_B, invert_y=True)

    # Zero line
    y_zero = PAD_T + (1 - (0 - d_min) / (d_max - d_min)) * (H - PAD_T - PAD_B)

    def format_date(ts):
        return datetime.utcfromtimestamp(ts).strftime("%b %Y")
    x_labels = []
    step = (x_max - x_min) / 6
    for i in range(7):
        ts = x_min + step * i
        x = PAD_L + step * i / (x_max - x_min) * (W - PAD_L - PAD_R)
        x_labels.append((x, format_date(ts)))

    svg = f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="font-family:Manrope,sans-serif">
  <rect width="{W}" height="{H}" fill="#04201c"/>
  <rect x="{PAD_L}" y="{PAD_T}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(H-PAD_T-PAD_B)}}" fill="#092b27" opacity="0.4"/>

  <!-- Zero line -->
  <line x1="{PAD_L}" y1="{int(y_zero)}" x2="{{W-PAD_R}}" y2="{int(y_zero)}" stroke="rgba(213,168,68,0.6)" stroke-width="1.5"/>
  <text x="{{W-PAD_R+4}}" y="{int(y_zero+4)}" font-size="9" fill="#d5a844" font-family="IBM Plex Mono,monospace">$0</text>

  <!-- Above zero: overvaluation (price > realized) -->
  <rect x="{PAD_L}" y="{PAD_T}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(y_zero-PAD_T)}}" fill="rgba(22,163,74,0.07)"/>
  <!-- Below zero: undervaluation -->
  <rect x="{PAD_L}" y="{int(y_zero)}" width="{{int(W-PAD_L-PAD_R)}}" height="{{int(H-PAD_B-y_zero)}}" fill="rgba(220,38,38,0.07)"/>

  <!-- Delta line -->
  <polyline points="{delta_pts}" fill="none" stroke="#ef765f" stroke-width="2"/>

  <!-- X axis -->
  {"".join(f'<text x="{x:.0f}" y="{{H-8}}" text-anchor="middle" font-size="9" fill="#63766f" font-family="IBM Plex Mono,monospace">{label}</text>' for x, label in x_labels)}

  <text x="8" y="{int((PAD_T+H-PAD_B)/2)}" text-anchor="middle" font-size="9" fill="#ef765f" font-family="IBM Plex Mono,monospace" transform="rotate(-90,8,{int((PAD_T+H-PAD_B)/2)})">Price — Realized (USD)</text>

  <text x="{W//2}" y="14" text-anchor="middle" font-size="11" fill="#f4eddd" font-family="Fraunces,serif" font-weight="500">Delta: Price minus Realized Price (Cost Basis)</text>
</svg>'''

    with open(output_path, "w") as f:
        f.write(svg)
    return True

# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    db = get_db()

    print("\n=== BTC Research Charts ===\n")

    charts = [
        ("MVRV + Price",     lambda: chart_mvrv_price(db,       f"{config.CHARTS_DIR}/mvrv_price.svg")),
        ("SOPR + Price",      lambda: chart_sopr_price(db,        f"{config.CHARTS_DIR}/sopr_price.svg")),
        ("RSI + MVRV",        lambda: chart_price_rsi(db,         f"{config.CHARTS_DIR}/rsi_mvrv.svg")),
        ("Price-Realized",    lambda: chart_realized_delta(db,   f"{config.CHARTS_DIR}/price_realized_delta.svg")),
    ]

    for name, fn in charts:
        try:
            ok = fn()
            status = "OK" if ok else "SKIP"
            print(f"  [{status}] {name}")
        except Exception as e:
            print(f"  [ERR] {name}: {e}")

    print(f"\nCharts saved to: {config.CHARTS_DIR}/")

if __name__ == "__main__":
    main()
