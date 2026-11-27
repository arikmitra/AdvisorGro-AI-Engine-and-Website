"""Wires configuration into the five real groups, using whichever providers are configured."""
from __future__ import annotations

from typing import Optional

from .config import Config
from .g01_technical.module import TechnicalFoundation
from .g02_search.module import SearchVisibility
from .g03_ai_visibility.engines import ClaudeEngine, OpenAICompatEngine
from .g03_ai_visibility.module import AIVisibility, Config as G03Config
from .g04_citations.module import CitationSurfaces
from .g05_compliance.module import ComplianceExposure
from .fallback import FallbackGroup
from .g03_ai_visibility.serp import SerpVisibility
from .iapd import IapdClient, SharedIapd
from .llm import ENGINE_NAME, OpenAICompatClient, make_llm_client
from .search import (BraveSearch, FallbackPlaces, FallbackSearch, GoogleCseSearch, PlacesClient, SerpApiPlaces,
                    SerpApiSearch, SerperPlaces, SerperSearch)
from .scrape import FallbackIapd, ScrapeIapd, ScrapeSearch, SiteInventory
from .unimplemented import NotImplementedGroup
from .web import SharedFetcher


def make_client(cfg: Config, provider: Optional[str] = None):
    """A client for `provider`, or for the configured primary provider. None when there is no key."""
    p = provider or cfg.primary_llm()
    return make_llm_client(p, cfg.key_for(p), cfg.model_for(p)) if p and cfg.key_for(p) else None


def build_engines(cfg: Config, client=None) -> list:
    """Group 03 answer engines: every keyed provider (or those named in ADVISORGRO_ANSWER_ENGINES),
    each with a model-knowledge engine and, unless switched off, a web-search engine."""
    engines = []
    primary = cfg.primary_llm() or "anthropic"
    providers = cfg.answer_providers()
    if client is not None and primary not in providers:
        providers = [primary] + providers                  # an injected client (tests, callers) stands in for the primary
    knowledge, searching = [], []
    for p in providers:
        c = client if (client is not None and p == primary) else make_client(cfg, p)
        if c is None:
            continue
        label = ENGINE_NAME[p]
        knowledge.append(ClaudeEngine(c, f"{label} (model knowledge)", search=False))
        if cfg.web_search_engine:
            searching.append(ClaudeEngine(c, f"{label} (web search)", search=True, est_input_tokens=4000))
    # Group 03 asks the first `max_engines` of this list. Putting every provider's cheap model-knowledge engine first
    # means that cap covers different AI assistants before it covers a second kind of question to the same one.
    engines = knowledge + searching
    for spec in cfg.extra_engines:
        if spec.get("key"):
            engines.append(OpenAICompatEngine(OpenAICompatClient(
                spec["base_url"], spec["key"], spec["model"], float(spec.get("price_in", 0)),
                float(spec.get("price_out", 0)), float(spec.get("per_request", 0))), spec["name"]))
    return engines


def build_scraper(cfg: Config, fetcher: SharedFetcher):
    """The scraping fallback for this run, or None when it is switched off (ADVISORGRO_SCRAPE_FALLBACK=0)."""
    if not cfg.scrape_fallback:
        return None
    inv = SiteInventory(fetcher, max_pages=cfg.scrape_max_pages)
    return ScrapeSearch(tuple(cfg.scrape_engines), gap_s=cfg.scrape_gap_s, inventory=inv)


def build_search(cfg: Config, scraper=None):
    """Keyed providers in order, then the scraper last: an absent key, a dead provider or a rejected key all end up there."""
    made = {"brave": lambda: BraveSearch(cfg.brave_key), "serper": lambda: SerperSearch(cfg.serper_key),
            "serpapi": lambda: SerpApiSearch(cfg.serpapi_key),
            "google_cse": lambda: GoogleCseSearch(cfg.google_cse_key, cfg.google_cse_id)}
    ps = [made[n]() for n in cfg.search_providers()] + ([scraper] if scraper else [])
    return None if not ps else ps[0] if len(ps) == 1 else FallbackSearch(ps)


def build_places(cfg: Config):
    made = {"google_places": lambda: PlacesClient(cfg.places_key), "serper": lambda: SerperPlaces(cfg.serper_key),
            "serpapi": lambda: SerpApiPlaces(cfg.serpapi_key)}
    ps = [made[n]() for n in cfg.places_providers()]
    return None if not ps else ps[0] if len(ps) == 1 else FallbackPlaces(ps)


def build_modules(cfg: Config, fetcher: SharedFetcher, iapd: SharedIapd, client=None) -> list:
    client = client or make_client(cfg)
    scraper = build_scraper(cfg, fetcher)
    inventory = scraper.inventory if scraper else None
    if scraper:                                            # IAPD: the API first, then the firm's own link and a search
        iapd = SharedIapd(FallbackIapd(iapd.client, ScrapeIapd(scraper, inventory)))
    engines = build_engines(cfg, client)
    if engines:
        g03 = AIVisibility(engines, G03Config(battery_size=cfg.battery_size, max_engines=cfg.max_engines))
        if scraper:                                        # every assistant failed or timed out: fall back to web-search visibility
            g03 = FallbackGroup(g03, SerpVisibility(scraper, cfg.battery_size))
    elif scraper:
        g03 = SerpVisibility(scraper, cfg.battery_size)
    else:
        g03 = NotImplementedGroup("g03_ai_visibility", 25.0,
                                  "a language-model key (ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY) or another answer-engine key")
    return [TechnicalFoundation(fetcher=fetcher),
            SearchVisibility(fetcher, iapd, build_search(cfg, scraper), build_places(cfg), inventory),
            g03,
            CitationSurfaces(fetcher, iapd, client, scraper=scraper, inventory=inventory),
            ComplianceExposure(fetcher, client)]