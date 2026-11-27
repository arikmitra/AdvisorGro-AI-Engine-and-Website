import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from advisorgro.engine.shield.mvp.iapd import IapdClient, SharedIapd
from advisorgro.engine.shield.mvp.kernel import Budget, RunContext
from advisorgro.engine.shield.mvp.nap import Nap
from advisorgro.engine.shield.mvp.search import BraveSearch, Place, PlacesClient, SearchError, SearchResult
from advisorgro.engine.shield.mvp.web import Page
from advisorgro.engine.shield.mvp.g02_search.module import SearchVisibility

HTML = ('<script type="application/ld+json">{"@type":"FinancialService","name":"Merrow Lane Wealth",'
        '"telephone":"512-555-0100","address":{"streetAddress":"123 Main St","addressLocality":"Austin",'
        '"addressRegion":"TX","postalCode":"78701"}}</script><p>hi</p>')


def ctx(): return RunContext("https://merrowlanewealth.com", "Merrow Lane Wealth", "Austin, TX", Budget())


class F:
    def __init__(self, html=HTML, ok=True): self.html, self.ok = html, ok
    def get(self, url, timeout=8.0, verify=True):
        return Page(self.ok, 200, url, "text/html", self.html, len(self.html)) if self.ok else Page(False, error="timed out", transient=True)


class Brave:
    def __init__(self, site, branded): self.site, self.branded = site, branded
    def search(self, q, count=20):
        v = self.site if q.startswith("site:") else self.branded
        if isinstance(v, Exception): raise v
        return v


def r(url): return SearchResult(url, "t", "s")


def iapd(**kw):
    src = {"firm_name": "Merrow Lane Wealth, LLC", "firm_source_id": "42", "firm_ia_address_details":
           json.dumps([{"officeAddress": {"street1": "123 Main Street", "city": "Austin", "state": "TX", "postalCode": "78701"}}])}
    return SharedIapd(IapdClient(get_json=lambda u, h, t: (200, {"hits": {"hits": [{"_source": src}]}})))


class Places:
    def __init__(self, places): self.places = places
    def find(self, q): return self.places


def mod(**kw):
    d = dict(fetcher=F(), iapd=iapd(), brave=Brave([r("https://merrowlanewealth.com/a")] * 12 + [r("https://x.com")],
             [r("https://yelp.com/m"), r("https://www.merrowlanewealth.com/")]),
             places=Places([Place("Merrow Lane Wealth", "https://merrowlanewealth.com/", Nap(street="123 Main St", city="Austin", state="TX"))]))
    d.update(kw); return SearchVisibility(**d)


def by(res): return {f.check: f for f in res.findings}


def test_everything_available_and_consistent():
    res = mod().run(ctx())
    f = by(res)
    assert res.score == 20 and not res.partial
    assert f["pages_indexed"].points == 6 and "12 result" in f["pages_indexed"].detail
    assert f["branded_rank_top3"].points == 7 and "#2" in f["branded_rank_top3"].detail
    assert f["nap_consistent_with_iapd"].points == 3


def test_without_keys_only_nap_runs_and_rest_unverified():
    res = mod(brave=None, places=None).run(ctx())
    f = by(res)
    assert res.partial and res.score == 3
    for k in ("pages_indexed", "branded_rank_top3", "google_business_profile"):
        assert f[k].status == "unverified" and "needs" in f[k].detail
    assert f["nap_consistent_with_iapd"].status == "ok"


def test_partial_credit_for_few_indexed_pages_and_rank_outside_top3():
    brave = Brave([r("https://merrowlanewealth.com/a")] * 4,
                  [r("https://a.com"), r("https://b.com"), r("https://c.com"), r("https://merrowlanewealth.com")])
    f = by(mod(brave=brave).run(ctx()))
    assert f["pages_indexed"].points == 2.4
    assert f["branded_rank_top3"].points == 0 and "#4" in f["branded_rank_top3"].detail


def test_search_failure_is_unverified_not_zero_failure():
    res = mod(brave=Brave(SearchError("timed out"), SearchError("timed out"))).run(ctx())
    assert by(res)["pages_indexed"].status == "unverified" and res.partial


def test_no_gbp_match_is_a_real_zero():
    f = by(mod(places=Places([Place("Totally Other Firm", "https://other.com", Nap())])).run(ctx()))
    assert f["google_business_profile"].points == 0 and f["google_business_profile"].status == "ok"


def test_nap_mismatch_scores_proportionally():
    html = HTML.replace("123 Main St", "999 Oak Ave").replace("78701", "78702")
    f = by(mod(fetcher=F(html), places=None).run(ctx()))["nap_consistent_with_iapd"]
    assert 0 < f.points < 3 and "differs on street" in f.detail


def test_site_without_nap_is_a_finding():
    f = by(mod(fetcher=F("<p>No contact details here. " + "We plan retirements for families across the region. " * 6 + "</p>"), places=None).run(ctx()))["nap_consistent_with_iapd"]
    assert f.points == 0 and f.status == "ok" and "no address or phone" in f.detail


def test_iapd_not_found_is_unverified():
    miss = SharedIapd(IapdClient(get_json=lambda u, h, t: (200, {"hits": {"hits": []}})))
    f = by(mod(iapd=miss).run(ctx()))["nap_consistent_with_iapd"]
    assert f.status == "unverified" and "not_found" in f.detail


def test_brave_and_places_parse_fixtures():
    b = BraveSearch("k", get=lambda u, h, t: (200, {"web": {"results": [{"url": "https://www.a.com/x", "title": "T", "description": "D"}]}}))
    assert b.search("q")[0].host == "a.com"
    calls = []
    def get(u, h, t): calls.append(1); return (429, {}) if len(calls) == 1 else (200, {"web": {"results": []}})
    assert BraveSearch("k", get=get, sleep=lambda s: None).search("q") == [] and len(calls) == 2
    p = PlacesClient("k", post=lambda *a: (200, {"places": [{"id": "1", "displayName": {"text": "Merrow"},
        "formattedAddress": "123 Main St, Austin, TX 78701, USA", "nationalPhoneNumber": "(512) 555-0100",
        "websiteUri": "https://m.com"}]}))
    pl = p.find("x")[0]
    assert (pl.nap.street, pl.nap.city, pl.nap.state, pl.nap.zip) == ("123 Main St", "Austin", "TX", "78701")
