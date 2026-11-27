from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import httpx
from fastapi import BackgroundTasks, Depends, Header, Request

from ..container import Container
from ..core.errors import AdvisorGroError, Unauthorized
from ..security.principal import Principal


def get_c(request: Request) -> Container:
    return request.app.state.c


def principal(request: Request, authorization: str | None = Header(default=None), c: Container = Depends(get_c)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    return c.verifier.verify(authorization.split(" ", 1)[1].strip())


def client_hash(request: Request) -> str:
    ip = request.client.host if request.client else "?"
    return hashlib.sha256(f"ag-ip|{ip}".encode()).hexdigest()[:16]


@dataclass
class RateLimiter:
    """Token bucket per key. In-memory per process; swap the store for Valkey for a shared limit."""
    per_min: int
    buckets: dict[str, tuple[float, float]] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, ts = self.buckets.get(key, (float(self.per_min), now))
        tokens = min(float(self.per_min), tokens + (now - ts) * self.per_min / 60.0)
        if tokens < 1:
            self.buckets[key] = (tokens, now)
            return False
        self.buckets[key] = (tokens - 1, now)
        return True


class RateLimited(AdvisorGroError):
    status, code = 429, "rate_limited"


def public_guard(request: Request, c: Container = Depends(get_c)) -> str:
    h = client_hash(request)
    if not request.app.state.limiter.allow(h):
        raise RateLimited("Too many requests. Please wait a moment and try again.")
    return h


def verify_turnstile(c: Container, token: str | None, ip: str) -> bool:
    if not c.settings.turnstile_secret:
        return True
    if not token:
        return False
    try:
        r = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", data={"secret": c.settings.turnstile_secret, "response": token}, timeout=5)
        return bool(r.json().get("success"))
    except Exception:  # noqa: BLE001 - fail closed on the public form
        return False


def bg_drain(request: Request, background: BackgroundTasks, c: Container = Depends(get_c)) -> None:
    """Single-process mode (AG_INLINE_WORKER=true): after any successful mutation, deliver its events once the response
    is sent, so an approval publishes without a separate worker. Off when a real worker runs."""
    if c.settings.inline_worker and request.method == "POST":
        background.add_task(c.bus.drain)
