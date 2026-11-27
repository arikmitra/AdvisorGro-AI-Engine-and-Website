"""Authenticated portal API. The tenant comes from the verified token; ids from the URL are always re-checked against it
(another firm's id is simply 'not found'). Every sensitive action is policy-checked and written to the activity log."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...container import Container
from ...core.errors import Forbidden, NotFound
from ...db.models import ActivityLog, AdLink, Firm, KillSwitch, Run
from ...db.session import session_scope
from ...engine.gro import knowledge
from ...security.activity import log_activity
from ...security.policy import authorize, require
from ...security.principal import Principal
from ...security.tokens import require_step_up
from ..deps import bg_drain, get_c, principal

router = APIRouter(prefix="/v1", dependencies=[Depends(bg_drain)])


class CampaignIn(BaseModel):
    firm_id: str
    offer_id: str
    platform: str = "google"
    monthly_budget_cents: int = Field(gt=0)
    budget_ceiling_cents: int = Field(gt=0)


class SubmitIn(BaseModel):
    creative_patch: dict | None = None
    note: str = ""


class DecisionIn(BaseModel):
    version: int
    content_hash: str = Field(min_length=64, max_length=64)
    decision: str
    comment: str = ""


class ReasonIn(BaseModel):
    reason: str = ""


class FirmIn(BaseModel):
    name: str
    domain: str
    area: str = ""
    aum_band: str = ""
    links: list[dict] = []


class FactIn(BaseModel):
    kind: str = Field(pattern="^(fact|approved_language|cco_decision)$")
    text: str = Field(min_length=3, max_length=2000)
    key: str = ""


class ReviewIn(BaseModel):
    decision: str
    notes: str = ""


class EnrollIn(BaseModel):
    firm_id: str


class PortfolioIn(BaseModel):
    firm_ids: list[str]


class KillIn(BaseModel):
    scope: str = Field(pattern="^(global|tenant|campaign)$")
    scope_id: str | None = None
    reason: str = ""
    active: bool = True


class AdLinkIn(BaseModel):
    firm_id: str
    platform: str = Field(pattern="^(google|meta)$")
    external_account_id: str
    secret_ref: str = ""


@router.get("/me")
def me(p: Principal = Depends(principal)):
    return {"user_id": p.user_id, "tenant_id": p.tenant_id, "roles": sorted(p.roles), "staff": p.is_staff, "mfa": p.mfa}


# -------------------------------------------------------------------- firms and knowledge
@router.get("/firms")
def firms(p: Principal = Depends(principal), c: Container = Depends(get_c)):
    require(p, "view", "firm_profile", resource_tenant=p.tenant_id)
    with session_scope(c.sm, tenant_id=p.tenant_id) as s:
        return [{"id": f.id, "name": f.name, "domain": f.domain, "area": f.area} for f in s.execute(select(Firm)).scalars()]


@router.post("/firms", status_code=201)
def create_firm(b: FirmIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    require(p, "edit", "firm_profile", resource_tenant=p.tenant_id)
    from ...core.ssrf import normalise_domain
    with session_scope(c.sm, tenant_id=p.tenant_id) as s:
        f = Firm(name=b.name[:200], domain=normalise_domain(b.domain), area=b.area[:200], aum_band=b.aum_band[:40], profile={"links": b.links[:10]})
        s.add(f)
        s.flush()
        log_activity(s, actor=p.user_id, action="firm.create", tenant_id=p.tenant_id, resource=f"firm:{f.id}")
        return {"id": f.id}


@router.get("/firms/{firm_id}/knowledge")
def get_knowledge(firm_id: str, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    require(p, "view", "firm_profile", resource_tenant=p.tenant_id)
    with session_scope(c.sm, tenant_id=p.tenant_id) as s:
        if not s.get(Firm, firm_id):
            raise NotFound("firm")
        return [{"id": f.id, "kind": f.kind, "key": f.key, "text": f.text} for f in knowledge.facts(s, firm_id, ("fact", "approved_language", "cco_decision"))]


@router.post("/firms/{firm_id}/knowledge", status_code=201)
def add_knowledge(firm_id: str, b: FactIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    """Facts and approved language are what generated copy may rely on; changing them is a CCO-visible, logged action."""
    require(p, "edit", "firm_profile", resource_tenant=p.tenant_id)
    with session_scope(c.sm, tenant_id=p.tenant_id) as s:
        if not s.get(Firm, firm_id):
            raise NotFound("firm")
        f = knowledge.add(s, firm_id, b.kind, b.text, b.key)
        log_activity(s, actor=p.user_id, action="knowledge.add", tenant_id=p.tenant_id, resource=f"firm:{firm_id}", meta={"kind": b.kind, "id": f.id})
        return {"id": f.id}


# -------------------------------------------------------------------- campaigns
@router.get("/campaigns")
def list_campaigns(p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.campaigns.list(p)


@router.post("/campaigns", status_code=201)
def create_campaign(b: CampaignIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.campaigns.create(p, **b.model_dump())


@router.get("/campaigns/{cid}")
def get_campaign(cid: str, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.campaigns.get(p, cid)


@router.post("/campaigns/{cid}/submit")
def submit(cid: str, b: SubmitIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.campaigns.submit_for_review(p, cid, creative_patch=b.creative_patch, note=b.note)


@router.post("/campaigns/{cid}/decision")
def decide(cid: str, b: DecisionIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    """CCO approval. Fresh MFA required; refused if the hash is not what the renderer produced for the current version."""
    return c.approvals.decide(p, cid, b.version, b.content_hash, b.decision, b.comment)


@router.post("/campaigns/{cid}/pause")
def pause(cid: str, b: ReasonIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.campaigns.pause(p, cid, b.reason)


@router.post("/campaigns/{cid}/resume")
def resume(cid: str, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return {"state": c.publisher.resume(p, cid)}


@router.post("/campaigns/{cid}/retire")
def retire(cid: str, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.campaigns.retire(p, cid)


@router.get("/campaigns/{cid}/report")
def weekly(cid: str, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    require(p, "view", "reports", resource_tenant=p.tenant_id)
    c.campaigns.get(p, cid)  # 404 for another tenant's campaign
    return c.measure.weekly_report(p.tenant_id, cid)


# -------------------------------------------------------------------- review queue (Shield reports with compliance flags)
@router.get("/review-queue")
def review_queue(p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.shield.review_queue(p)


@router.post("/review-queue/{run_id}/decision")
def review_decision(run_id: str, b: ReviewIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    if not p.is_staff:
        require_step_up(p, c.settings.step_up_max_age_s)
    return {"state": c.shield.decide_review(p, run_id, b.decision, b.notes)}


# -------------------------------------------------------------------- Shield
@router.post("/shield/enroll")
def enroll(b: EnrollIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    c.shield.enroll(p, b.firm_id)
    return {"ok": True}


@router.post("/shield/portfolio")
def portfolio(b: PortfolioIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    ids = c.shield.request_portfolio(p, b.firm_ids)
    return {"run_ids": ids}


@router.get("/shield/alerts")
def alerts(firm_id: str | None = None, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    require(p, "view", "shield", resource_tenant=p.tenant_id)
    return c.shield.alerts_for(p, firm_id)


@router.get("/shield/runs")
def runs(p: Principal = Depends(principal), c: Container = Depends(get_c)):
    require(p, "view", "shield", resource_tenant=p.tenant_id)
    with session_scope(c.sm, tenant_id=p.tenant_id) as s:
        return [{"id": r.id, "domain": r.domain, "trigger": r.trigger, "state": r.state, "score": r.score, "partial": r.partial,
                 "created_at": r.created_at.isoformat()} for r in s.execute(select(Run).order_by(Run.created_at.desc()).limit(50)).scalars()]


# -------------------------------------------------------------------- leads (masked by role)
@router.get("/leads")
def leads(campaign_id: str | None = None, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    return c.leads.list_for(p, campaign_id)


@router.get("/leads/sla")
def sla(p: Principal = Depends(principal), c: Container = Depends(get_c)):
    require(p, "view", "leads", resource_tenant=p.tenant_id)
    return c.leads.sla_report(p.tenant_id)


# -------------------------------------------------------------------- connections, kill switch, activity
@router.post("/ad-links", status_code=201)
def link_account(b: AdLinkIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    """Link status only: tokens live in the secrets manager (`secret_ref`), never in the database or the UI."""
    require(p, "edit", "connections", resource_tenant=p.tenant_id)
    require_step_up(p, c.settings.step_up_max_age_s)
    with session_scope(c.sm, tenant_id=p.tenant_id) as s:
        if not s.get(Firm, b.firm_id):
            raise NotFound("firm")
        link = s.execute(select(AdLink).where(AdLink.firm_id == b.firm_id, AdLink.platform == b.platform)).scalar_one_or_none() or AdLink(firm_id=b.firm_id, platform=b.platform)
        link.status, link.external_account_id, link.secret_ref = "linked", b.external_account_id[:60], b.secret_ref[:200]
        s.add(link)
        log_activity(s, actor=p.user_id, action="adlink.connect", tenant_id=p.tenant_id, resource=f"firm:{b.firm_id}:{b.platform}")
    return {"ok": True}


@router.post("/admin/kill-switch")
def kill_switch(b: KillIn, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    """Stops publishing instantly. Tenant and campaign scopes: the firm's admin or CCO. Global: AdvisorGro platform admin only."""
    if b.scope == "global":
        if not (p.is_staff and p.has("platform_admin", "security")):
            raise Forbidden("global kill switch is for AdvisorGro platform admins")
    else:
        if p.is_staff or not p.has("firm_admin", "cco"):
            raise Forbidden("only the firm's admin or CCO can do this")
        if b.scope == "tenant":
            b.scope_id = p.tenant_id
    with session_scope(c.sm, system=True) as s:
        s.add(KillSwitch(scope=b.scope, scope_id=b.scope_id, active=b.active, reason=b.reason[:300], set_by=p.user_id))
        log_activity(s, actor=p.user_id, action="killswitch.set" if b.active else "killswitch.clear", tenant_id=p.tenant_id, resource=f"{b.scope}:{b.scope_id}", reason=b.reason[:200])
    return {"ok": True}


@router.get("/activity")
def activity(limit: int = 100, p: Principal = Depends(principal), c: Container = Depends(get_c)):
    d = authorize(p, "view", "activity_log", resource_tenant=p.tenant_id)
    if not d:
        raise Forbidden(d.reason)
    with session_scope(c.sm, system=True) as s:
        q = select(ActivityLog).where(ActivityLog.tenant_id == p.tenant_id).order_by(ActivityLog.seq.desc()).limit(min(limit, 500))
        return [{"seq": a.seq, "at": a.at.isoformat(), "actor": a.actor, "action": a.action, "resource": a.resource, "result": a.result, "hash": a.hash} for a in s.execute(q).scalars()]


