"""Scraping fallback: real audit results when the paid APIs are absent, failing or not working.

Everything here uses only public web pages and the standard library. Nothing needs a key.

What it provides
  * ScrapeSearch    web search by reading a public results page (DuckDuckGo HTML, then Bing). Plugs into the same
                    `.search(query, count)` interface as BraveSearch/SerperSearch, so it sits at the end of the
                    FallbackSearch chain. A `site:` query that no search engine will answer is estimated from the
                    firm's own sitemap and links, and the result says so (`source`, `approximate`).
  * SiteInventory   sitemap.xml + same-site links from the homepage: pages found, profile links the firm publishes
                    (Google Maps, LinkedIn, Yelp, NAPFA, Paladin, Form ADV).
  * IapdScrape      finds the firm's adviserinfo.sec.gov page through a search (the site itself is a JavaScript app
                    that cannot be read as HTML), so the CRD number and legal name can still be confirmed.

Rules, same as the rest of the engine
  * Nothing is guessed. When a search engine blocks us (captcha, "unusual traffic", HTTP 202/403/429) the call raises
    SearchError; it never returns an empty list that would be read as "the firm is not indexed".
  * Every result carries where it came from, so reports can say "gathered by reading public pages".
  * Politeness: a minimum gap between requests to one search engine, a hard cap on pages crawled per firm, same-site
    links only, robots.txt honoured for the crawl, and answers cached for a short time.

CAVEAT: written against the public page layouts and tested with recorded fixtures, not against the live sites
(the sandbox it was written in cannot reach them). Search-result pages change layout and block automated reading
without notice; this is a fallback for when the APIs are not available, not a replacement for one paid key.
Automated reading of search engines can also be against their terms of use; keep volume low and decide for yourself.
"""
from __future__ import annotations

import html as htmllib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .search import SearchError, SearchResult, host_matches
from .web import Page, SharedFetcher, fetch_page

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
BLOCK_MARKERS = (
    "unusual traffic", "automated queries", "are you a human", "captcha", "verify you are human", "anomaly",
    "detected unusual", "please complete the security check", "challenge-form", "ddg-challenge", "bots use duckduckgo",
    "our systems have detected", "enable javascript to continue",
)


class ResultList(list):
    """A list of SearchResult that remembers where it came from."""
    source: str = ""
    approximate: bool = False

    def __init__(self, items=(), source: str = "", approximate: bool = False):
        super().__init__(items)
        self.source, self.approximate = source, approximate


