"""Append-only, hash-chained activity log. Every sensitive action writes here, with actor and content hash."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.clock import utcnow
from ..core.hashing import canonical_json, sha256_hex
from ..db.models import ActivityLog


def log_activity(s: Session, *, actor: str, action: str, tenant_id: str | None = None, resource: str = "",
                 result: str = "ok", reason: str = "", meta: dict | None = None) -> ActivityLog:
    last = s.execute(select(ActivityLog).order_by(ActivityLog.seq.desc()).limit(1)).scalar_one_or_none()
    seq = (last.seq if last and last.seq else 0) + 1
    prev = last.hash if last else ""
    at = utcnow()
    row = ActivityLog(seq=seq, tenant_id=tenant_id, actor=actor, action=action, resource=resource, result=result,
                      reason=reason, meta=meta or {}, prev_hash=prev, at=at)
    row.hash = sha256_hex(canonical_json([prev, seq, tenant_id, actor, action, resource, result, reason,
                                          meta or {}, at.isoformat()]))
    s.add(row)
    s.flush()
    return row


def verify_chain(s: Session) -> bool:
    rows = s.execute(select(ActivityLog).order_by(ActivityLog.seq)).scalars().all()
    prev = ""
    for r in rows:
        good = sha256_hex(canonical_json([prev, r.seq, r.tenant_id, r.actor, r.action, r.resource, r.result,
                                          r.reason, r.meta or {}, r.at.isoformat()]))
        if r.prev_hash != prev or r.hash != good:
            return False
        prev = r.hash
    return True


def count(s: Session) -> int:
    return s.execute(select(func.count()).select_from(ActivityLog)).scalar_one()
