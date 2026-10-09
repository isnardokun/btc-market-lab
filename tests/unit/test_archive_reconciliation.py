import datetime as dt
import sqlite3
import unittest
from validation.archive_reconciliation import audit_archive_against_html

DAY = "2026-10-09"
CLOSE = int(dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc).timestamp())

def make_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE daily_metrics(report_date TEXT, asset TEXT, metric TEXT, value REAL)")
    for asset, price in (("BTC", 81676.2), ("SPY", 773.93), ("GOLD", 4157.1), ("SILVER", 59.06), ("OIL", 91.49)):
        db.execute("INSERT INTO daily_metrics VALUES (?,?,?,?)", (DAY, asset, "price", price))
        db.execute("INSERT INTO daily_metrics VALUES (?,?,?,?)", (DAY, asset, "close_ts", CLOSE))
    db.execute("INSERT INTO daily_metrics VALUES (?,?,?,?)", (DAY, "BTC", "rsi_14", 46.92))
    db.execute("INSERT INTO daily_metrics VALUES (?,?,?,?)", (DAY, "BTC", "macd_hist", -495.376))
    return db

def html():
    labels = (("BTC/USD", "81676"), ("SPDR S&P 500", "773.93"), ("ORO FUT. GC=F", "4157"), ("PLATA FUT. SI=F", "59.06"), ("WTI (CL=F)", "91.49"))
    tickers = "".join('<div class="ticker-item"><div class="tk-pair">{}</div><div class="tk-price">${}</div></div>'.format(k,v) for k,v in labels)
    return tickers + '<section><div class="section-title">Bitcoin (BTC)</div><div data-btc-asof-utc="2026-10-08 00:00"></div><table><tr><td>RSI(14)</td><td class="val">46.9</td></tr><tr><td>MACD (12,26,9)</td><td class="val">-495.38</td></tr></table></section>'

class Tests(unittest.TestCase):
    def test_valid(self):
        with make_db() as db:
            self.assertEqual(audit_archive_against_html(html(), db, DAY), [])

    def test_hermes_discrepancy_rejected(self):
        with make_db() as db:
            db.execute("UPDATE daily_metrics SET value=83010 WHERE asset='BTC' AND metric='price'")
            db.execute("UPDATE daily_metrics SET value=51.76 WHERE asset='BTC' AND metric='rsi_14'")
            errors = audit_archive_against_html(html(), db, DAY)
            self.assertTrue(any("BTC: HTML price" in e for e in errors), errors)
            self.assertTrue(any("BTC: HTML rsi_14" in e for e in errors), errors)

    def test_current_day_candle_rejected(self):
        with make_db() as db:
            db.execute("UPDATE daily_metrics SET value=value+86400 WHERE asset='BTC' AND metric='close_ts'")
            errors = audit_archive_against_html(html(), db, DAY)
            self.assertTrue(any("vela del día" in e for e in errors), errors)

    def test_archiver_excludes_today_open_utc_bar(self):
        from validation.archive_metrics import _completed_yahoo
        from unittest.mock import patch
        now = dt.datetime.now(dt.timezone.utc)
        prior = now.date() - dt.timedelta(days=1)
        old = prior - dt.timedelta(days=1)
        def candle(day, price):
            ts = int(dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc).timestamp())
            return {"ts": ts, "open": price, "high": price+10,
                    "low": price-10, "close": price, "volume": 100}
        with patch("validation.archive_metrics.yahoo_ohlc",
                   return_value=[candle(prior, 81676), candle(now.date(), 83010)]):
            with self.assertRaises(RuntimeError):
                _completed_yahoo("BTC-USD", 252)
        with patch("validation.archive_metrics.yahoo_ohlc",
                   return_value=[candle(old, 82000), candle(prior, 81676),
                                 candle(now.date(), 83010)]):
            rows = _completed_yahoo("BTC-USD", 252)
        self.assertEqual([r["close"] for r in rows], [82000, 81676])

    def test_missing_data_rejected(self):
        with make_db() as db:
            db.execute("DELETE FROM daily_metrics WHERE asset='GOLD' AND metric='price'")
            errors = audit_archive_against_html(html(), db, DAY)
            self.assertTrue(any("GOLD: daily_metrics.price" in e for e in errors), errors)
