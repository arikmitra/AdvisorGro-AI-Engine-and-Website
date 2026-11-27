from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..container import Container, build_container
from ..core.errors import AdvisorGroError
from ..core.ids import correlation_id_var, new_correlation_id
from ..core.logging import configure_logging
from .deps import RateLimiter
from .routers import portal, public

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains", "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class Edge(BaseHTTPMiddleware):
    """Correlation id on every request, request-size cap, security headers."""

    def __init__(self, app, max_body: int):
        super().__init__(app)
        self.max_body = max_body

    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get("x-correlation-id", "")[:32] or new_correlation_id()
        tok = correlation_id_var.set(cid)
        try:
            if request.url.path.startswith("/p/"):
                resp = await call_next(request)
            else:
                size = int(request.headers.get("content-length") or 0)
                if size > self.max_body:
                    return JSONResponse({"ok": False, "message": "Request too large."}, status_code=413)
                resp = await call_next(request)
            for k, v in SECURITY_HEADERS.items():
                if not (k == "Cache-Control" and request.url.path.startswith("/p/")):
                    resp.headers.setdefault(k, v)
            resp.headers["X-Correlation-Id"] = cid
            return resp
        finally:
            correlation_id_var.reset(tok)


def create_app(container: Container | None = None) -> FastAPI:
    c = container or build_container()
    configure_logging()
    app = FastAPI(title="AdvisorGro API", version="1.0.0", docs_url=None if c.settings.env == "prod" else "/docs", redoc_url=None)
    app.state.c = c
    app.state.limiter = RateLimiter(c.settings.rate_limit_per_min)
    app.add_middleware(Edge, max_body=c.settings.max_body_bytes)
    app.add_middleware(CORSMiddleware, allow_origins=c.settings.cors_origins, allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Correlation-Id"], max_age=600)

    @app.exception_handler(AdvisorGroError)
    async def _domain(_: Request, e: AdvisorGroError):
        return JSONResponse({"ok": False, "error": e.code, "message": str(e)}, status_code=e.status)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        log.exception("unhandled")
        return JSONResponse({"ok": False, "error": "internal", "message": "Something went wrong."}, status_code=500)

    from ..observability import telemetry
    telemetry.init(app, c.settings.otel_endpoint)
    app.include_router(public.router)
    app.include_router(portal.router)
    return app


def app_factory() -> FastAPI:  # uvicorn advisorgro.api.main:app_factory --factory
    return create_app()
