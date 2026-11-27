import pytest
"""The scraping fallback: parsing, blocking, the fallback chain, each group's use of it, and a whole audit with no keys.

All pages here are recorded-style fixtures. Nothing touches the network.
"""
import base64
import sys, pathlib
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from advisorgro.engine.shield.mvp.config import Config
from advisorgro.engine.shield.mvp.engine import build_modules, build_search
from advisorgro.engine.shield.mvp.fallback import FallbackGroup, is_blank
from advisorgro.engine.shield.mvp.g02_search.module import SearchVisibility
from advisorgro.engine.shield.mvp.g03_ai_visibility.serp import CHECK as SERP_CHECK, SerpVisibility
from advisorgro.engine.shield.mvp.g04_citations.module import CitationSurfaces
from advisorgro.engine.shield.mvp.iapd import IapdClient, IapdError, IapdMatch, SharedIapd
from advisorgro.engine.shield.mvp.kernel import Budget, Finding, GroupResult, RunContext
from advisorgro.engine.shield.mvp.nap import Nap
from advisorgro.engine.shield.mvp.scrape import (FallbackIapd, ResultList, ScrapeIapd, ScrapeSearch, SiteInventory, find_iapd_in_results,
                    looks_blocked, parse_bing, parse_duckduckgo, profile_links)
from advisorgro.engine.shield.mvp.search import FallbackSearch, SearchError, SearchResult
from advisorgro.engine.shield.mvp.web import Page, SharedFetcher

ON = {"ADVISORGRO_SCRAPE_FALLBACK": "1"}
DOM = "merrowlanewealth.com"
NAME, AREA = "Merrow Lane Wealth", "Austin, TX"


# ---------------------------------------------------------------------------- fixtures
def ddg_html(results):
    rows = ""
    for url, title, snip in results:
        wrapped = "//duckduckgo.com/l/?uddg=" + urllib.parse.quote(url, safe="") + "&amp;rut=abc"
        rows += (f'<div class="result results_links web-result"><h2 class="result__title">'
                 f'<a rel="nofollow" class="result__a" href="{wrapped}">{title}</a></h2>'
                 f'<a class="result__snippet" href="{wrapped}">{snip}</a></div>')
    return f"<html><body><div id='links' class='results'>{rows}</div></body></html>"


def bing_html(results, wrap=False):
    rows = ""
    for url, title, snip in results:
        href = url
        if wrap:
            href = "https://www.bing.com/ck/a?!&&p=x&u=a1" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=") + "&ntb=1"
        rows += (f'<li class="b_algo"><h2><a href="{href}">{title}</a></h2>'
                 f'<div class="b_caption"><p>{snip}</p></div></li>')
    return f"<html><body><ol id='b_results'>{rows}</ol></body></html>"


BLOCK_PAGE = "<html><body><p>Our systems have detected unusual traffic from your computer network.</p></body></html>"


def scraper(handler, **kw):
    """A ScrapeSearch whose network is `handler(engine, query) -> (status, body)`."""
    def get(url, headers, timeout, data=None):
        if "duckduckgo" in url:
            q = urllib.parse.parse_qs(data.decode())["q"][0]
            return handler("duckduckgo", q)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
        return handler("bing", q)
    kw.setdefault("gap_s", 0.0)
    return ScrapeSearch(get=get, **kw)


def pages(mapping):
    """A SharedFetcher over a dict of url -> html (404 for anything else)."""
    def fetch(url, timeout=8.0, verify=True):
        v = mapping.get(url)
        if v is None:
            return Page(False, 404, url, error="HTTP 404")
        if isinstance(v, Page):
            return v
        return Page(True, 200, url, "text/html", v, len(v))
    return SharedFetcher(fetch=fetch, ttl_s=0.0)


HOME = ('<html><head><title>Merrow Lane Wealth | Austin</title>'
        '<script type="application/ld+json">{"@type":"FinancialService","name":"Merrow Lane Wealth","telephone":"512-555-0100",'
        '"address":{"streetAddress":"123 Main St","addressLocality":"Austin","addressRegion":"TX","postalCode":"78701"}}</script></head>'
        '<body><p>' + "We plan for retirees and business owners in Austin. " * 8 + '</p>'
        '<a href="/about">About</a><a href="/services">Services</a><a href="https://other.com/x">x</a>'
        '<a href="https://g.page/merrow-lane-wealth">Find us on Google</a>'
        '<a href="https://www.napfa.org/">NAPFA member</a>'
        '<a href="https://www.napfa.org/find-an-advisor/merrow-lane-wealth">Our NAPFA profile</a></body></html>')
