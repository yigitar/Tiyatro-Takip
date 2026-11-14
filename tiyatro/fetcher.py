"""HTTP page fetcher with browser-like headers."""
from __future__ import annotations

import codecs
import re
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
    """(status, body, Content-Type header). Custom transports may return (status, body)."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type")
    except urllib.error.HTTPError as exc:
        return exc.code, b"", None


_HEADER_CHARSET = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.I)
_META_CHARSET = re.compile(rb"<meta[^>]+?charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.I)
SNIFF_BYTES = 4096


def _codec(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return codecs.lookup(name.strip()).name
    except LookupError:
        return None


def decode_body(body: bytes, content_type: str | None = None) -> str:
    """Decode with the charset of the Content-Type header, then of a ``<meta>`` tag in the
    first bytes, else utf-8 (BOM stripped, undecodable bytes replaced). A BOM wins."""
    if body.startswith(codecs.BOM_UTF8):
        return body.decode("utf-8-sig", errors="replace")
    found = _HEADER_CHARSET.search(content_type or "")
    codec = _codec(found.group(1)) if found else None
    if codec is None:
        found = _META_CHARSET.search(body[:SNIFF_BYTES])
        codec = _codec(found.group(1).decode("ascii", "ignore")) if found else None
    if codec in (None, "utf-8"):
        return body.decode("utf-8-sig", errors="replace")
    return body.decode(codec, errors="replace")


def fetch(url: str, transport: Callable | None = None) -> FetchResult:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9",
               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    start = time.monotonic()
    failure = None
    try:
        status, body, *rest = (transport or _default_transport)(url, headers, TIMEOUT)
        content_type = rest[0] if rest else None
    except Exception as exc:
        failure = type(exc).__name__
    if failure is not None:
        raise FetchError(f"fetch failed ({failure}): {url}")
    if status >= 400:
        raise FetchError(f"HTTP {status}: {url}")
    return FetchResult(decode_body(body, content_type), status,
                       int((time.monotonic() - start) * 1000))
