"""Runtime configuration from environment variables and an optional .env file.

Providers are interchangeable:
  * language model: Anthropic (Claude), OpenAI or Google Gemini     ADVISORGRO_LLM_PROVIDER=auto|anthropic|openai|gemini
  * web search:     Brave, Serper, SerpApi or Google Programmable Search     ADVISORGRO_SEARCH_PROVIDER=auto|brave|serper|serpapi|google_cse
  * business data:  Google Places, Serper or SerpApi (Google Maps)  ADVISORGRO_PLACES_PROVIDER=auto|google_places|serper|serpapi
"auto" uses whatever has a key. Naming one provider uses only that one (and reports an error if its key is missing).
"""
from __future__ import annotations

import json
import re
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional

from .llm import DEFAULT_MODEL, DEFAULT_MODELS, KEY_ENV, PROVIDERS

SEARCH_PROVIDERS = ("brave", "serper", "serpapi", "google_cse")
PLACES_PROVIDERS = ("google_places", "serper", "serpapi")
_ALIASES = {"claude": "anthropic", "chatgpt": "openai", "google": "gemini", "bing": "", "google_places_api": "google_places",
            "cse": "google_cse", "google_search": "google_cse"}


ENGINE_DIR = Path(__file__).resolve().parent

# Names people commonly use for the same key. The canonical name always wins; these are only used when it is empty.
ALT_KEYS = {
    "ANTHROPIC_API_KEY": ("ANTHROPIC_KEY", "CLAUDE_API_KEY"),
    "OPENAI_API_KEY": ("OPENAI_KEY",),
    "GEMINI_API_KEY": ("GEMINI_KEY", "GOOGLE_GEMINI_API_KEY", "GOOGLE_AI_API_KEY"),
    "BRAVE_API_KEY": ("BRAVE_KEY", "BRAVE_SEARCH_API_KEY"),
    "SERPER_API_KEY": ("SERPER_KEY", "SERPER_DEV_API_KEY"),
    "SERPAPI_API_KEY": ("SERPAPI_KEY", "SERP_API_KEY", "SERPAPI_API", "SERPAPI"),
    "GOOGLE_PLACES_API_KEY": ("GOOGLE_PLACES_KEY", "PLACES_API_KEY"),
    "GOOGLE_CSE_API_KEY": ("GOOGLE_CSE_KEY", "GOOGLE_SEARCH_API_KEY"),
    "GOOGLE_CSE_ID": ("GOOGLE_CSE_CX", "GOOGLE_SEARCH_ENGINE_ID", "GOOGLE_CX"),
}


def env_candidates() -> list[Path]:
    """Where a .env is looked for, first match wins per variable: the folder the command was run from (VS Code's terminal
    usually opens at the project root, not engine/), then the engine folder, then the project root."""
    out: list[Path] = []
    for p in (Path.cwd() / ".env", ENGINE_DIR / ".env", ENGINE_DIR.parent / ".env"):
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if rp not in out:
            out.append(rp)
    return out


def clean_value(v: str, from_file: bool = False) -> str:
    """Trim spaces and wrapping quotes. `set KEY="abc"` in cmd keeps the quotes in the value, and Notepad adds stray spaces.
    In a .env file an unquoted value also loses a trailing ' # comment'."""
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    if from_file:
        m = re.search(r"\s#", v)
        if m:
            v = v[:m.start()]
    return v.strip().strip("\"'").strip()


def load_dotenv(path: Optional[str] = None, env: Optional[dict] = None) -> dict:
    """Environment variables (cleaned) plus every KEY=VALUE found in the .env files. A real, non-empty environment variable
    wins over the file. With `path`, only that file is read; without it, env_candidates() are."""
    base = os.environ if env is None else env
    out = {k: clean_value(str(v)) for k, v in base.items()}
    for p in ([Path(path)] if path else env_candidates()):
        try:
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8-sig")                      # -sig: Windows Notepad saves a BOM
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), clean_value(v, from_file=True)
                if v and not out.get(k):                                    # an empty variable does not block the file
                    out[k] = v
    return out


_HEX = re.compile(r"^[0-9a-fA-F]+$")