SITEMAP = "<urlset>" + "".join(f"<url><loc>https://{DOM}/p{i}</loc></url>" for i in range(14)) + "</urlset>"
NAPFA_PAGE = ('<html><head><title>Merrow Lane Wealth - NAPFA</title>'
              '<script type="application/ld+json">{"@type":"Organization","name":"Merrow Lane Wealth","telephone":"(512) 555-0100",'
              '"address":{"streetAddress":"123 Main Street","addressLocality":"Austin","addressRegion":"TX","postalCode":"78701"}}</script>'
              '</head><body>Merrow Lane Wealth, Austin, TX</body></html>')
SITE = {f"https://{DOM}/": HOME, f"https://{DOM}/sitemap.xml": SITEMAP,
        "https://www.napfa.org/find-an-advisor/merrow-lane-wealth": NAPFA_PAGE}


def ctx(**kw):
    return RunContext(f"https://{DOM}", NAME, AREA, Budget(0.5, 100.0), domain=DOM, **kw)


def firm_in_results(engine, q):
    """What a healthy search engine would say about this firm."""
    if q == f"site:{DOM}":
        return 200, ddg_html([(f"https://{DOM}/p{i}", f"Page {i}", "") for i in range(12)])
    if q == NAME:
        return 200, ddg_html([(f"https://www.{DOM}/", "Merrow Lane Wealth", "Austin RIA"), ("https://yelp.com/x", "Yelp", "")])
    if q.startswith("site:adviserinfo.sec.gov"):
        return 200, ddg_html([("https://adviserinfo.sec.gov/firm/summary/123456", "MERROW LANE WEALTH, LLC - Investment Adviser Firm Summary", "")])
    if q.startswith("site:napfa.org"):
        return 200, ddg_html([("https://www.napfa.org/find-an-advisor/merrow-lane-wealth", "Merrow Lane Wealth", "NAPFA")])
    if q.startswith("site:paladinregistry.com") or q.startswith("site:yelp.com"):
        return 200, ddg_html([("https://other.example/zzz", "Somebody else", "")])
    # buyer-intent questions: the firm shows up for some of them
    if "retirees" in q or "retirement" in q:
        return 200, ddg_html([(f"https://{DOM}/services", "Retirement planning in Austin", "Merrow Lane Wealth helps retirees")])
    return 200, ddg_html([("https://alpha.example/", "Alpha Advisors", "Austin")])


# ---------------------------------------------------------------------------- parsing
def test_duckduckgo_unwraps_redirects_and_drops_internal_links():
    body = ddg_html([("https://a.com/x?y=1", "A &amp; Co", "snippet a"), ("https://b.com/", "B", "snippet b")])
    body += '<a class="result__a" href="//duckduckgo.com/y.js?ad=1">ad</a>'
    res = parse_duckduckgo(body)
    assert [r.url for r in res] == ["https://a.com/x?y=1", "https://b.com/"]
    assert res[0].title == "A & Co" and res[0].snippet == "snippet a" and res[1].host == "b.com"


def test_bing_parses_plain_and_wrapped_links():
    for wrap in (False, True):
        res = parse_bing(bing_html([("https://a.com/x", "A <b>bold</b>", "snip")], wrap=wrap))
        assert [r.url for r in res] == ["https://a.com/x"] and res[0].title == "A bold" and res[0].snippet == "snip"


def test_block_pages_are_recognised():
    assert looks_blocked(BLOCK_PAGE) and not looks_blocked(ddg_html([("https://a.com", "A", "")]))


# ---------------------------------------------------------------------------- the search scraper
def test_search_reads_results_and_remembers_the_source():
    s = scraper(firm_in_results)
    res = s.search(NAME, 10)
    assert isinstance(res, ResultList) and res.source == "scrape:duckduckgo" and not res.approximate
    assert res[0].host == DOM


