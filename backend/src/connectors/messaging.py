"""Email (Postmark/SES), SMS (Twilio/Telnyx) and calendar (Cal.com). Deterministic; AI may draft text but code fills
in personal data after generation. Each sits behind an adapter so it can be swapped."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..core.errors import ProviderError, ProviderUnavailable


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, text: str, *, html: str | None = None, tag: str = "") -> str: ...


class SmsSender(Protocol):
    async def send(self, to: str, body: str) -> str: ...


class PostmarkEmail:
    def __init__(self, token: str, sender: str = "reports@advisorgro.com"):
        self.token, self.sender = token, sender

    async def send(self, to, subject, text, *, html=None, tag="") -> str:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post("https://api.postmarkapp.com/email", headers={"X-Postmark-Server-Token": self.token},
                             json={"From": self.sender, "To": to, "Subject": subject, "TextBody": text,
                                   **({"HtmlBody": html} if html else {}), "Tag": tag, "MessageStream": "outbound"})
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderUnavailable("postmark")
        if r.status_code >= 400:
            raise ProviderError(f"postmark {r.status_code}")
        return r.json().get("MessageID", "")


class TwilioSms:
    def __init__(self, sid: str, token: str, from_: str):
        self.sid, self.token, self.from_ = sid, token, from_

    async def send(self, to, body) -> str:
        async with httpx.AsyncClient(timeout=10, auth=(self.sid, self.token)) as c:
            r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                             data={"To": to, "From": self.from_, "Body": body})
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderUnavailable("twilio")
        if r.status_code >= 400:
            raise ProviderError(f"twilio {r.status_code}")
        return r.json().get("sid", "")


@dataclass
class FakeMessaging:
    """Records everything sent; used by dev, CI and shadow mode."""
    emails: list[dict] = field(default_factory=list)
    sms: list[dict] = field(default_factory=list)
    fail_email: bool = False
    fail_sms: bool = False
    log_path: str | None = None  # dev: also append every message as a JSON line so a tester can read it (there is no real inbox)

    def _log(self, kind: str, rec: dict) -> None:
        if self.log_path:
            import json
            import time
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"at": time.strftime("%H:%M:%S"), "kind": kind, **rec}) + "\n")

    async def send(self, to, subject, text, *, html=None, tag="") -> str:  # EmailSender
        if self.fail_email:
            raise ProviderUnavailable("email down")
        self.emails.append({"to": to, "subject": subject, "text": text, "tag": tag})
        self._log("email", {"to": to, "subject": subject, "tag": tag, "text": text})
        return f"em-{len(self.emails)}"

    @property
    def sms_sender(self) -> SmsSender:
        outer = self

        class _S:
            async def send(self, to: str, body: str) -> str:
                if outer.fail_sms:
                    raise ProviderUnavailable("sms down")
                outer.sms.append({"to": to, "body": body})
                outer._log("sms", {"to": to, "body": body})
                return f"sm-{len(outer.sms)}"
        return _S()


def parse_calcom_booking(payload: dict) -> dict:
    """Normalise a Cal.com BOOKING_CREATED webhook into {external_id, email, starts_at, lead_id?}."""
    p = payload.get("payload", payload)
    return {"external_id": str(p.get("uid") or p.get("bookingId") or p.get("id")), "email": ((p.get("attendees") or [{}])[0]).get("email", ""),
            "starts_at": p.get("startTime"), "lead_id": (p.get("metadata") or {}).get("lead_id")}
