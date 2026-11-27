"""Group 02: search visibility, 20 pts. Indexed pages 6, branded rank 7,
Google Business Profile 4, NAP consistency with IAPD 3.

Each check runs on its own. A check whose data source is not configured or is
unreachable is 'unverified' and earns nothing; it is never scored from a guess.
ASSUMPTION: full marks for indexing at 10 or more results (one query returns at most 20).

Fix in this version: if the site has almost no static text (a JavaScript-built page), the
NAP check is unverified instead of a failure, because the address may simply not be in the HTML.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..iapd import SharedIapd
from ..kernel import Finding, GroupResult, RunContext
from ..nap import Nap, agreement, compare, extract_site_nap, name_similarity
from ..search import BraveSearch, PlacesClient, SearchError, host_matches
from ..web import SharedFetcher, parse_page

THIN_PAGE_CHARS = 200


@dataclass
class SearchVisibility:
    fetcher: SharedFetcher = field(default_factory=SharedFetcher)
    iapd: Optional[SharedIapd] = None
    brave: Optional[BraveSearch] = None
    places: Optional[PlacesClient] = None
    inventory: Optional[object] = None       # scrape.SiteInventory: lets the business-profile check use the firm's own links
    indexed_full_at: int = 10
    group_id = "g02_search"
    max_points = 20.0

    def run(self, ctx: RunContext) -> GroupResult:
        F: list[Finding] = []
        ev: dict = {}

        def add(check, pts, mx, detail, unverified=False, status=None):
            F.append(Finding(check, pts >= mx, round(pts, 2), mx, detail,
                             status or ("unverified" if unverified else "ok")))

        def note_scrape(kind: str, what: str):
            ev.setdefault("scrape", {"used": True, "checks": {}})["checks"][kind] = what

        def via(res) -> str:
            src = getattr(res, "source", "") or ""
            if not src.startswith("scrape"):
                return ""
            engine = src.split(":", 1)[1] if ":" in src else "web"
            return {"duckduckgo": " (read from DuckDuckGo results, not Google)", "bing": " (read from Bing results, not Google)",
                    "site-crawl": " (estimated from the site's own sitemap and links)"}.get(engine, " (read from public web pages)")

        dom, name = ctx.domain, ctx.firm_name

        # 1. pages indexed (6)
        if not self.brave:
            add("pages_indexed", 0, 6, "needs a search API key (BRAVE_API_KEY, SERPER_API_KEY, SERPAPI_API_KEY or GOOGLE_CSE_API_KEY)", True)
        else:
            try:
                res = self.brave.search(f"site:{dom}", 20)
                n = sum(host_matches(r.host, dom) for r in res)
                ev["indexed_results"] = n
                if getattr(res, "approximate", False):
                    # no search engine would answer: this is what the site offers, not what is indexed. Half marks at most.
                    note_scrape("pages_indexed", "estimated from the site's own sitemap and links")
                    add("pages_indexed", 3 * min(1.0, n / self.indexed_full_at), 6,
                        f"{n} page(s) found on the site itself (sitemap and links){via(res)}; no search engine could be asked, so this "
                        f"shows what could be indexed, not what is", status="partial")
                else:
                    if via(res):
                        note_scrape("pages_indexed", via(res).strip(" ()"))
                    add("pages_indexed", 6 * min(1.0, n / self.indexed_full_at), 6,
                        f"{n} result(s) for site:{dom} (full marks at {self.indexed_full_at}+){via(res)}")
            except Exception as e:                    # noqa: BLE001 - one failed lookup must not sink the checks that passed
                add("pages_indexed", 0, 6, f"search failed: {e}", True)

        # 2. branded query ranks top 3 (7)
        if not self.brave:
            add("branded_rank_top3", 0, 7, "needs a search API key (BRAVE_API_KEY, SERPER_API_KEY, SERPAPI_API_KEY or GOOGLE_CSE_API_KEY)", True)
        else:
            try:
                res = self.brave.search(name, 10)
                rank = next((i + 1 for i, r in enumerate(res) if host_matches(r.host, dom)), None)
                ev["branded_rank"] = rank
                if via(res):
                    note_scrape("branded_rank_top3", via(res).strip(" ()"))
                add("branded_rank_top3", 7 if rank and rank <= 3 else 0, 7,
                    (f"ranks #{rank} for \"{name}\"" if rank else f"not in the top {len(res)} for \"{name}\"") + via(res))
            except Exception as e:                    # noqa: BLE001
                add("branded_rank_top3", 0, 7, f"search failed: {e}", True)

        # 3. Google Business Profile (4)
        place = None

        def profile_from_site(why: str) -> bool:
            """Without a places lookup, the strongest thing we can read is a link from the firm's own site to its Google
            Maps profile. That shows a profile exists; the profile page itself is not opened."""
            if not self.inventory:
                return False
            try:
                links = self.inventory.collect(dom).profiles.get("google_maps") or []
            except Exception:                         # noqa: BLE001
                return False
            if not links:
                return False
            ev["google_profile_link"] = links[0]
            note_scrape("google_business_profile", "the firm's own website links to its Google Maps profile")
            add("google_business_profile", 4, 4,
                f"The website links to its Google Maps profile ({links[0]}). The profile page itself was not opened ({why})")
            return True

        if not self.places:
            if not profile_from_site("no places API is configured"):
                add("google_business_profile", 0, 4, "needs a places API key (GOOGLE_PLACES_API_KEY, SERPER_API_KEY or SERPAPI_API_KEY); the website "
                    "does not link to a Google profile, so it could not be confirmed either way", True)
        else:
            try:
                cands = self.places.find(f"{name} {ctx.area}")
                place = next((p for p in cands if p.website and host_matches(
                    p.website.lower().split("//")[-1].split("/")[0].removeprefix("www."), dom)), None) \
                    or next((p for p in cands if name_similarity(p.name, name) >= 0.85), None)
                ev["places_candidates"] = [p.name for p in cands]
                if place:
                    ev["place"] = {"name": place.name, "website": place.website, **place.nap.present()}
                add("google_business_profile", 4 if place else 0, 4,
                    f"found: {place.name}" if place else "no matching business profile found")
            except Exception as e:                    # noqa: BLE001
                if not profile_from_site(f"Places lookup failed: {e}"):
                    add("google_business_profile", 0, 4, f"Places lookup failed: {e}", True)

        # 4. NAP consistent with IAPD (3)
        try:
            self._nap(ctx, place, add, ev)
        except Exception as e:                        # noqa: BLE001
            add("nap_consistent_with_iapd", 0, 3, f"could not be checked: {type(e).__name__}: {e}", True)

        return GroupResult(self.group_id, round(sum(f.points for f in F), 2), self.max_points, F, ev,
                           partial=any(f.status in ("unverified", "partial") for f in F))

    def _nap(self, ctx, place, add, ev):
        c = "nap_consistent_with_iapd"
        if not self.iapd:
            return add(c, 0, 3, "IAPD lookup not configured", True)
        m = self.iapd.match(ctx.firm_name, ctx.domain, ctx.aliases)
        ev["iapd"] = {"status": m.status, "confidence": m.confidence, "candidates": m.candidates,
                      "crd": m.record.crd if m.record else None, "error": m.error}
        if m.status == "error":
            return add(c, 0, 3, f"IAPD lookup failed: {m.error}", True)
        if m.status != "matched":
            return add(c, 0, 3, f"no single IAPD record matched ({m.status}); cannot compare", True)
        ref = m.record.nap
        if not (ref.street or ref.city or ref.phone):
            return add(c, 0, 3, "IAPD record found but no address or phone could be read from it", True)
        page = self.fetcher.get(f"https://{ctx.domain}/", 8.0)
        sources = {}
        thin = False
        if page.html:
            meta = parse_page(page.html)
            thin = len(meta.text) < THIN_PAGE_CHARS
            sources["website"] = extract_site_nap(meta)
        if place:
            sources["google_profile"] = place.nap
        if not sources:
            return add(c, 0, 3, f"nothing to compare: {page.error or 'page unavailable'}", True)
        scores, notes = [], []
        for label, nap in sources.items():
            cmp = compare(nap, ref)
            ev.setdefault("nap", {})[label] = {"found": nap.present(), "vs_iapd": cmp}
            a = agreement(cmp)
            if a is None:
                notes.append(f"{label}: no comparable fields")
            else:
                scores.append(a)
                bad = [k for k, v in cmp.items() if v is False]
                notes.append(f"{label}: {'matches' if not bad else 'differs on ' + ', '.join(bad)}")
        if not scores:
            if thin:
                return add(c, 0, 3, "; ".join(notes) + " (page has almost no static text, so it is likely "
                                   "JavaScript-built; the address may exist but could not be read)", True)
            return add(c, 0, 3, "; ".join(notes) + " (the site publishes no address or phone to compare)")
        add(c, 3 * sum(scores) / len(scores), 3, "; ".join(notes) + f" (IAPD CRD {m.record.crd})")