def test_blocked_engine_is_skipped_and_the_next_one_answers():
    calls = []

    def h(engine, q):
        calls.append(engine)
        return (202, "") if engine == "duckduckgo" else (200, bing_html([(f"https://{DOM}/", "Merrow", "")]))
    res = scraper(h).search(NAME)
    assert calls == ["duckduckgo", "bing"] and res.source == "scrape:bing" and res[0].host == DOM


def test_every_engine_blocked_is_an_error_never_an_empty_list():
    s = scraper(lambda e, q: (200, BLOCK_PAGE))
    try:
        s.search(NAME)
        assert False, "a blocked search must raise, or 'not indexed' would be reported as a finding"
    except SearchError as e:
        assert "human check" in str(e)


def test_layout_change_with_no_results_is_an_error_not_zero_results():
    s = scraper(lambda e, q: (200, "<html><body>hello</body></html>"))
    try:
        s.search(NAME)
        assert False
    except SearchError:
        pass


def test_site_query_falls_back_to_the_sites_own_pages_and_says_so():
    inv = SiteInventory(pages(SITE))
    s = scraper(lambda e, q: (429, ""), inventory=inv)
    res = s.search(f"site:{DOM}", 20)
    assert res.approximate and res.source == "scrape:site-crawl" and len(res) >= 10


def test_answers_are_cached_and_throttle_spaces_requests():
    n = []
    s = scraper(lambda e, q: (n.append(q), firm_in_results(e, q))[1])
    s.search(NAME)
    s.search(NAME)
    assert len(n) == 1                                        # second call served from the cache
    from advisorgro.engine.shield.mvp.scrape import _Throttle
    slept, now_box = [], [100.0]
    t = _Throttle(1.5, clock=lambda: now_box[0], sleep=lambda x: slept.append(round(x, 2)))
    t.wait("ddg")
    t.wait("ddg")
    assert slept == [1.5]                                     # the second request waited for the gap


# ---------------------------------------------------------------------------- the firm's own site
def test_inventory_reads_sitemap_links_and_profiles():
    inv = SiteInventory(pages(SITE)).collect(DOM)
    assert inv.sitemap_urls == 14 and any(u.endswith("/about") for u in inv.pages) and not any("other.com" in u for u in inv.pages)
    assert inv.profiles["google_maps"] == ["https://g.page/merrow-lane-wealth"]
    assert "https://www.napfa.org/find-an-advisor/merrow-lane-wealth" in inv.profiles["napfa"]


def test_inventory_honours_robots_txt():
    site = {**SITE, f"https://{DOM}/robots.txt": "User-agent: *\nDisallow: /p1\n"}
    inv = SiteInventory(pages(site)).collect(DOM)
    import re
    assert not any(re.search(r"/p1\d?$", u) for u in inv.pages) and any(u.endswith("/p2") for u in inv.pages)


def test_inventory_on_a_dead_site_reports_an_error_not_pages():
    inv = SiteInventory(pages({})).collect("nowhere.test")
    assert inv.pages == [] and inv.error


def test_profile_links_are_found_in_html():
    out = profile_links('<a href="https://www.linkedin.com/company/merrow">in</a> <a href="https://www.yelp.com/biz/merrow-austin?x=1">y</a>')
    assert out["linkedin"] and out["yelp"] == ["https://www.yelp.com/biz/merrow-austin"]


# ---------------------------------------------------------------------------- IAPD
def test_iapd_crd_comes_from_a_link_on_the_firm_site():
    html = HOME + '<a href="https://adviserinfo.sec.gov/firm/summary/987654">Form ADV</a>'
    inv = SiteInventory(pages({**SITE, f"https://{DOM}/": html}))
    m = ScrapeIapd(scraper(firm_in_results), inv).best_match(NAME, DOM)
    assert m.status == "matched" and m.record.crd == "987654" and not m.record.nap.present()


def test_iapd_found_by_search_and_matched_on_name():
    m = ScrapeIapd(scraper(firm_in_results), SiteInventory(pages(SITE))).best_match(NAME, DOM)
    assert m.status == "matched" and m.record.crd == "123456"
    hits = find_iapd_in_results([SearchResult("https://adviserinfo.sec.gov/firm/summary/5551212", "ACME ADVISORS, LLC - Investment Adviser")])
    assert hits[0].crd == "5551212" and hits[0].name == "ACME ADVISORS, LLC"


