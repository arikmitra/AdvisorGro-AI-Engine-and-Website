import json

import pytest
from sqlalchemy import select

from advisorgro.core.errors import (
    ApprovalMismatch,
    Conflict,
    Forbidden,
    IllegalTransition,
    ImmutableRecord,
    KillSwitchActive,
    NotFound,
    StepUpRequired,
)
from advisorgro.db.models import AdLink, Approval, Campaign, Incident, KillSwitch
from advisorgro.db.session import session_scope
from advisorgro.engine.gro.states import transition
from advisorgro.llm.fake import FakeProvider


def new_campaign(c, w, role="marketing"):
    with session_scope(c.sm, tenant_id=w.tenant) as s:
        if not s.execute(select(AdLink)).first():
            s.add(AdLink(firm_id=w.firm_id, platform="google", status="linked", external_account_id="123"))
    return c.campaigns.create(w.principal(role), firm_id=w.firm_id, offer_id="retirement_readiness", platform="google",
                              monthly_budget_cents=300000, budget_ceiling_cents=500000)


def submitted(c, w):
    camp = new_campaign(c, w)
    res = c.campaigns.submit_for_review(w.principal("marketing"), camp["id"])
    assert res["ok"], res
    return camp["id"], res


def test_full_path_approve_publish_read_back_live(c, world):
    cid, res = submitted(c, world)
    assert res["state"] == "PendingCCO" and len(res["content_hash"]) == 64
    out = c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "approve", "ok")
    assert out["state"] == "Approved"
    c.bus.drain()  # CampaignApproved -> publisher
    camp = c.campaigns.get(world.principal("cco"), cid)
    assert camp["state"] == "Live" and camp["live_version"] == 1
    plat = c.connectors["google"]
    assert [o["status"] for o in plat.objects.values()] == ["ENABLED"]
    assert "Retirement" in c.pages.fetch(world.tenant, cid, 1)


def test_approval_refusals(c, world):
    cid, res = submitted(c, world)
    h = res["content_hash"]
    with pytest.raises(Forbidden):
        c.approvals.decide(world.principal("marketing"), cid, 1, h, "approve")  # not the CCO
    with pytest.raises(Forbidden):
        c.approvals.decide(world.principal("firm_admin"), cid, 1, h, "approve")  # admin is not the approver
    with pytest.raises(StepUpRequired):
        c.approvals.decide(world.principal("cco", age=3600), cid, 1, h, "approve")  # stale sign-in
    with pytest.raises(StepUpRequired):
        c.approvals.decide(world.principal("cco", mfa=False), cid, 1, h, "approve")
    with pytest.raises(ApprovalMismatch):
        c.approvals.decide(world.principal("cco"), cid, 1, "0" * 64, "approve")  # not what was shown
    with pytest.raises(ApprovalMismatch):
        c.approvals.decide(world.principal("cco"), cid, 2, h, "approve")  # not the current version
    from advisorgro.security.principal import Principal
    staff = Principal(user_id="s1", tenant_id=world.tenant, roles=frozenset({"cco", "platform_admin"}), is_staff=True, mfa=True, auth_time=2**31)
    with pytest.raises(Forbidden):
        c.approvals.decide(staff, cid, 1, h, "approve")  # AdvisorGro staff never approve for a firm


def test_drafter_cannot_approve_own_work(c, world):
    import time

    from advisorgro.security.principal import Principal
    both = Principal(user_id=world.users["cco"], tenant_id=world.tenant, roles=frozenset({"marketing", "cco"}), mfa=True, auth_time=int(time.time()))
    camp = new_campaign(c, world)
    res = c.campaigns.submit_for_review(both, camp["id"])  # one person holding both roles drafts it ...
    with pytest.raises(Forbidden, match="drafter"):
        c.approvals.decide(both, camp["id"], 1, res["content_hash"], "approve")  # ... and may not approve it


