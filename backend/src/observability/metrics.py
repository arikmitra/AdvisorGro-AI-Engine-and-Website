"""Prometheus text exposition computed from the database at scrape time (golden signals come from the OTel collector;
these are the *business* and *pipeline* signals the runbooks alert on)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from ..core.clock import utcnow
from ..db.models import Campaign, Incident, LeadEvent, Outbox, Run
from ..db.session import session_scope


def render(c) -> str:
    now = utcnow()
    lines: list[str] = []

    def g(name: str, value: float, help_: str, **labels) -> None:
        lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
        lines.append(f"# HELP {name} {help_}\n# TYPE {name} gauge") if not any(ln.startswith(f"# TYPE {name} ") for ln in lines) else None
        lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")

    with session_scope(c.sm, system=True) as s:
        g("ag_outbox_pending", s.execute(select(func.count()).select_from(Outbox).where(Outbox.published_at.is_(None), Outbox.dead.is_(False))).scalar_one(), "events waiting for delivery")
        g("ag_outbox_oldest_pending_seconds", max([(now - o).total_seconds() for o in s.execute(select(Outbox.created_at).where(Outbox.published_at.is_(None), Outbox.dead.is_(False)).limit(1)).scalars()] or [0]), "age of the oldest undelivered event")
        g("ag_outbox_dead_letters", s.execute(select(func.count()).select_from(Outbox).where(Outbox.dead.is_(True))).scalar_one(), "events dead-lettered, replay by run id")
        for state, n in s.execute(select(Run.state, func.count()).where(Run.created_at >= now - timedelta(hours=1)).group_by(Run.state)).all():
            g("ag_runs_last_hour", n, "audit runs created in the last hour", state=state)
        g("ag_runs_stuck", s.execute(select(func.count()).select_from(Run).where(Run.state.in_(("Queued", "Running")), Run.created_at < now - timedelta(minutes=10))).scalar_one(), "runs not finished after 10 minutes")
        g("ag_campaigns_live", s.execute(select(func.count()).select_from(Campaign).where(Campaign.state == "Live")).scalar_one(), "campaigns currently live")
        g("ag_incidents_open", s.execute(select(func.count()).select_from(Incident).where(Incident.resolved.is_(False))).scalar_one(), "unresolved incidents (read-back mismatch, send failures)")
        ev = [e.meta for e in s.execute(select(LeadEvent).where(LeadEvent.type == "first_contact", LeadEvent.at >= now - timedelta(days=1))).scalars()]
        g("ag_lead_first_contacts_24h", len(ev), "first replies sent in 24h")
        g("ag_lead_first_contacts_within_sla_24h", sum(1 for m in ev if m.get("sla_met")), "first replies within the 90s SLA in 24h")
    return "\n".join(lines) + "\n"
