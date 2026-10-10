#!/usr/bin/env python3
"""Local resolver and transport configuration preflight (NO API calls).

DNS lookups are the only potential network traffic: no HTTP, TLS sessions,
API downloads, database access or file writes. Reports no IP addresses,
proxy URLs, passwords, query strings, local paths or secret values.
"""
import datetime as dt
import json
import os
import socket
import ssl
from urllib.request import getproxies

HOSTS = ("fapi.binance.com", "api.bybit.com")


def inspect(resolver=socket.getaddrinfo, proxy_reader=getproxies):
    hosts = {}
    for hostname in HOSTS:
        try:
            answers = resolver(hostname, 443, type=socket.SOCK_STREAM)
            hosts[hostname] = {"dns": "RESOLVED" if answers else "NO_ANSWERS"}
        except socket.gaierror:
            hosts[hostname] = {"dns": "DNS_ERROR"}
        except OSError:
            hosts[hostname] = {"dns": "RESOLVER_OS_ERROR"}
    try:
        proxy_names = set(proxy_reader())
    except (OSError, ValueError):
        proxy_names = set()
    trust = ssl.get_default_verify_paths()
    return {
        "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "probe_type": "DNS_AND_LOCAL_CONFIG_ONLY",
        "http_attempts": 0,
        "sqlite_writes": 0,
        "hosts": hosts,
        "proxy_config_present": bool(proxy_names),
        "https_proxy_config_present": bool({"https", "all"} & proxy_names),
        "ssl_cert_file_available": bool(trust.cafile and os.path.isfile(trust.cafile)),
        "ssl_cert_dir_available": bool(trust.capath and os.path.isdir(trust.capath)),
        "tls_handshake": "NOT_TESTED",
        "http_status": "NOT_TESTED",
        "historical_data_availability": "NOT_TESTED",
        "prior_failure_category": "UNRECOVERABLE_FROM_LEGACY_URLError_ONLY",
    }


def main():
    print(json.dumps(inspect(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