def test_iapd_unrelated_search_hit_is_not_a_match():
    def h(e, q):
        return 200, ddg_html([("https://adviserinfo.sec.gov/firm/summary/1", "SOMETHING ELSE CAPITAL - Investment Adviser", "")])
    m = ScrapeIapd(scraper(h), None).best_match(NAME, DOM)
    assert m.status == "not_found"


def test_iapd_api_failure_falls_back_but_api_success_is_untouched():
    class Api:
        def __init__(self, status): self.status = status
        def best_match(self, *a): return IapdMatch(self.status, error="HTTP 500" if self.status == "error" else "")
    s = ScrapeIapd(scraper(firm_in_results), SiteInventory(pages(SITE)))
    m = FallbackIapd(Api("error"), s).best_match(NAME, DOM)
    assert m.status == "matched" and "IAPD API failed" in m.error
    assert FallbackIapd(Api("not_found"), s).best_match(NAME, DOM).status == "not_found"     # a real answer is kept
    dead = ScrapeIapd(scraper(lambda e, q: (429, "")), None)
    assert FallbackIapd(Api("error"), dead).best_match(NAME, DOM).error == "HTTP 500"        # both failed: the API's error


# ---------------------------------------------------------------------------- group 02
class Dead:
    name = "dead"
    def search(self, q, count=20): raise SearchError("search returned HTTP 401", transient=False)


def g02(search, places=None, inventory=None, iapd=None):
    return SearchVisibility(pages(SITE), iapd, search, places, inventory)


def test_g02_failing_api_falls_through_to_the_scraper_and_scores_real_results():
    chain = FallbackSearch([Dead(), scraper(firm_in_results)])
    r = g02(chain, inventory=SiteInventory(pages(SITE))).run(ctx())
    f = {x.check: x for x in r.findings}
    assert f["pages_indexed"].points == 6 and "DuckDuckGo" in f["pages_indexed"].detail and f["pages_indexed"].status == "ok"
    assert f["branded_rank_top3"].points == 7 and "#1" in f["branded_rank_top3"].detail
    assert r.evidence["scrape"]["checks"]["pages_indexed"]
    assert f["google_business_profile"].points == 4 and "g.page/merrow-lane-wealth" in f["google_business_profile"].detail


def test_g02_with_no_keys_at_all_still_measures_search_checks():
    cfg = Config.from_env(ON)                                          # scraping is on by default from the environment
    search = build_search(cfg, scraper(firm_in_results))
    assert isinstance(search, ScrapeSearch)
    r = g02(search).run(ctx())
    assert [x for x in r.findings if x.check in ("pages_indexed", "branded_rank_top3")] and \
           all(x.status == "ok" for x in r.findings if x.check in ("pages_indexed", "branded_rank_top3"))


def test_g02_engines_blocked_estimates_from_the_site_and_never_awards_full_marks():
    s = scraper(lambda e, q: (429, ""), inventory=SiteInventory(pages(SITE)))
    r = g02(s).run(ctx())
    f = {x.check: x for x in r.findings}
    assert f["pages_indexed"].status == "partial" and f["pages_indexed"].points == 3 and "not what is" in f["pages_indexed"].detail
    assert f["branded_rank_top3"].status == "unverified" and f["branded_rank_top3"].points == 0     # blocked is not 'unranked'
    assert r.partial


def test_g02_google_profile_without_a_link_is_unverified_not_failed():
    site = {**SITE, f"https://{DOM}/": HOME.replace("https://g.page/merrow-lane-wealth", "https://x.com/y")}
    r = g02(scraper(firm_in_results), inventory=SiteInventory(pages(site))).run(ctx())
    gbp = next(x for x in r.findings if x.check == "google_business_profile")
    assert gbp.status == "unverified" and gbp.points == 0


