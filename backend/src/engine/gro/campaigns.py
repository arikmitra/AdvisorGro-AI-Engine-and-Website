"""Campaign lifecycle: create, generate (stages 1-5), edit (new version restarts at pre-check), pause, retire."""
from __future__ import annotations

import asyncio
import json
import logging as _l

from sqlalchemy import select

from ...core.errors import Conflict, NotFound, ValidationFailed
from ...core.events import emit
from ...db.models import Campaign, CampaignVersion, Firm, PreCheckRecord, RenderedSet, ReviewItem
from ...db.session import session_scope
from ...llm.gateway import Meter
from ...security.activity import log_activity
from ...security.policy import require
from ...security.principal import Principal
from . import factguard, knowledge, offers, precheck, renderer
from .schemas import AngleOut, CampaignBrief, CreativeSet, FollowUpSet, GoogleAdSet, MetaAdSet, PageCopy
from .states import transition

log = _l.getLogger(__name__)


class CampaignService:
    def __init__(self, c):
        self.c = c

    # ------------------------------------------------------------------ create / read
    def create(self, p: Principal, *, firm_id: str, offer_id: str, platform: str, monthly_budget_cents: int, budget_ceiling_cents: int) -> dict:
        require(p, "edit", "campaign_drafts", resource_tenant=p.tenant_id)
        offers.get(offer_id)
        if platform not in ("google", "meta"):
            raise ValidationFailed("platform must be google or meta")
        if not 0 < monthly_budget_cents <= budget_ceiling_cents:
            raise ValidationFailed("budget must be positive and within the client's ceiling")
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            if not s.get(Firm, firm_id):
                raise NotFound("firm")
            camp = Campaign(firm_id=firm_id, offer_id=offer_id, platform=platform, monthly_budget_cents=monthly_budget_cents,
                            budget_ceiling_cents=budget_ceiling_cents, created_by=p.user_id)
            s.add(camp)
            s.flush()
            log_activity(s, actor=p.user_id, action="campaign.create", tenant_id=p.tenant_id, resource=f"campaign:{camp.id}")
            return self._dto(camp)

    @staticmethod
    def _dto(c: Campaign) -> dict:
        return {"id": c.id, "firm_id": c.firm_id, "offer_id": c.offer_id, "platform": c.platform, "state": c.state, "current_version": c.current_version,
                "live_version": c.live_version, "monthly_budget_cents": c.monthly_budget_cents, "budget_ceiling_cents": c.budget_ceiling_cents, "pause_reason": c.pause_reason}

    def get(self, p: Principal, campaign_id: str) -> dict:
        require(p, "view", "campaign_drafts", resource_tenant=p.tenant_id)
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            c = s.get(Campaign, campaign_id)
            if not c:
                raise NotFound("campaign")
            out = self._dto(c)
            v = s.execute(select(CampaignVersion).where(CampaignVersion.campaign_id == c.id, CampaignVersion.version == c.current_version)).scalar_one_or_none()
            if v:
                rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == c.id, RenderedSet.version == v.version)).scalar_one_or_none()
                pc = s.execute(select(PreCheckRecord).where(PreCheckRecord.campaign_id == c.id, PreCheckRecord.version == v.version)).scalar_one_or_none()
                out["version"] = {"version": v.version, "creative_set": v.creative_set, "change_note": v.change_note, "content_hash": rs.content_hash if rs else None,
                                  "precheck": pc.report if pc else None, "rendered": rs.parts if rs else None,
                                  "page_html": self.c.store.get(rs.artefact_uris["page"]).decode() if rs and rs.artefact_uris.get("page") else None}
            return out

    def list(self, p: Principal) -> list[dict]:
        require(p, "view", "campaign_drafts", resource_tenant=p.tenant_id)
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            return [self._dto(c) for c in s.execute(select(Campaign).order_by(Campaign.created_at.desc())).scalars()]

    # ------------------------------------------------------------------ stages 1-5
    def submit_for_review(self, p: Principal, campaign_id: str, *, creative_patch: dict | None = None, note: str = "") -> dict:
        """Generate (or take an edit), fact-guard, pre-check, render, then hand to the CCO. Always a new version."""
        require(p, "edit", "campaign_drafts", resource_tenant=p.tenant_id)
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            camp = s.get(Campaign, campaign_id)
            if not camp:
                raise NotFound("campaign")
            if camp.state in ("Live", "Paused", "Approved", "PendingCCO", "Rejected", "Retired", "Publishing"):
                if camp.state in ("PendingCCO", "Rejected", "Retired", "Publishing"):
                    raise Conflict(f"cannot edit a campaign that is {camp.state}")
                was_live = camp.state == "Live"
                transition(camp, "ChangesRequested")  # unapproved content never runs: live ads pause while the new version awaits approval
                camp.pause_reason = "new version awaiting approval" if was_live else camp.pause_reason
            else:
                was_live = False
            transition(camp, "Generating")
            firm = s.get(Firm, camp.firm_id)
            firm_d = {"name": firm.name, "domain": firm.domain, "area": firm.area, "links": (firm.profile or {}).get("links", [])}
            fid, offer_id, budget, ceiling = camp.firm_id, camp.offer_id, camp.monthly_budget_cents, camp.budget_ceiling_cents
            platform, version = camp.platform, camp.current_version + 1
            prior = s.execute(select(CampaignVersion).where(CampaignVersion.campaign_id == campaign_id, CampaignVersion.version == camp.current_version)).scalar_one_or_none()
            prior_creative, prior_brief = (prior.creative_set, prior.brief) if prior else (None, None)
            facts = [f.text for f in knowledge.facts(s, fid, ("fact",))]
            lang = [f.text for f in knowledge.facts(s, fid, ("approved_language",)) if not f.key.startswith("disclosure.")]
            disc = [{"id": f.key, "text": f.text} for f in knowledge.facts(s, fid, ("approved_language",)) if f.key.startswith("disclosure.")]
        if was_live:
            self.c.publisher.pause_ads(p.tenant_id, campaign_id, "new version awaiting approval")
        meter = Meter()
        try:
            brief, creative = asyncio.run(self._generate(firm_d, offer_id, platform, budget, ceiling, fid, facts, lang, prior_creative, prior_brief, creative_patch, meter))
            guard = asyncio.run(factguard.check(self.c.gateway, self.c.prompts, creative, facts, lang + [d["text"] for d in disc], meter))
        except Exception as e:  # noqa: BLE001 - a failed generation returns the campaign to Draft with the reason
            self._back_to_draft(p, campaign_id, f"{type(e).__name__}: {e}"[:300])
            return {"ok": False, "state": "Draft", "error": str(e)[:300]}
        if not guard.ok:  # unmatched claims fail: no version is created and nothing reaches the CCO
            self._back_to_draft(p, campaign_id, "fact guard: unsupported claims")
            return {"ok": False, "state": "Draft", "unsupported_claims": guard.unsupported}
        rendered = renderer.render(creative, firm=firm_d, offer=offers.get(offer_id), disclosures=disc, campaign_id=campaign_id, version=version,
                                   api_base=self.c.settings.api_public_url)
        text = "\n".join(factguard.creative_strings(creative))
        pre = asyncio.run(precheck.run(self.c.gateway, self.c.prompts, text, [d["text"] for d in disc], [], meter))
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            camp = s.get(Campaign, campaign_id)
            s.add(CampaignVersion(campaign_id=campaign_id, version=version, brief=brief.model_dump(), creative_set=creative.model_dump(), parent_version=camp.current_version or None,
                                  change_note=note[:300], created_by=p.user_id))
            transition(camp, "PreChecking")
            s.add(PreCheckRecord(campaign_id=campaign_id, version=version, report=pre, ai_review_missing=pre["ai_review"] == "missing"))
            page_uri = self.c.store.put(p.tenant_id, f"page-{campaign_id}-v{version}-preview.html", rendered.page_html.encode(), content_type="text/html")
            cj_uri = self.c.store.put(p.tenant_id, f"creative-{campaign_id}-v{version}.json", json.dumps(creative.model_dump()).encode(), content_type="application/json")
            s.add(RenderedSet(campaign_id=campaign_id, version=version, content_hash=rendered.content_hash, parts=rendered.parts, artefact_uris={"page": page_uri, "creative": cj_uri}))
            camp.current_version = version
            transition(camp, "PendingCCO")
            s.add(ReviewItem(kind="campaign_version", ref_id=campaign_id, assigned_role="cco", flags=pre["material"] + pre["unsupported_claims"]))
            log_activity(s, actor=p.user_id, action="campaign.submit", tenant_id=p.tenant_id, resource=f"campaign:{campaign_id}:v{version}", meta={"content_hash": rendered.content_hash})
            emit(s, "CampaignSubmitted", tenant_id=p.tenant_id, firm_id=fid, idempotency_key=f"{campaign_id}:{version}", payload={"campaign_id": campaign_id, "version": version})
        return {"ok": True, "state": "PendingCCO", "version": version, "content_hash": rendered.content_hash, "precheck": pre["status"]}

    async def _generate(self, firm, offer_id, platform, budget, ceiling, firm_id, facts, lang, prior_creative, prior_brief, patch, meter):
        offer = offers.get(offer_id)
        gw = self.c.gateway
        if patch is not None and prior_creative is not None:  # an edit: no regeneration, the patch replaces parts of the last creative set
            merged = {**prior_creative, **patch}
            return CampaignBrief(**prior_brief), CreativeSet.model_validate(merged)
        ctx = dict(facts=facts or ["(none on file)"], language=lang or ["(none on file)"])
        angles = AngleOut(angles=[offer.summary.capitalize()], keyword_themes=[offer.name.lower()])
        if gw is not None:
            try:
                p, _ = self.c.prompts.render("brief_angles", offer_name=offer.name, offer_summary=offer.summary, area=firm["area"], audiences=", ".join(offer.audiences), firm_facts="\n".join(facts[:10]))
                angles = await gw.complete_json("You plan RIA campaigns.", p, AngleOut, meter=meter, tags={"stage": "brief"})
            except Exception:  # noqa: BLE001 - deterministic fallback angle
                log.debug("optional step skipped", exc_info=True)
        brief = CampaignBrief(offer_id=offer_id, firm_id=firm_id, area=firm["area"], angles=angles.angles, keyword_themes=angles.keyword_themes,
                              audiences=list(offer.audiences), monthly_budget_cents=budget, budget_ceiling_cents=ceiling)
        if gw is None:
            raise ValidationFailed("no language model is configured")
        bj = json.dumps(brief.model_dump())

        async def gen(name, schema, **kw):
            p, _ = self.c.prompts.render(name, brief=bj, **ctx, **kw)
            return await gw.complete_json("You write compliant RIA marketing drafts.", p, schema, meter=meter, tags={"stage": name})
        ads = gen("google_ads", GoogleAdSet) if platform == "google" else gen("meta_ads", MetaAdSet)
        ad, page, follow = await asyncio.gather(ads, gen("page_copy", PageCopy, sections=", ".join(offer.sections)), gen("emails", FollowUpSet))
        creative = CreativeSet(google=ad if platform == "google" else None, meta=ad if platform == "meta" else None, page=page, followup=follow)
        return brief, creative

    def _back_to_draft(self, p: Principal, campaign_id: str, why: str) -> None:
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            camp = s.get(Campaign, campaign_id)
            transition(camp, "Draft")
            log_activity(s, actor=p.user_id, action="campaign.generate_failed", tenant_id=p.tenant_id, resource=f"campaign:{campaign_id}", result="error", reason=why)

    # ------------------------------------------------------------------ pause / retire
    def pause(self, p: Principal, campaign_id: str, reason: str) -> dict:
        d = require(p, "pause", "campaign_pause", resource_tenant=p.tenant_id) if p.has("firm_admin", "cco") else None
        if d is None:  # marketing can only request a pause
            require(p, "request", "campaign_pause", resource_tenant=p.tenant_id)
            with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
                log_activity(s, actor=p.user_id, action="campaign.pause_requested", tenant_id=p.tenant_id, resource=f"campaign:{campaign_id}", reason=reason[:200])
            return {"state": "unchanged", "requested": True}
        self.c.publisher.pause_ads(p.tenant_id, campaign_id, reason[:200], actor=p.user_id)
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            return self._dto(s.get(Campaign, campaign_id))

    def retire(self, p: Principal, campaign_id: str) -> dict:
        require(p, "pause", "campaign_pause", resource_tenant=p.tenant_id)
        self.c.publisher.pause_ads(p.tenant_id, campaign_id, "retired", actor=p.user_id, quiet=True)
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            camp = s.get(Campaign, campaign_id)
            if camp.state != "Retired":
                transition(camp, "Retired")
            log_activity(s, actor=p.user_id, action="campaign.retire", tenant_id=p.tenant_id, resource=f"campaign:{campaign_id}")
            return self._dto(camp)