def test_deputy_can_approve_only_inside_delegation(c, world):
    from datetime import timedelta

    from advisorgro.core.clock import utcnow
    from advisorgro.db.models import Delegation, Membership, User
    cid, res = submitted(c, world)
    with session_scope(c.sm, system=True) as s:
        u = User(email="deputy@merrow.test")
        s.add(u)
        s.flush()
        s.add(Membership(tenant_id=world.tenant, user_id=u.id, role="advisor"))
        world.users["deputy"] = u.id
    import time

    from advisorgro.security.principal import Principal
    dep = Principal(user_id=world.users["deputy"], tenant_id=world.tenant, roles=frozenset({"advisor"}), mfa=True, auth_time=int(time.time()))
    with pytest.raises(Forbidden):
        c.approvals.decide(dep, cid, 1, res["content_hash"], "approve")
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        s.add(Delegation(approver_id=world.users["cco"], deputy_id=u.id, starts_at=utcnow() - timedelta(days=1), ends_at=utcnow() + timedelta(days=1)))
    assert c.approvals.decide(dep, cid, 1, res["content_hash"], "approve")["state"] == "Approved"


def test_approvals_are_insert_only(c, world):
    cid, res = submitted(c, world)
    c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "request_changes", "tone")
    with pytest.raises(ImmutableRecord):
        with session_scope(c.sm, tenant_id=world.tenant) as s:
            s.execute(select(Approval)).scalar_one().decision = "approve"
    with pytest.raises(ImmutableRecord):
        with session_scope(c.sm, tenant_id=world.tenant) as s:
            s.delete(s.execute(select(Approval)).scalar_one())


def test_edit_makes_new_version_and_old_approval_cannot_publish(c, world):
    cid, res = submitted(c, world)
    c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "approve")
    # edit before publish: new version restarts at pre-check; the v1 approval is for v1 only
    res2 = c.campaigns.submit_for_review(world.principal("marketing"), cid, creative_patch={"google": {"headlines": ["Changed headline one", "Changed headline two", "Changed three"],
                                                                                                         "descriptions": ["Book a no-obligation call.", "Talk with a fiduciary."]}}, note="new angle")
    assert res2["version"] == 2 and res2["content_hash"] != res["content_hash"]
    with pytest.raises(Conflict):
        c.publisher.publish(cid)  # campaign is PendingCCO again: nothing goes out
    with pytest.raises(ApprovalMismatch):
        c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "approve")


def test_read_back_mismatch_pauses_and_raises_incident(c, world):
    cid, res = submitted(c, world)
    c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "approve")
    c.bus.drain()
    plat = c.connectors["google"]
    pid = next(iter(plat.objects))
    plat.tamper(pid, "Guaranteed 12% returns")  # drift: someone edited the ad in the platform UI
    assert c.publisher.verify_live(cid) is False
    assert c.campaigns.get(world.principal("cco"), cid)["state"] == "Paused"
    assert plat.objects[pid]["status"] == "PAUSED"
    with session_scope(c.sm, system=True) as s:
        assert s.execute(select(Incident).where(Incident.kind == "readback_mismatch")).scalar_one()


def test_publish_blocked_by_kill_switch_and_budget(c, world):
    cid, res = submitted(c, world)
    c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "approve")
    with session_scope(c.sm, system=True) as s:
        s.add(KillSwitch(scope="tenant", scope_id=world.tenant, reason="incident drill"))
    with pytest.raises(KillSwitchActive):
        c.publisher.publish(cid)
    assert c.connectors["google"].calls["publish"] == 0  # nothing reached the platform


def test_publish_retry_is_idempotent(c, world):
    cid, res = submitted(c, world)
    c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "approve")
    plat = c.connectors["google"]
    plat.fail_publish = True
    from advisorgro.core.errors import ProviderUnavailable
    with pytest.raises(ProviderUnavailable):
        c.publisher.publish(cid)
    plat.fail_publish = False
    assert c.publisher.publish(cid) == "Live"
    assert len(plat.objects) == 1  # one campaign, however many attempts