# ---------------------------------------------------------------------------- group 03
def test_serp_visibility_counts_searches_naming_the_firm_and_caps_the_score():
    r = SerpVisibility(scraper(firm_in_results)).run(ctx())
    f = r.findings[0]
    assert f.check == SERP_CHECK and f.status == "partial" and r.partial
    assert r.evidence["named"] == 2 and r.evidence["searches"] == 6               # the "retirees" and "retirement" prompts
    assert f.points == 10.0                                                       # 33% of searches / 50% yardstick x 60% proxy cap x 25
    assert f.max_points == 25 and "Stand-in" in f.detail


def test_serp_visibility_search_down_is_unverified():
    r = SerpVisibility(scraper(lambda e, q: (429, ""))).run(ctx())
    assert r.findings[0].status == "unverified" and r.score == 0 and r.partial and is_blank(r)


class Boom:
    group_id, max_points = "g03_ai_visibility", 25.0
    def run(self, c): raise RuntimeError("bad key")


class Good:
    group_id, max_points = "g03_ai_visibility", 25.0
    def run(self, c): return GroupResult(self.group_id, 10, 25, [Finding("firm_named_in_category_prompts", False, 10, 25, "SoM 20%")], {"answers": 12})


class AllFailed:
    group_id, max_points = "g03_ai_visibility", 25.0
    def run(self, c):
        return GroupResult(self.group_id, 0, 25, [Finding("firm_named_in_category_prompts", False, 0, 25, "every call failed", status="unverified")],
                           {"reason": "error", "detail": "HTTP 401 invalid key"}, partial=True)


def test_fallback_group_uses_the_substitute_only_when_nothing_was_measured():
    serp = SerpVisibility(scraper(firm_in_results))
    assert FallbackGroup(Good(), serp).run(ctx()).findings[0].check == "firm_named_in_category_prompts"      # real data kept
    for broken in (AllFailed(), Boom()):
        r = FallbackGroup(broken, serp).run(ctx())
        assert r.findings[0].check == SERP_CHECK and r.evidence["scrape"]["used"] and r.evidence["primary_failure"]
    assert FallbackGroup(AllFailed(), serp).group_id == "g03_ai_visibility"


def test_fallback_group_keeps_the_primary_error_when_the_substitute_fails_too():
    dead = SerpVisibility(scraper(lambda e, q: (429, "")))
    r = FallbackGroup(AllFailed(), dead).run(ctx())
    assert r.evidence["reason"] == "error" and r.evidence["scrape"]["used"] is False and "401" in r.evidence["scrape"]["why"]


# ---------------------------------------------------------------------------- group 04
NO_IAPD = None


def g04(search=None, site=SITE, client=None):
    f = pages(site)
    return CitationSurfaces(f, None, client, scraper=search, inventory=SiteInventory(f)), f


def test_g04_scrape_verifies_a_listing_the_firm_links_to():
    m, _ = g04(scraper(firm_in_results))
    ref = Nap(street="123 Main St", city="Austin", state="TX", zip="78701", phone="512-555-0100")
    napfa = next(s for s in m.surfaces if s.key == "napfa")
    out = m._web(napfa, ctx(), ref)
    assert out["status"] == "verified" and out["scrape"] and out["found_via"] == "linked from the firm's website"
    assert out["url"].endswith("/merrow-lane-wealth")             # the bare napfa.org badge was ignored


def test_g04_scrape_finds_a_listing_by_search_when_the_site_does_not_link_one():
    site = {**SITE, f"https://{DOM}/": HOME.replace("napfa.org", "example.org")}
    m, _ = g04(scraper(firm_in_results), site)
    ref = Nap(street="123 Main St", city="Austin", state="TX", zip="78701", phone="512-555-0100")
    out = m._web(next(s for s in m.surfaces if s.key == "napfa"), ctx(), ref)
    assert out["status"] == "verified" and out["found_via"] == "found by web search"


def test_g04_scrape_page_that_blocks_us_is_unverified_not_not_found():
    site = {**SITE, "https://www.napfa.org/find-an-advisor/merrow-lane-wealth": Page(False, 403, error="HTTP 403 (server refused the probe)", transient=True)}
    m, _ = g04(scraper(firm_in_results), site)
    out = m._web(next(s for s in m.surfaces if s.key == "napfa"), ctx(), Nap(city="Austin"))
    assert out["status"] == "unverified" and "could not be read" in out["detail"]


