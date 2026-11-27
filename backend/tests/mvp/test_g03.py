import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from advisorgro.engine.shield.mvp.kernel import Budget, RunContext
from advisorgro.engine.shield.mvp.g03_ai_visibility.engines import StubEngine
from advisorgro.engine.shield.mvp.g03_ai_visibility.module import AIVisibility, Config
from advisorgro.engine.shield.mvp.g03_ai_visibility.grader import grade
from advisorgro.engine.shield.mvp.g03_ai_visibility.engines import Answer

FIRM, DOM = "Merrow Lane Wealth Partners", "merrowlanewealth.com"


def engines(cost=0.001, rate=0.4):
    return [StubEngine(n, FIRM, DOM, hit_rate=rate, cost_per_call=cost) for n in ("a", "b", "c")]


def ctx(usd=0.10):
    return RunContext("https://www.merrowlanewealth.com", FIRM, "Austin, TX", Budget(usd_cap=usd))


def test_domain_derived():
    assert ctx().domain == DOM


def test_scoring_scale():
    m = AIVisibility(engines())
    assert m.score(50) == 25 and m.score(80) == 25 and m.score(25) == 12.5 and m.score(0) == 0


def test_full_plan_when_cheap():
    r = AIVisibility(engines(cost=0.0001)).run(ctx())
    assert not r.partial and r.evidence["answers"] == 6 * 3 * 5


def test_degrades_when_expensive():
    r2 = AIVisibility(engines(cost=0.006)).run(ctx())  # only 1 engine x 2 repeats fits
    # a smaller-but-adequate sample is degraded, not partial (see the module docstring): the score still counts as measured
    assert r2.evidence["degraded"] and not r2.partial
    assert r2.evidence["engines"] == ["a"] and r2.evidence["repeats"] == 2
    more = r2.evidence["more_with_budget"]                    # ...and it says what a bigger budget would add
    assert more["applies"] and more["not_asked"] == ["b", "c"] and more["full"]["answers"] == 6 * 3 * 5
    assert more["current"]["answers"] == 12 and more["full"]["margin_pts"] < more["current"]["margin_pts"]


def test_cut_off_returns_partial_not_error():
    r = AIVisibility(engines(cost=0.02)).run(ctx())    # only 5 calls affordable
    assert r.partial and r.evidence["answers"] <= 5
    assert r.evidence["spent_usd"] <= 0.10 + 1e-9


def test_no_budget_gives_unverified_zero():
    r = AIVisibility(engines()).run(ctx(usd=0.0))
    assert r.partial and r.score == 0 and r.findings[0].status == "unverified"


def test_cache_makes_rerun_free():
    m = AIVisibility(engines(cost=0.0001))
    m.run(ctx())
    c2 = ctx()
    m.run(c2)
    assert c2.budget.spent_usd == 0


def test_slice_charges_parent():
    c = ctx()
    m = AIVisibility(engines(cost=0.0001), Config(budget_share=0.5))
    m.run(c)
    assert 0 < c.budget.spent_usd <= 0.05 + 1e-9


def test_grader_alias_and_citation():
    a = [Answer("e", "p", 0, "Try Merrow Lane Wealth", ("https://x.com",)),
         Answer("e", "p", 1, "Try ACME", ("https://merrowlanewealth.com/a",))]
    g = grade(a, FIRM, DOM, aliases=("Merrow Lane Wealth",))
    assert (g.named, g.cited, g.total) == (1, 1, 2)


# --- real-engine behaviour ---------------------------------------------------------------
import threading
from advisorgro.engine.shield.mvp.g03_ai_visibility.collector import QueryCache, choose_plan, collect
from advisorgro.engine.shield.mvp.g03_ai_visibility.engines import ClaudeEngine
from advisorgro.engine.shield.mvp.llm import AnthropicClient, LLMError


def claude_client(responder):
    return AnthropicClient("k", post=lambda url, h, b, t: responder(b), sleep=lambda s: None)


def text_resp(text, urls=(), searches=0):
    content = [{"type": "text", "text": text}] + [
        {"type": "web_search_tool_result", "content": [{"url": u}]} for u in urls]
    return 200, {"content": content, "usage": {"input_tokens": 100, "output_tokens": 50,
                 "server_tool_use": {"web_search_requests": searches}}}


def test_claude_engine_answers_are_graded_from_real_text():
    c = claude_client(lambda b: text_resp(f"Consider {FIRM} or Acme Advisors.", ["https://merrowlanewealth.com/"], 1))
    m = AIVisibility([ClaudeEngine(c, "claude-search", search=True)], Config(battery_size=2))
    r = m.run(ctx())
    assert r.evidence["share_of_model"] == 100.0 and r.evidence["cited_in_answers"] > 0
    assert r.evidence["spent_usd"] > 0.01 * r.evidence["answers"] * 0.99      # search cost counted


