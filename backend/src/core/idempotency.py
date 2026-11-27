"""Every mutating call takes an Idempotency-Key. Key + response are kept 24h so a retry cannot create a
second audit, lead or campaign."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.models import IdempotencyKey
from .clock import utcnow
from .errors import Conflict
from .hashing import canonical_json, sha256_hex

TTL = timedelta(hours=24)


def lookup(s: Session, key: str, scope: str, body: dict) -> tuple[int, dict] | None:
    row = s.get(IdempotencyKey, (key, scope))
    if not row or row.created_at < utcnow() - TTL:
        return None
    if row.request_hash != sha256_hex(canonical_json(body)):
        raise Conflict("Idempotency-Key reused with a different request", code="idempotency_mismatch")
    return row.status_code, row.response


def store(s: Session, key: str, scope: str, body: dict, status: int, response: dict) -> None:
    try:
        with s.begin_nested():
            old = s.get(IdempotencyKey, (key, scope))
            if old:
                s.delete(old)
                s.flush()
            s.add(IdempotencyKey(key=key, scope=scope, request_hash=sha256_hex(canonical_json(body)),
                                 status_code=status, response=response))
    except IntegrityError:
        pass