def test_g04_scrape_differing_address_is_inconsistent():
    bad = NAPFA_PAGE.replace("Austin", "Dallas").replace("78701", "75201")
    m, _ = g04(scraper(firm_in_results), {**SITE, "https://www.napfa.org/find-an-advisor/merrow-lane-wealth": bad})
    out = m._web(next(s for s in m.surfaces if s.key == "napfa"), ctx(), Nap(street="123 Main St", city="Austin", state="TX", zip="78701"))
    assert out["status"] == "inconsistent"


def test_g04_scrape_nothing_found_and_search_blocked_are_different_answers():
    site = {**SITE, f"https://{DOM}/": HOME.replace("napfa.org", "example.org")}
    ref = Nap(city="Austin")
    m, _ = g04(scraper(firm_in_results), site)
    assert m._web(next(s for s in m.surfaces if s.key == "paladin"), ctx(), ref)["status"] == "not_found"
    m2, _ = g04(scraper(lambda e, q: (429, "")), site)
    assert m2._web(next(s for s in m2.surfaces if s.key == "paladin"), ctx(), ref)["status"] == "unverified"


def test_g04_without_the_scraper_behaves_exactly_as_before():
    m = CitationSurfaces(pages(SITE), None, None)
    out = m._web(next(s for s in m.surfaces if s.key == "napfa"), ctx(), Nap())
    assert out["status"] == "unverified" and "language-model key" in out["detail"]


def test_g04_model_failure_falls_back_to_the_scraper():
    from advisorgro.engine.shield.mvp.llm import LLMError

    class Broken:
        search_cost = 0.01
        def estimate_cost(self, *a): return 0.001
        def complete(self, *a, **k): raise LLMError("HTTP 401")
    m, _ = g04(scraper(firm_in_results), client=Broken())
    ref = Nap(street="123 Main St", city="Austin", state="TX", zip="78701")
    out = m._web(next(s for s in m.surfaces if s.key == "napfa"), ctx(), ref)
    assert out["status"] == "verified" and "model discovery failed" in out["scrape_reason"]


# ---------------------------------------------------------------------------- configuration and readiness
def test_scraping_is_on_from_the_environment_and_off_when_asked():
    assert Config.from_env({}).scrape_fallback is False and Config().scrape_fallback is False      # opt-in
    assert Config.from_env(ON).scrape_fallback is True
    c = Config.from_env({"ADVISORGRO_SCRAPE_ENGINES": "bing, nonsense", "ADVISORGRO_SCRAPE_GAP_SECONDS": "3"})
    assert c.scrape_engines == ["bing"] and c.scrape_gap_s == 3.0


def test_with_no_keys_every_group_is_built_and_g03_is_the_search_stand_in():
    mods = build_modules(Config.from_env(ON), pages(SITE), SharedIapd(IapdClient()))
    assert [type(m).__name__ for m in mods] == ["TechnicalFoundation", "SearchVisibility", "SerpVisibility", "CitationSurfaces", "ComplianceExposure"]
    assert isinstance(mods[1].brave, ScrapeSearch)
    mods = build_modules(Config.from_env({**ON, "ANTHROPIC_API_KEY": "k", "BRAVE_API_KEY": "b"}), pages(SITE), SharedIapd(IapdClient()))
    assert type(mods[2]).__name__ == "FallbackGroup" and isinstance(mods[1].brave, FallbackSearch)   # keyed first, scraper last
    assert [type(m).__name__ for m in build_modules(Config(), pages(SITE), SharedIapd(IapdClient()))][2] == "NotImplementedGroup"


@pytest.mark.skip(reason="covers MVP server/audit/email layers, replaced by the platform (see tests/test_shield_service.py)")
def test_the_audit_button_is_not_disabled_when_only_scraping_is_available():
    from readiness import Readiness
    from serverconfig import ServerConfig
    from emailer import Mailer
    cfg = ServerConfig(data_dir="data-test", admin_token="t", dev_outbox=True)
    r = Readiness(cfg, Config.from_env(ON), Mailer(cfg, "data-test/outbox"), probe=lambda: (True, "ok"))
    r.refresh()
    assert r.report_ready() and not [p for p in r.detail()["problems"] if "Language" in p["name"]]
    r2 = Readiness(cfg, Config.from_env({}), Mailer(cfg, "data-test/outbox"), probe=lambda: (True, "ok"))
    assert not r2.report_ready()


