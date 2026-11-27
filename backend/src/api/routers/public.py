"""Public endpoints. The `/api/*` paths keep the MVP contract the existing site client uses; `/v1/*` is the versioned surface."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from ...connectors.messaging import parse_calcom_booking
from ...container import Container
from ...core import idempotency
from ...core.errors import NotFound, Unauthorized
from ...db.models import Campaign
from ...db.session import session_scope
from ...engine.shield.mvp.intake import domain_of, normalize_url
from ...security import webhooks
from ..deps import get_c, public_guard, verify_turnstile

router = APIRouter()


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _idem(c: Container, key: str | None, scope: str, body: dict, fn):
    """Run fn() once per Idempotency-Key; a retry gets the first response back."""
    if not key:
        return fn()
    with session_scope(c.sm, system=True) as s:
        hit = idempotency.lookup(s, key[:200], scope, body)
    if hit:
        return hit
    status, resp = fn()
    with session_scope(c.sm, system=True) as s:
        idempotency.store(s, key[:200], scope, body, status, resp)
    return status, resp


def _bg(c: Container, status: int):
    """Respond first, then (single-node/dev) process the events. With a worker deployed this is off."""
    return BackgroundTask(c.bus.drain) if (c.settings.inline_worker and status == 202) else None


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/readyz")
def readyz(c: Container = Depends(get_c)):
    with session_scope(c.sm, system=True) as s:
        s.execute(select(1))
    return {"ok": True, "env": c.settings.env, "fakes": c.settings.use_fakes, "llm": c.gateway is not None, "email": c.messaging is not None or bool(c.settings.postmark_token)}


@router.get("/internal/metrics")
def metrics(request: Request, c: Container = Depends(get_c)):
    from fastapi.responses import PlainTextResponse
    tok = c.settings.metrics_token
    if not tok or request.headers.get("authorization") != f"Bearer {tok}":
        raise NotFound("not found")  # off unless configured; scraped from inside the network only
    from ...observability.metrics import render
    return PlainTextResponse(render(c))


@router.get("/dev/outbox")
def dev_outbox(c: Container = Depends(get_c)):
    """DEVELOPMENT ONLY (env=dev with fake providers): every email and SMS this process "sent". Also in objects/sent_messages.jsonl."""
    if not (c.settings.env == "dev" and c.messaging is not None):
        raise NotFound("not found")
    return {"emails": c.messaging.emails, "sms": c.messaging.sms}


@router.get("/api/status")
def site_status(c: Container = Depends(get_c)):
    return {"ok": True, "audits_open": True, "email_ready": True, "message": ""}


# ---------------------------------------------------------------------- free audit
@router.post("/v1/audits")
@router.post("/api/audit")
async def submit_audit(request: Request, c: Container = Depends(get_c), ip: str = Depends(public_guard), idempotency_key: str | None = Header(default=None)):
    body = await _json(request)
    if not verify_turnstile(c, body.get("turnstile_token"), ip):
        return JSONResponse({"ok": False, "message": "We could not verify you are human. Please try again."}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    status, resp = await run_in_threadpool(_idem, c, idempotency_key, "audit", body, lambda: c.shield.submit(body, ip_hash=ip))
    return JSONResponse(resp, status_code=status, background=_bg(c, status))


@router.get("/v1/audits/{run_id}")
@router.get("/api/audit/{run_id}")
def audit_status(run_id: str, c: Container = Depends(get_c), _: str = Depends(public_guard)):
    return c.shield.view(run_id)


@router.get("/v1/reports/{token}")
@router.get("/api/report/{token}")
def report(token: str, c: Container = Depends(get_c), ip: str = Depends(public_guard)):
    return c.shield.open_report(token, ip_hash=ip)


@router.post("/v1/shield/snapshot")
@router.post("/api/shield/snapshot")
async def snapshot(request: Request, c: Container = Depends(get_c), _: str = Depends(public_guard)):
    body = await _json(request)
    try:
        url = normalize_url(str(body.get("website") or body.get("url") or ""))
    except ValueError:
        return JSONResponse({"ok": False, "message": "Please enter your firm\u2019s website address, for example yourfirm.com."}, status_code=400)
    bad = c.shield_engine.site_check(domain_of(url))
    if bad:
        return JSONResponse({"ok": False, "message": bad}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    snap = await run_in_threadpool(c.shield.snapshot, url)
    return {"ok": True, **snap}


@router.post("/v1/contact")
async def contact(request: Request, c: Container = Depends(get_c), _: str = Depends(public_guard)):
    b = await _json(request)
    if str(b.get("hp") or "").strip():
        return JSONResponse({"ok": True}, status_code=202)
    email, msg = str(b.get("email") or "")[:254], str(b.get("message") or "")[:4000]
    if "@" not in email or not msg.strip():
        return JSONResponse({"ok": False, "errors": {"email": "Enter a valid email.", "message": "Tell us how we can help."}}, status_code=400)
    if c.settings.review_email:
        await c.email.send(c.settings.review_email, f"Contact form: {str(b.get('name') or '')[:80]}", f"From: {email}\n\n{msg}", tag="contact")
    return JSONResponse({"ok": True}, status_code=202)


# ---------------------------------------------------------------------- landing pages and leads
@router.get("/p/{campaign_id}/{version}", response_class=HTMLResponse)
def landing_page(campaign_id: str, version: int, c: Container = Depends(get_c)):
    with session_scope(c.sm, system=True) as s:
        camp = s.get(Campaign, campaign_id)
        if not camp or camp.live_version != version or camp.state != "Live":
            raise NotFound("page")
        tenant = camp.tenant_id
    html = c.pages.fetch(tenant, campaign_id, version)
    if html is None:
        raise NotFound("page")
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=60", "X-Robots-Tag": "noindex"})


@router.post("/v1/leads")
async def submit_lead(request: Request, c: Container = Depends(get_c), _: str = Depends(public_guard), idempotency_key: str | None = Header(default=None)):
    body = await _json(request)
    from starlette.concurrency import run_in_threadpool
    status, resp = await run_in_threadpool(_idem, c, idempotency_key, "lead", body, lambda: c.leads.submit(body))
    return JSONResponse(resp, status_code=status, background=_bg(c, status))


# ---------------------------------------------------------------------- webhooks (signature + timestamp)
async def _verified(request: Request, secret: str) -> dict:
    raw = await request.body()
    webhooks.verify(secret, raw, request.headers.get("x-ag-timestamp", ""), request.headers.get("x-ag-signature", ""))
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError as e:
        raise Unauthorized("bad body") from e


@router.post("/v1/webhooks/calendar")
async def wh_calendar(request: Request, c: Container = Depends(get_c)):
    b = parse_calcom_booking(await _verified(request, c.settings.webhook_secret_calendar))
    from starlette.concurrency import run_in_threadpool
    ok = await run_in_threadpool(c.leads.record_booking, external_id=b["external_id"], email=b["email"], lead_id=b["lead_id"])
    return JSONResponse({"ok": ok}, background=_bg(c, 202))


@router.post("/v1/webhooks/sms")
async def wh_sms(request: Request, c: Container = Depends(get_c)):
    b = await _verified(request, c.settings.webhook_secret_sms)
    from starlette.concurrency import run_in_threadpool
    return {"ok": True, "opted_out": await run_in_threadpool(c.leads.handle_sms_inbound, b)}