# =====================================================================================================
# Search-result page parsing (pure functions: easy to test with saved pages)
# =====================================================================================================
class _Links(HTMLParser):
    """Collects <a> elements with their class, href and text, plus any snippet blocks that follow them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self._cur: Optional[dict] = None
        self._snip: Optional[list] = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        cls = a.get("class", "")
        if tag == "a" and a.get("href"):
            self._cur = {"href": a["href"], "class": cls, "text": [], "snippet": ""}
            self.links.append(self._cur)
        if tag in ("a", "div", "p", "td", "span") and "result__snippet" in cls:
            self._snip = []

    def handle_endtag(self, tag):
        if tag == "a":
            self._cur = None
        if self._snip is not None and tag in ("a", "div", "p", "td", "span"):
            text = re.sub(r"\s+", " ", "".join(self._snip)).strip()
            if text:
                for l in reversed(self.links):
                    if "result__a" in l["class"]:
                        l["snippet"] = l["snippet"] or text
                        break
            self._snip = None

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["text"].append(data)
        if self._snip is not None:
            self._snip.append(data)


def looks_blocked(body: str) -> bool:
    low = body.lower()
    return any(m in low for m in BLOCK_MARKERS)


def _ddg_target(href: str) -> str:
    """DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<encoded target>."""
    h = htmllib.unescape(href)
    if h.startswith("//"):
        h = "https:" + h
    u = urlparse(h)
    if u.hostname and u.hostname.endswith("duckduckgo.com") and u.path.startswith("/l/"):
        t = parse_qs(u.query).get("uddg", [""])[0]
        return urllib.parse.unquote(t)
    return h


def parse_duckduckgo(body: str) -> list[SearchResult]:
    p = _Links()
    p.feed(body)
    out, seen = [], set()
    for l in p.links:
        if "result__a" not in l["class"]:
            continue
        url = _ddg_target(l["href"])
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        host = urlparse(url).hostname or ""
        if host.endswith("duckduckgo.com"):            # an ad redirect or an internal link, not a result
            continue
        seen.add(url)
        out.append(SearchResult(url, re.sub(r"\s+", " ", "".join(l["text"])).strip(), l["snippet"]))
    return out


def _bing_target(href: str) -> str:
    """Bing sometimes wraps results as bing.com/ck/a?...&u=a1<base64url of the target>."""
    h = htmllib.unescape(href)
    u = urlparse(h)
    if u.hostname and u.hostname.endswith("bing.com") and u.path.startswith("/ck/"):
        t = parse_qs(u.query).get("u", [""])[0]
        if t.startswith("a1"):
            import base64
            raw = t[2:]
            try:
                return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
            except ValueError:
                return ""
    return h


def parse_bing(body: str) -> list[SearchResult]:
    """Organic results live in <li class="b_algo"><h2><a href=...>title</a></h2> ... <p>snippet</p>."""
    out, seen = [], set()
    for block in re.split(r'<li[^>]+class="[^"]*\bb_algo\b[^"]*"', body)[1:]:
        block = block.split("</li>")[0]
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url = _bing_target(m.group(1))
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        title = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        sn = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snippet = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", sn.group(1)))).strip() if sn else ""
        out.append(SearchResult(url, title, snippet))
    return out


# =====================================================================================================
# The search-engine scraper
# =====================================================================================================
def _default_get(url: str, headers: dict, timeout: float, data: Optional[bytes] = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:                       # noqa: S310
            return r.status, r.read(1_500_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except TimeoutError:
        raise SearchError("timed out")
    except OSError as e:
        raise SearchError(f"network error: {e}")


class _Throttle:
    """Minimum gap between two requests to the same engine, shared by every audit running in this process."""

    def __init__(self, gap_s: float, clock=time.monotonic, sleep=time.sleep):
        self.gap, self._clock, self._sleep, self._last, self._lock = gap_s, clock, sleep, {}, threading.Lock()

    def wait(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            due = self._last.get(key, -1e9) + self.gap
            self._last[key] = max(now, due)
        if due > now:
            self._sleep(due - now)


ENGINES = ("duckduckgo", "bing")
_GLOBAL_THROTTLE = _Throttle(1.5)          # one per process: two audits running at once must not double the request rate


class ScrapeSearch:
    """Web search by reading results pages. Tries each engine in turn; an engine that blocks us is skipped, and
    only when all of them fail does the call raise SearchError (so FallbackSearch and the checks treat it as
    'could not verify', never as 'zero results')."""
    name = "scrape"

    def __init__(self, engines: tuple[str, ...] = ENGINES, get: Callable = _default_get, gap_s: float = 1.5,
                 timeout: float = 12.0, cache_s: float = 900.0, inventory: Optional["SiteInventory"] = None,
                 throttle: Optional[_Throttle] = None, clock=time.monotonic):
        self.engines = tuple(e for e in engines if e in ENGINES) or ENGINES
        self._get, self.timeout, self.cache_s, self._clock = get, timeout, cache_s, clock
        self.inventory = inventory
        self._throttle = throttle or _GLOBAL_THROTTLE
        if throttle is None:
            _GLOBAL_THROTTLE.gap = gap_s
        self._cache: dict = {}
        self._lock = threading.Lock()
        self.last_errors: list[str] = []

    # -- one engine ---------------------------------------------------------------------------------
    def _one(self, engine: str, query: str) -> list[SearchResult]:
        h = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.8"}
        self._throttle.wait(engine)
        if engine == "duckduckgo":
            status, body = self._get("https://html.duckduckgo.com/html/", {**h, "Content-Type": "application/x-www-form-urlencoded"},
                                     self.timeout, urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode())
            parse = parse_duckduckgo
        else:
            status, body = self._get(f"https://www.bing.com/search?q={urllib.parse.quote(query)}&count=20&setmkt=en-US",
                                     h, self.timeout)
            parse = parse_bing
        if status != 200:                                  # 202/403/418/429 are how engines say "go away"
            raise SearchError(f"{engine} answered HTTP {status} (blocked or rate limited)")
        if looks_blocked(body):
            raise SearchError(f"{engine} asked for a human check")
        res = parse(body)
        if not res and "result" not in body.lower():
            raise SearchError(f"{engine} returned a page with no recognisable results (layout changed?)")
        return res

    def search(self, query: str, count: int = 20) -> ResultList:
        key = (query, count)
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < self.cache_s:
                return hit[1]
        errors: list[str] = []
        for eng in self.engines:
            try:
                res = self._one(eng, query)
            except SearchError as e:
                errors.append(str(e))
                continue
            out = ResultList(res[:count], source=f"scrape:{eng}")
            with self._lock:
                self._cache[key] = (self._clock(), out)
            return out
        self.last_errors = errors
        # no engine would answer. For "site:domain" we can still say something true from the site itself.
        m = re.fullmatch(r"\s*site:(\S+)\s*", query)
        if m and self.inventory:
            inv = self.inventory.collect(m.group(1))
            if inv.pages:
                urls = ResultList([SearchResult(u, "", "") for u in inv.pages[:count]], source="scrape:site-crawl",
                                  approximate=True)
                return urls
        raise SearchError("; ".join(errors) or "no search engine available", transient=True)


# =====================================================================================================
# The firm's own site: sitemap, links, published profile links
# =====================================================================================================
PROFILE_PATTERNS = {
    "google_maps": re.compile(r"https?://(?:www\.)?(?:google\.[a-z.]+/maps[^\s\"'<>]*|maps\.app\.goo\.gl/[^\s\"'<>]+|g\.page/[^\s\"'<>]+|goo\.gl/maps/[^\s\"'<>]+)", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[^\s\"'<>?#]+", re.I),
    "yelp": re.compile(r"https?://(?:www\.)?yelp\.com/biz/[^\s\"'<>?#]+", re.I),
    "napfa": re.compile(r"https?://(?:www\.)?napfa\.org/[^\s\"'<>]+", re.I),
    "paladin": re.compile(r"https?://(?:www\.)?paladinregistry\.com/[^\s\"'<>]+", re.I),
    "iapd": re.compile(r"https?://(?:www\.)?adviserinfo\.sec\.gov/[^\s\"'<>]+", re.I),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[^\s\"'<>?#]+", re.I),
}


@dataclass
class Inventory:
    domain: str
    pages: list[str] = field(default_factory=list)          # same-site page URLs found
    sitemap_urls: int = 0
    profiles: dict[str, list[str]] = field(default_factory=dict)
    robots_blocked: bool = False
    error: str = ""


def profile_links(html: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for kind, rx in PROFILE_PATTERNS.items():
        urls = []
        for m in rx.finditer(htmllib.unescape(html)):
            u = m.group(0).rstrip(".,;)")
            if u not in urls:
                urls.append(u)
        if urls:
            out[kind] = urls[:3]
    return out


class _Hrefs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            h = dict(attrs).get("href")
            if h:
                self.hrefs.append(h)


def _same_site(host: str, domain: str) -> bool:
    return host_matches(host.lower().removeprefix("www."), domain)


SKIP_EXT = re.compile(r"\.(?:pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|pptx?|mp[34]|css|js|ico|xml)(?:\?|$)", re.I)


class SiteInventory:
    """Reads sitemap.xml and the homepage's own links. Same-site only, capped, robots.txt honoured."""

    def __init__(self, fetcher: Optional[SharedFetcher] = None, max_pages: int = 60, timeout: float = 8.0):
        self.fetcher = fetcher or SharedFetcher()
        self.max_pages, self.timeout = max_pages, timeout
        self._cache: dict[str, Inventory] = {}
        self._lock = threading.Lock()

    def _get(self, url: str) -> Page:
        p = self.fetcher.get(url, self.timeout)
        if not p.ok and "certificate" in p.error:
            p = self.fetcher.get(url, self.timeout, verify=False)
        return p

    def _sitemap(self, domain: str, robots: str) -> list[str]:
        cands = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots) or [f"https://{domain}/sitemap.xml"]
        urls: list[str] = []
        todo, seen = list(cands[:3]), set()
        while todo and len(urls) < 500 and len(seen) < 6:
            sm = todo.pop(0)
            if sm in seen:
                continue
            seen.add(sm)
            p = self._get(sm)
            if not p.ok or "<loc" not in p.html:
                continue
            for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", p.html):
                loc = htmllib.unescape(loc)
                if urlparse(loc).path.lower().endswith(".xml"):
                    todo.append(loc)                          # a sitemap index: follow it (bounded by `seen`)
                elif _same_site(urlparse(loc).hostname or "", domain):
                    urls.append(loc)
        return urls

    def collect(self, domain: str) -> Inventory:
        domain = domain.lower().removeprefix("www.")
        with self._lock:
            if domain in self._cache:
                return self._cache[domain]
        inv = Inventory(domain)
        base = f"https://{domain}/"
        rp = self._get(f"{base}robots.txt")
        robots_txt = rp.html if rp.ok else ""
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(robots_txt.splitlines())
        allowed = (lambda u: parser.can_fetch("*", u)) if robots_txt else (lambda u: True)
        home = self._get(base)
        pages: list[str] = []
        if home.ok and home.html:
            inv.profiles = profile_links(home.html)
            h = _Hrefs()
            h.feed(home.html)
            for href in h.hrefs:
                u = urllib.parse.urljoin(home.final_url or base, href.split("#")[0])
                pu = urlparse(u)
                if pu.scheme in ("http", "https") and _same_site(pu.hostname or "", domain) and not SKIP_EXT.search(pu.path):
                    pages.append(u.rstrip("/") or u)
            pages.append(base.rstrip("/"))
        else:
            inv.error = home.error or "homepage unavailable"
        sm = self._sitemap(domain, robots_txt)
        inv.sitemap_urls = len(sm)
        for u in sm:
            if allowed(u):
                pages.append(u.rstrip("/"))
        seen, ordered = set(), []
        for u in pages:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        inv.pages = ordered[: max(self.max_pages, 0)]
        inv.robots_blocked = bool(robots_txt) and not allowed(base)
        with self._lock:
            self._cache[domain] = inv
        return inv


