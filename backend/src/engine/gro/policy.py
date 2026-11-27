"""Policy engine (deck 9.7): the checks code runs before anything can go live or spend more. The model never decides."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.clock import utcnow
from ...core.errors import ApprovalMismatch, KillSwitchActive, ValidationFailed
from ...db.models import Approval, Campaign, KillSwitch, RenderedSet


def kill_switch(s: Session, tenant_id: str, campaign_id: str | None = None) -> str | None:
    for k in s.execute(select(KillSwitch).where(KillSwitch.active.is_(True))).scalars():
        if k.scope == "global" or (k.scope == "tenant" and k.scope_id == tenant_id) or (k.scope == "campaign" and k.scope_id == campaign_id):
            return k.reason or k.scope
    return None


def valid_approval(s: Session, campaign_id: str, version: int, content_hash: str, now=None) -> Approval | None:
    """The latest decision for this exact version must be an unexpired approve over the exact content hash."""
    now = now or utcnow()
    last = s.execute(select(Approval).where(Approval.campaign_id == campaign_id, Approval.version == version)
                     .order_by(Approval.decided_at.desc(), Approval.id.desc()).limit(1)).scalar_one_or_none()
    if not last or last.decision != "approve" or last.content_hash != content_hash:
        return None
    if last.expires_at and last.expires_at < now:
        return None
    return last


def assert_publishable(s: Session, c: Campaign, version: int) -> RenderedSet:
    why = kill_switch(s, c.tenant_id, c.id)
    if why:
        raise KillSwitchActive(f"kill switch active: {why}")
    rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == c.id, RenderedSet.version == version)).scalar_one_or_none()
    if not rs:
        raise ApprovalMismatch("no rendered set for this version")
    if not valid_approval(s, c.id, version, rs.content_hash):
        raise ApprovalMismatch("no valid approval for this exact version and content hash")
    if c.monthly_budget_cents > c.budget_ceiling_cents:
        raise ValidationFailed("budget exceeds the client's ceiling")
    return rs
