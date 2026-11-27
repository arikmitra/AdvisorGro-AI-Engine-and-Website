import json
import time

import pytest
from fastapi.testclient import TestClient

from advisorgro.api.main import create_app
from advisorgro.db.session import session_scope
from advisorgro.security import webhooks
from advisorgro.security.tokens import mint_dev_token

from .conftest import World
from .test_gro import new_campaign

FORM = {"website": "merrowlane.com", "email": "ceo@merrowlane.com", "area": "Austin, Texas", "consent": True}


@pytest.fixture
def client(c):
    return TestClient(create_app(c))


def tok(c, w, role, **kw):
    return {"Authorization": "Bearer " + mint_dev_token(c.settings, sub=w.users[role], tenant_id=w.tenant, roles=[role], **kw)}


def test_free_audit_contract_over_http(c, client):
    r = client.post("/api/audit", json=FORM, headers={"Origin": "http://localhost:8000"})
    assert r.status_code == 202 and r.json()["ok"]
    assert r.headers["x-content-type-options"] == "nosniff" and "x-correlation-id" in r.headers
    rid = r.json()["id"]
    poll = client.get(f"/api/audit/{rid}").json()  # background task already ran (single-node mode)
    assert poll["status"] == "sent" and poll["report"]["score"]["total"] > 0
    token = c.messaging.emails[0]["text"].split("#report=")[1].split()[0]
    assert client.get(f"/api/report/{token}").json()["published"] is True
    assert client.get("/api/report/" + "z" * 43).status_code == 404
    assert client.get("/v1/audits/not-a-real-id").status_code == 404


