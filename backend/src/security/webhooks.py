"""Webhooks are verified by signature and timestamp (calendar, SMS, ads)."""
from __future__ import annotations

import hashlib
import hmac
import time

from ..core.errors import Unauthorized

TOLERANCE_S = 300


def sign(secret: str, body: bytes, ts: int | None = None) -> tuple[str, str]:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return str(ts), mac


def verify(secret: str, body: bytes, ts: str, signature: str, *, now: int | None = None) -> None:
    now = now or int(time.time())
    try:
        t = int(ts)
    except (TypeError, ValueError) as e:
        raise Unauthorized("bad timestamp") from e
    if abs(now - t) > TOLERANCE_S:
        raise Unauthorized("stale webhook")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise Unauthorized("bad signature")
