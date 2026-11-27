"""Publisher (deck 9.6, the load-bearing safety control). Nothing goes live on trust:
 1. policy: kill switch off, a valid approval over this exact (version, content hash), budget within the client ceiling;
 2. create every platform object PAUSED, idempotent by request key (a retry never makes a second campaign);
 3. READ BACK what the platform and the live page actually hold and recompute the content hash from it;
 4. hash equal -> activate -> Live;  hash different -> stay paused, incident, internal alert. Re-verified daily."""
from __future__ import annotations

import logging

from sqlalchemy import select

from ...core.clock import utcnow
from ...core.errors import Conflict, NotFound, ProviderError
from ...core.events import emit
from ...core.hashing import content_hash, normalise_html
from ...db.models import AdLink, AdObject, Alert, Campaign, Incident, RenderedSet
from ...db.session import session_scope
from ...security.activity import log_activity
from ...security.policy import require
from ...security.principal import Principal
from ..shield.alerts import week_key
from . import policy
from .renderer import norm_google, norm_meta
from .states import transition

log = logging.getLogger(__name__)


def live_hash(parts: dict, live: dict, live_page_html: str | None) -> str:
    """Recompute the content hash from what is live, with the renderer's own normalisation."""
    lp = dict(parts)
    cr = live.get("creative", {})
    if parts.get("google") is not None:
        lp["google"] = norm_google(cr.get("google") or {"headlines": [], "descriptions": []})
    if parts.get("meta") is not None:
        lp["meta"] = norm_meta(cr.get("meta") or {"variants": []})
    lp["page"] = normalise_html(live_page_html or "")
    return content_hash(lp)