def key_problems(name: str, value: str) -> list[str]:
    """Reasons a key value cannot be right, judged by shape only (a provider's 401/400 is the real test). Used by env_check.
    Returns [] when nothing looks off."""
    v = value or ""
    out = []
    if not v.strip():
        return ["empty"]
    if re.search(r"\s", v.strip()):
        out.append("contains a space or line break: only the key itself should be pasted")
    if any(ord(c) > 126 or ord(c) < 33 for c in v.strip() if not c.isspace()):
        out.append("contains a hidden or non-ASCII character (a smart quote or a copied symbol)")
    if re.search(r"(?i)your[_ -]?(api)?[_ -]?key|^x{6,}$|change[_-]?me|replace[_-]?me|placeholder|^<.*>$|paste[_-]?(your|key)", v):
        out.append("looks like a placeholder, not a real key")
    n = len(v.strip())
    if name == "SERPAPI_API_KEY":
        if _HEX.match(v) and n == 40:
            out.append("40 hex characters is the shape of a Serper.dev key; put it in SERPER_API_KEY (SerpApi keys are 64 hex characters)")
        elif not (_HEX.match(v) and n == 64):
            out.append(f"SerpApi keys are 64 hex characters; this is {n}. Copy it from serpapi.com/manage-api-key")
    elif name == "SERPER_API_KEY":
        if _HEX.match(v) and n == 64:
            out.append("64 hex characters is the shape of a SerpApi key; put it in SERPAPI_API_KEY (Serper.dev keys are 40 hex characters)")
        elif not (_HEX.match(v) and n == 40):
            out.append(f"Serper.dev keys are 40 hex characters; this is {n}")
    elif name in ("GEMINI_API_KEY", "GOOGLE_PLACES_API_KEY", "GOOGLE_CSE_API_KEY"):
        if not v.startswith("AIza") or n != 39:
            out.append(f"Google API keys start with AIza and are 39 characters; this is {n}"
                       + (" and does not start with AIza" if not v.startswith("AIza") else "")
                       + (". Gemini keys come from aistudio.google.com/apikey" if name == "GEMINI_API_KEY" else ""))
    elif name == "ANTHROPIC_API_KEY":
        if not v.startswith("sk-ant-"):
            out.append("Anthropic keys start with sk-ant-")
    elif name == "OPENAI_API_KEY":
        if not v.startswith("sk-"):
            out.append("OpenAI keys start with sk-")
    elif name == "BRAVE_API_KEY":
        if n < 20:
            out.append("looks too short for a Brave Search key")
    return out


def apply_alt_keys(e: dict) -> dict:
    out = dict(e)
    for canon, alts in ALT_KEYS.items():
        if not (out.get(canon) or "").strip():
            for a in alts:
                if (out.get(a) or "").strip():
                    out[canon] = out[a].strip()
                    break
    return out


def _choice(env: Mapping[str, str], name: str, allowed: tuple, errors: list[str]) -> str:
    v = (env.get(name) or "auto").strip().lower()
    v = _ALIASES.get(v, v)
    if v in ("", "auto"):
        return "auto"
    if v not in allowed:
        errors.append(f"{name}={env.get(name)!r} is not one of {', '.join(allowed)}; using auto")
        return "auto"
    return v


