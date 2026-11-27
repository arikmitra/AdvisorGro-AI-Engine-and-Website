"""Shield service: intake -> run -> record -> review gate -> release -> delivery.
One workflow for the three triggers (free audit, Shield monthly, portfolio batch) plus the daily probe."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from ...core.clock import utcnow
from ...core.errors import Forbidden, NotFound, SubscriptionRequired, ValidationFailed
from ...core.events import emit
from ...core.ids import PUBLIC_TENANT_ID, correlation_id_var, hash_token, new_id, new_report_token
from ...db.models import (
    AuditRequest,
    Consent,
    Finding,
    Firm,
    GroupResult,
    Report,
    ReviewItem,
    Run,
    ShieldEnrollment,
    Tenant,
    UsageEvent,
)
from ...db.session import session_scope
from ...security.activity import log_activity
from ...security.crypto import mask_email, normalise_email
from ...security.policy import require
from ...security.principal import Principal
from . import alerts
from .intake_form import PUBLIC_MESSAGE, validate_audit
from .mvp.report import build_reports
from .mvp.reportjson import safe_payload
from .mvp.shield import classify
from .orchestrator import run_groups
from .record import assemble, group_status

log = logging.getLogger(__name__)
TOKEN_TTL = timedelta(days=30)
STATUS = {"Queued": "queued", "Running": "running", "InReview": "pending_review", "Released": "released",
          "Delivered": "sent", "Failed": "failed"}


def _add_month(dt):
    y, m = divmod(dt.month, 12)
    return dt.replace(year=dt.year + y, month=m + 1, day=min(dt.day, 28))


def _incomplete_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r["status"] in ("not_run", "unverified"):
            r = {**r, "status": "unverified", "label": "Yet to be completed",
                 "detail": "Our team will add this to your completed report." if r["status"] == "not_run" else r["detail"]}
        out.append(r)
    return out


class ShieldService:
    def __init__(self, c):
        self.c = c
        self._snap: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------ intake (MVP contract: (status, body))
    def submit(self, payload: dict, *, ip_hash: str = "") -> tuple[int, dict]:
        s_ = self.c.settings
        if str(payload.get("hp") or "").strip():  # honeypot: pretend success
            return 202, {"ok": True, "id": "0" * 32, "message": PUBLIC_MESSAGE["queued"]}
        cl, errors = validate_audit(payload, self.c.shield_engine.site_check)
        if not payload.get("consent"):
            errors["consent"] = "Please confirm you agree to be contacted about your audit."
        if errors:
            return 400, {"ok": False, "errors": errors, "message": "Please check the highlighted fields."}
        now = utcnow()
        em = normalise_email(cl["email"])
        with session_scope(self.c.sm, tenant_id=PUBLIC_TENANT_ID) as s:
            busy = s.execute(select(func.count()).select_from(Run).where(Run.trigger == "free_audit", Run.created_at >= now - timedelta(days=1))).scalar_one()
            if busy >= s_.max_audits_per_day:
                return 503, {"ok": False, "message": "We are very busy right now. Please try again tomorrow or contact us."}
            # ONE free audit per firm, ever. The firm is identified by its domain (and its CRD when given). A run that
            # failed on our side does not use it up. After this, ongoing checks are a Shield subscription.
            prior = s.execute(select(Run).where(Run.trigger == "free_audit", Run.domain == cl["domain"], Run.state != "Failed")
                              .order_by(Run.created_at.desc()).limit(1)).scalar_one_or_none()
            if not prior and cl["crd"]:
                prior = s.execute(select(Run).join(AuditRequest, AuditRequest.run_id == Run.id).where(
                    Run.trigger == "free_audit", Run.state != "Failed", AuditRequest.crd == cl["crd"]).limit(1)).scalar_one_or_none()
            if prior:  # the same person sees their own audit again; nobody else gets it, or a second one
                mine = s.execute(select(AuditRequest.id).where(AuditRequest.run_id == prior.id, AuditRequest.email_hmac == self.c.crypto.keyed_hash(em))).first()
                if mine:
                    return 200, {"ok": True, "duplicate": True, "id": prior.id, "message": "You already used your free audit. Loading it now. To keep monitoring your firm, subscribe to Shield."}
                return 200, {"ok": True, "duplicate": True, "message": "The free audit has already been used for this firm. Check the inbox it was sent to. For ongoing monitoring, subscribe to Shield."}
            cr = Consent(text_id=str(payload.get("consent_text_id") or "audit-v1"), version=str(payload.get("consent_version") or "1"),
                         text=str(payload.get("consent_text") or "Contact me about my audit")[:1000], page=str(payload.get("page") or "")[:500], email_ok=True)
            s.add(cr)
            s.flush()
            run = Run(domain=cl["domain"], firm_name=cl["firm_name"], area=cl["area"], trigger="free_audit", priority=5,
                      period=now.strftime("%Y%m%d"), idempotency_key=f"free_audit:{cl['domain']}:{new_id()}",
                      correlation_id=correlation_id_var.get())
            s.add(run)
            s.flush()
            s.add(AuditRequest(domain=cl["domain"], firm_name=cl["firm_name"], area=cl["area"], email_enc=self.c.crypto.encrypt(PUBLIC_TENANT_ID, em),
                               email_hmac=self.c.crypto.keyed_hash(em), consent_id=cr.id, run_id=run.id, crd=cl["crd"], aum=cl["aum"], goal=cl["goal"],
                               contact_name_enc=self.c.crypto.encrypt(PUBLIC_TENANT_ID, cl["contact_name"])))
            emit(s, "AuditRequested", tenant_id=PUBLIC_TENANT_ID, idempotency_key=f"{cl['domain']}:{run.id}", payload={"run_id": run.id})
            return 202, {"ok": True, "id": run.id, "message": PUBLIC_MESSAGE["queued"]}

    def _has_shield(self, s, tenant_id: str) -> bool:
        """Monitoring is a subscription (plan shield or gro). The free audit is a one-time check per firm."""
        t = s.get(Tenant, tenant_id)
        return bool(t and t.status == "active" and t.plan in ("shield", "gro"))

    def request_portfolio(self, p: Principal, firm_ids: list[str]) -> list[str]:
        require(p, "edit", "shield", resource_tenant=p.tenant_id)
        with session_scope(self.c.sm, system=True) as s:
            if not self._has_shield(s, p.tenant_id):
                raise SubscriptionRequired("Portfolio audits need a Shield subscription.")
        if len(firm_ids) > self.c.settings.portfolio_batch_max:
            raise ValidationFailed(f"at most {self.c.settings.portfolio_batch_max} firms per batch")
        ids = []
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            firms = s.execute(select(Firm).where(Firm.id.in_(firm_ids))).scalars().all()
            if len(firms) != len(set(firm_ids)):
                raise NotFound("firm not found")
            for f in firms:
                r = Run(domain=f.domain, firm_name=f.name, area=f.area, trigger="portfolio", priority=3, firm_id=f.id,
                        period=utcnow().strftime("%Y%m%d%H"), idempotency_key=f"portfolio:{p.tenant_id}:{f.domain}:{utcnow().strftime('%Y%m%d%H')}")
                s.add(r)
                s.flush()
                ids.append(r.id)
                emit(s, "AuditRequested", tenant_id=p.tenant_id, firm_id=f.id, idempotency_key=f"{f.domain}:{r.id}", payload={"run_id": r.id})
        return ids

    # ------------------------------------------------------------------ execution
    def execute_run(self, run_id: str, *, only: list[str] | None = None) -> str:
        """Safe to call twice: a run that is past Queued is not re-run."""
        with session_scope(self.c.sm, system=True) as s:
            r = s.get(Run, run_id)
            if r is None:
                raise NotFound("run")
            if r.state != "Queued":
                return r.state
            r.state, r.started_at = "Running", utcnow()
            domain, trigger, firm_id, tenant, fname, area = r.domain, r.trigger, r.firm_id, r.tenant_id, r.firm_name, r.area
        eng = self.c.shield_engine
        url = f"https://{domain}"
        try:
            name, aliases, area_v = eng.resolve_firm(url, fname, area)
            mods = [m for m in eng.modules(domain) if not only or m.group_id in only]
            outcome = asyncio.run(run_groups(mods, url, name, area_v, seconds_cap=self.c.settings.shield_budget_seconds, domain=domain, aliases=aliases))
        except Exception as e:  # noqa: BLE001 - only intake/orchestration itself breaking fails a run
            log.exception("run %s failed", run_id)
            with session_scope(self.c.sm, system=True) as s:
                r = s.get(Run, run_id)
                r.state, r.spend = "Failed", {"error": f"{type(e).__name__}: {e}"[:300]}
            return "Failed"
        return self._persist(run_id, tenant, firm_id, trigger, domain, url, name, area_v, outcome)

    def _prev(self, s, run) -> dict | None:
        if not run.firm_id:
            return None
        p = s.execute(select(Run).where(Run.firm_id == run.firm_id, Run.trigger == "shield", Run.id != run.id, Run.finished_at.is_not(None))
                      .order_by(Run.finished_at.desc()).limit(1)).scalar_one_or_none()
        return {"scorecard": p.scorecard, "held_flags": p.held_flags} if p and p.scorecard else None

    def _persist(self, run_id, tenant, firm_id, trigger, domain, url, name, area, outcome) -> str:
        eng, st = self.c.shield_engine, self.c.settings
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            run = s.get(Run, run_id)
            prev = self._prev(s, run)
            record = assemble(outcome.results, domain=domain, url=url, firm_name=name, area=area, spend_usd=sum(outcome.spend_usd.values()),
                              duration_s=outcome.duration_s, run_id=run_id, prev=prev)
            card = record["scorecard"]
            extras = eng.extras(domain)
            try:
                rows = _incomplete_rows(classify(record, extras))
            except Exception:  # noqa: BLE001 - the extra rows are optional; the checks are not
                rows = []
            with tempfile.TemporaryDirectory() as d:
                built = build_reports(record, eng.client(), d, show_passed=st.always_show_passed)
                files = {"client": Path(built["client"]).read_bytes(), "evidence": Path(built["evidence"]).read_bytes(),
                         "record": Path(built["record"]).read_bytes()}
            payload = safe_payload(record, built, rows, firm=name, kind="shield" if trigger == "shield" else "audit", show_passed=st.always_show_passed)
            try:
                from .mvp.offers import recommend
                off = recommend(record, "")
                payload["recommendation"] = {"offer": off[0], "reason": off[1]}
            except Exception:  # noqa: BLE001
                log.debug("optional step skipped", exc_info=True)
            for kind, fname, data, ctype in (("client", "client_report.html", files["client"], "text/html"),
                                             ("evidence", "evidence_report.md", files["evidence"], "text/markdown"),
                                             ("record", "run_record.json", files["record"], "application/json"),
                                             ("payload", "payload.json", json.dumps(payload).encode(), "application/json")):
                uri = self.c.store.put(tenant, f"{run_id}-{fname}", data, content_type=ctype)
                from ...core.hashing import sha256_hex
                s.add(Report(run_id=run_id, kind=kind, uri=uri, content_hash=sha256_hex(data)))
            for gid, g in record["results"].items():
                s.add(GroupResult(run_id=run_id, group_key=gid, status=group_status(g), points=g["score"], max_points=g["max_points"],
                                  checks=g["findings"], unverified_reason=(g["evidence"] or {}).get("reason"), providers=[],
                                  duration_ms=int(outcome.duration_s * 1000),
                                  evidence_uri=self.c.store.put(tenant, f"{run_id}-{gid}.json", json.dumps(g, default=str).encode(), content_type="application/json")))
            for f in record["fixes"]:
                s.add(Finding(run_id=run_id, group_key=f["group"], code=f["check"], severity=f["priority"].lower(), title=f["check"],
                              detail=f["detail"], fix=f["action"], impact=min(5, int(f["lost"]) + 1), effort=f["effort"], is_compliance=bool(f["held"])))
            for gid, usd in outcome.spend_usd.items():
                if usd:
                    s.add(UsageEvent(tenant_id=tenant, run_id=run_id, group_key=gid, provider="llm+search", kind="llm", units=1, cost_micro_usd=int(usd * 1e6)))
            run.score, run.score_max, run.partial = card["total"], card["max_total"], not card["complete"]
            run.scorecard, run.release_status, run.held_flags = card, record["release_status"], record["held_flags"]
            run.spend, run.finished_at, run.firm_name = {"usd": record["spent_usd"], "by_group": outcome.spend_usd}, utcnow(), name
            s.flush()
            if trigger in ("shield", "probe") and firm_id:
                alerts.raise_alerts(s, run=run, record=record, prev=prev if trigger == "shield" else None, extras=extras)
                en = s.execute(select(ShieldEnrollment).where(ShieldEnrollment.firm_id == firm_id)).scalar_one_or_none()
                if en and trigger == "shield":
                    en.last_run_id = run.id
            if trigger == "probe":
                run.state = "Released"
                return run.state
            if record["release_status"] != "releasable":  # review gate: a person looks first
                run.state = "InReview"
                role = "cco" if (record["release_status"] == "pending_cco_review" and tenant != PUBLIC_TENANT_ID) else "reviewer"
                s.add(ReviewItem(kind="shield_run", ref_id=run_id, assigned_role=role,
                                 flags=[{"title": x} for x in record["held_flags"]] or [{"title": "compliance scan did not complete"}]))
                return run.state
            self._release(s, run)
            return run.state

    def _release(self, s, run: Run) -> None:
        tok = new_report_token()
        run.report_token_hash, run.token_expires_at = hash_token(tok), utcnow() + TOKEN_TTL
        run.notify_enc = self.c.crypto.encrypt(run.tenant_id, tok)
        run.state = "Released"
        for ar in s.execute(select(AuditRequest).where(AuditRequest.run_id == run.id)).scalars():
            emit(s, "ReportReady", tenant_id=run.tenant_id, idempotency_key=f"{run.id}:{ar.id}", firm_id=run.firm_id,
                 payload={"run_id": run.id, "audit_request_id": ar.id})

    # ------------------------------------------------------------------ review gate
    def decide_review(self, p: Principal, run_id: str, decision: str, notes: str = "") -> str:
        if decision not in ("release", "withhold"):
            raise ValidationFailed("decision must be release or withhold")
        with session_scope(self.c.sm, system=True) as s:
            run = s.get(Run, run_id)
            item = s.execute(select(ReviewItem).where(ReviewItem.ref_id == run_id, ReviewItem.kind == "shield_run")).scalar_one_or_none()
            if not run or not item:
                raise NotFound("review item")
            d = require(p, "approve", "review_queue", resource_tenant=run.tenant_id)
            if item.assigned_role == "cco" and p.is_staff:
                raise Forbidden("this item is for the firm's CCO")
            item.state = "released" if decision == "release" else "rejected"
            item.decided_by, item.decided_at = p.user_id, utcnow()
            log_activity(s, actor=p.user_id, action=f"review.{decision}", tenant_id=run.tenant_id, resource=f"run:{run_id}", meta={"via": d.reason, "notes": notes[:300]})
            if decision == "release":
                self._release(s, run)
            return run.state

    def review_queue(self, p: Principal) -> list[dict]:
        with session_scope(self.c.sm, system=True) as s:
            out = []
            for r in s.execute(select(ReviewItem).where(ReviewItem.state == "pending")).scalars():
                ok = (p.is_staff and p.has("reviewer") and r.assigned_role == "reviewer") or (not p.is_staff and p.tenant_id == r.tenant_id and p.has("cco"))
                if ok:
                    out.append({"id": r.id, "kind": r.kind, "run_id": r.ref_id, "flags": r.flags, "assigned_role": r.assigned_role, "created_at": r.created_at.isoformat()})
            return out

    # ------------------------------------------------------------------ read paths (MVP contract)
    def _payload(self, s, run_id: str) -> dict | None:
        rep = s.execute(select(Report).where(Report.run_id == run_id, Report.kind == "payload")).scalar_one_or_none()
        return json.loads(self.c.store.get(rep.uri)) if rep else None

    def view(self, run_id: str) -> dict:
        with session_scope(self.c.sm, system=True) as s:
            r = s.get(Run, run_id)
            if not r or r.tenant_id != PUBLIC_TENANT_ID:
                raise NotFound("audit")
            st = STATUS.get(r.state, "running")
            out: dict = {"ok": True, "status": st, "message": PUBLIC_MESSAGE.get(st, "")}
            if st in ("pending_review", "released", "sent"):
                ar = s.execute(select(AuditRequest).where(AuditRequest.run_id == run_id).limit(1)).scalar_one_or_none()
                to = mask_email(self.c.crypto.decrypt(PUBLIC_TENANT_ID, ar.email_enc)) if ar else ""
                out["mail"] = {"state": "sent" if st == "sent" else "scheduled",
                               "message": f"A copy has been emailed to {to}." if st == "sent" else
                               f"A person is reviewing your audit. The completed report will be emailed to {to} within one business day." if st == "pending_review"
                               else f"Your report is on its way to {to}."}
                if self.c.settings.results_preview or st != "pending_review":
                    rep = self._payload(s, run_id)
                    if rep:
                        rep["preliminary"] = st == "pending_review"
                        out["complete"] = bool(rep["score"]["complete"])
                        out["report"] = rep
            return out

    def open_report(self, token: str, *, ip_hash: str = "") -> dict:
        with session_scope(self.c.sm, system=True) as s:
            r = s.execute(select(Run).where(Run.report_token_hash == hash_token(token))).scalar_one_or_none()
            if not r or r.token_revoked or (r.token_expires_at and r.token_expires_at < utcnow()) or r.state not in ("Released", "Delivered"):
                raise NotFound("report")  # the link is the credential; anything else looks absent
            log_activity(s, actor="report-link", action="report.viewed", tenant_id=r.tenant_id, resource=f"run:{r.id}", meta={"ip": ip_hash})
            return {"ok": True, "published": True, "report": self._payload(s, r.id)}

    def revoke_token(self, p: Principal, run_id: str) -> None:
        require(p, "edit", "shield", resource_tenant=p.tenant_id)
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            s.get(Run, run_id).token_revoked = True

    def snapshot(self, url: str) -> dict:
        """Synchronous live check: technical rows only, cached for the cache TTL."""
        key = url.strip().lower()
        hit = self._snap.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        res = self.c.shield_engine.snapshot(url)
        self._snap[key] = (time.time() + self.c.settings.shield_cache_ttl_s, res)
        return res

    # ------------------------------------------------------------------ Shield flagship
    def enroll(self, p: Principal, firm_id: str) -> None:
        require(p, "edit", "shield", resource_tenant=p.tenant_id)
        with session_scope(self.c.sm, system=True) as s:
            if not self._has_shield(s, p.tenant_id):
                raise SubscriptionRequired("Monitoring needs a Shield subscription. The free audit is a one-time check.")
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            if not s.get(Firm, firm_id):
                raise NotFound("firm")
            en = s.execute(select(ShieldEnrollment).where(ShieldEnrollment.firm_id == firm_id)).scalar_one_or_none()
            if en:
                en.active = True
            else:
                s.add(ShieldEnrollment(firm_id=firm_id, next_run_at=utcnow()))
            log_activity(s, actor=p.user_id, action="shield.enroll", tenant_id=p.tenant_id, resource=f"firm:{firm_id}")

    def schedule_due(self, now=None) -> int:
        now = now or utcnow()
        with session_scope(self.c.sm, system=True) as s:
            due = [(e.id, e.tenant_id, e.firm_id) for e in s.execute(select(ShieldEnrollment).where(ShieldEnrollment.active.is_(True), ShieldEnrollment.next_run_at <= now)).scalars()
                   if self._has_shield(s, e.tenant_id)]  # a lapsed subscription stops scheduled runs
            for eid, tenant, fid in due:
                emit(s, "ShieldRunDue", tenant_id=tenant, firm_id=fid, idempotency_key=f"{fid}:{now.strftime('%Y%m')}", payload={"firm_id": fid})
                s.get(ShieldEnrollment, eid).next_run_at = _add_month(now)
        return len(due)

    def start_scheduled(self, tenant_id: str, firm_id: str, *, probe: bool = False) -> str:
        with session_scope(self.c.sm, tenant_id=tenant_id) as s:
            f = s.get(Firm, firm_id)
            if not f:
                raise NotFound("firm")
            period = utcnow().strftime("%Y%m%d" if probe else "%Y%m")
            trig = "probe" if probe else "shield"
            key = f"{trig}:{tenant_id}:{f.domain}:{period}"
            run = s.execute(select(Run).where(Run.idempotency_key == key)).scalar_one_or_none()
            if not run:
                run = Run(domain=f.domain, firm_name=f.name, area=f.area, trigger=trig, firm_id=firm_id, period=period, idempotency_key=key)
                s.add(run)
                s.flush()
            rid = run.id
        self.execute_run(rid, only=["g01_technical"] if probe else None)
        return rid

    def alerts_for(self, p: Principal, firm_id: str | None = None) -> list[dict]:
        from ...db.models import Alert
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            q = select(Alert).order_by(Alert.created_at.desc())
            if firm_id:
                q = q.where(Alert.firm_id == firm_id)
            return [{"id": a.id, "kind": a.kind, "route": a.route, "payload": a.payload, "at": a.created_at.isoformat()}
                    for a in s.execute(q).scalars() if a.route == "client" or p.has("cco")]
