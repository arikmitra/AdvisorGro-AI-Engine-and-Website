"""Run record: scorecard, delta vs the last run, review gate and the internal fix list.
Reuses the MVP scoring, gate and fix logic so a score means the same thing it always has."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict

from .mvp.fixes import build_fixes
from .mvp.kernel import GroupResult
from .mvp.orchestrator import Orchestrator as _Mvp
from .mvp.scoring import build_scorecard, score_delta


def assemble(results: dict[str, GroupResult], *, domain: str, url: str, firm_name: str, area: str, spend_usd: float,
             duration_s: float, run_id: str | None = None, prev: dict | None = None, now: float | None = None) -> dict:
    card = build_scorecard(results)
    record = {
        "run_id": run_id or uuid.uuid4().hex, "domain": domain, "url": url, "firm_name": firm_name, "area": area,
        "created_at": now or time.time(), "cached": False, "duration_s": duration_s, "spent_usd": round(spend_usd, 6),
        "scorecard": card, "delta": score_delta(prev["scorecard"] if prev else None, card),
        "results": {g: asdict(r) for g, r in results.items()},
        **_Mvp._review_gate(results),  # held_flags + release_status (a scan that did not run is not a clean scan)
    }
    record["fixes"] = build_fixes(record, include_held=True)  # internal list: keeps held compliance items
    record["incidents"] = []
    return record


def group_status(r: dict) -> str:
    fs = r.get("findings") or []
    if r.get("partial") and fs and all(f.get("status") in ("unverified", "manual") for f in fs):
        return "unverified"
    return "partial" if r.get("partial") else "verified"