def test_scraper_is_never_touched_while_the_apis_work():
    calls = []
    class Ok:
        def search(self, q, count=20):
            return [SearchResult(f"https://{DOM}/p{i}") for i in range(12)]
    s = scraper(lambda e, q: (calls.append(q), (200, ""))[1], inventory=SiteInventory(pages(SITE)))
    chain = FallbackSearch([Ok(), s])
    g02(chain).run(ctx())
    class Iapd:
        def best_match(self, *a): return IapdMatch("not_found")
    assert FallbackIapd(Iapd(), ScrapeIapd(s, None)).best_match(NAME, DOM).status == "not_found"
    assert FallbackGroup(Good(), SerpVisibility(s)).run(ctx()).findings[0].check == "firm_named_in_category_prompts"
    assert calls == []                                    # no search page was requested


# ---------------------------------------------------------------------------- a whole audit, no keys, APIs dead
@pytest.mark.skip(reason="covers MVP server/audit/email layers, replaced by the platform (see tests/test_shield_service.py)")
def test_whole_audit_with_no_keys_shows_real_results_and_says_how_they_were_made():
    from audit import run_audit
    from advisorgro.engine.shield.mvp.fixes import build_fixes
    from advisorgro.engine.shield.mvp.reportjson import build_payload
    from advisorgro.engine.shield.mvp.report import build_reports
    import tempfile

    fetcher = pages(SITE)
    cfg = Config.from_env(ON)
    mods = build_modules(cfg, fetcher, SharedIapd(IapdClient(get_json=lambda u, h, t: (_ for _ in ()).throw(IapdError("down")))))
    mods[0].renderer = None
    from advisorgro.engine.shield.mvp.g01_technical.probes import DnsResult, TlsResult
    mods[0].dns = lambda h, t: DnsResult(True, ["1.1.1.1"])
    mods[0].tls = lambda h, t: TlsResult(True, "TLSv1.3", "CA", "x", "", 90)
    mods[1].brave._get = lambda url, headers, timeout, data=None: (lambda q: firm_in_results("x", q))(
        urllib.parse.parse_qs(data.decode())["q"][0] if data else urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0])
    mods[1].brave._throttle.gap = 0.0
    for m in (mods[2].search, ):
        m._get, m._throttle = mods[1].brave._get, mods[1].brave._throttle
    mods[3].scraper._get = mods[1].brave._get
    # one shared scraper object per run, so that is the same one
    assert mods[1].brave is mods[2].search is mods[3].scraper

    record, built, _ = run_audit(f"https://{DOM}", cfg, firm=NAME, area=AREA, modules=mods, fetcher=fetcher,
                                 out_dir=tempfile.mkdtemp())
    res = record["results"]
    by = {f["check"]: f for g in res.values() for f in g["findings"]}
    # nothing is blank: each group measured something
    assert by["pages_indexed"]["points"] == 6 and by["branded_rank_top3"]["points"] == 7
    assert by["google_business_profile"]["points"] == 4
    assert by["surface:napfa"]["points"] > 0 and by["surface:napfa"]["status"] == "ok"
    assert SERP_CHECK in by and by[SERP_CHECK]["points"] > 0
    assert by["nap_consistent_with_iapd"]["status"] == "unverified"        # IAPD gives no address without its API: honest
    assert record["scorecard"]["total"] > 40 and not record["scorecard"]["complete"]          # a lower bound, flagged for review
    cats = {(i["group_id"], i["category"]) for i in record["incidents"]}
    assert ("g02_search", "scrape_fallback") in cats and ("g03_ai_visibility", "scrape_fallback") in cats
    assert not any(c == "partial_unspecified" for _, c in cats)          # a stand-in is a reason, not a mystery
    # and the page tells the visitor how the results were made
    payload = build_payload(record, built, [], firm=NAME)
    texts = " ".join(c["text"] for c in payload["coverage"])
    assert "public web pages" in texts and "ordinary web searches" in texts
    assert any(p["label"].startswith("Pages indexed") for p in payload["passed"])
    assert any(f["title"].startswith("Appears in web searches") for f in payload["fixes"])
