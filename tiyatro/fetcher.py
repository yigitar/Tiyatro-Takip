"""HTTP page fetcher with browser-like headers."""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 30


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    html: str
    status: int
    latency_ms: int


def _default_transport(url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def fetch(url: str, transport: Callable | None = None) -> FetchResult:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9",
               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    start = time.monotonic()
    failure = None
    try:
        status, body = (transport or _default_transport)(url, headers, TIMEOUT)
    except Exception as exc:
        failure = type(exc).__name__
    if failure is not None:
        raise FetchError(f"fetch failed ({failure}): {url}")
    if status >= 400:
        raise FetchError(f"HTTP {status}: {url}")
    return FetchResult(body.decode("utf-8", errors="replace"), status,
                       int((time.monotonic() - start) * 1000))
