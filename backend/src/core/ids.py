from __future__ import annotations

import contextvars
import hashlib
import secrets
import uuid

PUBLIC_TENANT_ID = "00000000-0000-0000-0000-000000000001"  # prospects who ran a free audit
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")


def new_id() -> str:
    return str(uuid.uuid4())


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def new_report_token() -> str:
    """Unguessable report link token (43 chars, inside the 20-64 pattern the contract keeps)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
