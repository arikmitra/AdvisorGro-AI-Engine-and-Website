"""Ad-platform connectors. Contract: create everything PAUSED, read back, then activate only after the Publisher has
verified the live content hash. Calls are idempotent by request key. Metrics gaps are None, never zero.

Google and Meta implementations follow the vendors' public APIs and MUST be validated against sandbox/test accounts
(shadow mode) before any real spend: payload details change between API versions.
"""
from __future__ import annotations

import json
import os
from typing import Protocol

import httpx

from ..core.errors import ProviderError, ProviderUnavailable


class AdConnector(Protocol):
    platform: str

    def publish(self, *, account: str, campaign_id: str, version: int, creative: dict, landing_url: str,
                monthly_budget_cents: int, request_key: str) -> list[dict]: ...
    def read_back(self, objects: list[dict]) -> dict: ...
    def activate(self, objects: list[dict]) -> None: ...
    def pause(self, objects: list[dict]) -> None: ...
    def fetch_metrics(self, account: str, objects: list[dict], day: str) -> dict | None: ...
    def send_conversion(self, *, account: str, click_ids: dict, event: str, value: float, request_key: str, hashed_email: str) -> str: ...


class FakeAdPlatform:
    """In-memory platform for dev, CI and shadow mode. `tamper` simulates drift (an edit made in the ad UI).
    With `state_path` the state lives in a JSON file so several local processes (API, worker, scripts) share one fake."""

    def __init__(self, platform: str = "google", state_path: str | None = None):
        self.platform = platform
        self.state_path = state_path
        self.objects: dict[str, dict] = {}  # platform_id -> state
        self.by_key: dict[str, list[dict]] = {}
        self.metrics: dict[tuple[str, str], dict | None] = {}
        self.conversions: dict[str, dict] = {}
        self.fail_publish = False
        self.calls = {"publish": 0}

    def _load(self) -> None:
        if self.state_path and os.path.exists(self.state_path):
            with open(self.state_path) as f:
                d = json.load(f)
            self.objects, self.by_key, self.conversions = d["objects"], d["by_key"], d["conversions"]
            self.metrics = {tuple(k.split("|", 1)): v for k, v in d["metrics"].items()}

    def _save(self) -> None:
        if self.state_path:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"objects": self.objects, "by_key": self.by_key, "conversions": self.conversions,
                           "metrics": {"|".join(k): v for k, v in self.metrics.items()}}, f)
            os.replace(tmp, self.state_path)

    def publish(self, *, account, campaign_id, version, creative, landing_url, monthly_budget_cents, request_key) -> list[dict]:
        self._load()
        self.calls["publish"] += 1
        if self.fail_publish:
            raise ProviderUnavailable("platform down")
        if request_key in self.by_key:  # idempotent: a retry never creates a second campaign
            return self.by_key[request_key]
        pid = f"{self.platform}-{len(self.objects) + 1}"
        self.objects[pid] = {"creative": {k: v for k, v in creative.items() if k in ("google", "meta")}, "landing_url": landing_url,
                             "budget": monthly_budget_cents, "status": "PAUSED", "account": account}
        objs = [{"object_type": "campaign", "platform_id": pid}]
        self.by_key[request_key] = objs
        self._save()
        return objs

    def read_back(self, objects) -> dict:
        self._load()
        pid = objects[0]["platform_id"]
        if pid not in self.objects:
            raise ProviderError(f"{pid} not found on the platform")
        o = self.objects[pid]
        return {"creative": o["creative"], "landing_url": o["landing_url"], "status": o["status"], "budget": o["budget"]}

    def activate(self, objects) -> None:
        self._load()
        for o in objects:
            self.objects[o["platform_id"]]["status"] = "ENABLED"
        self._save()

    def pause(self, objects) -> None:
        self._load()
        for o in objects:
            self.objects[o["platform_id"]]["status"] = "PAUSED"
        self._save()

    def tamper(self, platform_id: str, headline: str) -> None:
        """Simulate someone editing the live ad in the platform UI (tests, scripts/tamper_fake_ad.py, TEST.md)."""
        self._load()
        c = self.objects[platform_id]["creative"]
        if c.get("google"):
            c["google"]["headlines"][0] = headline
        self._save()

    def fetch_metrics(self, account, objects, day) -> dict | None:
        self._load()
        return self.metrics.get((objects[0]["platform_id"], day), None)

    def send_conversion(self, *, account, click_ids, event, value, request_key, hashed_email) -> str:
        self._load()
        self.conversions.setdefault(request_key, {"event": event, "click_ids": click_ids, "value": value})
        self._save()
        return request_key


