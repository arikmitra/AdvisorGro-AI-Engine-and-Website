"""LLM clients (stdlib only) with cost tracking: Anthropic (Claude), OpenAI and Google Gemini behind one interface
(`complete(...)`, `estimate_cost(...)`, optional web search), plus an OpenAI-compatible client for extra answer engines.
Pick one with `make_llm_client(provider, key, model)`.

Transport is injectable so everything is testable without a network or a key.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
# USD per million tokens (input, output). ASSUMPTIONS: verify against current pricing.
DEFAULT_PRICES = {"claude-haiku-4-5": (1.0, 5.0)}
FALLBACK_PRICE = (3.0, 15.0)            # deliberately conservative for unknown models
SEARCH_COST_USD = 0.01                  # per web search. ASSUMPTION: verify
TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}


class LLMError(Exception):
    def __init__(self, msg: str, *, transient: bool = False, fatal: bool = False):
        super().__init__(msg)
        self.transient, self.fatal = transient, fatal     # fatal: retrying cannot help


@dataclass
class LLMResult:
    text: str
    urls: list[str] = field(default_factory=list)   # URLs the search tool returned or cited
    cost_usd: float = 0.0
    searches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


def http_post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:                  # noqa: S310
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            return e.code, {}
    except (socket.timeout, TimeoutError):
        raise LLMError("request timed out", transient=True)
    except ValueError:
        raise LLMError("response was not JSON", transient=True)
    except OSError as e:
        raise LLMError(f"network error: {e}", transient=True)


def _price(model: str, prices: dict) -> tuple[float, float]:
    for k, v in prices.items():
        if model.startswith(k):
            return v
    return FALLBACK_PRICE


def _err_message(payload: dict) -> str:
    e = payload.get("error")
    return (e.get("message") if isinstance(e, dict) else str(e)) or "unknown error"


def parse_json(text: str) -> Any:
    """Extract the first JSON object or array from model output."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(t)
    except ValueError:
        pass
    spans = sorted((t.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if t.find(o) != -1)
    for i, _o, c in spans:
        j = t.rfind(c)
        if j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    raise ValueError("no JSON found in model output")


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 post: Callable = http_post, prices: Optional[dict] = None,
                 search_cost: float = SEARCH_COST_USD, retries: int = 1,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key, self.model, self._post = api_key, model, post
        self.prices, self.search_cost = prices or DEFAULT_PRICES, search_cost
        self.retries, self._sleep = retries, sleep

    def estimate_cost(self, in_tokens: int, max_tokens: int, search: bool = False,
                      model: Optional[str] = None) -> float:
        pin, pout = _price(model or self.model, self.prices)
        return (in_tokens * pin + max_tokens * pout) / 1e6 + (self.search_cost if search else 0.0)

    def complete(self, user: str, *, system: str = "", max_tokens: int = 400,
                 search: bool = False, max_searches: int = 1,
                 allowed_domains: Optional[list[str]] = None,
                 model: Optional[str] = None, timeout: float = 60.0) -> LLMResult:
        model = model or self.model
        body: dict = {"model": model, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": user}]}
        if system:
            body["system"] = system
        if search:
            tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}
            if allowed_domains:
                tool["allowed_domains"] = allowed_domains
            body["tools"] = [tool]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        for attempt in range(self.retries + 1):
            status, payload = self._post(API_URL, headers, body, timeout)
            if status == 200:
                break
            msg = f"HTTP {status}: {_err_message(payload)}"
            if status in TRANSIENT_STATUS:
                if attempt < self.retries:
                    self._sleep(2.0 * (attempt + 1))
                    continue
                raise LLMError(msg, transient=True)
            raise LLMError(msg, fatal=True)           # 400, 401, 403, 404: retrying cannot help
        return self._parse(payload, model)

    def _parse(self, payload: dict, model: str) -> LLMResult:
        texts, urls = [], []
        for block in payload.get("content") or []:
            t = block.get("type")
            if t == "text":
                texts.append(block.get("text", ""))
                for c in block.get("citations") or []:
                    if c.get("url"):
                        urls.append(c["url"])
            elif t == "web_search_tool_result":
                content = block.get("content")
                for item in content if isinstance(content, list) else []:
                    if item.get("url"):
                        urls.append(item["url"])
        u = payload.get("usage") or {}
        n_in, n_out = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        searches = int((u.get("server_tool_use") or {}).get("web_search_requests", 0))
        pin, pout = _price(model, self.prices)
        cost = (n_in * pin + n_out * pout) / 1e6 + searches * self.search_cost
        return LLMResult("".join(texts).strip(), list(dict.fromkeys(urls)), cost,
                         searches, n_in, n_out, model)


class OpenAICompatClient:
    """For answer engines that speak the OpenAI chat-completions protocol
    (OpenAI, Perplexity and others). Model, URL and prices come from config."""

    def __init__(self, base_url: str, api_key: str, model: str, price_in: float = 0.0,
                 price_out: float = 0.0, per_request: float = 0.0, post: Callable = http_post):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.price_in, self.price_out, self.per_request, self._post = price_in, price_out, per_request, post

    def complete(self, user: str, *, system: str = "", max_tokens: int = 400,
                 timeout: float = 60.0) -> LLMResult:
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": user}]
        status, p = self._post(f"{self.base_url}/chat/completions",
                               {"Authorization": f"Bearer {self.api_key}",
                                "content-type": "application/json"},
                               {"model": self.model, "messages": msgs, "max_tokens": max_tokens}, timeout)
        if status != 200:
            raise LLMError(f"HTTP {status}: {_err_message(p)}",
                           transient=status in TRANSIENT_STATUS, fatal=status not in TRANSIENT_STATUS)
        try:
            text = p["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError("unexpected response shape", transient=True)
        cites = [c if isinstance(c, str) else c.get("url", "") for c in p.get("citations") or []]
        u = p.get("usage") or {}
        cost = (u.get("prompt_tokens", 0) * self.price_in
                + u.get("completion_tokens", 0) * self.price_out) / 1e6 + self.per_request
        return LLMResult(text.strip(), [c for c in cites if c], cost, 0,
                         u.get("prompt_tokens", 0), u.get("completion_tokens", 0), self.model)



# =====================================================================================================
# OpenAI and Gemini. Same interface as AnthropicClient, so every check group works with any of them.
# UNVERIFIED AGAINST THE LIVE SERVICES: written to the public API documentation and tested with fixtures.
# Model names, prices and the web-search tool names change often: set them in .env (see README) and check
# them against the provider's current documentation before relying on the cost figures.
# =====================================================================================================
OPENAI_URL = "https://api.openai.com/v1/responses"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
# USD per million tokens (input, output). ASSUMPTIONS: verify against current pricing.
OPENAI_PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40)}
GEMINI_PRICES = {"gemini-3.5-flash-lite": (0.10, 0.40), "gemini-3.8-flash": (0.30, 2.50)}
OPENAI_SEARCH_COST_USD = 0.01           # per web search call. ASSUMPTION: verify
GEMINI_SEARCH_COST_USD = 0.035          # per grounded request. ASSUMPTION: verify

