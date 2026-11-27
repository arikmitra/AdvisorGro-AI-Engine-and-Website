"""Composition root. Everything that touches the outside world is built here, so tests and dev swap in fakes in one place."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .connectors.ads import FakeAdPlatform, MetaConnector
from .connectors.messaging import FakeMessaging, PostmarkEmail, TwilioSms
from .core.config import Settings, get_settings
from .core.events import EventBus
from .core.ids import PUBLIC_TENANT_ID
from .db.models import Tenant
from .db.session import create_all, make_engine, make_sessionmaker, session_scope
from .engine.gro.approvals import ApprovalService
from .engine.gro.campaigns import CampaignService
from .engine.gro.measure import Measurement
from .engine.gro.pages import StorePageHost
from .engine.gro.publisher import Publisher
from .engine.shield.engine import ShieldEngine
from .engine.shield.service import ShieldService
from .leads.service import LeadService
from .llm.fake import FakeProvider
from .llm.gateway import LLMGateway
from .llm.registry import PromptRegistry
from .security.crypto import Crypto, LocalKeyProvider
from .security.tokens import TokenVerifier
from .storage.object_store import LocalObjectStore, S3ObjectStore


@dataclass
class Container:
    settings: Settings
    sm: Any
    db_engine: Any
    crypto: Crypto
    store: Any
    verifier: TokenVerifier
    prompts: PromptRegistry
    bus: EventBus
    shield_engine: ShieldEngine
    gateway: LLMGateway | None
    email: Any
    sms: Any
    connectors: dict
    shield_cache: Any = None
    messaging: Any = None
    # services are attached after construction (they take the container)
    shield: ShieldService = field(init=False)
    campaigns: CampaignService = field(init=False)
    approvals: ApprovalService = field(init=False)
    publisher: Publisher = field(init=False)
    leads: LeadService = field(init=False)
    measure: Measurement = field(init=False)
    pages: StorePageHost = field(init=False)


def build_container(settings: Settings | None = None, *, create_tables: bool | None = None) -> Container:
    s = settings or get_settings()
    engine = make_engine(s.database_url)
    sm = make_sessionmaker(engine)
    if create_tables or (create_tables is None and s.database_url.startswith("sqlite")):
        create_all(engine)
    crypto = Crypto(LocalKeyProvider(s.kek_b64, s.hmac_key_b64))
    store = LocalObjectStore(s.object_store_dir) if s.object_store_backend == "local" else S3ObjectStore(s.s3_bucket)
    shield_engine = ShieldEngine(s)
    messaging = None
    if s.use_fakes:
        os.makedirs(s.object_store_dir, exist_ok=True)
        gateway: LLMGateway | None = LLMGateway([FakeProvider()])
        messaging = FakeMessaging(log_path=os.path.join(s.object_store_dir, "sent_messages.jsonl") if s.env == "dev" else None)
        email, sms = messaging, messaging.sms_sender
        persist = s.env == "dev"  # one shared fake across the API, worker and scripts when developing locally
        connectors = {p: FakeAdPlatform(p, os.path.join(s.object_store_dir, f"fake_{p}.json") if persist else None) for p in ("google", "meta")}
    else:
        from .llm.mvp_provider import build_gateway
        gateway = build_gateway(s, shield_engine.cfg)
        gateway = gateway if gateway.providers else None
        email, sms = PostmarkEmail(s.postmark_token), TwilioSms(s.twilio_sid, s.twilio_token, s.twilio_from)
        connectors = {"meta": MetaConnector(s.meta_token, s.meta_page_id)} if s.meta_token else {}
        if s.google_ads_config_path:
            from .connectors.ads import GoogleAdsConnector

            def _client():
                from google.ads.googleads.client import GoogleAdsClient
                return GoogleAdsClient.load_from_storage(s.google_ads_config_path)
            connectors["google"] = GoogleAdsConnector(_client)
    from .engine.shield.schema_cache import Cache
    c = Container(settings=s, sm=sm, db_engine=engine, crypto=crypto, store=store, verifier=TokenVerifier(s), prompts=PromptRegistry(),
                  bus=EventBus(sm), shield_engine=shield_engine, gateway=gateway, email=email, sms=sms, connectors=connectors,
                  shield_cache=Cache(), messaging=messaging)
    c.shield, c.campaigns, c.approvals = ShieldService(c), CampaignService(c), ApprovalService(c)
    c.publisher, c.leads, c.measure, c.pages = Publisher(c), LeadService(c), Measurement(c), StorePageHost(c)
    bootstrap(c)
    from .workflows.handlers import register
    register(c)
    return c


def bootstrap(c: Container) -> None:
    """The reserved tenant for prospects who ran a free audit."""
    with session_scope(c.sm, system=True) as s:
        if not s.get(Tenant, PUBLIC_TENANT_ID):
            s.add(Tenant(id=PUBLIC_TENANT_ID, name="Public prospects", plan="audit"))
