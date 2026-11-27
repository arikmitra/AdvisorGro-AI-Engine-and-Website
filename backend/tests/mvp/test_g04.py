import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from advisorgro.engine.shield.mvp.iapd import IapdClient, SharedIapd
from advisorgro.engine.shield.mvp.kernel import Budget, RunContext
from advisorgro.engine.shield.mvp.llm import AnthropicClient
from advisorgro.engine.shield.mvp.web import Page
from advisorgro.engine.shield.mvp.g04_citations.module import CitationSurfaces, parse_address

HTML = ('<script type="application/ld+json">{"@type":"FinancialService","name":"Merrow Lane Wealth",'
        '"telephone":"512-555-0100","address":{"streetAddress":"123 Main St","addressLocality":"Austin",'
        '"addressRegion":"TX","postalCode":"78701"}}</script>')


def ctx(usd=0.5): return RunContext("https://merrowlanewealth.com", "Merrow Lane Wealth", "Austin, TX", Budget(usd))


class F:
    def get(self, url, timeout=8.0, verify=True): return Page(True, 200, url, "text/html", HTML, len(HTML))


def iapd(city="Austin", found=True):
    src = {"firm_name": "Merrow Lane Wealth, LLC", "firm_source_id": "42", "firm_ia_address_details":
           json.dumps([{"officeAddress": {"street1": "123 Main Street", "city": city, "state": "TX", "postalCode": "78701"}}])}
    hits = [{"_source": src}] if found else []
    return SharedIapd(IapdClient(get_json=lambda u, h, t: (200, {"hits": {"hits": hits}})))


def client(by_domain):
    """by_domain: {'napfa.org': (listing_json_or_None, [urls returned by search])}"""
    seen = []
    def post(url, headers, body, timeout):
        dom = body["tools"][0]["allowed_domains"][0]
        seen.append(body)
        text, urls = by_domain.get(dom, (None, []))
        if isinstance(text, Exception): raise text
        content = [{"type": "web_search_tool_result", "content": [{"url": u} for u in urls]},
                   {"type": "text", "text": json.dumps(text) if isinstance(text, dict) else (text or '{"found": false}')}]
        return 200, {"content": content, "usage": {"input_tokens": 3000, "output_tokens": 100,
                     "server_tool_use": {"web_search_requests": 1}}}
    c = AnthropicClient("k", post=post, sleep=lambda s: None); c.seen = seen
    return c


LISTING = {"found": True, "url": "https://www.napfa.org/firm/merrow", "name": "Merrow Lane Wealth, LLC",
           "address": "123 Main St, Austin, TX 78701", "phone": "(512) 555-0100", "website": "merrowlanewealth.com"}


def by(r): return {f.check: f for f in r.findings}


def test_verified_listing_scores_its_share():
    c = client({"napfa.org": (LISTING, ["https://www.napfa.org/firm/merrow"])})
    r = CitationSurfaces(F(), iapd(), c).run(ctx())
    f = by(r)
    assert f["surface:napfa"].points == round(20 / 6, 2) and "verified" in f["surface:napfa"].detail
    assert f["surface:iapd"].points == round(20 / 6, 2)
    assert c.seen[0]["tools"][0]["allowed_domains"] == ["napfa.org"]             # agent is domain-restricted


def test_manual_surfaces_are_held_not_failed():
    r = CitationSurfaces(F(), iapd(), client({})).run(ctx())
    f = by(r)
    assert f["surface:linkedin"].status == "manual" and f["surface:media"].status == "manual"
    assert r.partial and r.evidence["manual_review"] == ["LinkedIn company page", "Media mentions"]


def test_hallucinated_url_is_rejected():
    c = client({"napfa.org": (LISTING, ["https://www.napfa.org/some/other/page"])})
    f = by(CitationSurfaces(F(), iapd(), c).run(ctx()))["surface:napfa"]
    assert f.points == 0 and "did not return" in f.detail


def test_wrong_domain_url_is_rejected():
    bad = {**LISTING, "url": "https://evil.com/merrow"}
    c = client({"napfa.org": (bad, ["https://evil.com/merrow"])})
    assert by(CitationSurfaces(F(), iapd(), c).run(ctx()))["surface:napfa"].points == 0


def test_different_firm_is_not_accepted():
    other = {**LISTING, "name": "Totally Different Advisors", "website": "other.com"}
    c = client({"napfa.org": (other, [LISTING["url"]])})
    f = by(CitationSurfaces(F(), iapd(), c).run(ctx()))["surface:napfa"]
    assert f.points == 0 and "does not match the firm name" in f.detail


def test_nap_mismatch_is_inconsistent_not_verified():
    wrong = {**LISTING, "address": "9 Elm Rd, Dallas, TX 75001", "phone": "214-555-9999"}
    c = client({"napfa.org": (wrong, [LISTING["url"]])})
    f = by(CitationSurfaces(F(), iapd(), c).run(ctx()))["surface:napfa"]
    assert f.points == 0 and "inconsistent" in f.detail and f.status == "ok"


def test_not_found_and_garbage_output():
    c = client({"napfa.org": ('{"found": false}', []), "paladinregistry.com": ("I could not find it, sorry", [])})
    f = by(CitationSurfaces(F(), iapd(), c).run(ctx()))
    assert f["surface:napfa"].status == "ok" and "not_found" in f["surface:napfa"].detail
    assert f["surface:paladin"].status == "unverified" and "unreadable" in f["surface:paladin"].detail


def test_llm_error_is_unverified():
    from advisorgro.engine.shield.mvp.llm import LLMError
    c = client({"napfa.org": (LLMError("timed out", transient=True), [])})
    f = by(CitationSurfaces(F(), iapd(), c).run(ctx()))["surface:napfa"]
    assert f.status == "unverified" and "discovery failed" in f.detail


def test_no_client_means_unverified_with_reason():
    f = by(CitationSurfaces(F(), iapd(), None).run(ctx()))["surface:napfa"]
    assert f.status == "unverified" and "ANTHROPIC_API_KEY" in f.detail


def test_budget_exhausted_skips_the_call():
    c = client({"napfa.org": (LISTING, [LISTING["url"]])})
    f = by(CitationSurfaces(F(), iapd(), c).run(ctx(usd=0.0)))["surface:napfa"]
    assert f.status == "unverified" and "budget" in f.detail and not c.seen


def test_iapd_record_missing_or_inconsistent():
    f = by(CitationSurfaces(F(), iapd(found=False), None).run(ctx()))["surface:iapd"]
    assert f.points == 0 and "not_found" in f.detail
    f = by(CitationSurfaces(F(), iapd(city="Dallas"), None).run(ctx()))["surface:iapd"]
    assert f.points == 0 and "inconsistent" in f.detail


def test_cost_is_charged_to_the_budget():
    c = client({"napfa.org": (LISTING, [LISTING["url"]])})
    x = ctx(); CitationSurfaces(F(), iapd(), c).run(x)
    assert x.budget.spent_usd > 0.01


def test_parse_address():
    n = parse_address("123 Main St, Suite 4, Austin, TX 78701")
    assert (n.city, n.state, n.zip) == ("Austin", "TX", "78701") and n.street.startswith("123 Main")