PROVIDERS = ("anthropic", "openai", "gemini")
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
DEFAULT_MODELS = {"anthropic": DEFAULT_MODEL, "openai": OPENAI_DEFAULT_MODEL, "gemini": GEMINI_DEFAULT_MODEL}
ENGINE_NAME = {"anthropic": "claude", "openai": "chatgpt", "gemini": "gemini"}   # how group 03 labels each engine


def _request(post: Callable, sleep: Callable, retries: int, url: str, headers: dict, body: dict, timeout: float) -> dict:
    """POST with the same retry rules as the Claude client: transient errors retry, the rest fail at once."""
    for attempt in range(retries + 1):
        status, payload = post(url, headers, body, timeout)
        if status == 200:
            return payload
        msg = f"HTTP {status}: {_err_message(payload)}"
        if status in TRANSIENT_STATUS:
            if attempt < retries:
                sleep(2.0 * (attempt + 1))
                continue
            raise LLMError(msg, transient=True)
        raise LLMError(msg, fatal=True)
    raise LLMError("no response", transient=True)             # unreachable


def _host_of(u: str) -> str:
    h = (urllib.parse.urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _only_domains(urls: list[str], allowed: Optional[list[str]]) -> list[str]:
    if not allowed:
        return urls
    return [u for u in urls if any(_host_of(u) == d or _host_of(u).endswith("." + d) for d in allowed)]


def _strip_tracking(u: str) -> str:
    """OpenAI appends ?utm_source=openai to citations; the checks compare plain URLs."""
    p = urllib.parse.urlparse(u)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k != "utm_source" or v != "openai"]
    return urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode(q)))


