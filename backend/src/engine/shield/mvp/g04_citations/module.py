"""Group 04: citation surfaces, 20 pts spread across six surfaces.

Discovery agent: Claude with web search, restricted to the surface's own domain, finds the
firm's listing. Verifier: deterministic. A claimed listing only counts if the URL was really
returned by the search tool, the name matches, and the NAP agrees with the reference record.
Blocked or failed surfaces are 'unverified', never failed. Manual-only surfaces go to review.

ASSUMPTIONS: the directory and review sites below (the design names none), and that listing
NAP counts as consistent at 75% field agreement.

Fix in this version: a listing is only 'verified' when at least one NAP field was actually
compared. If the reference or the listing has nothing comparable, the surface is 'unverified'.

OPEN PRODUCT DECISION (not changed here): two of the six surfaces are manual-only and can
never score, so the group tops out at 13.3 of 20.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from ..iapd import SharedIapd
from ..kernel import Finding, GroupResult, RunContext
from ..llm import AnthropicClient, LLMError, parse_json
from ..nap import CSZ_RE, Nap, agreement, compare, extract_site_nap, is_consistent, name_similarity, norm_name, norm_text
from ..search import host_matches
from ..web import SharedFetcher, parse_page

CONSISTENT_AT = 0.75


@dataclass(frozen=True)
class Surface:
    key: str
    label: str
    mode: str                       # api | web | manual
    domains: tuple[str, ...] = ()


DEFAULT_SURFACES = (
    Surface("iapd", "SEC IAPD record", "api"),
    Surface("napfa", "NAPFA directory", "web", ("napfa.org",)),
    Surface("paladin", "Paladin Registry", "web", ("paladinregistry.com",)),
    Surface("yelp", "Yelp (review site)", "web", ("yelp.com",)),
    Surface("linkedin", "LinkedIn company page", "manual"),
    Surface("media", "Media mentions", "manual"),
)

SYSTEM = ("You find a firm's listing page using web search. You report only what search results "
          "show. You never guess a URL or a detail.")
PROMPT = ('Find the listing page for this firm on {domains}.\nFirm: {name}\nWebsite: {site}\n'
          'Location: {area}\n\nReply with ONLY JSON, no prose: {{"found": true or false, "url": "", '
          '"name": "", "address": "", "phone": "", "website": ""}}. Leave a field "" if the results '
          'do not show it. If you cannot find this firm\'s own listing, reply {{"found": false}}.')


def _clean(u: str) -> str:
    return u.split("#")[0].rstrip("/")


def parse_address(addr: str) -> Nap:
    nap = Nap()
    if m := CSZ_RE.search(addr):
        nap.city, nap.state, nap.zip = m.group(1).strip(), m.group(2), m.group(3)
        nap.street = addr[: m.start()].strip(" ,")
    elif addr:
        nap.street = addr
    return nap


@dataclass
class CitationSurfaces:
    fetcher: SharedFetcher = field(default_factory=SharedFetcher)
    iapd: Optional[SharedIapd] = None
    client: Optional[AnthropicClient] = None
    surfaces: tuple[Surface, ...] = DEFAULT_SURFACES
    max_searches: int = 2
    scraper: Optional[object] = None          # scrape.ScrapeSearch: finds listings by reading search results when no model can
    inventory: Optional[object] = None        # scrape.SiteInventory: the profile links the firm publishes itself
    group_id = "g04_citations"
    max_points = 20.0

    # --- reference NAP: the regulator record when we have it, else what the site says --------
    def _reference(self, ctx: RunContext) -> tuple[Nap, str]:
        if self.iapd:
            m = self.iapd.match(ctx.firm_name, ctx.domain, ctx.aliases)
            if m.status == "matched" and (m.record.nap.city or m.record.nap.phone):
                return m.record.nap, f"IAPD CRD {m.record.crd}"
        page = self.fetcher.get(f"https://{ctx.domain}/", 8.0)
        if page.html:
            n = extract_site_nap(parse_page(page.html))
            if n.present():
                return n, "website"
        return Nap(), "none"

    def run(self, ctx: RunContext) -> GroupResult:
        per = self.max_points / len(self.surfaces)
        ref, ref_src = self._reference(ctx)
        results: dict[str, dict] = {}

        def one(s: Surface) -> dict:
            if s.mode == "manual":
                return {"status": "manual", "detail": "manual-only surface; held for human review"}
            if s.mode == "api":
                return self._iapd(ctx, ref)
            return self._web(s, ctx, ref)

        with ThreadPoolExecutor(max_workers=3) as pool:
            for s, res in zip(self.surfaces, pool.map(one, self.surfaces)):
                results[s.key] = res

        findings, score = [], 0.0
        for s in self.surfaces:
            r = results[s.key]
            ok = r["status"] == "verified"
            score += per if ok else 0.0
            st = {"unverified": "unverified", "manual": "manual"}.get(r["status"], "ok")
            findings.append(Finding(f"surface:{s.key}", ok, round(per if ok else 0.0, 2), round(per, 2),
                                    f"{s.label}: {r['status']}. {r['detail']}", st))
        partial = any(f.status in ("unverified", "manual") for f in findings)
        return GroupResult(self.group_id, round(score, 2), self.max_points, findings,
                           {"reference": {"source": ref_src, **ref.present()}, "surfaces": results,
                            "manual_review": [s.label for s in self.surfaces if s.mode == "manual"]},
                           partial)

    def _iapd(self, ctx, ref: Nap) -> dict:
        if not self.iapd:
            return {"status": "unverified", "detail": "IAPD lookup not configured"}
        m = self.iapd.match(ctx.firm_name, ctx.domain, ctx.aliases)
        if m.status == "error":
            return {"status": "unverified", "detail": f"IAPD lookup failed: {m.error}"}
        if m.status == "ambiguous":
            return {"status": "unverified", "detail": "several IAPD records fit the name", "candidates": m.candidates}
        if m.status == "not_found":
            return {"status": "not_found", "detail": "no IAPD record matched the firm name", "candidates": m.candidates}
        rec = m.record
        page = self.fetcher.get(f"https://{ctx.domain}/", 8.0)
        site = extract_site_nap(parse_page(page.html)) if page.html else Nap()
        cmp = compare(site, rec.nap)
        a = agreement(cmp)
        out = {"url": rec.url, "found": rec.nap.present(), "vs_website": cmp, "agreement": a}
        if a is None:
            return {**out, "status": "unverified",
                    "detail": f"record found (CRD {rec.crd}), but the website gave no NAP to compare "
                              f"(it may be JavaScript-built)"}
        if not is_consistent(cmp, CONSISTENT_AT):
            bad = ", ".join(k for k, v in cmp.items() if v is False)
            return {**out, "status": "inconsistent", "detail": f"record exists but the website differs on {bad}"}
        return {**out, "status": "verified", "detail": f"record found (CRD {rec.crd})"}

    def _web(self, s: Surface, ctx, ref: Nap) -> dict:
        if not self.client:
            if self.scraper or self.inventory:
                return self._web_scrape(s, ctx, ref, "no language-model key")
            return {"status": "unverified", "detail": "needs a language-model key (ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY) for the discovery agent"}
        est = self.client.estimate_cost(4000, 500, True) + self.client.search_cost * (self.max_searches - 1)
        if not ctx.budget.can_afford(est):
            if self.scraper or self.inventory:
                return self._web_scrape(s, ctx, ref, "budget exhausted")
            return {"status": "unverified", "detail": "budget exhausted before this surface"}
        prompt = PROMPT.format(domains=", ".join(s.domains), name=ctx.firm_name, site=ctx.domain, area=ctx.area)
        try:
            res = self.client.complete(prompt, system=SYSTEM, max_tokens=500, search=True,
                                       max_searches=self.max_searches, allowed_domains=list(s.domains),
                                       timeout=60.0)
        except LLMError as e:
            if self.scraper or self.inventory:
                return self._web_scrape(s, ctx, ref, f"model discovery failed: {e}")
            return {"status": "unverified", "detail": f"discovery failed: {e}"}
        ctx.budget.charge(res.cost_usd)
        cost = round(res.cost_usd, 5)
        try:
            d = parse_json(res.text)
            if not isinstance(d, dict):
                raise ValueError("not an object")
        except ValueError:
            return {"status": "unverified", "detail": "discovery agent returned unreadable output", "cost": cost}
        if not d.get("found"):
            return {"status": "not_found", "detail": "no listing found by search", "cost": cost}
        url = str(d.get("url", ""))
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not any(host_matches(host, dom) for dom in s.domains) or _clean(url) not in {_clean(u) for u in res.urls}:
            return {"status": "not_found", "cost": cost,
                    "detail": "agent named a URL the search did not return, so it was rejected"}
        found = parse_address(str(d.get("address", "")))
        found.name, found.phone = str(d.get("name", "")), str(d.get("phone", ""))
        site_ok = bool(d.get("website")) and host_matches(
            (urlparse(str(d["website"]) if "//" in str(d["website"]) else "//" + str(d["website"])).hostname or "")
            .removeprefix("www."), ctx.domain)
        return self._verify(ctx, ref, url, found, site_ok or name_similarity(found.name, ctx.firm_name) >= 0.8, cost, found.name)

    def _verify(self, ctx, ref: Nap, url: str, found: Nap, name_ok: bool, cost: float = 0.0, listed_name: str = "",
                extra: Optional[dict] = None) -> dict:
        """Shared by the model path and the scraping path: a listing counts only if it is the firm's, and only 'verified'
        when at least one NAP field was really compared and the fields agree."""
        extra = {**(extra or {}), **({"cost": cost} if cost else {})}
        if not name_ok:
            return {"status": "not_found", "url": url, **extra,
                    "detail": f"listing '{listed_name}' does not match the firm name"}
        cmp = compare(found, ref)
        a = agreement(cmp)
        out = {"url": url, "found": found.present(), "vs_reference": cmp, "agreement": a, **extra}
        if a is None:
            # nothing was compared (no reference NAP, or the listing shows no address or phone)
            return {**out, "status": "unverified",
                    "detail": f"{url} found, but there are no comparable NAP fields to check consistency"}
        if not is_consistent(cmp, CONSISTENT_AT):
            bad = ", ".join(k for k, v in cmp.items() if v is False)
            return {**out, "status": "inconsistent", "detail": f"listing differs from the reference on {bad}"}
        return {**out, "status": "verified", "detail": url}

    # --- the scraping version of discovery: no model, only public pages ---------------------------------------
    def _web_scrape(self, s: Surface, ctx, ref: Nap, why: str) -> dict:
        """Find the listing without a model: the links the firm publishes itself, then a search restricted to the
        surface's domain. Each candidate page is read and checked by the same rules as the model path. A page that cannot
        be read is 'unverified', never a failure, and a URL is only a candidate if it came from the firm's own site or
        from search results."""
        mark = {"scrape": True, "scrape_reason": why}
        on_surface = lambda host: any(host_matches(host.removeprefix("www."), d) for d in s.domains)   # noqa: E731
        cands: list[tuple[str, str]] = []            # (url, where it came from)
        if self.inventory:
            try:
                for u in (self.inventory.collect(ctx.domain).profiles.get(s.key) or []):
                    pu = urlparse(u)
                    # a badge that points at the directory's home page is not a listing
                    if on_surface(pu.hostname or "") and (len([x for x in pu.path.split("/") if x]) >= 2 or pu.query):
                        cands.append((u, "linked from the firm's website"))
            except Exception:                         # noqa: BLE001 - the search below can still answer
                pass
        search_err = ""
        if self.scraper:
            try:
                for q in (f'site:{s.domains[0]} "{ctx.firm_name}"', f'{ctx.firm_name} {s.label.split(" (")[0]} {ctx.area}'):
                    for r in self.scraper.search(q, 10):
                        if not on_surface(r.host) or any(r.url == c[0] for c in cands):
                            continue
                        head = re.split(r"\s[|\-–—·]\s", r.title)[0] if r.title else ""
                        if name_similarity(head, ctx.firm_name) >= 0.6 or norm_name(ctx.firm_name) in norm_text(r.title + " " + r.snippet):
                            cands.append((r.url, "found by web search"))
                    if len(cands) >= 3:
                        break
            except Exception as e:                    # noqa: BLE001
                search_err = str(e)
        unreadable = None
        for url, source in cands[:3]:
            page = self.fetcher.get(url, 8.0)
            if not page.ok or not page.html:
                unreadable = unreadable or (url, source, page.error or "empty page")
                continue
            meta = parse_page(page.html)
            found = extract_site_nap(meta)
            title = meta.title or ""
            site_ok = ctx.domain in page.html.lower()
            name_ok = site_ok or (bool(norm_name(ctx.firm_name)) and norm_name(ctx.firm_name) in norm_text(title + " " + meta.text[:4000]))
            if not name_ok:
                continue                              # someone else's page, or the directory's front page: try the next
            found.name = found.name or title
            return self._verify(ctx, ref, url, found, True, 0.0, title, {**mark, "found_via": source})
        if unreadable:
            url, source, err = unreadable
            return {"status": "unverified", "url": url, **mark, "found_via": source,
                    "detail": f"a listing was found ({source}) at {url} but the page could not be read ({err}), so its details were not checked"}
        if search_err and not cands:
            return {"status": "unverified", **mark, "detail": f"could not search for the listing: {search_err}"}
        return {"status": "not_found", **mark, "detail": "no listing found on the firm's website or in web search results"}