class Publisher:
    def __init__(self, c):
        self.c = c

    def _account(self, s, camp: Campaign) -> str:
        link = s.execute(select(AdLink).where(AdLink.firm_id == camp.firm_id, AdLink.platform == camp.platform, AdLink.status == "linked")).scalar_one_or_none()
        if not link:
            raise Conflict("ad account is not linked for this firm and platform")
        return link.external_account_id

    def _tenant_of(self, campaign_id: str) -> str:
        with session_scope(self.c.sm, system=True) as s:
            c = s.get(Campaign, campaign_id)
            if not c:
                raise NotFound("campaign")
            return c.tenant_id

    def _objects(self, s, campaign_id: str, version: int) -> list[dict]:
        rows = s.execute(select(AdObject).where(AdObject.campaign_id == campaign_id, AdObject.version == version, AdObject.platform != "web", AdObject.status == "active")).scalars()
        return [{"object_type": r.object_type, "platform_id": r.platform_id} for r in rows]

    def _verify(self, tenant: str, camp: Campaign, version: int, rs: RenderedSet, objs: list[dict], page_url: str) -> tuple[bool, str]:
        conn = self.c.connectors[camp.platform]
        live = conn.read_back(objs)
        html = self.c.pages.fetch(tenant, camp.id, version)
        got = live_hash(rs.parts, live, html)
        ok = got == rs.content_hash and live.get("landing_url") == page_url
        return ok, got

    # ------------------------------------------------------------------ publish
    def publish(self, campaign_id: str) -> str:
        tenant = self._tenant_of(campaign_id)
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            camp = s.get(Campaign, campaign_id)
            if camp.state != "Approved":
                raise Conflict(f"campaign is {camp.state}, not Approved")
            version = camp.current_version
            rs = policy.assert_publishable(s, camp, version)
            account = self._account(s, camp)
            transition(camp, "Publishing")
            parts, chash, platform, budget = rs.parts, rs.content_hash, camp.platform, camp.monthly_budget_cents
            page_html = self.c.store.get(rs.artefact_uris["page"]).decode()
        conn = self.c.connectors[platform]
        page_url = self.c.pages.publish(tenant, campaign_id, version, page_html)
        base = f"{campaign_id}:{version}:{chash[:12]}"
        try:
            objs = conn.publish(account=account, campaign_id=campaign_id, version=version, creative={"google": parts.get("google"), "meta": parts.get("meta")},
                                landing_url=page_url, monthly_budget_cents=budget, request_key=base)
        except ProviderError as e:  # nothing was created that we know of; back to Approved so the workflow retries
            with session_scope(self.c.sm, tenant_id=tenant) as s:
                transition(s.get(Campaign, campaign_id), "Approved")
                log_activity(s, actor="publisher", action="publish.failed", tenant_id=tenant, resource=f"campaign:{campaign_id}:v{version}", result="error", reason=str(e)[:200])
            raise
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            for o in objs:
                key = f"{base}:{o['platform_id']}"
                if not s.execute(select(AdObject).where(AdObject.request_key == key)).scalar_one_or_none():
                    s.add(AdObject(campaign_id=campaign_id, version=version, platform=platform, object_type=o["object_type"], platform_id=o["platform_id"], request_key=key))
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            camp = s.get(Campaign, campaign_id)
            rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == campaign_id, RenderedSet.version == version)).scalar_one()
            ok, got = self._verify(tenant, camp, version, rs, objs, page_url)
            if ok:
                conn.activate(objs)
                transition(camp, "Live")
                camp.live_version, camp.pause_reason = version, None
                for r in s.execute(select(AdObject).where(AdObject.campaign_id == campaign_id, AdObject.version == version, AdObject.platform == platform)).scalars():
                    r.read_back_hash, r.verified_at = got, utcnow()
                log_activity(s, actor="publisher", action="publish.live", tenant_id=tenant, resource=f"campaign:{campaign_id}:v{version}", meta={"content_hash": chash, "read_back": got})
                emit(s, "AdPublished", tenant_id=tenant, firm_id=camp.firm_id, idempotency_key=f"{campaign_id}:{version}:{chash[:12]}", payload={"campaign_id": campaign_id, "version": version})
                return "Live"
            conn.pause(objs)
            self._mismatch(s, camp, tenant, version, chash, got)
            transition(camp, "Paused")
            return "Paused"

    def _mismatch(self, s, camp: Campaign, tenant: str, version: int, expected: str, got: str) -> None:
        camp.pause_reason = "read-back mismatch: live content differs from the approved version"
        s.add(Incident(kind="readback_mismatch", tenant_id=tenant, detail={"campaign_id": camp.id, "version": version, "expected": expected, "live": got}))
        try:
            with s.begin_nested():
                s.add(Alert(firm_id=camp.firm_id, kind="publish_mismatch", route="internal", week_key=week_key(), payload={"campaign_id": camp.id, "version": version}))
        except Exception:  # noqa: BLE001 - one alert per firm per week
            log.debug("optional step skipped", exc_info=True)
        log_activity(s, actor="publisher", action="readback.mismatch", tenant_id=tenant, resource=f"campaign:{camp.id}:v{version}", result="error", meta={"expected": expected, "live": got})
        emit(s, "CampaignPaused", tenant_id=tenant, firm_id=camp.firm_id, idempotency_key=f"{camp.id}:{version}:mismatch:{utcnow().strftime('%Y%m%d%H')}", payload={"campaign_id": camp.id, "reason": "mismatch"})

    # ------------------------------------------------------------------ daily verification
    def verify_live(self, campaign_id: str) -> bool:
        tenant = self._tenant_of(campaign_id)
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            camp = s.get(Campaign, campaign_id)
            if camp.state != "Live":
                return True
            version = camp.live_version
            rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == campaign_id, RenderedSet.version == version)).scalar_one()
            objs = self._objects(s, campaign_id, version)
            url = self.c.pages.url(campaign_id, version)
            ok, got = self._verify(tenant, camp, version, rs, objs, url)
            if ok and not policy.kill_switch(s, tenant, campaign_id) and policy.valid_approval(s, campaign_id, version, rs.content_hash):
                return True
            self.c.connectors[camp.platform].pause(objs)
            if not ok:
                self._mismatch(s, camp, tenant, version, rs.content_hash, got)
            else:
                camp.pause_reason = "approval expired or kill switch active"
            transition(camp, "Paused")
            return False

    # ------------------------------------------------------------------ pause / resume
    def pause_ads(self, tenant: str, campaign_id: str, reason: str, *, actor: str = "system", quiet: bool = False) -> None:
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            camp = s.get(Campaign, campaign_id)
            if not camp:
                raise NotFound("campaign")
            if camp.live_version is not None and camp.platform in self.c.connectors:
                objs = self._objects(s, campaign_id, camp.live_version)
                if objs:
                    self.c.connectors[camp.platform].pause(objs)
            if camp.state == "Live":
                transition(camp, "Paused")
            camp.pause_reason = reason
            log_activity(s, actor=actor, action="campaign.pause", tenant_id=tenant, resource=f"campaign:{campaign_id}", reason=reason)
            if not quiet:
                emit(s, "CampaignPaused", tenant_id=tenant, firm_id=camp.firm_id, idempotency_key=f"{campaign_id}:pause:{utcnow().strftime('%Y%m%d%H%M%S')}", payload={"campaign_id": campaign_id, "reason": reason})

    def resume(self, p: Principal, campaign_id: str) -> str:
        require(p, "pause", "campaign_pause", resource_tenant=p.tenant_id)
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            camp = s.get(Campaign, campaign_id)
            if camp.state != "Paused" or camp.live_version is None:
                raise Conflict("only a paused, previously published campaign can resume")
            rs = policy.assert_publishable(s, camp, camp.live_version)  # approval must still be valid
            objs = self._objects(s, campaign_id, camp.live_version)
            ok, got = self._verify(p.tenant_id, camp, camp.live_version, rs, objs, self.c.pages.url(campaign_id, camp.live_version))
            if not ok:
                self._mismatch(s, camp, p.tenant_id, camp.live_version, rs.content_hash, got)
                return "Paused"
            self.c.connectors[camp.platform].activate(objs)
            transition(camp, "Live")
            camp.pause_reason = None
            log_activity(s, actor=p.user_id, action="campaign.resume", tenant_id=p.tenant_id, resource=f"campaign:{campaign_id}")
            return "Live"
