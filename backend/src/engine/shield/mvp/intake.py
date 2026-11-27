"""Intake: normalize a request so duplicates and cache hits are detectable."""
from __future__ import annotations

from urllib.parse import urlparse


def normalize_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if not host or "." not in host:
        raise ValueError(f"not a valid site URL: {url!r}")
    if host.startswith("www."):
        host = host[4:]
    return f"https://{host}"


def domain_of(url: str) -> str:
    return urlparse(normalize_url(url)).hostname or ""
