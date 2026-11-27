"""Name / address / phone extraction and comparison."""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from .web import PageMeta

ABBR = {"street": "st", "avenue": "ave", "road": "rd", "suite": "ste", "boulevard": "blvd",
        "drive": "dr", "lane": "ln", "floor": "fl", "north": "n", "south": "s",
        "east": "e", "west": "w", "place": "pl", "court": "ct", "highway": "hwy"}
SUFFIXES = {"llc", "inc", "lp", "llp", "ltd", "corp", "co", "company", "the", "and"}
PHONE_RE = re.compile(r"\(?\b(\d{3})\)?[\s.\-]\s?(\d{3})[\s.\-](\d{4})\b")
CSZ_RE = re.compile(r"((?:[A-Z][A-Za-z.'\-]+ ){0,2}[A-Z][A-Za-z.'\-]+),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?")


@dataclass
class Nap:
    name: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    phone: str = ""

    def present(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and "))).strip()


def norm_name(s: str) -> str:
    return " ".join(w for w in norm_text(s).split() if w not in SUFFIXES)


def name_similarity(a: str, b: str) -> float:
    a, b = norm_name(a), norm_name(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def digits(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else ""


def street_key(street: str) -> str:
    toks = [ABBR.get(t, t) for t in norm_text(street).split()]
    return " ".join(toks[:3])            # number + street name; ignores suite and unit noise


def _walk(n):
    if isinstance(n, dict):
        yield n
        for v in n.values():
            yield from _walk(v)
    elif isinstance(n, list):
        for v in n:
            yield from _walk(v)


def extract_site_nap(meta: PageMeta) -> Nap:
    nap = Nap()
    for node in _walk(meta.json_ld):
        a = node.get("address")
        if isinstance(a, dict) and not nap.street and not nap.city:
            nap.street = str(a.get("streetAddress", "")).strip()
            nap.city = str(a.get("addressLocality", "")).strip()
            nap.state = str(a.get("addressRegion", "")).strip()
            nap.zip = str(a.get("postalCode", "")).strip()
        if node.get("telephone") and not nap.phone:
            nap.phone = str(node["telephone"])
        if node.get("name") and not nap.name and node.get("@type"):
            nap.name = str(node["name"])
    if not nap.phone and (m := PHONE_RE.search(meta.text)):
        nap.phone = "".join(m.groups())
    if not nap.city and (m := CSZ_RE.search(meta.text)):
        nap.city, nap.state, nap.zip = m.group(1).strip(), m.group(2), m.group(3)
    return nap


def compare(a: Nap, b: Nap) -> dict[str, Optional[bool]]:
    """True/False per field, None where one side lacks the field."""
    def cmp(x, y, fn):
        return None if not (fn(x) and fn(y)) else fn(x) == fn(y)
    return {"street": cmp(a.street, b.street, street_key),
            "city": cmp(a.city, b.city, norm_text),
            "state": cmp(a.state, b.state, lambda s: s.strip().upper()),
            "zip": cmp(a.zip, b.zip, lambda z: re.sub(r"\D", "", z)[:5]),
            "phone": cmp(a.phone, b.phone, digits)}


def agreement(cmp: dict) -> Optional[float]:
    """Share of comparable fields that match, or None if nothing was comparable."""
    vals = [v for v in cmp.values() if v is not None]
    return sum(vals) / len(vals) if vals else None


def is_consistent(cmp: dict, threshold: float = 0.75) -> bool:
    """Consistent if enough comparable fields agree AND the city and state, when both are
    known, do not differ. A different city is never 'mostly the same'."""
    a = agreement(cmp)
    if a is None:
        return True                      # nothing comparable: nothing contradicts
    return a >= threshold and cmp.get("city") is not False and cmp.get("state") is not False
