"""Measurement: metrics collector, rule-based optimiser (the model only explains) and the verified weekly report.
Numbers always come from code. A metrics gap is stored as a gap ("not verified"), never as zero."""
from __future__ import annotations

import asyncio
import logging as _l
import re
from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select

from ...core.clock import utcnow
from ...core.errors import Conflict, ProviderError
from ...db.models import AdObject, Booking, Campaign, Lead, MetricsDaily, OptimiserProposal
from ...db.session import session_scope
from ...security.activity import log_activity

log = _l.getLogger(__name__)


class _Expl(BaseModel):
    explanation: str = ""


class _Narr(BaseModel):
    narrative: str = ""


class Measurement:
    def __init__(self, c):
        self.c = c

    def collect(self, tenant: str, campaign_id: str, day: date) -> bool:
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            camp = s.get(Campaign, campaign_id)
            if camp.live_version is None:
                return False
            objs = [{"object_type": o.object_type, "platform_id": o.platform_id} for o in s.execute(select(AdObject).where(
                AdObject.campaign_id == campaign_id, AdObject.version == camp.live_version, AdObject.platform == camp.platform)).scalars()]
            platform, acct = camp.platform, "acct"
        try:
            m = self.c.connectors[platform].fetch_metrics(acct, objs, day.isoformat())
        except ProviderError:
            m = None
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            row = s.execute(select(MetricsDaily).where(MetricsDaily.campaign_id == campaign_id, MetricsDaily.day == day, MetricsDaily.platform == platform)).scalar_one_or_none()
            if not row:
                row = MetricsDaily(campaign_id=campaign_id, day=day, platform=platform)
                s.add(row)
            if m is None:
                row.verified = False  # a gap, not a zero
            else:
                row.spend_cents, row.impressions, row.clicks, row.platform_conversions, row.verified = m["spend_cents"], m["impressions"], m["clicks"], m["conversions"], True
        return m is not None

    def funnel(self, tenant: str, campaign_id: str, days: int = 7) -> dict:
        since = utcnow() - timedelta(days=days)
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            rows = s.execute(select(MetricsDaily).where(MetricsDaily.campaign_id == campaign_id, MetricsDaily.day >= (utcnow() - timedelta(days=days)).date())).scalars().all()
            good = [r for r in rows if r.verified]
            leads = s.execute(select(func.count()).select_from(Lead).where(Lead.campaign_id == campaign_id, Lead.created_at >= since)).scalar_one()
            booked = s.execute(select(func.count()).select_from(Lead).where(Lead.campaign_id == campaign_id, Lead.created_at >= since, Lead.state == "booked")).scalar_one()
            _ = Booking
            spend = sum(r.spend_cents for r in good)
            return {"spend_cents": spend, "clicks": sum(r.clicks for r in good), "impressions": sum(r.impressions for r in good), "leads": leads, "bookings": booked,
                    "cost_per_lead_cents": round(spend / leads) if leads and good else None, "days_without_data": len(rows) - len(good)}

    # ------------------------------------------------------------------ optimiser: rules decide, model explains
    def propose(self, tenant: str, campaign_id: str, *, target_cpl_cents: int = 15000, min_clicks: int = 30) -> list[dict]:
        f = self.funnel(tenant, campaign_id, 14)
        props = []
        if f["clicks"] >= min_clicks and f["cost_per_lead_cents"] and f["cost_per_lead_cents"] > 2 * target_cpl_cents:
            props.append(("pause_ad", {"reason": "cost per lead above twice the target", **f}))
        if f["clicks"] >= min_clicks and f["leads"] == 0:
            props.append(("new_variant", {"reason": "clicks without leads: try a new angle (restarts at pre-check)", **f}))
        out = []
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            for kind, payload in props:
                expl = self._explain(kind, payload)
                p = OptimiserProposal(campaign_id=campaign_id, kind=kind, payload=payload, explanation=expl)
                s.add(p)
                s.flush()
                out.append({"id": p.id, "kind": kind, "explanation": expl})
        return out

    def _explain(self, kind: str, payload: dict) -> str:
        fallback = f"Rule fired: {payload.get('reason', kind)}."
        gw = self.c.gateway
        if gw is None:
            return fallback
        try:
            pr, _ = self.c.prompts.render("optimiser_explain", proposal=f"{kind}: {payload}")
            return asyncio.run(gw.complete_json("Explain.", pr, _Expl, tags={"stage": "optimiser"})).explanation or fallback
        except Exception:  # noqa: BLE001
            return fallback

    def apply(self, tenant: str, proposal_id: str, actor: str = "optimiser") -> str:
        """The optimiser can pause or shrink spend. It can never launch, raise spend above the ceiling or change creative."""
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            p = s.get(OptimiserProposal, proposal_id)
            if p.kind == "new_variant":
                p.state = "needs_precheck"  # a new variant is a new version: full pipeline and CCO approval
                return p.state
            if p.kind == "budget_shift" and p.payload.get("new_budget_cents", 0) > s.get(Campaign, p.campaign_id).budget_ceiling_cents:
                p.state = "rejected"
                raise Conflict("proposal exceeds the client's budget ceiling")
            cid = p.campaign_id
        if p.kind == "pause_ad":
            self.c.publisher.pause_ads(tenant, cid, "optimiser: cost per lead too high", actor=actor)
        with session_scope(self.c.sm, tenant_id=tenant) as s:
            s.get(OptimiserProposal, proposal_id).state = "applied"
            log_activity(s, actor=actor, action=f"optimiser.{p.kind}", tenant_id=tenant, resource=f"campaign:{cid}")
        return "applied"

    # ------------------------------------------------------------------ weekly report: code numbers, model words, verified
    def weekly_report(self, tenant: str, campaign_id: str) -> dict:
        f = self.funnel(tenant, campaign_id, 7)
        numbers = {k: v for k, v in f.items() if v is not None}
        narrative = f"This week: {f['leads']} lead(s), {f['bookings']} booking(s) from ${f['spend_cents'] / 100:,.0f} of spend."
        gw = self.c.gateway
        if gw is not None:
            try:
                pr, _ = self.c.prompts.render("weekly_narrative", numbers=numbers)
                text = asyncio.run(gw.complete_json("Summarise.", pr, _Narr, tags={"stage": "report"})).narrative
                if text and numbers_ok(text, numbers):  # every figure the model wrote must exist in the code's numbers
                    narrative = text
            except Exception:  # noqa: BLE001 - the numbers-only template is used instead
                log.debug("narrative skipped", exc_info=True)
        return {"campaign_id": campaign_id, "numbers": numbers, "narrative": narrative, "verified": f["days_without_data"] == 0}


def numbers_ok(text: str, numbers: dict) -> bool:
    allowed: set[str] = set()
    for k, v in numbers.items():
        allowed |= {str(v), f"{v:,}" if isinstance(v, int) else str(v)}
        if k.endswith("_cents") and isinstance(v, int):
            allowed |= {f"{v / 100:,.0f}", f"{v / 100:,.2f}", f"{v / 100:g}"}
    found = re.findall(r"\d[\d,]*\.?\d*", text)
    return all(x.rstrip(".,") in allowed for x in found)
