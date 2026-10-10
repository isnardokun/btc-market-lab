#!/usr/bin/env python3
"""One authorized HTTPS connectivity test to the Binance USD-M ping endpoint.

PLAN ONLY unless explicitly passed --probe. Never accesses SQLite or other
market endpoints. Exactly one GET, TLS certificate verification, no redirects,
no retries, no API credentials, no response-body printing or file writes.
"""
import argparse
import datetime as dt
import json
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, Request, build_opener, ProxyHandler
)

from ingestion.market_network_errors import error_category

HOST = "fapi.binance.com"
PING_URL = "https://" + HOST + "/fapi/v1/ping"


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(request, timeout):
    # No custom CA bypass: normal TLS hostname and certificate verification.
    # Respect default system proxy configuration, if any, without logging it.
    # No cookies, credentials, token headers or manual redirect follow-up.
    context = ssl.create_default_context()
    opener = build_opener(
        ProxyHandler(),
        HTTPSHandler(context=context),
        RejectRedirect(),
    )
    return opener.open(request, timeout=timeout)


def check(*, open_request=transport):
    started = time.monotonic()
    report = {
        "utc":dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "target_host":HOST,
        "endpoint_path":"/fapi/v1/ping",
        "probe_type":"ONE_HTTPS_REQUEST_ONLY",
        "http_attempts":1,
        "sqlite_writes":0,
        "market_observations_created":0,
        "http_status":None,
        "tls_certificate_verification":"NOT_CONFIRMED",
        "redirects_followed":0,
        "outcome":"FAILED",
        "error_class":None,
    }
    request = Request(PING_URL,headers={
        "User-Agent":"BTCMarketLab/2.0 connectivity check",
        "Accept":"application/json",
        "Connection":"close",
    }, method="GET")
    try:
        # urllib's context manager always closes the response.
        with open_request(request,timeout=8) as response:
            code=response.getcode()
            if code!=200:
                report["http_status"]=int(code)
                report["error_class"]="UnexpectedHTTPStatus"
            else:
                report["http_status"]=200
                report["tls_certificate_verification"]="HTTPS_RESPONSE_VERIFIED"
                body=response.read(1025)
                # Binance documents this endpoint as a connectivity check
                # with no data; tolerate its common empty-object variant.
                if body.strip() in (b"",b"{}"):
                    report["outcome"]="PASS_HTTPS_CONNECTIVITY"
                else:
                    report["error_class"]="UnexpectedPingBody"
    except HTTPError as exc:
        report["http_status"]=int(exc.code)
        report["error_class"]=error_category(exc)
        report["tls_certificate_verification"]="HTTPS_RESPONSE_VERIFIED"
    except (URLError, OSError, ValueError) as exc:
        report["error_class"]=error_category(exc)
    finally:
        report["elapsed_ms"]=int((time.monotonic()-started)*1000)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe",action="store_true",
                        help="Authorize exactly one HTTPS GET to the Binance ping endpoint")
    args=parser.parse_args(argv)
    if not args.probe:
        print(json.dumps({
            "outcome":"PLAN_ONLY", "endpoint_path":"/fapi/v1/ping",
            "target_host":HOST, "http_attempts":0, "sqlite_writes":0,
            "requires":"--probe",
        },sort_keys=True))
        return 0
    report=check()
    print(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2))
    return 0 if report["outcome"]=="PASS_HTTPS_CONNECTIVITY" else 1


if __name__=="__main__":
    raise SystemExit(main())