def test_name_matching_uses_whole_words_not_substrings():
    from advisorgro.engine.shield.mvp.g03_ai_visibility.grader import grade
    a = [Answer("e", "p", 0, "This material is great and ria-like things", ())]
    assert grade(a, "Ria", "ria.marketing").named == 0          # short name: needs the domain
    b = [Answer("e", "p", 0, "Visit ria.marketing for help", ())]
    assert grade(b, "Ria", "ria.marketing").named == 1
    c = [Answer("e", "p", 0, "Merrow Lane Wealth Partners, LLC is a firm", ())]
    assert grade(c, "Merrow Lane Wealth", "x.com").named == 1
    d = [Answer("e", "p", 0, "Merrow Lane Wealthy Partners", ())]
    assert grade(d, "Merrow Lane Wealth", "x.com").named == 0


def test_fatal_api_error_stops_early_and_is_reported_unverified():
    calls = []
    def responder(b):
        calls.append(1); return 401, {"error": {"message": "invalid x-api-key"}}
    r = AIVisibility([ClaudeEngine(claude_client(responder), "c")], Config(battery_size=6)).run(ctx())
    assert r.partial and r.evidence["reason"] == "error" and "invalid x-api-key" in r.evidence["detail"]
    assert r.score == 0 and len(calls) <= 4                     # one wave, then stop


def test_transient_failures_are_counted_but_scored_from_what_succeeded():
    def responder(b):                       # the client retries once, so fail some prompts for good
        if "retirement" in b["messages"][0]["content"]:
            return 503, {}
        return text_resp(f"Try {FIRM}")
    r = AIVisibility([ClaudeEngine(claude_client(responder), "c")], Config(battery_size=6)).run(ctx())
    assert r.partial and r.evidence["errors"] > 0 and r.evidence["share_of_model"] == 100.0
    assert "call(s) failed" in r.findings[0].detail


def test_calls_run_concurrently():
    seen, peak, lock = [0], [0], threading.Lock()
    class Slow:
        name, cost_per_call = "s", 0.0001
        def ask(self, p, r):
            with lock: seen[0] += 1; peak[0] = max(peak[0], seen[0])
            import time; time.sleep(0.05)
            with lock: seen[0] -= 1
            return Answer("s", p, r, "x", (), 0.0001)
    from advisorgro.engine.shield.mvp.kernel import Budget as B
    collect(["a", "b", "c", "d"], choose_plan([Slow()], 4, B()), B(), QueryCache(), workers=4)
    assert peak[0] >= 3


def test_tiered_plan_prefers_two_engines_over_one():
    from advisorgro.engine.shield.mvp.kernel import Budget as B
    es = engines(cost=0.004)                                    # full 0.36, (3,2) 0.144, (2,2) 0.096
    p = choose_plan(es, 6, B())
    assert len(p.engines) == 2 and p.repeats == 2 and p.degraded


# ---- what more budget would add ---------------------------------------------------------------
def test_margin_shrinks_with_more_answers_and_is_wide_with_few():
    from advisorgro.engine.shield.mvp.g03_ai_visibility.module import margin_points
    assert margin_points(40, 12) > margin_points(40, 90) > margin_points(40, 500)
    assert 20 < margin_points(40, 12) < 28 and 8 < margin_points(40, 90) < 12
    assert margin_points(0, 20) > 0 and margin_points(50, 0) == 100.0          # never claims certainty


def test_no_note_when_the_full_plan_ran():
    r = AIVisibility(engines(cost=0.0001)).run(ctx())
    assert r.evidence["more_with_budget"] == {"applies": False}


def test_note_names_the_cost_and_the_budget_to_set():
    # inside a run the group gets 55% of the budget; the note says what the whole run would need
    run = Budget(usd_cap=0.10)
    c = RunContext("https://www.merrowlanewealth.com", FIRM, "Austin, TX", run.slice(0.55))
    r = AIVisibility(engines(cost=0.006)).run(c)
    m = r.evidence["more_with_budget"]
    assert m["full"]["est_cost_usd"] == round(0.018 * 6 * 5, 4)                  # 3 engines x 6 prompts x 5 repeats
    assert m["suggested_run_budget_usd"] == 9.82 or abs(m["suggested_run_budget_usd"] - 0.54 / 0.55) < 0.02


def test_engines_beyond_the_cap_are_reported_not_silently_dropped():
    es = [StubEngine(n, FIRM, DOM, cost_per_call=0.0001) for n in "abcde"]
    r = AIVisibility(es, Config(max_engines=3)).run(ctx())
    m = r.evidence["more_with_budget"]
    assert r.evidence["engines"] == ["a", "b", "c"] and m["applies"] and m["beyond_cap"] == ["d", "e"]
    r = AIVisibility(es, Config(max_engines=5)).run(ctx())
    assert r.evidence["engines"] == list("abcde") and r.evidence["more_with_budget"] == {"applies": False}