# =====================================================================================================
# SEC IAPD by search
# =====================================================================================================
@dataclass
class IapdHit:
    crd: str
    name: str
    url: str


IAPD_RE = re.compile(r"adviserinfo\.sec\.gov/(?:firm/(?:summary|brochure)|firm)/(\d{3,8})", re.I)


def find_iapd_in_results(results: list[SearchResult]) -> list[IapdHit]:
    out, seen = [], set()
    for r in results:
        m = IAPD_RE.search(r.url)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            name = re.sub(r"\s*[-|–]\s*(?:SEC|Investment Adviser|IAPD|Form ADV).*$", "", r.title or "", flags=re.I).strip()
            out.append(IapdHit(m.group(1), name, f"https://adviserinfo.sec.gov/firm/summary/{m.group(1)}"))
    return out


# =====================================================================================================
# IAPD fallback client: same best_match() interface as iapd.IapdClient
# =====================================================================================================
class ScrapeIapd:
    """Confirms the firm's SEC registration without the IAPD API. Two sources, best first:
       1. a link to adviserinfo.sec.gov on the firm's own homepage (the CRD number is in the URL);
       2. a web search restricted to adviserinfo.sec.gov.
    The record it returns has no address or phone (the IAPD site is a JavaScript app), so checks that compare
    addresses stay 'unverified'; what it does establish is that a registration exists and its CRD number."""

    def __init__(self, search: ScrapeSearch, inventory: Optional[SiteInventory] = None):
        self.search_engine, self.inventory = search, inventory

    def best_match(self, name: str, domain: str, aliases: tuple[str, ...] = ()):
        from .iapd import IapdMatch, IapdRecord
        from .nap import name_similarity
        if self.inventory:
            for u in (self.inventory.collect(domain).profiles.get("iapd") or []):
                m = re.search(r"/firm/(?:summary|brochure)?/?(\d{3,8})", u) or re.search(r"/(\d{3,8})(?:[/?#]|$)", u)
                if m:
                    crd = m.group(1)
                    rec = IapdRecord(crd=crd, name=name, website=domain, status="linked from the firm's website")
                    return IapdMatch("matched", rec, 1.0, [{"name": name, "crd": crd, "score": 1.0}])
        hits: dict[str, IapdHit] = {}
        try:
            for q in dict.fromkeys([name, *aliases]):
                for h in find_iapd_in_results(self.search_engine.search(f'site:adviserinfo.sec.gov "{q}"', 10)):
                    hits[h.crd] = h
        except SearchError as e:
            from .iapd import IapdMatch as M
            return M("error", error=f"scrape: {e}", transient=True)
        scored = sorted(((max([name_similarity(n, h.name) for n in (name, *aliases)]) if h.name else 0.0, h)
                         for h in hits.values()), key=lambda x: -x[0])
        cands = [{"name": h.name, "crd": h.crd, "score": round(s, 2)} for s, h in scored[:5]]
        if not scored or scored[0][0] < 0.85:
            return IapdMatch("not_found", confidence=scored[0][0] if scored else 0.0, candidates=cands)
        if len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.05:
            return IapdMatch("ambiguous", confidence=scored[0][0], candidates=cands)
        best = scored[0][1]
        return IapdMatch("matched", IapdRecord(crd=best.crd, name=best.name, website="", status="found by web search"),
                         scored[0][0], cands)


class FallbackIapd:
    """The IAPD API first; the scraper only when the API errors (unreachable, changed shape, blocked)."""

    def __init__(self, primary, secondary):
        self.primary, self.secondary = primary, secondary

    def best_match(self, name: str, domain: str, aliases: tuple[str, ...] = ()):
        m = self.primary.best_match(name, domain, aliases)
        if m.status != "error":
            return m
        s = self.secondary.best_match(name, domain, aliases)
        if s.status == "error":
            return m                                  # both failed: report the API's error, it is the actionable one
        s.error = f"IAPD API failed ({m.error}); used web search instead"
        return s
