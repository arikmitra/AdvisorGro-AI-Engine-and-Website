from datetime import timedelta

import pytest
from sqlalchemy import select

from advisorgro.core.clock import utcnow
from advisorgro.core.errors import Conflict, TenantContextMissing
from advisorgro.core.events import emit
from advisorgro.db.models import Alert, Firm, Outbox, ProcessedEvent, Run
from advisorgro.db.session import session_scope
from advisorgro.engine.gro.measure import numbers_ok
from advisorgro.workflows import jobs

from .conftest import World
from .test_gro import new_campaign


def test_orm_tenant_scoping(c, world):
    other = World(c)
    with session_scope(c.sm, tenant_id=other.tenant) as s:
        assert s.execute(select(Firm)).scalars().all()[0].tenant_id == other.tenant and len(s.execute(select(Firm)).scalars().all()) == 1
    with session_scope(c.sm) as s:  # no tenant: deny by default, not "everything"
        assert s.execute(select(Firm)).scalars().all() == []
    with pytest.raises(TenantContextMissing):
        with session_scope(c.sm) as s:
            s.add(Firm(name="x", domain="x.com"))
    with pytest.raises(TenantContextMissing):
        with session_scope(c.sm, tenant_id=world.tenant) as s:
            s.add(Firm(tenant_id=other.tenant, name="x", domain="x.com"))  # cross-tenant write refused


def test_shield_monthly_probe_and_alert_dedupe(c, world):
    from advisorgro.db.models import Firm
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        f = Firm(name="Low Tls", domain="lowtls-firm.com", area="Austin")
        s.add(f)
        s.flush()
        fid = f.id
    c.shield.enroll(world.principal("firm_admin"), fid)
    out = jobs.daily(c)
    assert out["shield_due"] == 1 and out["probes"] == 1
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        runs = s.execute(select(Run).where(Run.firm_id == fid)).scalars().all()
        assert {r.trigger for r in runs} == {"shield", "probe"}
    first = c.shield.alerts_for(world.principal("firm_admin"), fid)
    jobs.daily(c)  # same day again: idempotent, no second run, no duplicate alerts
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        assert len(s.execute(select(Run).where(Run.firm_id == fid)).scalars().all()) == 2
    assert len(c.shield.alerts_for(world.principal("firm_admin"), fid)) == len(first)
    assert all(a["route"] != "internal" for a in c.shield.alerts_for(world.principal("marketing"), fid))  # compliance alerts: CCO/reviewer only


def test_dead_letter_and_replay(c, world):
    calls = {"n": 0}

    def boom(ev):
        calls["n"] += 1
        raise RuntimeError("poison")
    c.bus.subscribe("CampaignPaused", "test.poison", boom)
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        emit(s, "CampaignPaused", tenant_id=world.tenant, idempotency_key="k1", payload={"run_id": "R9"})
    c.bus.drain()
    with session_scope(c.sm, system=True) as s:
        ob = s.execute(select(Outbox).where(Outbox.type == "CampaignPaused")).scalar_one()
        assert ob.dead and ob.attempts == 5 and "poison" in ob.last_error
    assert c.bus.replay(run_id="R9") == 1
    with session_scope(c.sm, system=True) as s:
        assert not s.execute(select(Outbox).where(Outbox.type == "CampaignPaused")).scalar_one().dead


def test_outbox_duplicate_event_is_dropped(c, world):
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        assert emit(s, "LeadCreated", tenant_id=world.tenant, idempotency_key="same", payload={}) is not None
        assert emit(s, "LeadCreated", tenant_id=world.tenant, idempotency_key="same", payload={}) is None


def live_campaign(c, world):
    camp = new_campaign(c, world)
    r = c.campaigns.submit_for_review(world.principal("marketing"), camp["id"])
    c.approvals.decide(world.principal("cco"), camp["id"], 1, r["content_hash"], "approve")
    c.bus.drain()
    return camp["id"]


def test_metrics_gap_is_not_zero_and_optimiser_guardrails(c, world):
    cid = live_campaign(c, world)
    plat = c.connectors["google"]
    pid = next(iter(plat.objects))
    day = (utcnow() - timedelta(days=1)).date()
    assert c.measure.collect(world.tenant, cid, day) is False  # no data from the platform: a gap, recorded as not verified
    f = c.measure.funnel(world.tenant, cid, 7)
    assert f["days_without_data"] == 1 and f["spend_cents"] == 0 and f["cost_per_lead_cents"] is None
    plat.metrics[(pid, day.isoformat())] = {"spend_cents": 400000, "impressions": 9000, "clicks": 200, "conversions": 0}
    assert c.measure.collect(world.tenant, cid, day) is True
    props = c.measure.propose(world.tenant, cid)
    assert {p["kind"] for p in props} == {"new_variant"}  # clicks but no leads -> new variant, never an auto-launch
    c.measure.apply(world.tenant, props[0]["id"])
    assert c.campaigns.get(world.principal("cco"), cid)["state"] == "Live" and c.campaigns.get(world.principal("cco"), cid)["current_version"] == 1  # nothing changed; it needs the full pipeline


def test_optimiser_cannot_exceed_ceiling(c, world):
    from advisorgro.db.models import OptimiserProposal
    cid = live_campaign(c, world)
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        p = OptimiserProposal(campaign_id=cid, kind="budget_shift", payload={"new_budget_cents": 9_999_999})
        s.add(p)
        s.flush()
        pid = p.id
    with pytest.raises(Conflict):
        c.measure.apply(world.tenant, pid)


def test_weekly_report_numbers_come_from_code():
    nums = {"spend_cents": 123400, "leads": 7, "bookings": 2}
    assert numbers_ok("7 leads and 2 bookings from $1,234 of spend", nums)
    assert not numbers_ok("12 leads this week", nums)  # an invented figure is rejected, the template wording is used instead


def test_resume_requires_valid_approval_and_clean_readback(c, world):
    cid = live_campaign(c, world)
    c.publisher.pause_ads(world.tenant, cid, "manual")
    assert c.publisher.resume(world.principal("firm_admin"), cid) == "Live"
    c.publisher.pause_ads(world.tenant, cid, "manual again")
    from sqlalchemy import text
    with c.db_engine.begin() as conn:
        conn.execute(text("UPDATE approvals SET expires_at = '2000-01-01 00:00:00'"))
    from advisorgro.core.errors import ApprovalMismatch
    with pytest.raises(ApprovalMismatch):
        c.publisher.resume(world.principal("firm_admin"), cid)
    _ = (Alert, ProcessedEvent)