class MetaConnector:
    """Meta Marketing API over HTTPS (Graph). Objects are created PAUSED."""
    platform = "meta"

    def __init__(self, token: str, page_id: str = "", version: str = "v21.0"):
        self.token, self.page_id, self.base = token, page_id, f"https://graph.facebook.com/{version}"

    def _post(self, path: str, data: dict) -> dict:
        r = httpx.post(f"{self.base}/{path}", data={**data, "access_token": self.token}, timeout=20)
        if r.status_code in (429, 500, 502, 503, 504):
            raise ProviderUnavailable(f"meta {r.status_code}")
        if r.status_code >= 400:
            raise ProviderError(f"meta {r.status_code}: {r.text[:200]}")
        return r.json()

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = httpx.get(f"{self.base}/{path}", params={**(params or {}), "access_token": self.token}, timeout=20)
        if r.status_code >= 400:
            raise ProviderError(f"meta {r.status_code}")
        return r.json()

    def publish(self, *, account, campaign_id, version, creative, landing_url, monthly_budget_cents, request_key) -> list[dict]:
        import json
        v = (creative.get("meta") or {}).get("variants", [])
        if not v:
            raise ProviderError("no meta creative")
        camp = self._post(f"act_{account}/campaigns", {"name": f"ag-{campaign_id[:8]}-v{version}", "objective": "OUTCOME_LEADS", "status": "PAUSED", "special_ad_categories": "[]"})
        adset = self._post(f"act_{account}/adsets", {"name": f"set-{request_key[:12]}", "campaign_id": camp["id"], "daily_budget": max(100, monthly_budget_cents // 30),
                                                     "billing_event": "IMPRESSIONS", "optimization_goal": "LEAD_GENERATION", "status": "PAUSED", "targeting": "{}"})
        objs = [{"object_type": "campaign", "platform_id": camp["id"]}, {"object_type": "adset", "platform_id": adset["id"]}]
        for i, x in enumerate(v):
            cr = self._post(f"act_{account}/adcreatives", {"name": f"cr-{i}", "object_story_spec": json.dumps({"page_id": self.page_id, "link_data": {
                "link": landing_url, "message": x["primary_text"], "name": x["headline"], "call_to_action": {"type": "LEARN_MORE"}}})})
            ad = self._post(f"act_{account}/ads", {"name": f"ad-{i}", "adset_id": adset["id"], "creative": json.dumps({"creative_id": cr["id"]}), "status": "PAUSED"})
            objs.append({"object_type": "ad", "platform_id": ad["id"]})
        return objs

    def read_back(self, objects) -> dict:
        variants, link = [], ""
        for o in objects:
            if o["object_type"] == "ad":
                d = self._get(o["platform_id"], {"fields": "creative{object_story_spec},status"})
                spec = (d.get("creative") or {}).get("object_story_spec", {}).get("link_data", {})
                variants.append({"primary_text": spec.get("message", ""), "headline": spec.get("name", ""), "cta": "Learn more"})
                link = spec.get("link", link)
        return {"creative": {"meta": {"variants": variants}}, "landing_url": link}

    def _set(self, objects, status: str) -> None:
        for o in objects:
            self._post(o["platform_id"], {"status": status})

    def activate(self, objects) -> None:
        self._set(objects, "ACTIVE")

    def pause(self, objects) -> None:
        self._set(objects, "PAUSED")

    def fetch_metrics(self, account, objects, day) -> dict | None:
        try:
            d = self._get(f"{objects[0]['platform_id']}/insights", {"time_range": f'{{"since":"{day}","until":"{day}"}}', "fields": "spend,impressions,clicks,actions"})
        except ProviderError:
            return None
        rows = d.get("data", [])
        if not rows:
            return None
        r = rows[0]
        leads = sum(int(a["value"]) for a in r.get("actions", []) if a.get("action_type") == "lead")
        return {"spend_cents": int(float(r.get("spend", 0)) * 100), "impressions": int(r.get("impressions", 0)), "clicks": int(r.get("clicks", 0)), "conversions": leads}

    def send_conversion(self, *, account, click_ids, event, value, request_key, hashed_email) -> str:
        raise ProviderError("Meta Conversions API needs a pixel id: configure before enabling offline conversions")


class GoogleAdsConnector:
    """Google Ads via the official client library (optional extra `ads`). Objects are created PAUSED."""
    platform = "google"

    def __init__(self, client_factory):
        self._factory = client_factory  # returns a GoogleAdsClient for the linked customer

    def publish(self, *, account, campaign_id, version, creative, landing_url, monthly_budget_cents, request_key) -> list[dict]:
        g = (creative.get("google") or {})
        c = self._factory()
        svc = lambda n: c.get_service(n)  # noqa: E731
        budget_op = c.get_type("CampaignBudgetOperation")
        b = budget_op.create
        b.name, b.amount_micros, b.delivery_method = f"ag-{request_key[:16]}", max(1, monthly_budget_cents // 30) * 10_000, c.enums.BudgetDeliveryMethodEnum.STANDARD
        budget = svc("CampaignBudgetService").mutate_campaign_budgets(customer_id=account, operations=[budget_op]).results[0].resource_name
        camp_op = c.get_type("CampaignOperation")
        cm = camp_op.create
        cm.name, cm.status, cm.campaign_budget = f"ag-{campaign_id[:8]}-v{version}", c.enums.CampaignStatusEnum.PAUSED, budget
        cm.advertising_channel_type = c.enums.AdvertisingChannelTypeEnum.SEARCH
        cm.manual_cpc.enhanced_cpc_enabled = False
        camp = svc("CampaignService").mutate_campaigns(customer_id=account, operations=[camp_op]).results[0].resource_name
        ag_op = c.get_type("AdGroupOperation")
        ag = ag_op.create
        ag.name, ag.campaign, ag.status = f"grp-{request_key[:12]}", camp, c.enums.AdGroupStatusEnum.PAUSED
        group = svc("AdGroupService").mutate_ad_groups(customer_id=account, operations=[ag_op]).results[0].resource_name
        ad_op = c.get_type("AdGroupAdOperation")
        aa = ad_op.create
        aa.ad_group, aa.status = group, c.enums.AdGroupAdStatusEnum.PAUSED
        aa.ad.final_urls.append(landing_url)
        for h in g.get("headlines", []):
            a = c.get_type("AdTextAsset")
            a.text = h
            aa.ad.responsive_search_ad.headlines.append(a)
        for d in g.get("descriptions", []):
            a = c.get_type("AdTextAsset")
            a.text = d
            aa.ad.responsive_search_ad.descriptions.append(a)
        ad = svc("AdGroupAdService").mutate_ad_group_ads(customer_id=account, operations=[ad_op]).results[0].resource_name
        return [{"object_type": "campaign", "platform_id": camp}, {"object_type": "ad_group", "platform_id": group}, {"object_type": "ad", "platform_id": ad}]

    def read_back(self, objects) -> dict:
        c = self._factory()
        ad_rn = next(o["platform_id"] for o in objects if o["object_type"] == "ad")
        cust = ad_rn.split("/")[1]
        q = f"SELECT ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, ad_group_ad.ad.final_urls FROM ad_group_ad WHERE ad_group_ad.resource_name = '{ad_rn}'"  # noqa: S608
        for row in c.get_service("GoogleAdsService").search(customer_id=cust, query=q):
            rsa = row.ad_group_ad.ad.responsive_search_ad
            return {"creative": {"google": {"headlines": [h.text for h in rsa.headlines], "descriptions": [d.text for d in rsa.descriptions]}},
                    "landing_url": next(iter(row.ad_group_ad.ad.final_urls), "")}
        raise ProviderError("ad not found on read-back")

    def _status(self, objects, name: str) -> None:
        c = self._factory()
        for o in objects:
            if o["object_type"] == "campaign":
                op = c.get_type("CampaignOperation")
                op.update.resource_name, op.update.status = o["platform_id"], getattr(c.enums.CampaignStatusEnum, name)
                c.copy_from(op.update_mask, __import__("google.api_core.protobuf_helpers", fromlist=["field_mask"]).field_mask(None, op.update._pb))
                c.get_service("CampaignService").mutate_campaigns(customer_id=o["platform_id"].split("/")[1], operations=[op])

    def activate(self, objects) -> None:
        self._status(objects, "ENABLED")

    def pause(self, objects) -> None:
        self._status(objects, "PAUSED")

    def fetch_metrics(self, account, objects, day) -> dict | None:
        c = self._factory()
        camp = next(o["platform_id"] for o in objects if o["object_type"] == "campaign")
        q = f"SELECT metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions FROM campaign WHERE campaign.resource_name = '{camp}' AND segments.date = '{day}'"  # noqa: S608
        try:
            for row in c.get_service("GoogleAdsService").search(customer_id=account, query=q):
                m = row.metrics
                return {"spend_cents": int(m.cost_micros / 10_000), "impressions": m.impressions, "clicks": m.clicks, "conversions": int(m.conversions)}
        except Exception:  # noqa: BLE001 - a gap is recorded as a gap, never as zero
            return None
        return None

    def send_conversion(self, *, account, click_ids, event, value, request_key, hashed_email) -> str:
        raise ProviderError("offline conversion upload needs a conversion action id: configure before enabling")
