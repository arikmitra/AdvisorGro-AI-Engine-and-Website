"""CCO approval: the one gate. An approval is an insert-only row over (campaign, version, content hash). It is refused
unless the approver is the CCO or a named deputy (never the drafter, never AdvisorGro staff), the sign-in is fresh MFA,
the version is the current one and the hash equals what the renderer produced. Any edit makes a new version, so an
old approval can never authorise new content."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ...core.clock import utcnow
from ...core.errors import ApprovalMismatch, Conflict, NotFound, ValidationFailed
from ...core.events import emit
from ...db.models import Approval, Campaign, CampaignVersion, Delegation, RenderedSet, ReviewItem
from ...db.session import session_scope
from ...security.activity import log_activity
from ...security.policy import require
from ...security.principal import Principal
from ...security.tokens import require_step_up
from .states import transition

DECISIONS = {"approve": "Approved", "request_changes": "ChangesRequested", "reject": "Rejected"}


class ApprovalService:
    def __init__(self, c):
        self.c = c

    def decide(self, p: Principal, campaign_id: str, version: int, content_hash: str, decision: str, comment: str = "") -> dict:
        if decision not in DECISIONS:
            raise ValidationFailed("decision must be approve, request_changes or reject")
        require_step_up(p, self.c.settings.step_up_max_age_s)
        now = utcnow()
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            camp = s.get(Campaign, campaign_id)
            if not camp:
                raise NotFound("campaign")
            if camp.state != "PendingCCO":
                raise Conflict(f"campaign is {camp.state}, not PendingCCO")
            if version != camp.current_version:
                raise ApprovalMismatch("this version has been superseded; review the current version")
            rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == campaign_id, RenderedSet.version == version)).scalar_one_or_none()
            if not rs or rs.content_hash != content_hash:
                raise ApprovalMismatch("content hash does not match what you were shown")
            cv = s.execute(select(CampaignVersion).where(CampaignVersion.campaign_id == campaign_id, CampaignVersion.version == version)).scalar_one()
            deputies = frozenset(d.approver_id for d in s.execute(select(Delegation).where(
                Delegation.deputy_id == p.user_id, Delegation.starts_at <= now, Delegation.ends_at >= now)).scalars())
            d = require(p, "approve", "campaign_approval", resource_tenant=camp.tenant_id, drafter_id=cv.created_by, deputy_for=deputies)
            ap = Approval(campaign_id=campaign_id, version=version, content_hash=content_hash, approver_id=p.user_id, decision=decision,
                          comment=comment[:2000], expires_at=now + timedelta(days=self.c.settings.approval_expiry_days) if decision == "approve" else None)
            s.add(ap)
            transition(camp, DECISIONS[decision])
            for it in s.execute(select(ReviewItem).where(ReviewItem.ref_id == campaign_id, ReviewItem.kind == "campaign_version", ReviewItem.state == "pending")).scalars():
                it.state, it.decided_by, it.decided_at = ("released" if decision == "approve" else "rejected"), p.user_id, now
            log_activity(s, actor=p.user_id, action=f"campaign.{decision}", tenant_id=p.tenant_id, resource=f"campaign:{campaign_id}:v{version}",
                         meta={"content_hash": content_hash, "via": d.reason, "deputy": bool(deputies)})
            if decision == "approve":
                emit(s, "CampaignApproved", tenant_id=p.tenant_id, firm_id=camp.firm_id, idempotency_key=f"{campaign_id}:{version}:{content_hash[:12]}",
                     payload={"campaign_id": campaign_id, "version": version})
            return {"campaign_id": campaign_id, "version": version, "state": camp.state, "decision": decision, "expires_at": ap.expires_at.isoformat() if ap.expires_at else None}
