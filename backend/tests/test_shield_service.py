from sqlalchemy import select

from advisorgro.db.models import GroupResult, Outbox, ReviewItem, Run
from advisorgro.db.session import session_scope

FORM = {"website": "merrowlane.com", "email": "ceo@merrowlane.com", "area": "Austin, Texas", "firm_name": "Merrow Lane Wealth", "consent": True}


def run_pipeline(c, form):
    code, body = c.shield.submit(form)
    assert code == 202, body
    c.bus.drain()
    return body["id"]


def test_free_audit_end_to_end_releases_and_emails(c):
    rid = run_pipeline(c, FORM)
    view = c.shield.view(rid)
    assert view["status"] == "sent" and view["report"]["score"]["total"] > 0
    assert len(c.messaging.emails) == 1 and "#report=" in c.messaging.emails[0]["text"]
    token = c.messaging.emails[0]["text"].split("#report=")[1].split()[0]
    assert c.shield.open_report(token)["report"]["domain"] == "merrowlane.com"


def test_report_link_is_the_credential(c):
    run_pipeline(c, FORM)
    import pytest

    from advisorgro.core.errors import NotFound
    with pytest.raises(NotFound):
        c.shield.open_report("x" * 43)


def test_held_compliance_goes_to_review_and_is_not_emailed(c):
    rid = run_pipeline(c, {**FORM, "website": "heldfirm.com", "email": "a@heldfirm.com"})
    assert c.shield.view(rid)["status"] == "pending_review"
    assert c.messaging.emails == []
    rep = c.shield.view(rid)["report"]
    assert rep["preliminary"] is True
    g5 = next(g for g in rep["groups"] if g["id"] == "g05_compliance") if "id" in rep["groups"][0] else None
    assert "performance claim" not in str(rep).lower()  # held detail never reaches the prospect
    with session_scope(c.sm, system=True) as s:
        assert s.execute(select(ReviewItem)).scalar_one().assigned_role == "reviewer"
    _ = g5


def test_unverified_compliance_is_not_a_clean_scan(c):
    rid = run_pipeline(c, {**FORM, "website": "nog5firm.com", "email": "a@nog5firm.com"})
    assert c.shield.view(rid)["status"] == "pending_review"
    with session_scope(c.sm, system=True) as s:
        assert s.get(Run, rid).release_status == "compliance_unverified"


def test_reviewer_release_sends_report(c):
    from advisorgro.security.principal import Principal
    rid = run_pipeline(c, {**FORM, "website": "heldfirm.com", "email": "a@heldfirm.com"})
    rev = Principal(user_id="rev-1", tenant_id=None, roles=frozenset({"reviewer"}), is_staff=True)
    assert [q["run_id"] for q in c.shield.review_queue(rev)] == [rid]
    c.shield.decide_review(rev, rid, "release")
    c.bus.drain()
    assert c.shield.view(rid)["status"] == "sent" and len(c.messaging.emails) == 1


def test_a_group_that_explodes_or_is_late_is_unverified_not_a_failed_run(c):
    rid = run_pipeline(c, {**FORM, "website": "boomslow.com", "email": "a@boomslow.com"})  # g03 raises; also slow
    with session_scope(c.sm, system=True) as s:
        run = s.get(Run, rid)
        g3 = s.execute(select(GroupResult).where(GroupResult.run_id == rid, GroupResult.group_key == "g03_ai_visibility")).scalar_one()
        assert run.state in ("Delivered", "InReview", "Released") and run.partial
        assert g3.status == "unverified"


def test_honeypot_validation_and_duplicates(c):
    assert c.shield.submit({**FORM, "hp": "bot"})[1]["id"] == "0" * 32
    code, body = c.shield.submit({**FORM, "email": "nope", "consent": False})
    assert code == 400 and {"email", "consent"} <= set(body["errors"])
    rid = run_pipeline(c, FORM)
    code, body = c.shield.submit(FORM)  # same person asks again: gets the same audit
    assert code == 200 and body["duplicate"] and body["id"] == rid
    code, body = c.shield.submit({**FORM, "email": "other@x.com"})  # someone else: never gets the id
    assert code == 200 and body["duplicate"] and "id" not in body


