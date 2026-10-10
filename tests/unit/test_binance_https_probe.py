"""Offline-only contract tests for one-shot Binance HTTPS reachability check."""
import contextlib
from io import StringIO
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import binance_https_probe as probe


class Response:
    def __init__(self,code,body):
        self.code=code
        self.body=body
        self.read_calls=[]
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def getcode(self):
        return self.code
    def read(self,count):
        self.read_calls.append(count)
        return self.body


class OneShotPingTests(unittest.TestCase):
    def test_plan_mode_does_not_open_any_http_connection(self):
        with patch.object(probe,"transport",side_effect=AssertionError("No network")):
            with contextlib.redirect_stdout(StringIO()) as output:
                code=probe.main([])
        self.assertEqual(code,0)
        self.assertIn('"http_attempts": 0',output.getvalue())
        self.assertIn('"outcome": "PLAN_ONLY"',output.getvalue())

    def test_one_get_tls_verified_and_small_body_read(self):
        urls=[]
        response=Response(200,b"{}")
        def no_network(req,timeout):
            urls.append((req.full_url,req.get_method(),timeout))
            return response
        out=probe.check(open_request=no_network)
        self.assertEqual(out["outcome"],"PASS_HTTPS_CONNECTIVITY")
        self.assertEqual(out["http_attempts"],1)
        self.assertEqual(out["sqlite_writes"],0)
        self.assertEqual(response.read_calls,[1025])
        self.assertEqual(urls,[("https://fapi.binance.com/fapi/v1/ping","GET",8)])
        self.assertEqual(out["redirects_followed"],0)

    def test_urLError_classifies_and_does_not_expose_message(self):
        def dns_denied(_req,timeout):
            raise URLError(socket.gaierror(-3,"top secret resolver text"))
        out=probe.check(open_request=dns_denied)
        self.assertEqual(out["outcome"],"FAILED")
        self.assertEqual(out["error_class"],"URLError_DNS")
        self.assertEqual(out["http_attempts"],1)
        self.assertNotIn("secret",str(out))
        self.assertEqual(out["http_status"],None)
        def tls_denied(_req,timeout):
            raise URLError(ssl.SSLError("secret TLS certificate"))
        self.assertEqual(probe.check(open_request=tls_denied)["error_class"],"URLError_TLS")

    def test_http_denial_403_no_follow_up_and_no_secret(self):
        def denied(req,timeout):
            raise HTTPError(req.full_url,403,"Secret response details",None,None)
        out=probe.check(open_request=denied)
        self.assertEqual(out["error_class"],"HTTPError_403")
        self.assertEqual(out["http_status"],403)
        self.assertEqual(out["http_attempts"],1)
        self.assertNotIn("Secret",str(out))

    def test_redirect_handler_fails_closed(self):
        self.assertIsNone(probe.RejectRedirect().redirect_request(
            object(),None,302,"Moved",{},"https://other.example.com"))

    def test_unexpected_payload_is_error_not_output(self):
        def response(_req,timeout):
            return Response(200,b"internal response should not print")
        out=probe.check(open_request=response)
        self.assertEqual(out["outcome"],"FAILED")
        self.assertEqual(out["error_class"],"UnexpectedPingBody")
        self.assertNotIn("internal response",str(out))

if __name__=="__main__":
    unittest.main()
