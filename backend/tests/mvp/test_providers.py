from advisorgro.engine.shield.mvp.llm import GEMINI_DEFAULT_MODEL
"""OpenAI / Gemini clients, provider selection, and the alternative search and places providers. No network."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest

from advisorgro.engine.shield.mvp.config import Config
from advisorgro.engine.shield.mvp.engine import build_engines, build_modules, build_places, build_search, make_client
from advisorgro.engine.shield.mvp.iapd import IapdClient, SharedIapd
from advisorgro.engine.shield.mvp.llm import AnthropicClient, GeminiClient, LLMError, OpenAIClient, make_llm_client
from advisorgro.engine.shield.mvp.search import (BraveSearch, FallbackPlaces, FallbackSearch, GoogleCseSearch, PlacesClient, SearchError,
                    SerpApiPlaces, SerpApiSearch, SerperPlaces, SerperSearch)
from .conftest import fetcher

OPENAI_OK = {"output": [
    {"type": "web_search_call", "status": "completed"},
    {"type": "message", "content": [{"type": "output_text", "text": "Try Merrow Lane.", "annotations": [
        {"type": "url_citation", "url": "https://napfa.org/x?utm_source=openai"},
        {"type": "url_citation", "url": "https://other.com/y?utm_source=openai"}]}]}],
    "usage": {"input_tokens": 1000, "output_tokens": 200}}

GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "Merrow "}, {"text": "Lane."}]},
    "groundingMetadata": {"webSearchQueries": ["merrow lane"], "groundingChunks": [
        {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA", "title": "napfa.org"}},
        {"web": {"uri": "https://example.org/page", "title": "Example"}}]}}],
    "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 150, "thoughtsTokenCount": 50}}


def mk(cls, responses, **kw):
    calls, it = [], iter(responses)
    def post(url, headers, body, timeout):
        calls.append((url, headers, body)); return next(it)
    return cls("k", post=post, sleep=lambda s: None, **kw), calls


# ---- OpenAI ---------------------------------------------------------------------------------
def test_openai_request_and_parse():
    c, calls = mk(OpenAIClient, [(200, OPENAI_OK)])
    r = c.complete("hi", system="sys", search=True, max_searches=2, allowed_domains=["napfa.org"], max_tokens=300)
    url, headers, body = calls[0]
    assert url.endswith("/v1/responses") and headers["Authorization"] == "Bearer k"
    assert body["input"] == "hi" and body["instructions"] == "sys" and body["max_output_tokens"] == 300
    assert body["tools"] == [{"type": "web_search", "filters": {"allowed_domains": ["napfa.org"]}}] and body["max_tool_calls"] == 2
    assert r.text == "Try Merrow Lane."
    assert r.urls == ["https://napfa.org/x"]                      # tracking removed, off-list site dropped
    assert r.searches == 1 and r.cost_usd == pytest.approx((1000 * 0.15 + 200 * 0.60) / 1e6 + 0.01)


def test_openai_no_tools_without_search_and_error_handling():
    c, calls = mk(OpenAIClient, [(200, OPENAI_OK)])
    c.complete("hi")
    assert "tools" not in calls[0][2]
    c, _ = mk(OpenAIClient, [(429, {}), (200, OPENAI_OK)])
    assert c.complete("hi").text                                  # transient: retried once
    c, _ = mk(OpenAIClient, [(401, {"error": {"message": "bad key"}})])
    with pytest.raises(LLMError) as e:
        c.complete("hi")
    assert e.value.fatal and "bad key" in str(e.value)


# ---- Gemini ---------------------------------------------------------------------------------
def test_gemini_request_and_parse():
    c, calls = mk(GeminiClient, [(200, GEMINI_OK)], resolve=lambda u: "")
    r = c.complete("hi", system="sys", search=True, allowed_domains=["napfa.org"])
    url, headers, body = calls[0]
    assert f"{GEMINI_DEFAULT_MODEL}:generateContent" in url and headers["x-goog-api-key"] == "k"
    assert body["tools"] == [{"google_search": {}}] and body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert "napfa.org" in body["contents"][0]["parts"][0]["text"]            # grounding cannot filter sites, so the prompt does
    assert ("thinkingConfig" in body["generationConfig"]) == GEMINI_DEFAULT_MODEL.startswith("gemini-2.5-flash")
    assert r.text == "Merrow Lane."
    assert r.urls == ["https://napfa.org"]                                   # redirect not resolved: the title is the domain
    assert r.cost_usd == pytest.approx((1000 * 0.30 + 200 * 2.50) / 1e6 + 0.035)


def test_gemini_follows_redirect_when_it_can_and_rejects_empty_answers():
    c, _ = mk(GeminiClient, [(200, GEMINI_OK)], resolve=lambda u: "https://www.napfa.org/find/merrow")
    assert c.complete("hi", search=True).urls == ["https://www.napfa.org/find/merrow", "https://example.org/page"]
    c, _ = mk(GeminiClient, [(200, {"promptFeedback": {"blockReason": "SAFETY"}})])
    with pytest.raises(LLMError, match="SAFETY"):
        c.complete("hi")


def test_make_llm_client():
    assert isinstance(make_llm_client("anthropic", "k"), AnthropicClient)
    assert isinstance(make_llm_client("openai", "k"), OpenAIClient)
    g = make_llm_client("gemini", "k", "gemini-2.5-pro")
    assert isinstance(g, GeminiClient) and g.model == "gemini-2.5-pro"
    with pytest.raises(ValueError):
        make_llm_client("bard", "k")


# ---- selection ------------------------------------------------------------------------------
def test_auto_picks_first_key_and_named_provider_wins():
    assert Config.from_env({"OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g"}).primary_llm() == "openai"
    c = Config.from_env({"OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g", "ADVISORGRO_LLM_PROVIDER": "gemini"})
    assert c.primary_llm() == "gemini" and c.answer_providers() == ["gemini", "openai"]
    assert Config.from_env({"GOOGLE_API_KEY": "g"}).gemini_key == "g"                 # the other common variable name
    assert Config.from_env({"ADVISORGRO_LLM_PROVIDER": "chatgpt", "OPENAI_API_KEY": "o"}).llm_provider == "openai"


def test_named_provider_without_key_is_an_error_not_a_silent_switch():
    c = Config.from_env({"ADVISORGRO_LLM_PROVIDER": "openai", "ANTHROPIC_API_KEY": "a"})
    assert c.primary_llm() == "" and not c.llm_ready and "OPENAI_API_KEY" in " ".join(c.config_errors)
    c = Config.from_env({"ADVISORGRO_LLM_PROVIDER": "nonsense"})
    assert c.llm_provider == "auto" and c.config_errors


def test_models_per_provider():
    c = Config.from_env({"ADVISORGRO_OPENAI_MODEL": "gpt-x"})
    assert c.model_for("openai") == "gpt-x" and c.model_for("gemini") == GEMINI_DEFAULT_MODEL and "claude" in c.model_for("anthropic")


def test_group_03_engines_follow_the_keys():
    c = Config.from_env({"OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g"})
    # every provider's model-knowledge engine first, so group 03's engine cap spans different AI assistants
    assert [e.name for e in build_engines(c)] == ["chatgpt (model knowledge)", "gemini (model knowledge)",
                                                   "chatgpt (web search)", "gemini (web search)"]
    c = Config.from_env({"OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g", "ADVISORGRO_ANSWER_ENGINES": "gemini",
                         "ADVISORGRO_WEB_SEARCH_ENGINE": "0"})
    assert [e.name for e in build_engines(c)] == ["gemini (model knowledge)"]
    assert build_engines(Config()) == []
    assert type(make_client(Config.from_env({"GEMINI_API_KEY": "g"}))).__name__ == "GeminiClient"


def test_any_single_provider_builds_all_five_groups():
    off = {"ADVISORGRO_SCRAPE_FALLBACK": "0"}                # the scraping fallback has its own tests (test_scrape.py)
    for env in ({"ANTHROPIC_API_KEY": "a"}, {"OPENAI_API_KEY": "o"}, {"GEMINI_API_KEY": "g"}):
        mods = build_modules(Config.from_env({**env, **off}), fetcher(), SharedIapd(IapdClient()))
        assert [type(m).__name__ for m in mods][2] == "AIVisibility"
    assert type(build_modules(Config(), fetcher(), SharedIapd(IapdClient()))[2]).__name__ == "NotImplementedGroup"


# ---- search and places ----------------------------------------------------------------------
def poster(status, payload, calls=None):
    def post(url, headers, body, timeout):
        if calls is not None: calls.append((url, headers, body))
        return status, payload
    return post


def test_serper_search():
    calls = []
    s = SerperSearch("key", post=poster(200, {"organic": [{"link": "https://a.com", "title": "A", "snippet": "s"}, {"title": "no link"}]}, calls))
    out = s.search("site:a.com", 30)
    assert [r.url for r in out] == ["https://a.com"] and out[0].host == "a.com"
    assert calls[0][1]["X-API-KEY"] == "key" and calls[0][2] == {"q": "site:a.com", "num": 20}
    with pytest.raises(SearchError) as e:
        SerperSearch("k", post=poster(429, {})).search("q")
    assert e.value.transient


def test_google_cse_pages_through_results():
    pages = [{"items": [{"link": f"https://s{i}.com"} for i in range(10)]}, {"items": [{"link": "https://last.com"}]}]
    urls = []
    def get(url, headers, timeout):
        urls.append(url); return 200, pages[len(urls) - 1]
    out = GoogleCseSearch("k", "cx1", get=get).search("q", 20)
    assert len(out) == 11 and "start=11" in urls[1] and "cx=cx1" in urls[0]
    with pytest.raises(SearchError):
        GoogleCseSearch("k", "cx", get=lambda u, h, t: (403, {})).search("q")


def test_serper_places():
    p = SerperPlaces("k", post=poster(200, {"places": [{"title": "Merrow Lane Wealth", "address": "123 Main St, Austin, TX 78701",
                                                        "phoneNumber": "(512) 555-0100", "website": "https://merrowlane.com", "cid": 77}]}))
    (pl,) = p.find("Merrow Lane Austin")
    assert pl.name == "Merrow Lane Wealth" and pl.website == "https://merrowlane.com" and pl.place_id == "77"
    assert pl.nap.city == "Austin" and pl.nap.state == "TX" and pl.nap.phone == "(512) 555-0100"


def test_fallback_tries_the_next_provider():
    class Bad:
        name = "bad"
        def search(self, q, count=20): raise SearchError("down")
        def find(self, q): raise SearchError("down")
    class Good:
        name = "good"
        def search(self, q, count=20): return ["ok"]
        def find(self, q): return ["ok"]
    assert FallbackSearch([Bad(), Good()]).search("q") == ["ok"] and FallbackPlaces([Bad(), Good()]).find("q") == ["ok"]
    with pytest.raises(SearchError):
        FallbackSearch([Bad()]).search("q")


def test_search_and_places_selection():
    c = Config.from_env({"BRAVE_API_KEY": "b", "SERPER_API_KEY": "s", "GOOGLE_PLACES_API_KEY": "p"})
    assert isinstance(build_search(c), FallbackSearch) and build_search(c).name == "brave+serper"
    assert build_places(c).name == "google_places+serper"
    c = Config.from_env({"BRAVE_API_KEY": "b", "SERPER_API_KEY": "s", "ADVISORGRO_SEARCH_PROVIDER": "serper",
                         "ADVISORGRO_PLACES_PROVIDER": "serper"})
    assert isinstance(build_search(c), SerperSearch) and isinstance(build_places(c), SerperPlaces)
    assert isinstance(build_search(Config.from_env({"BRAVE_API_KEY": "b"})), BraveSearch)
    assert isinstance(build_places(Config.from_env({"GOOGLE_PLACES_API_KEY": "p"})), PlacesClient)
    c = Config.from_env({"GOOGLE_CSE_API_KEY": "k"})                       # needs both the key and the engine id
    assert build_search(c) is None
    assert isinstance(build_search(Config.from_env({"GOOGLE_CSE_API_KEY": "k", "GOOGLE_CSE_ID": "x"})), GoogleCseSearch)
    c = Config.from_env({"ADVISORGRO_SEARCH_PROVIDER": "brave"})
    assert build_search(c) is None and "BRAVE_API_KEY" in " ".join(c.config_errors)


def test_new_clients_work_as_group_03_answer_engines():
    from advisorgro.engine.shield.mvp.g03_ai_visibility.engines import ClaudeEngine
    c, _ = mk(OpenAIClient, [(200, OPENAI_OK)])
    a = ClaudeEngine(c, "chatgpt (web search)", search=True, est_input_tokens=4000).ask("best advisor in Austin?", 0)
    assert a.engine == "chatgpt (web search)" and "Merrow" in a.text and a.cited_urls and a.cost_usd > 0
    c, _ = mk(GeminiClient, [(200, GEMINI_OK)], resolve=lambda u: "")
    e = ClaudeEngine(c, "gemini (web search)", search=True)
    assert e.ask("best advisor in Austin?", 0).cited_urls and e.cost_per_call > 0

# ---------------------------------------------------------------------------- SerpApi
def getter(status, payload, calls=None):
    def get(url, headers, timeout):
        if calls is not None:
            calls.append(url)
        return status, payload
    return get


def test_serpapi_search_parses_organic_results_and_sends_the_key():
    import urllib.parse as up
    calls = []
    s = SerpApiSearch("KEY", get=getter(200, {"organic_results": [
        {"link": "https://a.com/x", "title": "A", "snippet": "s"}, {"title": "no link"}]}, calls=calls))
    out = s.search("site:a.com", 30)
    assert [r.url for r in out] == ["https://a.com/x"] and out[0].host == "a.com" and out[0].snippet == "s"
    q = up.parse_qs(up.urlparse(calls[0]).query)
    assert q["engine"] == ["google"] and q["api_key"] == ["KEY"] and q["q"] == ["site:a.com"] and q["num"] == ["20"]


def test_serpapi_errors_are_errors_but_no_results_is_an_answer():
    with pytest.raises(SearchError) as e:
        SerpApiSearch("k", get=getter(429, {})).search("q")
    assert e.value.transient
    with pytest.raises(SearchError) as e:
        SerpApiSearch("bad", get=getter(401, {})).search("q")
    assert not e.value.transient
    with pytest.raises(SearchError) as e:                                   # SerpApi can say this with HTTP 200
        SerpApiSearch("k", get=getter(200, {"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"})).search("q")
    assert "Invalid API key" in str(e.value) and not e.value.transient
    assert SerpApiSearch("k", get=getter(200, {"error": "Google hasn't returned any results for this query."})).search("q") == []


def test_serpapi_places_handles_a_list_and_a_single_exact_match():
    many = {"local_results": [{"title": "Merrow Lane Wealth", "address": "123 Main St, Austin, TX 78701, USA",
                               "phone": "(512) 555-0100", "website": "https://merrowlanewealth.com/", "place_id": "ChIJ1"}]}
    out = SerpApiPlaces("k", get=getter(200, many)).find("Merrow Lane Wealth Austin")
    assert out[0].name == "Merrow Lane Wealth" and out[0].website == "https://merrowlanewealth.com/" and out[0].place_id == "ChIJ1"
    assert out[0].nap.phone == "(512) 555-0100" and out[0].nap.state == "TX" and out[0].nap.zip == "78701"
    single = {"place_results": {"title": "Acme", "address": "1 A St, Dallas, TX 75201", "data_id": "0xabc"}}
    assert SerpApiPlaces("k", get=getter(200, single)).find("Acme")[0].name == "Acme"
    assert SerpApiPlaces("k", get=getter(200, {})).find("nothing") == []
    with pytest.raises(SearchError):
        SerpApiPlaces("k", get=getter(500, {})).find("x")


def test_serpapi_is_selectable_and_chained_with_the_other_providers():
    c = Config.from_env({"SERPAPI_API_KEY": "s"})
    assert c.search_ready and c.places_ready and c.search_providers() == ["serpapi"] and c.places_providers() == ["serpapi"]
    assert isinstance(build_search(c), SerpApiSearch) and isinstance(build_places(c), SerpApiPlaces)
    c = Config.from_env({"BRAVE_API_KEY": "b", "SERPER_API_KEY": "r", "SERPAPI_API_KEY": "s", "GOOGLE_PLACES_API_KEY": "p"})
    assert build_search(c).name == "brave+serper+serpapi" and build_places(c).name == "google_places+serper+serpapi"
    c = Config.from_env({"SERPER_API_KEY": "r", "SERPAPI_API_KEY": "s", "ADVISORGRO_SEARCH_PROVIDER": "serpapi",
                         "ADVISORGRO_PLACES_PROVIDER": "serpapi"})
    assert isinstance(build_search(c), SerpApiSearch) and isinstance(build_places(c), SerpApiPlaces)
    c = Config.from_env({"ADVISORGRO_SEARCH_PROVIDER": "serpapi"})                       # named but no key: said out loud
    assert any("SERPAPI_API_KEY" in m for m in c.config_errors)


def test_a_failing_serpapi_falls_through_to_the_next_provider():
    class Down:
        name = "down"
        def search(self, q, count=20): raise SearchError("HTTP 500")
    class Up:
        name = "up"
        def search(self, q, count=20): return ["ok"]
    chain = FallbackSearch([SerpApiSearch("k", get=getter(429, {})), Up()])
    assert chain.search("q") == ["ok"]