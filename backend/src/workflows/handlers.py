"""Event consumers. Each is idempotent (the bus also records processed events) and tenant-scoped.
In production the same functions run as Temporal activities (workflows/temporal_defs.py)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from ..core.errors import ProviderError
from ..core.events import Event
from ..db.models import Alert, AuditRequest, Membership, Run, User
from ..db.session import session_scope

log = logging.getLogger(__name__)


def _emails_for_role(c, tenant: str, role: str) -> list[str]:
    with session_scope(c.sm, tenant_id=tenant) as s:
        ids = [m.user_id for m in s.execute(select(Membership).where(Membership.role == role)).scalars()]
    with session_scope(c.sm, system=True) as s:
        return [u.email for u in s.execute(select(User).where(User.id.in_(ids), User.disabled.is_(False))).scalars()] if ids else []


def register(c) -> None:
    bus = c.bus

    def on_audit_requested(ev: Event) -> None:
        c.shield.execute_run(ev.payload["run_id"])

    def on_shield_due(ev: Event) -> None:
        c.shield.start_scheduled(ev.tenant_id, ev.payload["firm_id"])

    def on_report_ready(ev: Event) -> None:
        with session_scope(c.sm, tenant_id=ev.tenant_id) as s:
            run = s.get(Run, ev.payload["run_id"])
            ar = s.get(AuditRequest, ev.payload.get("audit_request_id")) if ev.payload.get("audit_request_id") else None
            if not run or not ar or not run.notify_enc:
                return
            to, token = c.crypto.decrypt(ev.tenant_id, ar.email_enc), c.crypto.decrypt(ev.tenant_id, run.notify_enc)
            card, firm, rid = run.scorecard, run.firm_name or run.domain, run.id
        link = f"{c.settings.report_page_url}#report={token}"
        body = (f"Your AdvisorGro audit for {firm} is ready.\n\nScore: {card.get('total', 0):g} out of {card.get('max_total', 100):g}"
                + ("" if card.get("complete") else " (a lower bound: some checks could not be verified)")
                + f"\n\nFull report, with what is working and a ranked fix list:\n{link}\n\nThis audit reads public pages only. It is not legal or compliance advice.")
        try:
            asyncio.run(c.email.send(to, f"Your AdvisorGro audit: {firm}", body, tag="audit-report"))
        except ProviderError:
            raise  # the bus retries, then dead-letters; the page tells the visitor mail is delayed
        with session_scope(c.sm, tenant_id=ev.tenant_id) as s:
            r = s.get(Run, rid)
            if r.state == "Released":
                r.state = "Delivered"

    def on_alert(ev: Event) -> None:
        with session_scope(c.sm, tenant_id=ev.tenant_id) as s:
            a = s.get(Alert, ev.payload["alert_id"])
            title, detail, route = a.payload.get("title", a.kind), a.payload.get("detail", ""), a.route
        to = _emails_for_role(c, ev.tenant_id, "firm_admin") if route == "client" else ([c.settings.review_email] if c.settings.review_email else [])
        for addr in to:
            asyncio.run(c.email.send(addr, f"[Shield alert] {title}", f"{title}\n\n{detail}\n\nShield checks public signals only and is not legal or compliance advice.", tag="alert"))

    def on_submitted(ev: Event) -> None:
        for addr in _emails_for_role(c, ev.tenant_id, "cco"):
            asyncio.run(c.email.send(addr, "A campaign is waiting for your approval", "Open the AdvisorGro portal to review the exact page, ads and emails.", tag="approval-request"))

    def on_approved(ev: Event) -> None:
        c.publisher.publish(ev.payload["campaign_id"])

    def on_lead(ev: Event) -> None:
        c.leads.follow_up(ev.tenant_id, ev.payload["lead_id"])

    def on_booking(ev: Event) -> None:
        c.leads.send_conversion(ev.tenant_id, ev.payload["lead_id"])

    for t, name, fn in [("AuditRequested", "shield.run", on_audit_requested), ("ShieldRunDue", "shield.scheduled", on_shield_due),
                        ("ReportReady", "shield.deliver", on_report_ready), ("AlertRaised", "shield.alert", on_alert),
                        ("CampaignSubmitted", "gro.notify_cco", on_submitted), ("CampaignApproved", "gro.publish", on_approved),
                        ("LeadCreated", "leads.followup", on_lead), ("BookingConfirmed", "leads.conversion", on_booking)]:
        bus.subscribe(t, name, fn)
