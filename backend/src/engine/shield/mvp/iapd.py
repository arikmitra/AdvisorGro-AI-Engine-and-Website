"""SEC IAPD (Investment Adviser Public Disclosure) lookup.

UNVERIFIED AGAINST THE LIVE SERVICE: the field names below follow the public
IAPD search endpoint as I know it, and could not be tested from the development
sandbox. Parsing is defensive: if fields are missing the result says so rather
than guessing, and the raw hit is kept in evidence for debugging.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from .nap import Nap, name_similarity, norm_name

BASE = "https://api.adviserinfo.sec.gov"


class IapdError(Exception):
    def __init__(self, msg: str, transient: bool = True):
        super().__init__(msg)
        self.transient = transient


@dataclass
class IapdRecord:
    crd: str
    name: str
    other_names: list[str] = field(default_factory=list)
    nap: Nap = field(default_factory=Nap)
    website: str = ""
    status: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://adviserinfo.sec.gov/firm/summary/{self.crd}" if self.crd else ""


@dataclass
class IapdMatch:
    status: str                              # matched | not_found | ambiguous | error
    record: Optional[IapdRecord] = None
    confidence: float = 0.0
    candidates: list[dict] = field(default_factory=list)
    error: str = ""
    transient: bool = False


def http_get_json(url: str, headers: dict, timeout: float) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (socket.timeout, TimeoutError):
        raise IapdError("timed out")
    except ValueError:
        raise IapdError("response was not JSON")
    except OSError as e:
        raise IapdError(f"network error: {e}")


def _find(d, *keys):
    keys = {k.lower() for k in keys}
    stack = [d]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            for k, v in n.items():
                if k.lower() in keys and v not in (None, "", [], {}):
                    return v
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)
    return ""


def record_from_hit(src: dict) -> IapdRecord:
    addr = src.get("firm_ia_address_details") or src.get("address") or {}
    if isinstance(addr, str):
        try:
            addr = json.loads(addr)
        except ValueError:
            addr = {}
    other = src.get("firm_other_names") or []
    if isinstance(other, str):
        other = [o.strip() for o in other.split(";") if o.strip()]
    nap = Nap(name=str(src.get("firm_name") or src.get("name") or ""),
              street=str(_find(addr, "street1", "streetAddress")),
              city=str(_find(addr, "city")), state=str(_find(addr, "state")),
              zip=str(_find(addr, "postalCode", "zip")),
              phone=str(_find(src, "phone", "firm_phone", "telephone")))
    return IapdRecord(crd=str(src.get("firm_source_id") or src.get("firm_crd") or ""),
                      name=nap.name, other_names=[str(o) for o in other], nap=nap,
                      website=str(_find(src, "website", "firm_website")),
                      status=str(src.get("firm_ia_scope") or src.get("firm_scope") or ""),
                      raw={k: src[k] for k in list(src)[:12]})


class IapdClient:
    def __init__(self, get_json: Callable = http_get_json, contact: str = "",
                 timeout: float = 10.0):
        self._get, self.timeout = get_json, timeout
        self.headers = {"User-Agent": f"AdvisorGro-audit/0.1 {contact}".strip(),
                        "Accept": "application/json"}

    def search(self, query: str, rows: int = 10) -> list[IapdRecord]:
        url = (f"{BASE}/search/firm?query={urllib.parse.quote(query)}&hl=true"
               f"&nrows={rows}&start=0&r=25&sort=score+desc&wt=json")
        status, payload = self._get(url, self.headers, self.timeout)
        if status != 200:
            raise IapdError(f"IAPD returned HTTP {status}", transient=status in (429, 500, 502, 503, 504))
        hits = ((payload.get("hits") or {}).get("hits")) or []
        return [record_from_hit(h.get("_source") or {}) for h in hits if h.get("_source")]

    def best_match(self, name: str, domain: str, aliases: tuple[str, ...] = ()) -> IapdMatch:
        found: dict[str, IapdRecord] = {}
        try:
            for q in dict.fromkeys([name, *aliases]):
                for r in self.search(q):
                    found[r.crd or r.name] = r
        except IapdError as e:
            return IapdMatch("error", error=str(e), transient=e.transient)
        scored = []
        for r in found.values():
            s = max([name_similarity(name, n) for n in [r.name, *r.other_names]] or [0.0])
            if r.website and domain and domain in r.website.lower():
                s = 1.0
            scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        cands = [{"name": r.name, "crd": r.crd, "score": round(s, 2)} for s, r in scored[:5]]
        if not scored or scored[0][0] < 0.85:
            return IapdMatch("not_found", confidence=scored[0][0] if scored else 0.0, candidates=cands)
        if len(scored) > 1 and scored[0][0] < 1.0 and scored[1][0] >= scored[0][0] - 0.05:
            return IapdMatch("ambiguous", confidence=scored[0][0], candidates=cands)
        return IapdMatch("matched", scored[0][1], scored[0][0], cands)


class SharedIapd:
    """One IAPD lookup per firm per run, shared by groups 02 and 04."""

    def __init__(self, client: IapdClient):
        self.client, self._cache, self._lock = client, {}, threading.Lock()

    def match(self, name: str, domain: str, aliases: tuple[str, ...] = ()) -> IapdMatch:
        key = (norm_name(name), domain)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            m = self.client.best_match(name, domain, aliases)
            if not m.transient:
                self._cache[key] = m
            return m
