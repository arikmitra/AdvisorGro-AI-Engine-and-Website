"""Shield alert rules: thresholds live in a table (tunable without a deploy); logic is the MVP's build_alerts.
Technical faults go to the client; compliance items go to the reviewer or CCO only ("internal").
Unverified checks never raise an alert, and incomplete runs carry no score delta."""
from __future__ import annotations

import logging as _l
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.clock import utcnow
from ...core.events import emit
from ...db.models import Alert, AlertRule
from .mvp import shield as mvp_shield

log = _l.getLogger(__name__)

DEFAULTS = {"cert_amber_days": (30.0, "client"), "domain_amber_days": (30.0, "client"), "score_drop": (-3.0, "client")}
_lock = threading.Lock()


def seed_rules(s: Session) -> None:
    for k, (th, route) in DEFAULTS.items():
        if not s.get(AlertRule, k):
            s.add(AlertRule(key=k, threshold=th, route=route))
    s.flush()


def week_key(dt=None) -> str:
    y, w, _ = (dt or utcnow()).isocalendar()
    return f"{y}-W{w:02d}"


def raise_alerts(s: Session, *, run, record: dict, prev: dict | None, extras: dict) -> list[Alert]:
    if not run.firm_id:
        return []
    seed_rules(s)
    rules = {r.key: r.threshold for r in s.execute(select(AlertRule).where(AlertRule.enabled.is_(True))).scalars()}
    with _lock:  # the MVP rules read module constants; set them from the table for this call
        mvp_shield.CERT_AMBER_DAYS = int(rules.get("cert_amber_days", 30))
        mvp_shield.DOMAIN_AMBER_DAYS = int(rules.get("domain_amber_days", 30))
        mvp_shield.SCORE_DROP_ALERT = float(rules.get("score_drop", -3.0))
        drafts = mvp_shield.build_alerts(record, prev, extras)
    out: list[Alert] = []
    for d in drafts:
        try:
            with s.begin_nested():
                a = Alert(firm_id=run.firm_id, run_id=run.id, kind=d["key"], route=d["audience"], week_key=week_key(),
                          payload={"severity": d["severity"], "title": d["title"], "detail": d["detail"]})
                s.add(a)
                s.flush()
            out.append(a)
            emit(s, "AlertRaised", tenant_id=run.tenant_id, firm_id=run.firm_id, idempotency_key=f"{run.firm_id}:{d['key']}:{a.week_key}",
                 payload={"alert_id": a.id, "route": a.route})
        except Exception:  # noqa: BLE001 - unique (firm, kind, week): already alerted this week
            log.debug("alert already raised this week")
            continue
    return out