class OpenAIClient:
    """OpenAI Responses API. Web search is the built-in `web_search` tool (name configurable)."""
    provider = "openai"

    def __init__(self, api_key: str, model: str = OPENAI_DEFAULT_MODEL, post: Callable = http_post,
                 prices: Optional[dict] = None, search_cost: float = OPENAI_SEARCH_COST_USD, retries: int = 1,
                 sleep: Callable[[float], None] = time.sleep, search_tool: str = "web_search", url: str = OPENAI_URL):
        self.api_key, self.model, self._post = api_key, model, post
        self.prices, self.search_cost, self.retries, self._sleep = prices or OPENAI_PRICES, search_cost, retries, sleep
        self.search_tool, self.url = search_tool, url

    def estimate_cost(self, in_tokens: int, max_tokens: int, search: bool = False, model: Optional[str] = None) -> float:
        pin, pout = _price(model or self.model, self.prices)
        return (in_tokens * pin + max_tokens * pout) / 1e6 + (self.search_cost if search else 0.0)

    def complete(self, user: str, *, system: str = "", max_tokens: int = 400, search: bool = False,
                 max_searches: int = 1, allowed_domains: Optional[list[str]] = None,
                 model: Optional[str] = None, timeout: float = 60.0) -> LLMResult:
        model = model or self.model
        body: dict = {"model": model, "input": user, "max_output_tokens": max(16, max_tokens)}
        if system:
            body["instructions"] = system
        if search:
            tool: dict = {"type": self.search_tool}
            if allowed_domains:
                tool["filters"] = {"allowed_domains": list(allowed_domains)}
            body["tools"], body["max_tool_calls"] = [tool], max(1, max_searches)
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        payload = _request(self._post, self._sleep, self.retries, self.url, headers, body, timeout)
        return self._parse(payload, model, allowed_domains)

    def _parse(self, payload: dict, model: str, allowed: Optional[list[str]]) -> LLMResult:
        texts, urls, searches = [], [], 0
        for item in payload.get("output") or []:
            t = item.get("type")
            if t == "web_search_call":
                searches += 1
            elif t == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        texts.append(c.get("text", ""))
                        for a in c.get("annotations") or []:
                            if a.get("type") == "url_citation" and a.get("url"):
                                urls.append(_strip_tracking(a["url"]))
        u = payload.get("usage") or {}
        n_in, n_out = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        pin, pout = _price(model, self.prices)
        cost = (n_in * pin + n_out * pout) / 1e6 + searches * self.search_cost
        return LLMResult("".join(texts).strip(), _only_domains(list(dict.fromkeys(urls)), allowed), cost,
                         searches, n_in, n_out, model)


