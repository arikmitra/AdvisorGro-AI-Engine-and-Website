"""Firm details from a domain and, when available, from the site's own markup."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from html.parser import HTMLParser

KEYWORDS = sorted(["wealth", "capital", "advisors", "advisers", "partners", "financial",
    "management", "group", "investments", "investment", "planning", "associates",
    "asset", "trust", "advisory", "counsel", "holdings", "fiduciary"],
    key=len, reverse=True)
ORG_HINTS = ("Organization", "Business", "Service", "Corporation", "Firm", "Company")


# --- details from the URL alone -------------------------------------------------------
def derive_firm_name(domain: str) -> tuple[str, tuple[str, ...]]:
    """Best-effort name from a domain. Concatenated labels can't be split
    reliably, so only trailing industry words are peeled off."""
    label = domain.split(".")[0]
    parts = [p for p in label.split("-") if p]
    if len(parts) == 1:
        parts, rest = [], label
        while True:
            hit = next((k for k in KEYWORDS if rest.endswith(k) and len(rest) > len(k)), None)
            if not hit:
                break
            parts.insert(0, hit)
            rest = rest[: -len(hit)]
        parts.insert(0, rest)
    name = " ".join(p.capitalize() for p in parts)
    aliases = {label.capitalize(), " ".join(p.capitalize() for p in parts[:-1])} - {name, ""}
    return name, tuple(sorted(a for a in aliases if len(a) >= 5))


# --- optional: details from the page itself ---------------------------------------------
class _Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.site_name, self.ld = "", "", []
        self._in_title = self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and a.get("property") == "og:site_name":
            self.site_name = a.get("content") or ""
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld = True
            self.ld.append("")

    def handle_endtag(self, tag):
        self._in_title = self._in_title and tag != "title"
        self._in_ld = self._in_ld and tag != "script"

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_ld:
            self.ld[-1] += data


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


RANK = {"FinancialService": 0, "ProfessionalService": 0, "Organization": 1, "LocalBusiness": 1,
        "Corporation": 1, "Firm": 2, "Company": 2, "Business": 3}


def _type_rank(t):
    """Lower is a better firm-name source. A bare 'Service' node describes an offering, not the firm."""
    t = str(t)
    if t in RANK:
        return RANK[t]
    if t == "Service":
        return None
    return 3 if any(h in t for h in ORG_HINTS) else None


def extract_details(html: str, domain: str) -> dict:
    """Pull name and area from JSON-LD, then og:site_name, then <title>."""
    p = _Head()
    p.feed(html)
    out: dict = {}
    cands = []
    for raw in p.ld:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for n in _walk(data):
            types = n.get("@type", "")
            types = types if isinstance(types, list) else [types]
            ranks = [r for r in map(_type_rank, types) if r is not None]
            nm = n.get("name")
            if not (ranks and isinstance(nm, str) and nm.strip()):
                continue
            addr, area = n.get("address"), ""
            if isinstance(addr, dict) and addr.get("addressLocality"):
                area = ", ".join(str(x) for x in (addr["addressLocality"], addr.get("addressRegion")) if x)
            cands.append((min(ranks), len(cands), nm.strip(), area))
    if cands:
        best = min(cands)
        out["name"] = (best[2], "JSON-LD")
        area = best[3] or next((c[3] for c in sorted(cands) if c[3]), "")
        if area:
            out["area"] = (area, "JSON-LD address")
    if "name" not in out and p.site_name.strip():
        out["name"] = (p.site_name.strip(), "og:site_name")
    if "name" not in out and p.title.strip():
        label = re.sub(r"\W", "", domain.split(".")[0]).lower()
        segs = [s.strip() for s in re.split(r"\s[|\-–—·]\s", p.title) if s.strip()]
        best = max(segs, key=lambda s: SequenceMatcher(
            None, re.sub(r"\W", "", s).lower(), label).ratio())
        out["name"] = (best, "<title>")
    return out