import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest

from advisorgro.engine.shield.mvp.llm import AnthropicClient, LLMError, OpenAICompatClient, parse_json

OK = {"content": [
    {"type": "text", "text": "Try "},
    {"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": "https://a.com/x"}]},
    {"type": "text", "text": "Merrow Lane.", "citations": [{"type": "web_search_result_location", "url": "https://merrow.com/"},
                                                           {"url": "https://a.com/x"}]}],
    "usage": {"input_tokens": 1000, "output_tokens": 200, "server_tool_use": {"web_search_requests": 2}}}


def client(responses, **kw):
    calls = []
    it = iter(responses)
    def post(url, headers, body, timeout):
        calls.append((url, headers, body)); return next(it)
    return AnthropicClient("k", post=post, sleep=lambda s: None, **kw), calls


def test_parse_text_urls_and_cost():
    c, calls = client([(200, OK)])
    r = c.complete("hi", search=True, max_searches=2, allowed_domains=["napfa.org"])
    assert r.text == "Try Merrow Lane." and r.urls == ["https://a.com/x", "https://merrow.com/"]
    assert r.searches == 2
    assert r.cost_usd == pytest.approx((1000 * 1.0 + 200 * 5.0) / 1e6 + 2 * 0.01)
    body = calls[0][2]
    assert body["tools"][0]["type"] == "web_search_20250305" and body["tools"][0]["max_uses"] == 2
    assert body["tools"][0]["allowed_domains"] == ["napfa.org"]
    assert calls[0][1]["x-api-key"] == "k" and calls[0][1]["anthropic-version"]


def test_no_tools_without_search():
    c, calls = client([(200, OK)]); c.complete("hi", system="s")
    assert "tools" not in calls[0][2] and calls[0][2]["system"] == "s"


def test_retry_transient_then_succeed():
    c, calls = client([(529, {"error": {"message": "overloaded"}}), (200, OK)])
    assert c.complete("hi").text and len(calls) == 2


def test_transient_exhausted_and_fatal_errors():
    c, _ = client([(503, {}), (503, {})])
    with pytest.raises(LLMError) as e: c.complete("hi")
    assert e.value.transient and not e.value.fatal
    c, calls = client([(401, {"error": {"message": "invalid x-api-key"}})])
    with pytest.raises(LLMError) as e: c.complete("hi")
    assert e.value.fatal and "invalid x-api-key" in str(e.value) and len(calls) == 1


def test_estimate_cost_is_an_upper_bound_for_search():
    c, _ = client([])
    assert c.estimate_cost(100, 300, search=True) > c.estimate_cost(100, 300)


def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}
    assert parse_json('[{"id": 1}, {"id": 2}]') == [{"id": 1}, {"id": 2}]
    with pytest.raises(ValueError): parse_json("no json here")


def test_openai_compat_parses_citations_and_cost():
    resp = {"choices": [{"message": {"content": "Merrow Lane"}}], "citations": ["https://x.com"],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    c = OpenAICompatClient("https://api.x.ai/v1/", "k", "m", 1.0, 2.0, 0.005, post=lambda *a: (200, resp))
    r = c.complete("q")
    assert r.text == "Merrow Lane" and r.urls == ["https://x.com"]
    assert r.cost_usd == pytest.approx((100 * 1 + 50 * 2) / 1e6 + 0.005)
