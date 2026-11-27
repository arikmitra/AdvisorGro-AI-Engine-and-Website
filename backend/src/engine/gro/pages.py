"""Landing-page host. Pages are static HTML in object storage behind a CDN in prod; the dev host serves them through
the API (`/p/{campaign}/{version}`). `fetch` is what the Publisher uses to read back what is really live."""
from __future__ import annotations

from sqlalchemy import select

from ...db.models import AdObject
from ...db.session import session_scope


class StorePageHost:
    def __init__(self, c):
        self.c = c

    def url(self, campaign_id: str, version: int) -> str:
        return f"{self.c.settings.pages_base_url.rstrip('/')}/p/{campaign_id}/{version}"

    def publish(self, tenant_id: str, campaign_id: str, version: int, html: str) -> str:
        uri = self.c.store.put(tenant_id, f"page-{campaign_id}-v{version}.html", html.encode(), content_type="text/html")
        with session_scope(self.c.sm, tenant_id=tenant_id) as s:
            key = f"page:{campaign_id}:{version}"
            if not s.execute(select(AdObject).where(AdObject.request_key == key)).scalar_one_or_none():
                s.add(AdObject(campaign_id=campaign_id, version=version, platform="web", object_type="landing_page", platform_id=uri, request_key=key))
        return self.url(campaign_id, version)

    def fetch(self, tenant_id: str, campaign_id: str, version: int) -> str | None:
        with session_scope(self.c.sm, tenant_id=tenant_id) as s:
            o = s.execute(select(AdObject).where(AdObject.request_key == f"page:{campaign_id}:{version}", AdObject.status == "active")).scalar_one_or_none()
            return self.c.store.get(o.platform_id).decode() if o else None

    def take_down(self, tenant_id: str, campaign_id: str, version: int) -> None:
        with session_scope(self.c.sm, tenant_id=tenant_id) as s:
            o = s.execute(select(AdObject).where(AdObject.request_key == f"page:{campaign_id}:{version}")).scalar_one_or_none()
            if o:
                o.status = "removed"