def test_edge_protections(c, client):
    assert client.post("/api/audit", content=b"x" * 20000, headers={"content-type": "application/json"}).status_code == 413
    pre = client.options("/api/audit", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers
    ok = client.options("/api/audit", headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "POST"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:8000"
    assert client.post("/api/audit", json={**FORM, "hp": "bot"}).json()["id"] == "0" * 32


def test_rate_limit(c):
    c.settings.rate_limit_per_min = 5
    cl = TestClient(create_app(c))
    codes = [cl.get("/api/audit/x").status_code for _ in range(8)]
    assert 429 in codes


def test_idempotency_key_returns_same_response_and_rejects_changed_body(c, client):
    h = {"Idempotency-Key": "abc-123"}
    a = client.post("/v1/audits", json=FORM, headers=h)
    b = client.post("/v1/audits", json=FORM, headers=h)
    assert a.json() == b.json() and len(c.messaging.emails) == 1
    assert client.post("/v1/audits", json={**FORM, "area": "Dallas"}, headers=h).status_code == 409


def test_auth_required_and_tenant_isolation(c, client, world):
    assert client.get("/v1/campaigns").status_code == 401
    assert client.get("/v1/campaigns", headers={"Authorization": "Bearer junk"}).status_code == 401
    camp = new_campaign(c, world)
    other = World(c)
    assert client.get(f"/v1/campaigns/{camp['id']}", headers=tok(c, other, "cco")).status_code == 404  # not 403: no existence leak
    assert client.get(f"/v1/campaigns/{camp['id']}", headers=tok(c, world, "cco")).status_code == 200
    forged = {"Authorization": "Bearer " + mint_dev_token(c.settings, sub=world.users["cco"], tenant_id=other.tenant, roles=["cco"])}
    assert client.get(f"/v1/campaigns/{camp['id']}", headers=forged).status_code == 404  # tenant is in the signed token only


def test_portal_approval_flow_and_role_limits(c, client, world):
    camp = new_campaign(c, world)
    r = client.post(f"/v1/campaigns/{camp['id']}/submit", json={}, headers=tok(c, world, "marketing"))
    assert r.status_code == 200 and r.json()["state"] == "PendingCCO"
    h = r.json()["content_hash"]
    body = {"version": 1, "content_hash": h, "decision": "approve"}
    assert client.post(f"/v1/campaigns/{camp['id']}/decision", json=body, headers=tok(c, world, "marketing")).status_code == 403
    stale = {"Authorization": "Bearer " + mint_dev_token(c.settings, sub=world.users["cco"], tenant_id=world.tenant, roles=["cco"], auth_time=int(time.time()) - 7200)}
    assert client.post(f"/v1/campaigns/{camp['id']}/decision", json=body, headers=stale).json()["error"] == "step_up_required"
    ok = client.post(f"/v1/campaigns/{camp['id']}/decision", json=body, headers=tok(c, world, "cco"))
    assert ok.status_code == 200 and ok.json()["state"] == "Approved"
    c.bus.drain()
    assert client.get(f"/v1/campaigns/{camp['id']}", headers=tok(c, world, "read_only")).json()["state"] == "Live"
    acts = client.get("/v1/activity", headers=tok(c, world, "cco")).json()
    assert any(a["action"] == "campaign.approve" for a in acts) and any(a["action"] == "publish.live" for a in acts)
    assert client.get("/v1/activity", headers=tok(c, world, "marketing")).status_code == 403


def go_live(c, client, world):
    camp = new_campaign(c, world)
    h = client.post(f"/v1/campaigns/{camp['id']}/submit", json={}, headers=tok(c, world, "marketing")).json()["content_hash"]
    client.post(f"/v1/campaigns/{camp['id']}/decision", json={"version": 1, "content_hash": h, "decision": "approve"}, headers=tok(c, world, "cco"))
    c.bus.drain()
    return camp["id"]


def test_lead_capture_followup_sla_optout_booking_and_masking(c, client, world):
    cid = go_live(c, client, world)
    page = client.get(f"/p/{cid}/1")
    assert page.status_code == 200 and "Past performance" in page.text and 'name="consent"' in page.text
    lead = {"campaign_id": cid, "page_version": 1, "name": "Pat Jones", "email": "pat@example.com", "phone": "(512) 555-0100", "consent": True, "gclid": "G123"}
    assert client.post("/v1/leads", json={**lead, "consent": False}).status_code == 400
    assert client.post("/v1/leads", json={**lead, "campaign_id": "nope"}).status_code == 404
    assert client.post("/v1/leads", json=lead).status_code == 202
    assert client.post("/v1/leads", json=lead).json().get("duplicate") is True
    sent = [e for e in c.messaging.emails if e["tag"] == "lead-first"]
    assert len(sent) == 1 and sent[0]["to"] == "pat@example.com"  # sent from the approved template
    assert c.leads.sla_report(world.tenant) == {"first_contacts": 1, "within_sla": 1}
    # masking by role
    cco = client.get("/v1/leads", headers=tok(c, world, "cco")).json()[0]
    mkt = client.get("/v1/leads", headers=tok(c, world, "marketing")).json()[0]
    assert cco["email"] == "pat@example.com" and mkt["email"] == "p***@example.com" and mkt["phone"].startswith("***")
    assert client.get("/v1/leads", headers=tok(c, world, "advisor")).json() == []  # advisors see their own leads only
    # booking webhook: bad signature refused, good one books, replay is harmless, conversion goes to the platform once
    body = json.dumps({"payload": {"uid": "bk1", "attendees": [{"email": "pat@example.com"}]}}).encode()
    ts, sig = webhooks.sign(c.settings.webhook_secret_calendar, body)
    assert client.post("/v1/webhooks/calendar", content=body, headers={"x-ag-timestamp": ts, "x-ag-signature": "bad"}).status_code == 401
    for _ in range(2):
        assert client.post("/v1/webhooks/calendar", content=body, headers={"x-ag-timestamp": ts, "x-ag-signature": sig}).json()["ok"]
    assert len(c.connectors["google"].conversions) == 1
    # a stale timestamp is refused
    old_ts, old_sig = webhooks.sign(c.settings.webhook_secret_calendar, body, ts=int(time.time()) - 3600)
    assert client.post("/v1/webhooks/calendar", content=body, headers={"x-ag-timestamp": old_ts, "x-ag-signature": old_sig}).status_code == 401


def test_sms_stop_ends_sequences(c, client, world):
    from datetime import timedelta

    from advisorgro.core.clock import utcnow
    cid = go_live(c, client, world)
    client.post("/v1/leads", json={"campaign_id": cid, "page_version": 1, "name": "Sam", "email": "sam@example.com", "phone": "5125550111", "consent": True})
    body = json.dumps({"from": "+1 512 555 0111", "body": "STOP"}).encode()
    ts, sig = webhooks.sign(c.settings.webhook_secret_sms, body)
    assert client.post("/v1/webhooks/sms", content=body, headers={"x-ag-timestamp": ts, "x-ag-signature": sig}).json()["opted_out"] == 1
    n = len(c.messaging.emails)
    assert c.leads.run_due_sequences(utcnow() + timedelta(days=120)) == 0 and len(c.messaging.emails) == n


def test_kill_switch_and_scopes(c, client, world):
    cid = go_live(c, client, world)
    assert client.post("/v1/admin/kill-switch", json={"scope": "global", "reason": "x"}, headers=tok(c, world, "firm_admin")).status_code == 403
    assert client.post("/v1/admin/kill-switch", json={"scope": "tenant", "reason": "drill"}, headers=tok(c, world, "firm_admin")).status_code == 200
    assert c.publisher.verify_live(cid) is False  # the daily verification pauses a live campaign under a kill switch
    assert client.get(f"/v1/campaigns/{cid}", headers=tok(c, world, "cco")).json()["state"] == "Paused"


def test_metrics_endpoint_is_off_by_default_and_token_gated(c, client):
    assert client.get("/internal/metrics").status_code == 404
    c.settings.metrics_token = "m-token"
    assert client.get("/internal/metrics").status_code == 404
    r = client.get("/internal/metrics", headers={"Authorization": "Bearer m-token"})
    assert r.status_code == 200 and "ag_outbox_dead_letters 0" in r.text and "ag_campaigns_live 0" in r.text


def test_approval_publishes_without_a_separate_worker_in_single_process_mode(c, client, world):
    camp = new_campaign(c, world)
    h = client.post(f"/v1/campaigns/{camp['id']}/submit", json={}, headers=tok(c, world, "marketing")).json()["content_hash"]
    r = client.post(f"/v1/campaigns/{camp['id']}/decision", json={"version": 1, "content_hash": h, "decision": "approve"}, headers=tok(c, world, "cco"))
    assert r.status_code == 200  # no manual bus.drain(): the background task delivered CampaignApproved
    assert client.get(f"/v1/campaigns/{camp['id']}", headers=tok(c, world, "cco")).json()["state"] == "Live"


def test_enroll_without_subscription_is_402_over_http(c, client, world):
    from advisorgro.db.models import Tenant
    with session_scope(c.sm, system=True) as s:
        s.get(Tenant, world.tenant).plan = "audit"
    r = client.post("/v1/shield/enroll", json={"firm_id": world.firm_id}, headers=tok(c, world, "firm_admin"))
    assert r.status_code == 402 and r.json()["error"] == "subscription_required"
