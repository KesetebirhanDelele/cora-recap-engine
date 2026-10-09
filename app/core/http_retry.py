"""Retry the CONNECT step of outbound HTTP calls (2026-10-08/09: six `[Errno 101] Network is unreachable` failures in 5 hours).

httpx's transport retries apply only to failures while opening the connection (ConnectError / ConnectTimeout): the request was
never sent, so retrying is safe for GET and POST alike (no duplicate call, text or email). Each attempt resolves the host again.
"""
from __future__ import annotations

import httpx

CONNECT_RETRIES = 3


def connect_retry_transport() -> httpx.HTTPTransport:
    return httpx.HTTPTransport(retries=CONNECT_RETRIES)
