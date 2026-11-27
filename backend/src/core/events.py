"""Transactional outbox + idempotent consumers (guide 3.7, section 4).

Business writes and event publication share one transaction (`emit`). A relay publishes rows at least once;
each consumer checks processed_events before acting. Poison messages go to the dead-letter state and can be
replayed by run id. Events carry tenant, firm, correlation id and schema version.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.models import Outbox, ProcessedEvent
from ..db.session import session_scope
from .clock import utcnow
from .ids import correlation_id_var

log = logging.getLogger(__name__)

EVENT_TYPES = {
    "AuditRequested", "GroupCompleted", "ReportReady", "ShieldRunDue", "AlertRaised", "CampaignSubmitted",
    "CampaignApproved", "AdPublished", "LeadCreated", "BookingConfirmed", "CampaignPaused",
}


@dataclass
class Event:
    id: str
    type: str
    tenant_id: str
    firm_id: str | None
    correlation_id: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1


def emit(s: Session, type_: str, *, tenant_id: str, idempotency_key: str, payload: dict | None = None,
         firm_id: str | None = None) -> Outbox | None:
    """Write the event in the caller's transaction. Duplicate (type, key) is a no-op."""
    assert type_ in EVENT_TYPES, f"unknown event {type_}"
    dup = s.execute(select(Outbox.id).where(Outbox.type == type_, Outbox.idempotency_key == idempotency_key)).first()
    if dup:
        return None
    row = Outbox(type=type_, tenant_id=tenant_id, firm_id=firm_id, correlation_id=correlation_id_var.get(),
                 idempotency_key=idempotency_key, payload=payload or {})
    s.add(row)
    s.flush()
    return row


Handler = Callable[[Event], None]


class EventBus:
    """In-process relay for dev/tests and for single-node deploys. In prod the same handlers run as
    Temporal activities; the delivery contract (at-least-once, idempotent) is identical."""

    def __init__(self, sm, max_attempts: int = 5):
        self.sm = sm
        self.handlers: dict[str, list[tuple[str, Handler]]] = {}
        self.max_attempts = max_attempts

    def subscribe(self, event_type: str, consumer: str, fn: Handler) -> None:
        self.handlers.setdefault(event_type, []).append((consumer, fn))

    def relay_once(self, limit: int = 100) -> int:
        """Deliver pending outbox rows. Returns the number of rows delivered or dead-lettered."""
        n = 0
        with session_scope(self.sm, system=True) as s:
            rows = s.execute(select(Outbox).where(Outbox.published_at.is_(None), Outbox.dead.is_(False))
                             .order_by(Outbox.created_at).limit(limit)).scalars().all()
            batch = [Event(r.id, r.type, r.tenant_id, r.firm_id, r.correlation_id, r.idempotency_key,
                           dict(r.payload), r.schema_version) for r in rows]
        for ev in batch:
            err = None
            for consumer, fn in self.handlers.get(ev.type, []):
                try:
                    self._deliver(consumer, fn, ev)
                except Exception as e:  # noqa: BLE001 - any consumer failure is retried then dead-lettered
                    log.exception("consumer %s failed for %s", consumer, ev.id)
                    err = f"{consumer}: {e}"
                    break
            with session_scope(self.sm, system=True) as s:
                r = s.get(Outbox, ev.id)
                if err is None:
                    r.published_at = utcnow()
                else:
                    r.attempts += 1
                    r.last_error = err[:500]
                    if r.attempts >= self.max_attempts:
                        r.dead = True
            n += 1
        return n

    def drain(self, max_rounds: int = 20) -> int:
        total = 0
        for _ in range(max_rounds):
            n = self.relay_once()
            total += n
            if n == 0:
                break
        return total

    def _deliver(self, consumer: str, fn: Handler, ev: Event) -> None:
        key = f"{ev.type}:{ev.idempotency_key}"
        with session_scope(self.sm, system=True) as s:
            if s.get(ProcessedEvent, (consumer, key)):
                return
        fn(ev)  # side effects are themselves idempotent; the marker prevents re-delivery
        try:
            with session_scope(self.sm, system=True) as s:
                s.add(ProcessedEvent(consumer=consumer, event_key=key))
        except IntegrityError:
            pass

    def replay(self, *, run_id: str | None = None, event_id: str | None = None) -> int:
        """Admin replay of dead-lettered events, by run id or event id."""
        with session_scope(self.sm, system=True) as s:
            q = select(Outbox).where(Outbox.dead.is_(True))
            rows = [r for r in s.execute(q).scalars() if (event_id and r.id == event_id) or (run_id and r.payload.get("run_id") == run_id)]
            for r in rows:
                s.execute(update(Outbox).where(Outbox.id == r.id).values(dead=False, attempts=0, last_error=None))
            return len(rows)
