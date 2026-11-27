"""Lead capture and follow-up (deck 10). Consent is stored before the lead; contact details are encrypted at the field
level; dedupe is by keyed hash; a lead can only be posted to a page that is live and approved; the first reply goes out
within the SLA (90 s) from approved text, with code filling in personal data; opt-out stops everything."""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta

from sqlalchemy import select

from ..core.clock import utcnow
from ..core.errors import NotFound, ProviderError
from ..core.events import emit
from ..core.ids import correlation_id_var
from ..db.models import (
    Booking,
    Campaign,
    Consent,
    ConversionSent,
    Incident,
    Lead,
    LeadEvent,
    RenderedSet,
    Sequence,
)
from ..db.session import session_scope
from ..security.crypto import mask_email, mask_phone, normalise_email, normalise_phone
from ..security.policy import require
from ..security.principal import Principal

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CHECKIN_DAYS = (30, 60, 90)
STOP_WORDS = {"stop", "unsubscribe", "cancel", "end", "quit", "stopall"}


class LeadService:
    def __init__(self, c):
        self.c = c

    # ------------------------------------------------------------------ capture
    def submit(self, payload: dict) -> tuple[int, dict]:
        if str(payload.get("hp") or "").strip():
            return 202, {"ok": True}
        cid = str(payload.get("campaign_id") or "")
        with session_scope(self.c.sm, system=True) as s:
            camp = s.get(Campaign, cid) if cid else None
            if not camp or camp.state != "Live" or camp.live_version != _int(payload.get("page_version")):
                return 404, {"ok": False, "message": "This page is not accepting enquiries."}
            tenant, firm_id = camp.tenant_id, camp.firm_id
        email, phone, name = normalise_email(str(payload.get("email") or "")), normalise_phone(str(payload.get("phone") or "")), str(payload.get("name") or "").strip()[:120]
        errors = {}
        if not EMAIL_RE.match(email):
            errors["email"] = "Please enter a valid email."
        if not name:
            errors["name"] = "Please enter your name."
        if not payload.get("consent"):
            errors["consent"] = "Please agree to be contacted."
        if errors:
            return 400, {"ok": False, "errors": errors}
        dk = self.c.crypto.keyed_hash(f"{firm_id}|{email}")
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            if s.execute(select(Lead.id).where(Lead.dedupe_key == dk)).first():
                return 200, {"ok": True, "duplicate": True}
            consent = Consent(text_id=str(payload.get("consent_text_id") or ""), version=str(payload.get("consent_version") or ""), text=str(payload.get("consent_text") or self._consent_text(s, cid, camp.live_version)),
                              page=f"{cid}/v{camp.live_version}", email_ok=True, sms_ok=bool(phone))
            s.add(consent)
            s.flush()
            lead = Lead(firm_id=firm_id, campaign_id=cid, page_version=camp.live_version, name_enc=self.c.crypto.encrypt(tenant, name), email_enc=self.c.crypto.encrypt(tenant, email),
                        phone_enc=self.c.crypto.encrypt(tenant, phone), email_hmac=self.c.crypto.keyed_hash(email), phone_hmac=self.c.crypto.keyed_hash(phone) if phone else "",
                        consent_id=consent.id, dedupe_key=dk, click_ids={k: str(payload[k])[:200] for k in ("gclid", "fbclid") if payload.get(k)},
                        utm={k: str(payload[k])[:100] for k in ("utm_source", "utm_campaign") if payload.get(k)})
            s.add(lead)
            s.flush()
            s.add(LeadEvent(lead_id=lead.id, type="created", meta={"correlation_id": correlation_id_var.get()}))
            emit(s, "LeadCreated", tenant_id=tenant, firm_id=firm_id, idempotency_key=lead.id, payload={"lead_id": lead.id})
            return 202, {"ok": True}

    def _consent_text(self, s, cid: str, version: int) -> str:
        rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == cid, RenderedSet.version == version)).scalar_one_or_none()
        return (rs.parts.get("consent", {}).get("text") if rs else "") or "consent text unavailable"

    # ------------------------------------------------------------------ follow-up (the 90-second SLA)
    def follow_up(self, tenant: str, lead_id: str) -> dict:
        now = utcnow()
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            lead = s.get(Lead, lead_id)
            if not lead or lead.state == "opted_out" or lead.first_contact_at:
                return {"sent": False}
            consent = s.get(Consent, lead.consent_id)
            rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == lead.campaign_id, RenderedSet.version == lead.page_version)).scalar_one()
            name, email, phone = (self.c.crypto.decrypt(tenant, x) for x in (lead.name_enc, lead.email_enc, lead.phone_enc))
            created, link = lead.created_at, f"{self.c.settings.pages_base_url.rstrip('/')}/p/{lead.campaign_id}/{lead.page_version}#book"
            email_tpl, sms_tpl = rs.parts["emails"][0], (rs.parts["sms"] or [""])[0]
            sms_ok = consent.sms_ok and bool(phone)
        first = (name.split() or [""])[0]
        fill = lambda t: t.replace("{first_name}", first).replace("{link}", link)  # noqa: E731 - code, not the model, fills personal data
        sent: list[str] = []
        try:
            asyncio.run(self.c.email.send(email, fill(email_tpl["subject"]), fill(email_tpl["body"]), tag="lead-first"))
            sent.append("email")
        except ProviderError as e:
            self._incident(tenant, "followup_email_failed", lead_id, str(e))
        if sms_ok and sms_tpl:
            h = now.hour
            quiet = self.c.settings.quiet_hours
            in_quiet = (h >= quiet[0] or h < quiet[1]) if quiet[0] > quiet[1] else (quiet[0] <= h < quiet[1])
            if in_quiet:
                sent.append("sms_deferred")
            else:
                try:
                    asyncio.run(self.c.sms.send(phone, fill(sms_tpl)))
                    sent.append("sms")
                except ProviderError as e:
                    self._incident(tenant, "followup_sms_failed", lead_id, str(e))
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            lead = s.get(Lead, lead_id)
            if "email" in sent or "sms" in sent:
                lead.first_contact_at, lead.state = utcnow(), "contacted"
                s.add(Sequence(lead_id=lead_id, step=0, next_at=created + timedelta(days=CHECKIN_DAYS[0])))
            latency = (utcnow() - created).total_seconds()
            s.add(LeadEvent(lead_id=lead_id, type="first_contact", meta={"channels": sent, "latency_s": round(latency, 1), "sla_met": latency <= self.c.settings.followup_sla_s}))
        return {"sent": bool(sent), "channels": sent}

    def run_due_sequences(self, now=None) -> int:
        now, n = now or utcnow(), 0
        with session_scope(self.c.sm, system=True) as s:
            due = [(q.id, q.tenant_id, q.lead_id, q.step) for q in s.execute(select(Sequence).where(Sequence.state == "active", Sequence.next_at <= now)).scalars()]
        for qid, tenant, lead_id, step in due:
            with session_scope(self.c.sm, tenant_id=tenant) as s:
                q, lead = s.get(Sequence, qid), s.get(Lead, lead_id)
                if lead.state in ("opted_out", "booked"):
                    q.state = "stopped"
                    continue
                rs = s.execute(select(RenderedSet).where(RenderedSet.campaign_id == lead.campaign_id, RenderedSet.version == lead.page_version)).scalar_one()
                emails = rs.parts["emails"]
                idx = step + 1
                if idx >= len(emails):
                    q.state = "done"
                    continue
                name, email = self.c.crypto.decrypt(tenant, lead.name_enc), self.c.crypto.decrypt(tenant, lead.email_enc)
                tpl = emails[idx]
            first = (name.split() or [""])[0]
            try:
                asyncio.run(self.c.email.send(email, tpl["subject"].replace("{first_name}", first), tpl["body"].replace("{first_name}", first), tag="lead-checkin"))
            except ProviderError as e:
                self._incident(tenant, "checkin_failed", lead_id, str(e))
                continue
            with session_scope(self.c.sm, tenant_id=tenant) as s:
                q = s.get(Sequence, qid)
                q.step = idx
                days = CHECKIN_DAYS
                q.next_at, q.state = (s.get(Lead, lead_id).created_at + timedelta(days=days[idx]), "active") if idx < len(days) else (None, "done")
                s.add(LeadEvent(lead_id=lead_id, type="checkin", meta={"step": idx}))
            n += 1
        return n

    def _incident(self, tenant, kind, lead_id, err):
        with session_scope(self.c.sm, system=True) as s:
            s.add(Incident(kind=kind, tenant_id=tenant, detail={"lead_id": lead_id, "error": err[:200]}))

    # ------------------------------------------------------------------ opt-out, bookings, conversions
    def opt_out(self, *, email: str = "", phone: str = "") -> int:
        hashes = [h for h in (self.c.crypto.keyed_hash(normalise_email(email)) if email else "", self.c.crypto.keyed_hash(normalise_phone(phone)) if phone else "") if h]
        n = 0
        with session_scope(self.c.sm, system=True) as s:
            leads = [(x.id, x.tenant_id) for x in s.execute(select(Lead).where((Lead.email_hmac.in_(hashes)) | (Lead.phone_hmac.in_(hashes)))).scalars()]
        for lid, tenant in leads:
            with session_scope(self.c.sm, tenant_id=tenant) as s:
                s.get(Lead, lid).state = "opted_out"
                for q in s.execute(select(Sequence).where(Sequence.lead_id == lid, Sequence.state == "active")).scalars():
                    q.state = "stopped"
                s.add(LeadEvent(lead_id=lid, type="opt_out"))
            n += 1
        return n

    def handle_sms_inbound(self, body: dict) -> int:
        if str(body.get("body", "")).strip().lower() in STOP_WORDS:
            return self.opt_out(phone=str(body.get("from", "")))
        return 0

    def record_booking(self, *, external_id: str, email: str = "", lead_id: str | None = None, starts_at=None) -> bool:
        with session_scope(self.c.sm, system=True) as s:
            lead = s.get(Lead, lead_id) if lead_id else s.execute(select(Lead).where(Lead.email_hmac == self.c.crypto.keyed_hash(normalise_email(email))).limit(1)).scalar_one_or_none()
            if not lead:
                return False
            tenant, lid, firm = lead.tenant_id, lead.id, lead.firm_id
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            if s.execute(select(Booking.id).where(Booking.external_id == external_id)).first():
                return True  # webhook retry
            s.add(Booking(lead_id=lid, external_id=external_id, starts_at=starts_at))
            lead = s.get(Lead, lid)
            lead.state = "booked"
            for q in s.execute(select(Sequence).where(Sequence.lead_id == lid, Sequence.state == "active")).scalars():
                q.state = "stopped"
            s.add(LeadEvent(lead_id=lid, type="booked", meta={"external_id": external_id}))
            emit(s, "BookingConfirmed", tenant_id=tenant, firm_id=firm, idempotency_key=external_id, payload={"lead_id": lid})
        return True

    def send_conversion(self, tenant: str, lead_id: str) -> list[str]:
        sent = []
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            lead = s.get(Lead, lead_id)
            consent = s.get(Consent, lead.consent_id)
            camp = s.get(Campaign, lead.campaign_id)
            if not consent.email_ok or not lead.click_ids:
                return []
            platform, clicks, hashed = camp.platform, dict(lead.click_ids), lead.email_hmac
            acct = ""
        key = f"conv:{lead_id}:{platform}:booking"
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            if s.execute(select(ConversionSent.id).where(ConversionSent.request_key == key)).first():
                return []
        try:
            self.c.connectors[platform].send_conversion(account=acct, click_ids=clicks, event="booking", value=0.0, request_key=key, hashed_email=hashed)
        except ProviderError as e:
            self._incident(tenant, "conversion_failed", lead_id, str(e))
            return []
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            s.add(ConversionSent(lead_id=lead_id, platform=platform, event="booking", request_key=key))
        sent.append(platform)
        return sent

    # ------------------------------------------------------------------ read (policy-masked)
    def list_for(self, p: Principal, campaign_id: str | None = None) -> list[dict]:
        d = require(p, "view", "leads", resource_tenant=p.tenant_id)
        out = []
        with session_scope(self.c.sm, tenant_id=p.tenant_id) as s:
            q = select(Lead).order_by(Lead.created_at.desc())
            if campaign_id:
                q = q.where(Lead.campaign_id == campaign_id)
            for lead in s.execute(q).scalars():
                if d.own_only and lead.assigned_advisor_id != p.user_id:
                    continue
                em, ph, nm = (self.c.crypto.decrypt(p.tenant_id, x) for x in (lead.email_enc, lead.phone_enc, lead.name_enc))
                out.append({"id": lead.id, "campaign_id": lead.campaign_id, "state": lead.state, "created_at": lead.created_at.isoformat(),
                            "name": (nm[:1] + "***") if d.masked else nm, "email": mask_email(em) if d.masked else em, "phone": mask_phone(ph) if d.masked and ph else ph})
        return out

    def sla_report(self, tenant: str, days: int = 7) -> dict:
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            ev = [e.meta for e in s.execute(select(LeadEvent).where(LeadEvent.type == "first_contact", LeadEvent.at >= utcnow() - timedelta(days=days))).scalars()]
        return {"first_contacts": len(ev), "within_sla": sum(1 for m in ev if m.get("sla_met"))}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


__all__ = ["LeadService", "NotFound"]
