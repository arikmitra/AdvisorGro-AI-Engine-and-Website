"""Scheduled jobs shared by the in-process runner and the Temporal schedules (same functions, same behaviour)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ..core.clock import utcnow
from ..db.models import Campaign, ShieldEnrollment
from ..db.session import session_scope


def daily(c, now=None) -> dict:
    now = now or utcnow()
    out = {"shield_due": c.shield.schedule_due(now)}
    with session_scope(c.sm, system=True) as s:
        enrolled = [(e.tenant_id, e.firm_id) for e in s.execute(select(ShieldEnrollment).where(ShieldEnrollment.active.is_(True))).scalars()]
        live = [(x.tenant_id, x.id) for x in s.execute(select(Campaign).where(Campaign.state == "Live")).scalars()]
    for tenant, fid in enrolled:  # daily probes: certificate and domain expiry
        c.shield.start_scheduled(tenant, fid, probe=True)
    out["probes"] = len(enrolled)
    ok = 0
    for tenant, cid in live:  # daily read-back re-verification, then yesterday's metrics
        ok += bool(c.publisher.verify_live(cid))
        c.measure.collect(tenant, cid, (now - timedelta(days=1)).date())
    out["live_verified"], out["live_total"] = ok, len(live)
    out["checkins"] = c.leads.run_due_sequences(now)
    out["events"] = c.bus.drain()
    return out
