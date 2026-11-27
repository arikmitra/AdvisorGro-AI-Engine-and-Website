"""Bridge between platform settings and the vendored MVP check groups.

`ShieldEngine.modules()` is the one place providers are chosen (MVP engine.build_modules). With `use_fakes`
it returns canned groups so dev, CI and shadow mode need no keys and touch no client data."""
from __future__ import annotations

from .mvp.config import Config
from .mvp.firm import derive_firm_name, extract_details
from .mvp.guard import check_public_site
from .mvp.intake import domain_of
from .offline import fake_modules

OFFLINE_ROWS = [
    {"area": "Domain and security", "status": "green", "label": "Green", "detail": "Certificate valid for 62 days", "live": True},
    {"area": "Email delivery", "status": "amber", "label": "Amber", "detail": "SPF found, DMARC missing", "live": True},
    {"area": "Google Business Profile", "status": "not_run", "label": "In the free audit", "detail": "Included in the free audit", "live": False},
]


class ShieldEngine:
    def __init__(self, settings, *, offline: bool | None = None):
        self.s = settings
        self.offline = settings.use_fakes if offline is None else offline
        self._cfg: Config | None = None

    def env(self) -> dict[str, str]:
        s = self.s
        return {k: v for k, v in {
            "ANTHROPIC_API_KEY": s.anthropic_api_key, "OPENAI_API_KEY": s.openai_api_key, "GEMINI_API_KEY": s.gemini_api_key,
            "BRAVE_API_KEY": s.brave_api_key, "SERPER_API_KEY": s.serper_api_key, "SERPAPI_API_KEY": s.serpapi_api_key,
            "GOOGLE_CSE_API_KEY": s.google_cse_api_key, "GOOGLE_CSE_ID": s.google_cse_id,
            "GOOGLE_PLACES_API_KEY": s.google_places_api_key, "ADVISORGRO_CONTACT": s.contact,
            "ADVISORGRO_SCRAPE_FALLBACK": "1" if s.scrape_fallback else "0",
            "ADVISORGRO_ALWAYS_SHOW_PASSED": "1" if s.always_show_passed else "0",
        }.items() if v}

    @property
    def cfg(self) -> Config:
        if self._cfg is None:
            self._cfg = Config.from_env(self.env())
        return self._cfg

    def client(self):
        if self.offline:
            return None
        from .mvp.engine import make_client
        return make_client(self.cfg)

    def modules(self, domain: str = "") -> list:
        if self.offline:
            return fake_modules(domain)
        from .mvp.engine import build_modules
        from .mvp.iapd import IapdClient, SharedIapd
        from .mvp.web import SharedFetcher
        return build_modules(self.cfg, SharedFetcher(), SharedIapd(IapdClient(contact=self.cfg.contact)), self.client())

    def resolve_firm(self, url: str, firm: str, area: str) -> tuple[str, tuple[str, ...], str]:
        dom = domain_of(url)
        name, aliases = derive_firm_name(dom)
        out_area = "the United States"
        if not self.offline:
            from .mvp.web import SharedFetcher
            f = SharedFetcher()
            page = f.get(url + "/", 8.0)
            if not page.ok and "certificate" in page.error:
                page = f.get(url + "/", 8.0, verify=False)
            if page.html:
                found = extract_details(page.html, dom)
                if "name" in found:
                    name = found["name"][0]
                    aliases = tuple(sorted({*aliases, derive_firm_name(dom)[0]} - {name}))
                if "area" in found:
                    out_area = found["area"][0]
        return (firm or name), aliases, (area or out_area)

    def extras(self, domain: str) -> dict:
        if self.offline:
            return {}
        from .mvp.shield import collect_extras
        try:
            return collect_extras(domain)
        except Exception:  # noqa: BLE001 - optional detail, never fatal
            return {}

    def site_check(self, domain: str) -> str:
        return "" if self.offline else check_public_site(domain)

    def snapshot(self, url: str) -> dict:
        import time

        from .mvp.intake import normalize_url
        if self.offline:
            dom = domain_of(normalize_url(url))
            return {"domain": dom, "checked_at": int(time.time()), "rows": OFFLINE_ROWS, "live_rows": 2}
        from .mvp.shield import snapshot
        return snapshot(url)
