"""Fetch and parse a web page. Stdlib only, shared by the real groups."""
from __future__ import annotations

import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable

UA = "Mozilla/5.0 (compatible; AdvisorGro-probe/0.1)"
# getaddrinfo codes meaning "could not ask", not "the name does not exist"
TRANSIENT_DNS_ERRNOS = {getattr(socket, "EAI_AGAIN", None), 11002} - {None}


@dataclass
class Page:
    ok: bool
    status: int | None = None
    final_url: str = ""
    content_type: str = ""
    html: str = ""
    size: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    transient: bool = False        # could not be verified from here (network, block, rate limit)
    insecure: bool = False         # fetched with certificate checks off


def fetch_page(url: str, timeout: float = 8.0, verify: bool = True,
               max_bytes: int = 1_000_000) -> Page:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
    t0 = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read(max_bytes)
            try:
                html = raw.decode(r.headers.get_content_charset() or "utf-8", "replace")
            except LookupError:
                html = raw.decode("utf-8", "replace")
            return Page(True, r.status, r.geturl(), r.headers.get("Content-Type", ""),
                        html, len(raw), round(time.monotonic() - t0, 3), insecure=not verify)
    except urllib.error.HTTPError as e:
        # 401/403/429 and gateway errors mean "we could not verify from here" (bot
        # protection, rate limits), not "the page is broken", so they are not cached
        # and groups report them as unverified. A 404 or 500 is a real finding.
        note = " (server refused the probe; possible bot protection)" if e.code in (401, 403) else ""
        return Page(False, e.code, url, error=f"HTTP {e.code}{note}",
                    elapsed_s=round(time.monotonic() - t0, 3),
                    transient=e.code in (401, 403, 429, 502, 503, 504))
    except urllib.error.URLError as e:
        r = e.reason
        if isinstance(r, ssl.SSLCertVerificationError):
            return Page(False, error=f"certificate verification failed: {r.verify_message or r}")
        if isinstance(r, ssl.SSLError):
            return Page(False, error=f"TLS error: {r}")
        if isinstance(r, socket.gaierror):
            return Page(False, error=f"DNS: {r.strerror or r}",
                        transient=r.errno in TRANSIENT_DNS_ERRNOS)
        if isinstance(r, ConnectionRefusedError):
            return Page(False, error="connection refused")
        return Page(False, error=f"{type(r).__name__}: {r}", transient=True)
    except (socket.timeout, TimeoutError):
        return Page(False, error="timed out", transient=True)
    except (OSError, ValueError) as e:
        return Page(False, error=f"{type(e).__name__}: {e}", transient=True)


class SharedFetcher:
    """One fetch per URL shared by every group in a run. Transient failures are
    not cached. Use a fresh instance per run, or rely on the short TTL."""

    def __init__(self, fetch: Callable[..., Page] = fetch_page, ttl_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch, self.ttl_s, self._clock = fetch, ttl_s, clock
        self._cache: dict = {}
        self._locks: dict = {}
        self._glock = threading.Lock()

    def get(self, url: str, timeout: float = 8.0, verify: bool = True) -> Page:
        key = (url, verify)
        with self._glock:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < self.ttl_s:
                return hit[1]
            page = self._fetch(url, timeout=timeout, verify=verify)
            if not page.transient:
                self._cache[key] = (self._clock(), page)
            return page


@dataclass
class PageMeta:
    title: str = ""
    canonical: str = ""
    viewport: str = ""
    json_ld: list = field(default_factory=list)
    json_ld_invalid: int = 0
    script_count: int = 0
    text: str = ""

    @property
    def json_ld_types(self) -> list[str]:
        out: list[str] = []

        def walk(n):
            if isinstance(n, dict):
                t = n.get("@type")
                out.extend(t if isinstance(t, list) else [t] if t else [])
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(self.json_ld)
        return sorted({str(t) for t in out})


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.m, self._ld, self._title, self._skip, self._text = PageMeta(), None, False, 0, []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._title = True
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.m.canonical = self.m.canonical or a.get("href", "").strip()
        elif tag == "meta" and a.get("name", "").lower() == "viewport":
            self.m.viewport = a.get("content", "").strip()
        elif tag == "script":
            self.m.script_count += 1
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = []
            else:
                self._skip += 1
        elif tag in ("style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag == "script":
            if self._ld is not None:
                raw, self._ld = "".join(self._ld).strip(), None
                try:
                    self.m.json_ld.append(json.loads(raw))
                except ValueError:
                    self.m.json_ld_invalid += 1
            else:
                self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif self._title:
            self.m.title += data
        elif not self._skip:
            self._text.append(data)


def parse_page(html: str) -> PageMeta:
    p = _Parser()
    p.feed(html)
    p.m.title = p.m.title.strip()
    p.m.text = re.sub(r"\s+", " ", " ".join(p._text)).strip()[:200_000]
    return p.m
