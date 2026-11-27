"""End-to-end smoke test against a RUNNING API with fake providers (AG_USE_FAKES=true). Prints PASS/FAIL per step and
exits non-zero on any failure. Safe to re-run: every run seeds nothing and uses fresh emails/domains.

    # terminal 1 (backend folder): python ../scripts/seed_dev.py   then   uvicorn advisorgro.api.main:app_factory --factory --port 8000
    # terminal 2 (backend folder): python ../scripts/smoke_test.py [--base http://localhost:8000]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import uuid

import httpx

from advisorgro.core.config import get_settings
from advisorgro.security import webhooks
from advisorgro.security.tokens import mint_dev_token

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8000")
base = ap.parse_args().base.rstrip("/")
seed = json.loads((pathlib.Path(__file__).resolve().parent.parent / ".dev" / "seed.json").read_text())
cfg = get_settings()
fails = 0
http = httpx.Client(base_url=base, timeout=30)


def tok(role: str, **kw) -> dict:
    now = int(time.time())
    c = {"sub": seed["users"][role], "roles": [role], "tenant_id": seed["tenant"], "auth_time": now, "amr": ["pwd", "mfa"], "exp": now + 600, **kw}
    return {"Authorization": "Bearer " + mint_dev_token(cfg, **c)}


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))


def wait(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.5)
    return None


uid = uuid.uuid4().hex[:8]
r = http.get("/readyz")
check("API is up and using fakes", r.status_code == 200 and r.json().get("fakes") is True, r.text)

# 1. free audit (clean)
r = http.post("/api/audit", json={"website": f"clean-{uid}.com", "email": f"a-{uid}@example.com", "area": "Austin, Texas", "consent": True})
check("free audit accepted with 202", r.status_code == 202 and r.json().get("ok"), r.text)
rid = r.json().get("id", "")
poll = wait(lambda: (lambda d: d if d.get("status") in ("sent", "released", "pending_review", "failed") else None)(http.get(f"/api/audit/{rid}").json()))
check("clean audit completes and is released", bool(poll) and poll["status"] in ("sent", "released") and poll["report"]["score"]["total"] > 0, str(poll)[:200])

# 2. audit held for review; detail never reaches the prospect
r = http.post("/api/audit", json={"website": f"held-{uid}.com", "email": f"b-{uid}@example.com", "area": "Dallas", "consent": True})
hid = r.json().get("id", "")
held = wait(lambda: (lambda d: d if d.get("status") in ("pending_review", "sent", "failed") else None)(http.get(f"/api/audit/{hid}").json()))
check("audit with a material compliance flag is held for a person", bool(held) and held["status"] == "pending_review", str(held)[:200])
check("held compliance detail is not in the prospect's report", "12% annual returns" not in json.dumps(held or {}))

# 3. validation, duplicates, honeypot
r = http.post("/api/audit", json={"website": "nope", "email": "bad", "area": "", "consent": False})
check("bad form returns 400 with field errors", r.status_code == 400 and {"email", "area", "consent"} <= set(r.json().get("errors", {})), r.text)
r = http.post("/api/audit", json={"website": f"clean-{uid}.com", "email": f"a-{uid}@example.com", "area": "Austin, Texas", "consent": True})
check("same person asking again gets the same audit", r.status_code == 200 and r.json().get("duplicate") and r.json().get("id") == rid, r.text)
r = http.post("/api/audit", json={"website": f"clean-{uid}.com", "email": f"someone-else-{uid}@example.com", "area": "Austin, Texas", "consent": True})
check("free audit is ONE per firm: anyone else gets no audit and is pointed to Shield", r.status_code == 200 and r.json().get("duplicate") and "id" not in r.json() and "Shield" in r.json().get("message", ""), r.text)
r = http.post("/api/audit", json={"website": f"clean-{uid}.com", "email": f"other-{uid}@example.com", "area": "Austin, Texas", "consent": True})
check("free audit is ONE-TIME per firm: a different person gets no id and is pointed to Shield", r.status_code == 200 and r.json().get("duplicate") and "id" not in r.json() and "Shield" in r.json().get("message", ""), r.text)
r = http.post("/api/audit", json={"website": f"www.CLEAN-{uid}.com/about", "email": f"third-{uid}@example.com", "area": "Austin", "consent": True})
check("www / case / path variants of the same site do not get a second free audit", r.json().get("duplicate") is True, r.text)
check("honeypot is silently accepted", http.post("/api/audit", json={"hp": "x"}).json().get("id") == "0" * 32)

# 4. campaign: draft -> CCO approval -> live (read-back verified) -> page -> lead -> booking
r = http.post("/v1/campaigns", headers=tok("marketing"), json={"firm_id": seed["firm_id"], "offer_id": "retirement_readiness", "platform": "google", "monthly_budget_cents": 300000, "budget_ceiling_cents": 500000})
check("marketing creates a campaign draft", r.status_code == 201, r.text)
cid = r.json().get("id", "")
r = http.post(f"/v1/campaigns/{cid}/submit", headers=tok("marketing"), json={})
check("generate -> fact guard -> pre-check -> render reaches PendingCCO", r.status_code == 200 and r.json().get("state") == "PendingCCO", r.text)
h = r.json().get("content_hash", "")
body = {"version": 1, "content_hash": h, "decision": "approve"}
check("marketing cannot approve (403)", http.post(f"/v1/campaigns/{cid}/decision", headers=tok("marketing"), json=body).status_code == 403)
check("stale sign-in cannot approve (step_up_required)", http.post(f"/v1/campaigns/{cid}/decision", headers=tok("cco", auth_time=int(time.time()) - 7200), json=body).json().get("error") == "step_up_required")
check("wrong content hash is refused", http.post(f"/v1/campaigns/{cid}/decision", headers=tok("cco"), json={**body, "content_hash": "0" * 64}).json().get("error") == "approval_mismatch")
r = http.post(f"/v1/campaigns/{cid}/decision", headers=tok("cco"), json=body)
check("CCO approves with fresh MFA", r.status_code == 200 and r.json().get("state") == "Approved", r.text)
live = wait(lambda: (lambda d: d if d.get("state") == "Live" else None)(http.get(f"/v1/campaigns/{cid}", headers=tok("cco")).json()))
check("publisher goes Live after read-back verification", bool(live), "state never became Live")
page = http.get(f"/p/{cid}/1")
check("landing page is served with disclosures and a consent box", page.status_code == 200 and "Past performance" in page.text and 'name="consent"' in page.text)
lead = {"campaign_id": cid, "page_version": 1, "name": "Pat Jones", "email": f"pat-{uid}@example.com", "phone": "(512) 555-0100", "consent": True, "gclid": "G123"}
check("lead without consent is rejected", http.post("/v1/leads", json={**lead, "consent": False}).status_code == 400)
check("lead accepted (202)", http.post("/v1/leads", json=lead).status_code == 202)
check("duplicate lead is recognised", http.post("/v1/leads", json=lead).json().get("duplicate") is True)
sla = wait(lambda: (lambda d: d if d.get("first_contacts", 0) >= 1 else None)(http.get("/v1/leads/sla", headers=tok("cco")).json()))
check("first reply sent within the 90 s SLA", bool(sla) and sla["within_sla"] >= 1, str(sla))
cco_view = http.get("/v1/leads", headers=tok("cco")).json()
mkt_view = http.get("/v1/leads", headers=tok("marketing")).json()
check("contact details masked for marketing, visible for CCO", cco_view and mkt_view and "@" in cco_view[0]["email"] and "***" in mkt_view[0]["email"])
raw = json.dumps({"payload": {"uid": f"bk-{uid}", "attendees": [{"email": lead["email"]}]}}).encode()
ts, sig = webhooks.sign(cfg.webhook_secret_calendar, raw)
check("booking webhook with a bad signature is refused", http.post("/v1/webhooks/calendar", content=raw, headers={"x-ag-timestamp": ts, "x-ag-signature": "bad"}).status_code == 401)
check("booking webhook with a good signature is accepted", http.post("/v1/webhooks/calendar", content=raw, headers={"x-ag-timestamp": ts, "x-ag-signature": sig}).json().get("ok") is True)

# 5. isolation and activity log
other = {"Authorization": "Bearer " + mint_dev_token(cfg, sub=seed["users"]["cco"], roles=["cco"], tenant_id="00000000-0000-0000-0000-0000000000ff", amr=["mfa"], exp=int(time.time()) + 600)}
check("another tenant gets 404 for this campaign", http.get(f"/v1/campaigns/{cid}", headers=other).status_code == 404)
acts = http.get("/v1/activity", headers=tok("cco")).json()
check("activity log recorded the approval and go-live", any(a["action"] == "campaign.approve" for a in acts) and any(a["action"] == "publish.live" for a in acts))

# 6. monitoring is a subscription, not another free audit
adm = {"Authorization": "Bearer " + mint_dev_token(cfg, sub=seed["users"]["audit_admin"], roles=["firm_admin"], tenant_id=seed["audit_only"]["tenant"], amr=["mfa"], exp=int(time.time()) + 600)}
r = http.post("/v1/shield/enroll", headers=adm, json={"firm_id": seed["audit_only"]["firm_id"]})
check("free-audit-only customer cannot enrol in monitoring (402 subscription_required)", r.status_code == 402 and r.json().get("error") == "subscription_required", r.text)
check("Shield subscriber can enrol a firm", http.post("/v1/shield/enroll", headers=tok("firm_admin"), json={"firm_id": seed["firm_id"]}).status_code == 200)

# 7. kill switch pauses on the next verification (daily job); here we just prove the control is reachable and scoped
check("firm admin cannot set the global kill switch", http.post("/v1/admin/kill-switch", headers=tok("firm_admin"), json={"scope": "global", "reason": "x"}).status_code == 403)
print("\n" + ("ALL PASSED" if not fails else f"{fails} FAILED"))
sys.exit(1 if fails else 0)
