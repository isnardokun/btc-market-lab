"""Regression tests: classify URL failures without messages or network calls."""
import socket
import ssl
from urllib.error import HTTPError, URLError
import unittest

from ingestion.market_network_errors import error_category
from scripts.market_network_preflight import inspect
from scripts import market_history_pilot as history
from storage.market_context import install,raw_payload,store_derivative
from pathlib import Path
import sqlite3
import tempfile
import datetime as dt

class TransportClassificationTests(unittest.TestCase):
    def test_five_network_categories_and_http(self):
        cases=[
            (URLError(socket.gaierror(-3,"sensitive DNS text")),"URLError_DNS"),
            (URLError(ssl.SSLError("sensitive certificate detail")),"URLError_TLS"),
            (URLError(TimeoutError("private host address")),"URLError_TIMEOUT"),
            (URLError(ConnectionRefusedError("private host address")),"URLError_REFUSED"),
            (URLError(ConnectionResetError("private host address")),"URLError_RESET"),
            (URLError("private proxy URL"),"URLError_UNCLASSIFIED"),
            (HTTPError("https://internal.example",429,"private",None,None),"HTTPError_429"),
        ]
        for error, expected in cases:
            with self.subTest(kind=expected):
                output=error_category(error)
                self.assertEqual(output,expected)
                self.assertNotIn("private",output)
                self.assertNotIn("sensitive",output)

    def test_preflight_does_not_call_any_http_or_output_network_details(self):
        calls=[]
        def resolver(host,port,*,type):
            calls.append((host,port))
            if host=="fapi.binance.com":
                raise socket.gaierror(-3,"secret details")
            return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("192.0.2.15",443))]
        report=inspect(resolver=resolver,proxy_reader=lambda:{
            "https":"https://user:secret@proxy.private:3128"})
        self.assertEqual(report["hosts"]["fapi.binance.com"]["dns"],"DNS_ERROR")
        self.assertEqual(report["hosts"]["api.bybit.com"]["dns"],"RESOLVED")
        self.assertTrue(report["https_proxy_config_present"])
        self.assertEqual(report["http_attempts"],0)
        self.assertEqual(len(calls),2)
        self.assertNotIn("secret",str(report))
        self.assertNotIn("192.0.2.15",str(report))
        self.assertNotIn("proxy.private",str(report))

    def test_failed_history_run_records_precise_dns_error_and_zero_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path=Path(temp)/"btc_research.db"
            with sqlite3.connect(db_path) as db:
                install(db)
                endpoint="https://fapi.binance.com/futures/data/openInterestHist"
                sha=raw_payload(db,"binance",endpoint,b'{"fixture":true}',"application/json")
                store_derivative(db,provider="binance",symbol="BTCUSDT",
                    metric="open_interest",observed_utc=dt.datetime(2026,10,10,tzinfo=dt.timezone.utc).isoformat(),
                    interval_label="5m",value=100,unit="BTC",endpoint=endpoint,sha=sha)
                def no_http(url,params):
                    raise URLError(socket.gaierror(-3,"private DNS resolver detail"))
                result=history.execute(db,"binance","open_interest",fetch=no_http)
                self.assertEqual(result["status"],"failed")
                self.assertEqual(result["error_code"],"URLError_DNS")
                self.assertEqual(result["requests"],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM market_source_cursors").fetchone()[0],0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM market_source_runs "
                    "WHERE error_code='URLError_DNS' AND requests=1").fetchone()[0],1)

if __name__=="__main__":
    unittest.main()