def resolve_redirect(uri: str, timeout: float = 4.0) -> str:
    """Gemini's grounding links are redirects through vertexaisearch.cloud.google.com; follow one to the real page."""
    try:
        req = urllib.request.Request(uri, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:                    # noqa: S310
            return r.geturl()
    except Exception:                                                              # noqa: BLE001
        return ""


class GeminiClient:
    """Google Gemini generateContent. Web search is Google Search grounding."""
    provider = "gemini"

    def __init__(self, api_key: str, model: str = GEMINI_DEFAULT_MODEL, post: Callable = http_post,
                 prices: Optional[dict] = None, search_cost: float = GEMINI_SEARCH_COST_USD, retries: int = 1,
                 sleep: Callable[[float], None] = time.sleep, resolve: Optional[Callable[[str], str]] = resolve_redirect,
                 url_template: str = GEMINI_URL):
        self.api_key, self.model, self._post = api_key, model, post
        self.prices, self.search_cost, self.retries, self._sleep = prices or GEMINI_PRICES, search_cost, retries, sleep
        self.resolve, self.url_template = resolve, url_template

    def estimate_cost(self, in_tokens: int, max_tokens: int, search: bool = False, model: Optional[str] = None) -> float:
        pin, pout = _price(model or self.model, self.prices)
        return (in_tokens * pin + max_tokens * pout) / 1e6 + (self.search_cost if search else 0.0)

    def complete(self, user: str, *, system: str = "", max_tokens: int = 400, search: bool = False,
                 max_searches: int = 1, allowed_domains: Optional[list[str]] = None,
                 model: Optional[str] = None, timeout: float = 60.0) -> LLMResult:
        model = model or self.model
        if search and allowed_domains:         # grounding cannot be restricted to sites, so say it in the prompt
            user = f"{user}\n\nOnly use pages from these sites: {', '.join(allowed_domains)}."
        gen: dict = {"maxOutputTokens": max_tokens}
        if model.startswith("gemini-2.5-flash"):
            gen["thinkingConfig"] = {"thinkingBudget": 0}     # otherwise hidden reasoning can use the whole token allowance
        body: dict = {"contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if search:
            body["tools"] = [{"google_search": {}}]
        headers = {"x-goog-api-key": self.api_key, "content-type": "application/json"}
        payload = _request(self._post, self._sleep, self.retries, self.url_template.format(model=model),
                           headers, body, timeout)
        return self._parse(payload, model, allowed_domains)

    def _real_url(self, chunk: dict) -> str:
        web = chunk.get("web") or {}
        uri, title = web.get("uri", ""), (web.get("title") or "").strip()
        if uri and "vertexaisearch.cloud.google.com" not in uri:
            return uri
        real = self.resolve(uri) if (self.resolve and uri) else ""
        if real and "vertexaisearch.cloud.google.com" not in real:
            return real
        return f"https://{title}" if "." in title and " " not in title else uri       # the title is the site's domain

    def _parse(self, payload: dict, model: str, allowed: Optional[list[str]]) -> LLMResult:
        cands = payload.get("candidates") or []
        if not cands:
            why = (payload.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({why})")
        cand = cands[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [] if not p.get("thought"))
        meta = cand.get("groundingMetadata") or {}
        chunks = (meta.get("groundingChunks") or [])[:10]
        urls = [u for u in (self._real_url(c) for c in chunks) if u]
        queries = meta.get("webSearchQueries") or []
        searches = len(queries) or (1 if chunks else 0)
        u = payload.get("usageMetadata") or {}
        n_in = int(u.get("promptTokenCount", 0))
        n_out = int(u.get("candidatesTokenCount", 0)) + int(u.get("thoughtsTokenCount", 0))
        pin, pout = _price(model, self.prices)
        cost = (n_in * pin + n_out * pout) / 1e6 + searches * self.search_cost
        return LLMResult(text.strip(), _only_domains(list(dict.fromkeys(urls)), allowed), cost, searches, n_in, n_out, model)


def make_llm_client(provider: str, key: str, model: str = "", post: Callable = http_post):
    """The one place a provider name becomes a client."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown LLM provider {provider!r}; choose one of {', '.join(PROVIDERS)}")
    model = model or DEFAULT_MODELS[provider]
    if provider == "anthropic":
        return AnthropicClient(key, model, post=post)
    if provider == "openai":
        return OpenAIClient(key, model, post=post)
    if provider == "gemini":
        return GeminiClient(key, model, post=post)
    raise ValueError(f"unknown LLM provider {provider!r}; choose one of {', '.join(PROVIDERS)}")