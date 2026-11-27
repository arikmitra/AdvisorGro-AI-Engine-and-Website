"""Settings. Every value comes from the environment (prefix AG_); secrets come from a secrets manager in prod."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRETS = {"dev-cal", "dev-sms", "dev-ads", "dev-link-secret"}  # noqa: S105 - placeholders, refused in staging/prod
DEV_KEK = "ZGV2LW9ubHkta2V5LWRvLW5vdC11c2UtaW4tcHJvZC0zMmI="  # noqa: S105 - dev placeholder, rejected in prod


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AG_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    database_url: str = "sqlite+pysqlite:///./advisorgro_dev.db"
    inline_worker: bool = True  # process events in-process after responding (dev / single node); false when a worker runs
    use_fakes: bool = True  # fake providers and ad platforms (dev, test, shadow mode)

    # object storage (reports, creatives, evidence); S3 with Object Lock in prod
    object_store_backend: Literal["local", "s3"] = "local"
    object_store_dir: str = "./.objects"
    s3_bucket: str = ""
    link_secret: str = "dev-link-secret"  # signs short-lived download links  # noqa: S105

    # identity (Keycloak OIDC in prod; HS256 shared secret in dev/test only)
    jwt_mode: Literal["hs256", "jwks"] = "hs256"
    jwt_secret: str = "dev-jwt-secret-change-me-32-bytes-min!"  # noqa: S105
    jwt_issuer: str = "http://localhost:8080/realms/advisorgro"
    jwt_audience: str = "advisorgro-api"
    jwks_url: str = ""
    step_up_max_age_s: int = 300
    idle_timeout_s: int = 1800

    # crypto
    kek_b64: str = DEV_KEK
    hmac_key_b64: str = DEV_KEK

    # edge
    cors_origins: list[str] = ["http://localhost:8000", "http://localhost:5173", "http://localhost:8081"]
    turnstile_secret: str = ""
    max_body_bytes: int = 16 * 1024
    rate_limit_per_min: int = 30
    website_url: str = "https://advisorgro.com"
    pages_base_url: str = "https://pages.advisorgro.com"
    api_public_url: str = "http://localhost:8000"

    # providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    perplexity_api_key: str = ""
    brave_api_key: str = ""
    serper_api_key: str = ""
    serpapi_api_key: str = ""
    google_cse_api_key: str = ""
    google_cse_id: str = ""
    google_places_api_key: str = ""
    meta_token: str = ""
    meta_page_id: str = ""
    google_ads_config_path: str = ""
    postmark_token: str = ""
    twilio_sid: str = ""
    twilio_token: str = ""
    twilio_from: str = ""
    llm_primary_model: str = "claude-sonnet-5-5"
    llm_fallbacks: list[str] = ["openai", "gemini"]
    llm_timeout_s: float = 45.0

    metrics_token: str = ""  # empty disables /internal/metrics
    otel_endpoint: str = ""

    # webhooks
    webhook_secret_calendar: str = "dev-cal"  # noqa: S105 - dev placeholder, rejected in staging/prod
    webhook_secret_sms: str = "dev-sms"  # noqa: S105
    webhook_secret_ads: str = "dev-ads"  # noqa: S105

    # Shield defaults (MVP values from the architecture deck)
    shield_budget_seconds: int = 120
    shield_cache_ttl_s: int = 3600
    portfolio_batch_max: int = 10

    # free audit (MVP contract)
    report_page_url: str = "https://advisorgro.com/shield"
    max_audits_per_day: int = 200
    results_preview: bool = True  # show a preliminary copy while a person reviews
    always_show_passed: bool = True
    contact: str = ""  # sent to SEC IAPD in the User-Agent
    scrape_fallback: bool = True
    review_email: str = ""

    # Gro / leads
    followup_sla_s: int = 90
    approval_expiry_days: int = 30
    quiet_hours: tuple[int, int] = (21, 8)  # local-hour window; counsel confirms

    @model_validator(mode="after")
    def _prod_guards(self) -> Settings:
        if self.env in ("staging", "prod"):
            bad = [
                n for n, v in {
                    "kek_b64": self.kek_b64 == DEV_KEK,
                    "hmac_key_b64": self.hmac_key_b64 == DEV_KEK,
                    "jwt_mode": self.jwt_mode == "hs256",
                    "use_fakes": self.use_fakes,
                    "inline_worker": self.inline_worker,
                    "webhook_secrets": DEV_SECRETS & {self.webhook_secret_calendar, self.webhook_secret_sms, self.webhook_secret_ads},
                    "link_secret": self.link_secret in DEV_SECRETS,
                }.items() if v
            ]
            if bad:
                raise ValueError(f"unsafe settings for {self.env}: {', '.join(bad)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
