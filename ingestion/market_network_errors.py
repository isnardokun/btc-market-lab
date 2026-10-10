"""Classify transport exceptions without exposing internal messages."""
import socket
import ssl
from urllib.error import HTTPError, URLError


def error_category(exc):
    if isinstance(exc, HTTPError):
        try:
            code = int(exc.code)
        except (ValueError, TypeError):
            return "HTTPError_UNKNOWN"
        return "HTTPError_" + str(code) if 100 <= code <= 599 else "HTTPError_UNKNOWN"
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return "URLError_DNS"
        if isinstance(reason, ssl.SSLError):
            return "URLError_TLS"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "URLError_TIMEOUT"
        if isinstance(reason, ConnectionRefusedError):
            return "URLError_REFUSED"
        if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return "URLError_RESET"
        return "URLError_UNCLASSIFIED"
    return type(exc).__name__
