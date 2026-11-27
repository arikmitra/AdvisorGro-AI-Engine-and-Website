"""The single policy function (guide 15.5). Called on every operation; deny by default.

Permission letters (15.3): V view, E edit, A approve, X export, P pause, R request.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.errors import Forbidden
from .principal import Principal

M = {
    "firm_profile":     {"firm_admin": "VE", "cco": "V", "marketing": "V", "advisor": "V", "read_only": "V"},
    "reports":          {"firm_admin": "VX", "cco": "VX", "marketing": "V", "advisor": "V", "read_only": "V"},
    "campaign_drafts":  {"firm_admin": "VE", "cco": "V", "marketing": "VE", "advisor": "V", "read_only": "V"},
    "campaign_approval": {"cco": "A"},
    "campaign_pause":   {"firm_admin": "P", "cco": "P", "marketing": "R"},
    "leads":            {"firm_admin": "VX", "cco": "V", "marketing": "V", "advisor": "VE", "read_only": "V"},
    "connections":      {"firm_admin": "E", "cco": "V"},
    "billing":          {"firm_admin": "VE"},
    "activity_log":     {"firm_admin": "V", "cco": "V", "read_only": "V"},
    "review_queue":     {"cco": "VA"},
    "shield":           {"firm_admin": "VE", "cco": "V", "marketing": "V", "advisor": "V", "read_only": "V"},
}
MASKED_LEADS = {"marketing", "read_only"}  # contact details masked
OWN_LEADS_ONLY = {"advisor"}

# AdvisorGro staff (15.4). Staff never approve for a firm.
S = {
    "support":        {"firm_profile": "V", "leads": "V", "shield": "V"},  # masked; needs ticket + firm consent
    "reviewer":       {"reports": "V", "review_queue": "VA", "shield": "V"},
    "engineer":       {},  # break-glass only
    "platform_admin": {"tenants": "VE", "staff": "VE", "billing": "V"},
    "security":       {"activity_log": "V", "sessions": "VE"},
}
LETTER = {"view": "V", "edit": "E", "approve": "A", "export": "X", "pause": "P", "request": "R"}


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    masked: bool = False
    own_only: bool = False
    extra: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.allowed


def authorize(p: Principal, action: str, resource: str, *, resource_tenant: str | None = None,
              owner_id: str | None = None, drafter_id: str | None = None,
              break_glass_tenants: frozenset[str] = frozenset(), deputy_for: frozenset[str] = frozenset()) -> Decision:
    letter = LETTER[action]
    # tenant first
    if resource_tenant is not None and not p.is_staff and p.tenant_id != resource_tenant:
        return Decision(False, "cross-tenant")
    if p.is_staff:
        return _staff(p, letter, resource, resource_tenant, break_glass_tenants)
    if not p.tenant_id:
        return Decision(False, "no tenant")
    # separation of duties: the person who drafts cannot approve
    if resource == "campaign_approval" and letter == "A":
        if drafter_id and drafter_id == p.user_id:
            return Decision(False, "drafter cannot approve")
        if not (p.has("cco") or deputy_for):
            return Decision(False, "approver role is the CCO or a named deputy")
        return Decision(True, "approver")
    allowed_for: dict[str, str] = M.get(resource, {})
    for r in p.roles:
        if letter in allowed_for.get(r, ""):
            d = Decision(True, r)
            if resource == "leads":
                d.masked = r in MASKED_LEADS
                d.own_only = r in OWN_LEADS_ONLY
                if d.own_only and owner_id is not None and owner_id != p.user_id:
                    return Decision(False, "advisors see their own leads only")
                if letter == "V" and r in ("cco",):
                    d.masked = False
            return d
    return Decision(False, "role lacks permission")


def _staff(p: Principal, letter: str, resource: str, tenant: str | None, bg: frozenset[str]) -> Decision:
    if letter == "A" and resource == "campaign_approval":
        return Decision(False, "staff cannot approve a firm's marketing on its behalf")
    for r in p.roles:
        if letter in S.get(r, {}).get(resource, ""):
            if r == "support":
                if not (p.ticket and p.consent):
                    return Decision(False, "support access needs a ticket and the firm's consent")
                return Decision(True, r, masked=True)
            return Decision(True, r)
    if "engineer" in p.roles and tenant and tenant in bg and letter == "V":
        return Decision(True, "break-glass", extra={"break_glass": True})
    return Decision(False, "staff role lacks permission")


def require(p: Principal, action: str, resource: str, **kw) -> Decision:
    d = authorize(p, action, resource, **kw)
    if not d:
        raise Forbidden(d.reason)
    return d
