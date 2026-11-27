"""Canonical JSON, content hashes and keyed hashes.

The content hash is computed over *normalised* text, links and disclosures so the Publisher can fetch
a live page, normalise it the same way and compare (guide 17.3).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def hmac_hex(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


_WS = re.compile(r"\s+")


def normalise_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return _WS.sub(" ", s).strip()


class _TextLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "head"):
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href.strip())

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.text.append(data)


def normalise_html(html: str) -> dict[str, Any]:
    """Visible text, sorted unique links. Markup, whitespace and script/style are ignored."""
    p = _TextLinks()
    p.feed(html)
    return {"text": normalise_text(" ".join(p.text)), "links": sorted(set(p.links))}


def content_hash(parts: dict[str, Any]) -> str:
    """Hash over named, already-normalised artefact parts."""
    return sha256_hex(canonical_json(parts))
