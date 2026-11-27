import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from advisorgro.engine.shield.mvp.iapd import IapdClient, IapdError, SharedIapd, record_from_hit
from advisorgro.engine.shield.mvp.nap import Nap, agreement, compare, digits, extract_site_nap, name_similarity, street_key
from advisorgro.engine.shield.mvp.web import parse_page


def hit(name, crd, street="123 Main Street, Suite 4", city="Austin", state="TX", zip_="78701", **extra):
    src = {"firm_name": name, "firm_source_id": crd, "firm_ia_scope": "ACTIVE",
           "firm_ia_address_details": json.dumps([{"officeAddress": {"street1": street, "city": city,
                                                  "state": state, "postalCode": zip_}}])}
    src.update(extra)
    return {"_source": src}


def client(*hits, status=200):
    return IapdClient(get_json=lambda url, h, t: (status, {"hits": {"hits": list(hits)}}))


def test_street_key_and_digits():
    assert street_key("123 Main Street, Suite 4") == street_key("123 main st") == "123 main st"
    assert digits("(512) 555-0100") == digits("1-512-555-0100") == "5125550100"


def test_extract_site_nap_from_jsonld_and_fallback():
    html = ('<script type="application/ld+json">{"@type":"FinancialService","name":"Merrow",'
            '"telephone":"+1 512 555 0100","address":{"streetAddress":"123 Main St","addressLocality":"Austin",'
            '"addressRegion":"TX","postalCode":"78701"}}</script><p>x</p>')
    n = extract_site_nap(parse_page(html))
    assert (n.street, n.city, n.state, n.zip) == ("123 Main St", "Austin", "TX", "78701")
    n2 = extract_site_nap(parse_page("<p>Visit us in Austin, TX 78701 or call (512) 555-0100.</p>"))
    assert (n2.city, n2.state, n2.phone) == ("Austin", "TX", "5125550100")


def test_compare_and_agreement():
    a = Nap(street="123 Main St", city="Austin", state="TX", phone="512-555-0100")
    b = Nap(street="123 Main Street Suite 4", city="austin", state="tx", zip="78701")
    c = compare(a, b)
    assert c == {"street": True, "city": True, "state": True, "zip": None, "phone": None}
    assert agreement(c) == 1.0
    assert agreement(compare(Nap(city="Dallas"), b)) == 0.0
    assert agreement(compare(Nap(), b)) is None


def test_record_from_hit_parses_address_json_string():
    r = record_from_hit(hit("Merrow Lane Wealth Partners, LLC", "123456", phone="512-555-0100")["_source"])
    assert r.crd == "123456" and r.nap.city == "Austin" and r.nap.street.startswith("123 Main")
    assert r.nap.phone == "512-555-0100" and r.url.endswith("/123456")


def test_best_match_exact_ambiguous_and_missing():
    m = client(hit("Merrow Lane Wealth Partners, LLC", "1"), hit("Other Co", "2")).best_match(
        "Merrow Lane Wealth Partners", "merrowlanewealth.com")
    assert m.status == "matched" and m.record.crd == "1" and m.confidence >= 0.95
    amb = client(hit("Merrow Lane Wealth", "1"), hit("Merrow Lane Wealth Group", "2")).best_match(
        "Merrow Lane Wealth", "x.com")
    assert amb.status in ("matched", "ambiguous")
    none = client(hit("Totally Different Advisors", "9")).best_match("Merrow Lane Wealth", "x.com")
    assert none.status == "not_found" and none.candidates
    assert client().best_match("Anything", "x.com").status == "not_found"


def test_website_in_record_forces_match():
    m = client(hit("Merrow LW Advisory", "7", website="https://www.merrowlanewealth.com")).best_match(
        "Merrow Lane Wealth", "merrowlanewealth.com")
    assert m.status == "matched" and m.record.crd == "7"


def test_errors_are_reported_not_raised_and_not_cached():
    calls = []
    def get(url, h, t):
        calls.append(url)
        raise IapdError("timed out")
    s = SharedIapd(IapdClient(get_json=get))
    m = s.match("Merrow", "x.com")
    assert m.status == "error" and m.transient
    s.match("Merrow", "x.com")
    assert len(calls) == 2                                  # transient: retried, not cached


def test_http_error_status():
    m = client(status=503).best_match("Merrow", "x.com")
    assert m.status == "error" and m.transient
    assert client(status=404).best_match("Merrow", "x.com").transient is False


def test_name_similarity_ignores_suffixes():
    assert name_similarity("Merrow Lane Wealth Partners LLC", "Merrow Lane Wealth Partners") == 1.0