def test_outbox_is_idempotent_and_dead_letters(c):
    run_pipeline(c, FORM)
    n_before = len(c.messaging.emails)
    with session_scope(c.sm, system=True) as s:
        for ob in s.execute(select(Outbox)).scalars():
            ob.published_at = None  # force redelivery of everything
    c.bus.drain()
    assert len(c.messaging.emails) == n_before  # processed_events stops duplicate sends


def test_free_audit_is_once_per_firm_ever_then_shield(c):
    from datetime import timedelta

    from advisorgro.core.clock import utcnow
    rid = run_pipeline(c, FORM)
    with session_scope(c.sm, system=True) as s:  # a year later it is still used up: there is no renewal window
        s.get(Run, rid).created_at = utcnow() - timedelta(days=400)
    code, body = c.shield.submit({**FORM, "email": "someone.else@merrowlane.com"})
    assert code == 200 and body["duplicate"] and "id" not in body and "Shield" in body["message"]
    code, body = c.shield.submit(FORM)  # the same person only gets their own audit back
    assert body["id"] == rid and "Shield" in body["message"]
    # the firm is identified by its CRD too: a new domain with the same CRD does not get a second free audit
    first = {**FORM, "website": "crdfirm.com", "email": "a@crdfirm.com", "crd": "123456"}
    run_pipeline(c, first)
    code, body = c.shield.submit({**first, "website": "crdfirm-new-site.com", "email": "b@crdfirm.com"})
    assert code == 200 and body["duplicate"] and "id" not in body


def test_a_failed_run_does_not_use_up_the_free_audit(c):
    rid = run_pipeline(c, {**FORM, "website": "retryfirm.com", "email": "a@retryfirm.com"})
    with session_scope(c.sm, system=True) as s:
        s.get(Run, rid).state = "Failed"  # our side failed
    code, body = c.shield.submit({**FORM, "website": "retryfirm.com", "email": "a@retryfirm.com"})
    assert code == 202 and body["id"] != rid


def test_free_audit_is_one_time_per_firm_forever(c):
    from datetime import timedelta

    from advisorgro.core.clock import utcnow
    rid = run_pipeline(c, FORM)
    with session_scope(c.sm, system=True) as s:  # a year later it is still the same single free audit
        s.get(Run, rid).created_at = utcnow() - timedelta(days=400)
    code, body = c.shield.submit({**FORM, "email": "someone.else@merrowlane.com"})
    assert code == 200 and body["duplicate"] and "id" not in body and "Shield" in body["message"]
    code, body = c.shield.submit(FORM)  # the original requester can still open it
    assert code == 200 and body["duplicate"] and body["id"] == rid
    code, body = c.shield.submit({**FORM, "website": "www.MerrowLane.com/about"})  # www / case / path do not make it a new firm
    assert body["duplicate"] is True


def test_monitoring_needs_a_shield_subscription(c, world):
    import pytest

    from advisorgro.core.errors import SubscriptionRequired
    from advisorgro.db.models import Tenant
    with session_scope(c.sm, system=True) as s:
        s.get(Tenant, world.tenant).plan = "audit"  # free-audit-only customer
    with pytest.raises(SubscriptionRequired):
        c.shield.enroll(world.principal("firm_admin"), world.firm_id)
    with pytest.raises(SubscriptionRequired):
        c.shield.request_portfolio(world.principal("firm_admin"), [world.firm_id])
    with session_scope(c.sm, system=True) as s:
        s.get(Tenant, world.tenant).plan = "shield"
    c.shield.enroll(world.principal("firm_admin"), world.firm_id)
    with session_scope(c.sm, system=True) as s:  # subscription lapses: scheduled runs stop
        s.get(Tenant, world.tenant).plan = "audit"
    assert c.shield.schedule_due() == 0


def test_same_firm_by_crd_cannot_get_a_second_free_audit_under_another_domain(c):
    run_pipeline(c, {**FORM, "crd": "123456"})
    code, body = c.shield.submit({**FORM, "website": "newbrand-site.com", "email": "x@newbrand-site.com", "crd": "123456"})
    assert code == 200 and body["duplicate"] and "id" not in body
