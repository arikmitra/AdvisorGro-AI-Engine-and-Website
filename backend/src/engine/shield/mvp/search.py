"""Search and Google Places clients. Keys come from config; without a key the
caller reports the check as unverified.

UNVERIFIED AGAINST THE LIVE SERVICES: written to the public API shapes and
tested against fixtures only.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from .llm import LLMError, http_post
from .nap import Nap


class SearchError(Exception):
    def __init__(self, msg: str, transient: bool = True):
        super().__init__(msg)
        self.transient = transient


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""

    @property
    def host(self) -> str:
        h = (urlparse(self.url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h


def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def get_json(url: str, headers: dict, timeout: float) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (socket.timeout, TimeoutError):
        raise SearchError("timed out")
    except ValueError:
        raise SearchError("response was not JSON")
    except OSError as e:
        raise SearchError(f"network error: {e}")


class BraveSearch:
    URL = "https://api.search.brave.com/res/v1/web/search"
    name = "brave"

    def __init__(self, key: str, get: Callable = get_json, sleep: Callable = time.sleep,
                 timeout: float = 10.0):
        self.key, self._get, self._sleep, self.timeout = key, get, sleep, timeout

    def search(self, query: str, count: int = 20) -> list[SearchResult]:
        url = f"{self.URL}?q={urllib.parse.quote(query)}&count={min(count, 20)}"
        headers = {"X-Subscription-Token": self.key, "Accept": "application/json"}
        for attempt in (0, 1):
            status, payload = self._get(url, headers, self.timeout)
            if status == 429 and attempt == 0:          # free plans allow about 1 request/second
                self._sleep(1.2)
                continue
            break
        if status != 200:
            raise SearchError(f"search returned HTTP {status}",
                              transient=status in (429, 500, 502, 503, 504))
        return [SearchResult(r.get("url", ""), r.get("title", ""), r.get("description", ""))
                for r in ((payload.get("web") or {}).get("results") or []) if r.get("url")]


@dataclass
class Place:
    name: str
    website: str
    nap: Nap
    place_id: str = ""


def _split_address(formatted: str) -> Nap:
    parts = [p.strip() for p in formatted.split(",")]
    nap = Nap()
    if len(parts) >= 3:
        nap.street, nap.city = parts[0], parts[-3] if parts[-1].upper() in ("USA", "US") else parts[-2]
        m = re.search(r"([A-Z]{2})\s+(\d{5})", formatted)
        if m:
            nap.state, nap.zip = m.group(1), m.group(2)
    return nap


class PlacesClient:
    URL = "https://places.googleapis.com/v1/places:searchText"
    name = "google_places"

    def __init__(self, key: str, post: Callable = http_post, timeout: float = 10.0):
        self.key, self._post, self.timeout = key, post, timeout

    def find(self, query: str) -> list[Place]:
        headers = {"X-Goog-Api-Key": self.key, "content-type": "application/json",
                   "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,"
                                       "places.nationalPhoneNumber,places.websiteUri"}
        try:
            status, p = self._post(self.URL, headers, {"textQuery": query, "pageSize": 5}, self.timeout)
        except LLMError as e:
            raise SearchError(str(e), transient=e.transient)
        if status != 200:
            raise SearchError(f"Places returned HTTP {status}", transient=status in (429, 500, 502, 503, 504))
        out = []
        for pl in p.get("places") or []:
            nap = _split_address(pl.get("formattedAddress", ""))
            nap.name = (pl.get("displayName") or {}).get("text", "")
            nap.phone = pl.get("nationalPhoneNumber", "")
            out.append(Place(nap.name, pl.get("websiteUri", ""), nap, pl.get("id", "")))
        return out



# =====================================================================================================
# Alternatives to Brave (web search) and Google Places (business profile). Any one of them satisfies
# group 02; with ADVISORGRO_SEARCH_PROVIDER / ADVISORGRO_PLACES_PROVIDER left on "auto" every configured
# provider is tried in order until one answers. Same caveat as above: written to the public API
# documentation, tested against fixtures, not against the live services.
# =====================================================================================================
def _post_json(post: Callable, url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    try:
        return post(url, headers, body, timeout)
    except LLMError as e:                      # http_post reports network faults as LLMError
        raise SearchError(str(e), transient=e.transient)


class SerperSearch:
    """serper.dev: Google results as JSON. One key (SERPER_API_KEY) also serves SerperPlaces."""
    URL = "https://google.serper.dev/search"
    name = "serper"

    def __init__(self, key: str, post: Callable = http_post, timeout: float = 10.0):
        self.key, self._post, self.timeout = key, post, timeout

    def search(self, query: str, count: int = 20) -> list[SearchResult]:
        status, p = _post_json(self._post, self.URL, {"X-API-KEY": self.key, "Content-Type": "application/json"},
                               {"q": query, "num": min(count, 20)}, self.timeout)
        if status != 200:
            raise SearchError(f"Serper returned HTTP {status}", transient=status in (429, 500, 502, 503, 504))
        return [SearchResult(r.get("link", ""), r.get("title", ""), r.get("snippet", ""))
                for r in p.get("organic") or [] if r.get("link")]


def _serpapi_get(get: Callable, engine: str, params: dict, key: str, timeout: float, label: str) -> dict:
    """One SerpApi (serpapi.com) request. HTTP errors and the {"error": ...} bodies SerpApi sometimes sends with a 200 become
    SearchError; 'Google hasn't returned any results' is a real answer (no results), not an error."""
    q = urllib.parse.urlencode({"engine": engine, **params, "api_key": key})
    status, p = get(f"https://serpapi.com/search.json?{q}", {"Accept": "application/json"}, timeout)
    if status != 200:
        raise SearchError(f"{label} returned HTTP {status}", transient=status in (429, 500, 502, 503, 504))
    err = str(p.get("error") or "")
    if err and "hasn't returned any results" not in err and "returned any results" not in err:
        raise SearchError(f"{label} error: {err[:120]}", transient=False)
    return p