def test_fact_guard_blocks_invented_claims(c, world):
    bad = json.dumps({"headlines": ["Our 12% annual returns", "Award-winning advisors", "Top ranked since 1998"], "descriptions": ["We beat the market every year.", "Book a call today."]})
    c.gateway.providers = [FakeProvider(handlers={"TASK:google_ads": lambda p: bad})]
    camp = new_campaign(c, world)
    res = c.campaigns.submit_for_review(world.principal("marketing"), camp["id"])
    assert res["ok"] is False and res["state"] == "Draft" and res["unsupported_claims"]
    assert c.campaigns.get(world.principal("cco"), camp["id"])["current_version"] == 0  # nothing reached the CCO


def test_overlong_headline_gets_one_repair_then_fails_cleanly(c, world):
    long = json.dumps({"headlines": ["x" * 40, "y", "z"], "descriptions": ["a" * 20, "b" * 20]})
    c.gateway.providers = [FakeProvider(handlers={"TASK:google_ads": lambda p: long})]
    camp = new_campaign(c, world)
    res = c.campaigns.submit_for_review(world.principal("marketing"), camp["id"])
    assert res["ok"] is False and "invalid" in res["error"]
    assert sum("TASK:google_ads" in p for p in c.gateway.providers[0].calls) == 2  # original + exactly one repair


def test_true_but_regulated_claim_passes_fact_guard_and_is_flagged_to_cco(c, world):
    from advisorgro.engine.gro import knowledge
    from advisorgro.llm.gateway import LLMGateway
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        knowledge.add(s, world.firm_id, "fact", "Clients earned 12% annual returns last year.")
    risky = json.dumps({"headlines": ["Fiduciary advisor", "Local retirement help", "Book a call"],
                        "descriptions": ["Our clients earned 12% annual returns last year.", "Call us today."]})
    c.gateway = LLMGateway([FakeProvider(handlers={"TASK:google_ads": lambda p: risky, "TASK:compliance_review": lambda p: "not json"})])
    camp = new_campaign(c, world)
    res = c.campaigns.submit_for_review(world.principal("marketing"), camp["id"])
    assert res["ok"] and res["precheck"] == "flags"  # reaches the CCO, with the flag, because only a human decides
    pre = c.campaigns.get(world.principal("cco"), camp["id"])["version"]["precheck"]
    assert any(m["category"] == "performance_claims" for m in pre["material"])
    assert pre["ai_review"] == "missing" and "rules-only" in pre["note"]  # reviewer model failed: flagged, not silently skipped


def test_state_machine_rejects_shortcuts(c, world):
    camp = new_campaign(c, world)
    with session_scope(c.sm, tenant_id=world.tenant) as s:
        row = s.get(Campaign, camp["id"])
        for bad in ("Live", "Publishing", "Approved", "PendingCCO"):
            with pytest.raises(IllegalTransition):
                transition(row, bad)


def test_tenant_isolation(c, world):
    from .conftest import World
    other = World(c)
    cid, _ = submitted(c, world)
    with pytest.raises(NotFound):
        c.campaigns.get(other.principal("cco"), cid)
    assert c.campaigns.list(other.principal("cco")) == []
    with pytest.raises(Forbidden):
        c.campaigns.list(world.principal("cco").__class__(user_id="x", tenant_id=world.tenant, roles=frozenset()))


def test_marketing_can_only_request_pause(c, world):
    cid, res = submitted(c, world)
    c.approvals.decide(world.principal("cco"), cid, 1, res["content_hash"], "approve")
    c.bus.drain()
    assert c.campaigns.pause(world.principal("marketing"), cid, "looks off")["requested"] is True
    assert c.campaigns.get(world.principal("cco"), cid)["state"] == "Live"
    assert c.campaigns.pause(world.principal("firm_admin"), cid, "pausing")["state"] == "Paused"
