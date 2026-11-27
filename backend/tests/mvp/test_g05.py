import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from advisorgro.engine.shield.mvp.kernel import Budget, RunContext
from advisorgro.engine.shield.mvp.web import Page
from advisorgro.engine.shield.mvp.g05_compliance.module import ComplianceExposure
from advisorgro.engine.shield.mvp.g05_compliance.scanner import review, scan

FORM = '<a href="https://adviserinfo.sec.gov/firm/1">Form CRS</a>'
PAD = " Lorem ipsum filler sentence goes here to keep passages apart. " * 4


def run(body, form=True):
    html = f"<html><body><p>{body}</p><p>{PAD}</p>{FORM if form else ''}</body></html>"   # long enough not to look JavaScript-built

    class F:
        def get(self, url, timeout=8.0, verify=True):
            return Page(True, 200, url, "text/html", html, len(html))
    return ComplianceExposure(fetcher=F()).run(RunContext("https://f.com", "F", "A", Budget()))


def ev(r, sev): return [f for f in r.evidence["flags"] if f["severity"] == sev]


def test_clean_page_scores_full():
    r = run("We build financial plans for families.")
    assert r.score == 15 and not r.evidence["material_flags"] and not r.partial


def test_performance_claim_without_disclosure_is_material():
    r = run("Our portfolios delivered 12% annual returns last year.")
    assert r.score == 10 and len(r.evidence["material_flags"]) == 1
    assert r.evidence["material_flags"][0].startswith("performance_claims:")


def test_performance_claim_with_disclosure_downgrades_to_minor():
    r = run("Our portfolios delivered 12% annual returns. Past performance is no guarantee of future results.")
    assert r.score == 14 and not r.evidence["material_flags"] and len(ev(r, "minor")) == 1


def test_no_guarantee_disclaimer_is_not_flagged_as_a_guarantee():
    assert scan("Past performance is no guarantee of future performance.") == []


def test_guarantee_language_is_always_material():
    r = run("We guarantee returns above the market. Past performance is no guarantee of future results.")
    assert any("guarantee" in f["reason"] for f in ev(r, "material"))


def test_best_interests_dismissed_but_kept_in_evidence():
    r = run("We always act in your best interests.")
    assert r.score == 15 and r.evidence["dismissed_count"] == 1


def test_superlative_is_minor():
    r = run("The leading wealth manager in town.")
    assert r.score == 14 and ev(r, "minor")[0]["category"] == "superlatives"


def test_testimonial_material_without_disclosure():
    r = run("Read our client testimonials.")
    assert ev(r, "material")[0]["category"] == "testimonials" and r.score == 10


def test_missing_form_reference_is_minor():
    r = run("We build financial plans.", form=False)
    assert r.score == 14 and ev(r, "minor")[0]["category"] == "missing_disclosure"


def test_score_floors_at_zero():
    body = PAD.join(f"We delivered {n}% annual returns." for n in (10, 11, 12, 13, 14))
    assert run(body).score == 0


def test_page_unavailable_is_unverified_partial():
    class F:
        def get(self, url, timeout=8.0, verify=True): return Page(False, error="timed out", transient=True)
    r = ComplianceExposure(fetcher=F()).run(RunContext("https://f.com", "F", "A", Budget()))
    assert r.partial and r.score == 0 and r.findings[0].status == "unverified"


def test_cert_error_falls_back_to_insecure_scan():
    seen = []
    class F:
        def get(self, url, timeout=8.0, verify=True):
            seen.append(verify)
            return Page(False, error="certificate verification failed: x") if verify else \
                   Page(True, 200, url, "text/html", "<p>Hello</p><p>" + PAD + "</p>" + FORM, 20, insecure=True)
    r = ComplianceExposure(fetcher=F()).run(RunContext("https://f.com", "F", "A", Budget()))
    assert seen == [True, False] and r.score == 15


def test_review_is_separate_from_scan():
    flags = scan("Our clients love us. Read our testimonials.")
    assert all(f.severity == "unrated" for f in flags)
    assert any(f.severity == "material" for f in review(flags, "Read our testimonials.", FORM))


# --- LLM reviewer ----------------------------------------------------------------------
import json
from advisorgro.engine.shield.mvp.llm import AnthropicClient


def llm_client(reply):
    def post(url, h, body, t):
        text = reply(body) if callable(reply) else reply
        return 200, {"content": [{"type": "text", "text": text}], "usage": {"input_tokens": 1500, "output_tokens": 300}}
    return AnthropicClient("k", post=post, sleep=lambda s: None)


def run_llm(body, reply, form=True):
    html = f"<html><body><p>{body}</p><p>{PAD}</p>{FORM if form else ''}</body></html>"
    class F:
        def get(self, url, timeout=8.0, verify=True): return Page(True, 200, url, "text/html", html, len(html))
    x = RunContext("https://f.com", "F", "A", Budget())
    r = ComplianceExposure(fetcher=F(), client=llm_client(reply)).run(x)
    return r, x


def test_llm_ratings_replace_rules_and_cost_is_charged():
    body = "The leading wealth manager in town."
    r, x = run_llm(body, json.dumps({"ratings": [{"id": 0, "severity": "dismissed", "reason": "generic"}], "additional": []}))
    assert r.evidence["reviewer"] == "llm" and r.score == 15 and x.budget.spent_usd > 0


def test_llm_additional_item_must_quote_the_page():
    body = "Our proven strategy never loses money for patient clients. Call us today."
    good = {"ratings": [], "additional": [{"category": "performance_claims", "quote": "never loses money for patient clients",
            "severity": "material", "reason": "absolute claim"}]}
    r, _ = run_llm(body, json.dumps(good))
    assert r.evidence["additional_accepted"] == 1 and len(r.evidence["material_flags"]) == 1 and r.score == 10
    fake = {"ratings": [], "additional": [{"category": "other", "quote": "text that is not on the page at all",
            "severity": "material", "reason": "invented"}]}
    r, _ = run_llm(body, json.dumps(fake))
    assert r.evidence["additional_rejected"] == 1 and not r.evidence["material_flags"] and r.score == 15


def test_llm_must_rate_every_candidate_else_rules_take_over():
    body = "We delivered 12% annual returns. Read our testimonials."
    r, _ = run_llm(body, json.dumps({"ratings": [{"id": 0, "severity": "minor", "reason": "x"}], "additional": []}))
    assert r.evidence["reviewer"] == "rules" and "did not rate" in r.evidence["llm_fallback_reason"]
    assert len(r.evidence["material_flags"]) == 2                    # rules found both as material


def test_llm_garbage_or_error_falls_back_to_rules():
    r, _ = run_llm("Read our client testimonials.", "I am sorry, I cannot do that")
    assert r.evidence["reviewer"] == "rules" and "llm_fallback_reason" in r.evidence and r.score == 10


def test_llm_cannot_downgrade_missing_form_reference_rule():
    r, _ = run_llm("We plan retirements.", json.dumps({"ratings": [], "additional": []}), form=False)
    assert r.evidence["reviewer"] == "llm" and r.score == 14