class SerpApiSearch:
    """SerpApi (serpapi.com): Google results as JSON. One key (SERPAPI_API_KEY) also serves SerpApiPlaces."""
    name = "serpapi"

    def __init__(self, key: str, get: Callable = get_json, timeout: float = 15.0):
        self.key, self._get, self.timeout = key, get, timeout

    def search(self, query: str, count: int = 20) -> list[SearchResult]:
        p = _serpapi_get(self._get, "google", {"q": query, "num": min(count, 20), "hl": "en", "gl": "us"},
                         self.key, self.timeout, "SerpApi")
        return [SearchResult(r.get("link", ""), r.get("title", ""), r.get("snippet", ""))
                for r in p.get("organic_results") or [] if r.get("link")][:count]


class GoogleCseSearch:
    """Google Programmable Search (Custom Search JSON API). Needs an API key and a search-engine id (cx)
    set to search the entire web. Returns at most 10 results per request; more are fetched by paging.
    Check that your Google account can still enable this API before choosing it."""
    URL = "https://www.googleapis.com/customsearch/v1"
    name = "google_cse"

    def __init__(self, key: str, cx: str, get: Callable = get_json, timeout: float = 10.0):
        self.key, self.cx, self._get, self.timeout = key, cx, get, timeout

    def search(self, query: str, count: int = 20) -> list[SearchResult]:
        out: list[SearchResult] = []
        for start in range(1, min(count, 20), 10):
            url = (f"{self.URL}?key={urllib.parse.quote(self.key)}&cx={urllib.parse.quote(self.cx)}"
                   f"&q={urllib.parse.quote(query)}&num=10&start={start}")
            status, p = self._get(url, {"Accept": "application/json"}, self.timeout)
            if status != 200:
                raise SearchError(f"Google search returned HTTP {status}", transient=status in (429, 500, 502, 503, 504))
            items = p.get("items") or []
            out += [SearchResult(r.get("link", ""), r.get("title", ""), r.get("snippet", "")) for r in items if r.get("link")]
            if len(items) < 10:
                break
        return out[:count]


class SerperPlaces:
    """serper.dev Google Maps results, so the profile data is Google's own."""
    URL = "https://google.serper.dev/places"
    name = "serper"

    def __init__(self, key: str, post: Callable = http_post, timeout: float = 10.0):
        self.key, self._post, self.timeout = key, post, timeout

    def find(self, query: str) -> list[Place]:
        status, p = _post_json(self._post, self.URL, {"X-API-KEY": self.key, "Content-Type": "application/json"},
                               {"q": query}, self.timeout)
        if status != 200:
            raise SearchError(f"Serper places returned HTTP {status}", transient=status in (429, 500, 502, 503, 504))
        out = []
        for pl in (p.get("places") or [])[:5]:
            nap = _split_address(pl.get("address", ""))
            nap.name, nap.phone = pl.get("title", ""), pl.get("phoneNumber", "")
            out.append(Place(nap.name, pl.get("website", ""), nap, str(pl.get("cid", ""))))
        return out


class SerpApiPlaces:
    """SerpApi Google Maps results, so the profile data is Google's own."""
    name = "serpapi"

    def __init__(self, key: str, get: Callable = get_json, timeout: float = 15.0):
        self.key, self._get, self.timeout = key, get, timeout

    def find(self, query: str) -> list[Place]:
        p = _serpapi_get(self._get, "google_maps", {"q": query, "type": "search", "hl": "en"}, self.key, self.timeout,
                         "SerpApi maps")
        rows = list(p.get("local_results") or [])
        if isinstance(p.get("place_results"), dict):          # an exact match comes back as a single place, not a list
            rows.insert(0, p["place_results"])
        out = []
        for pl in rows[:5]:
            nap = _split_address(pl.get("address", ""))
            nap.name, nap.phone = pl.get("title", ""), pl.get("phone", "")
            out.append(Place(nap.name, pl.get("website", ""), nap, str(pl.get("place_id") or pl.get("data_id") or "")))
        return out


class FallbackSearch:
    """Tries each search provider in turn; the first one that answers wins."""
    def __init__(self, providers: list):
        self.providers = providers
        self.name = "+".join(getattr(p, "name", type(p).__name__) for p in providers)

    def search(self, query: str, count: int = 20) -> list[SearchResult]:
        last: Optional[SearchError] = None
        for p in self.providers:
            try:
                return p.search(query, count)
            except SearchError as e:
                last = e
        raise last or SearchError("no search provider configured", transient=False)


class FallbackPlaces:
    def __init__(self, providers: list):
        self.providers = providers
        self.name = "+".join(getattr(p, "name", type(p).__name__) for p in providers)

    def find(self, query: str) -> list[Place]:
        last: Optional[SearchError] = None
        for p in self.providers:
            try:
                return p.find(query)
            except SearchError as e:
                last = e
        raise last or SearchError("no places provider configured", transient=False)