@dataclass
class Config:
    # --- language models ---
    anthropic_key: str = ""
    openai_key: str = ""
    gemini_key: str = ""
    model: str = DEFAULT_MODEL                         # the Claude model (kept for backward compatibility)
    openai_model: str = ""                             # blank = llm.OPENAI_DEFAULT_MODEL
    gemini_model: str = ""                             # blank = llm.GEMINI_DEFAULT_MODEL
    llm_provider: str = "auto"                         # which one writes reports and runs the review / discovery steps
    answer_engines: list[str] = field(default_factory=list)   # group 03 engines; empty = every provider that has a key
    # --- search and business data ---
    brave_key: str = ""
    serper_key: str = ""
    serpapi_key: str = ""
    google_cse_key: str = ""
    google_cse_id: str = ""
    places_key: str = ""                               # Google Places
    search_provider: str = "auto"
    places_provider: str = "auto"
    # --- run limits and behaviour ---
    contact: str = ""                       # included in the User-Agent for SEC requests
    extra_engines: list[dict] = field(default_factory=list)
    usd_cap: float = 0.50                   # the design targets $0.10; see README
    seconds_cap: float = 150.0
    battery_size: int = 6
    max_engines: int = 3                    # most answer engines group 03 asks in one run (cost control)
    web_search_engine: bool = True          # also add a retrieval-backed engine per provider to group 03
    always_show_passed: bool = True         # reports always list the checks that passed, whatever else failed
    # --- scraping fallback (scrape.py): real results from public pages when the APIs are absent or failing ---
    scrape_fallback: bool = False           # opt-in: ADVISORGRO_SCRAPE_FALLBACK=1
    scrape_engines: list[str] = field(default_factory=lambda: ["duckduckgo", "bing"])
    scrape_gap_s: float = 1.5               # minimum seconds between requests to one search engine, across all audits
    scrape_max_pages: int = 60              # most pages of the firm's own site read to build its inventory
    config_errors: list[str] = field(default_factory=list)   # problems found while reading the environment

    # --- language-model helpers ---
    def key_for(self, provider: str) -> str:
        return {"anthropic": self.anthropic_key, "openai": self.openai_key, "gemini": self.gemini_key}.get(provider, "")

    def model_for(self, provider: str) -> str:
        if provider == "anthropic":
            return self.model or DEFAULT_MODELS["anthropic"]
        return (self.openai_model if provider == "openai" else self.gemini_model) or DEFAULT_MODELS[provider]

    def llm_keys(self) -> list[str]:
        return [p for p in PROVIDERS if self.key_for(p)]

    def primary_llm(self) -> str:
        """The provider for report wording, compliance review and citation discovery; "" when none is usable."""
        if self.llm_provider in PROVIDERS:
            return self.llm_provider if self.key_for(self.llm_provider) else ""
        keyed = self.llm_keys()
        return keyed[0] if keyed else ""

    @property
    def llm_ready(self) -> bool:
        return bool(self.primary_llm())

    def answer_providers(self) -> list[str]:
        """Providers that act as answer engines in group 03, the primary one first."""
        keyed = self.llm_keys()
        if self.answer_engines:
            keyed = [p for p in keyed if p in self.answer_engines]
        first = self.primary_llm()
        return sorted(keyed, key=lambda p: p != first)

    def without_llm(self) -> "Config":
        return replace(self, anthropic_key="", openai_key="", gemini_key="", extra_engines=[])

    # --- search and places helpers ---
    def search_providers(self) -> list[str]:
        have = {"brave": bool(self.brave_key), "serper": bool(self.serper_key), "serpapi": bool(self.serpapi_key),
                "google_cse": bool(self.google_cse_key and self.google_cse_id)}
        if self.search_provider in SEARCH_PROVIDERS:
            return [self.search_provider] if have[self.search_provider] else []
        return [p for p in SEARCH_PROVIDERS if have[p]]

    def places_providers(self) -> list[str]:
        have = {"google_places": bool(self.places_key), "serper": bool(self.serper_key), "serpapi": bool(self.serpapi_key)}
        if self.places_provider in PLACES_PROVIDERS:
            return [self.places_provider] if have[self.places_provider] else []
        return [p for p in PLACES_PROVIDERS if have[p]]

    @property
    def search_ready(self) -> bool:
        return bool(self.search_providers())

    @property
    def places_ready(self) -> bool:
        return bool(self.places_providers())

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        e = apply_alt_keys(dict(env) if env is not None else load_dotenv())
        extra, errors = [], []
        if e.get("ADVISORGRO_EXTRA_ENGINES"):
            try:
                extra = json.loads(e["ADVISORGRO_EXTRA_ENGINES"])
                if not isinstance(extra, list) or not all(isinstance(s, dict) for s in extra):
                    raise ValueError("expected a JSON list of objects")
                for spec in extra:                # resolve each engine's key from its named variable
                    spec["key"] = e.get(spec.get("key_env", ""), "")
            except ValueError as ex:
                extra = []
                errors.append(f"ADVISORGRO_EXTRA_ENGINES ignored: {ex}")
        llm_provider = _choice(e, "ADVISORGRO_LLM_PROVIDER", PROVIDERS, errors)
        search_provider = _choice(e, "ADVISORGRO_SEARCH_PROVIDER", SEARCH_PROVIDERS, errors)
        places_provider = _choice(e, "ADVISORGRO_PLACES_PROVIDER", PLACES_PROVIDERS, errors)
        engines = []
        for name in (e.get("ADVISORGRO_ANSWER_ENGINES") or "").split(","):
            name = _ALIASES.get(name.strip().lower(), name.strip().lower())
            if name in PROVIDERS:
                engines.append(name)
            elif name:
                errors.append(f"ADVISORGRO_ANSWER_ENGINES: unknown engine {name!r}; choose from {', '.join(PROVIDERS)}")
        cfg = cls(anthropic_key=e.get("ANTHROPIC_API_KEY", ""),
                  openai_key=e.get("OPENAI_API_KEY", ""),
                  gemini_key=e.get("GEMINI_API_KEY", "") or e.get("GOOGLE_API_KEY", ""),
                  model=e.get("ADVISORGRO_MODEL", DEFAULT_MODEL),
                  openai_model=e.get("ADVISORGRO_OPENAI_MODEL", ""),
                  gemini_model=e.get("ADVISORGRO_GEMINI_MODEL", ""),
                  llm_provider=llm_provider, answer_engines=engines,
                  brave_key=e.get("BRAVE_API_KEY", ""),
                  serper_key=e.get("SERPER_API_KEY", ""),
                  serpapi_key=e.get("SERPAPI_API_KEY", ""),
                  google_cse_key=e.get("GOOGLE_CSE_API_KEY", ""),
                  google_cse_id=e.get("GOOGLE_CSE_ID", ""),
                  places_key=e.get("GOOGLE_PLACES_API_KEY", ""),
                  search_provider=search_provider, places_provider=places_provider,
                  contact=e.get("ADVISORGRO_CONTACT", ""),
                  extra_engines=extra, config_errors=errors,
                  usd_cap=float(e.get("ADVISORGRO_BUDGET_USD", 0.50)),
                  seconds_cap=float(e.get("ADVISORGRO_SECONDS", 150)),
                  max_engines=max(1, int(e.get("ADVISORGRO_MAX_ENGINES", 3))),
                  web_search_engine=e.get("ADVISORGRO_WEB_SEARCH_ENGINE", "1") not in ("0", "false"),
                  always_show_passed=e.get("ADVISORGRO_ALWAYS_SHOW_PASSED", "1") not in ("0", "false"),
                  scrape_fallback=e.get("ADVISORGRO_SCRAPE_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on"),
                  scrape_engines=[x.strip().lower() for x in (e.get("ADVISORGRO_SCRAPE_ENGINES") or "duckduckgo,bing").split(",")
                                  if x.strip().lower() in ("duckduckgo", "bing")] or ["duckduckgo", "bing"],
                  scrape_gap_s=max(0.0, float(e.get("ADVISORGRO_SCRAPE_GAP_SECONDS", 1.5))),
                  scrape_max_pages=max(1, int(e.get("ADVISORGRO_SCRAPE_MAX_PAGES", 60))))
        # a named provider with no key is a mistake worth saying out loud, not something to paper over
        if llm_provider in PROVIDERS and not cfg.key_for(llm_provider):
            errors.append(f"ADVISORGRO_LLM_PROVIDER={llm_provider} but {KEY_ENV[llm_provider]} is not set")
        if search_provider != "auto" and not cfg.search_providers():
            errors.append(f"ADVISORGRO_SEARCH_PROVIDER={search_provider} but its key is not set "
                          f"(brave: BRAVE_API_KEY, serper: SERPER_API_KEY, serpapi: SERPAPI_API_KEY, google_cse: GOOGLE_CSE_API_KEY and GOOGLE_CSE_ID)")
        if places_provider != "auto" and not cfg.places_providers():
            errors.append(f"ADVISORGRO_PLACES_PROVIDER={places_provider} but its key is not set "
                          f"(google_places: GOOGLE_PLACES_API_KEY, serper: SERPER_API_KEY, serpapi: SERPAPI_API_KEY)")
        return cfg