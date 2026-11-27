"""Orchestrator: intake -> fan-out x5 -> budget clock, retries, partial-run policy
-> scoring -> review gate -> append-only storage."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
import logging
from typing import Callable, Optional

from .fixes import build_fixes
from .intake import domain_of, normalize_url
from .log import KEDB, Incident
from .kernel import ACTIVE_MODULES, Budget, Finding, GroupModule, GroupResult, RunContext
from .scoring import build_scorecard, score_delta
from .storage import RunStore

# ASSUMPTION: how the run budget is split. Slices cap each group; the parent cap
# still binds the run total. Decide with real per-group cost data.
DEFAULT_SHARES = {"g01_technical": 0.02, "g02_search": 0.03, "g03_ai_visibility": 0.55,
                  "g04_citations": 0.30, "g05_compliance": 0.10}


def _failed(module: GroupModule, reason: str, detail: str) -> GroupResult:
    """A group that errored or ran late is unverified, never a run failure."""
    return GroupResult(module.group_id, 0.0, module.max_points,
        [Finding("group_run", False, 0, module.max_points, detail, status="unverified")],
        {"reason": reason, "detail": detail}, partial=True)


@dataclass
class Orchestrator:
    store: RunStore
    modules: list[GroupModule] = field(default_factory=lambda: list(ACTIVE_MODULES))
    usd_cap: float = 0.10
    seconds_cap: float = 120.0
    retries: int = 1                       # extra attempts after the first
    backoff_s: float = 0.2
    shares: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SHARES))
    cache_ttl_s: float = 3600.0
    wall_clock: Callable[[], float] = time.time
    kedb: Optional[KEDB] = None            # logs failed and faulty runs when set

    # -- one group, with retries, inside its own worker thread ------------------
    def _run_group(self, module: GroupModule, ctx: RunContext) -> GroupResult:
        last = ""
        for attempt in range(self.retries + 1):
            if ctx.budget.remaining_seconds <= 0:
                return _failed(module, "timeout", "clock expired before attempt")
            try:
                return module.run(ctx)
            except Exception as e:                       # noqa: BLE001 - isolate groups
                last = f"{type(e).__name__}: {e}"
                if attempt < self.retries:
                    time.sleep(min(self.backoff_s * (attempt + 1),
                                   max(ctx.budget.remaining_seconds, 0)))
        return _failed(module, "error", last)

    # -- the run ------------------------------------------------------------------
    def run(self, url: str, firm_name: str, area: str, *, use_cache: bool = True,
            aliases: tuple[str, ...] = ()) -> dict:
        try:
            url = normalize_url(url)
        except ValueError as e:
            self._log_incident(Incident("", "", "intake", "invalid_input", str(e)))
            raise
        domain = domain_of(url)
        now = self.wall_clock()

        prev = self.store.latest(domain)
        # only a complete run is reusable: a run made before a key was added must not be served again
        if (use_cache and prev and prev["scorecard"].get("complete")
                and now - prev["created_at"] < self.cache_ttl_s):
            return {**prev, "cached": True}

        budget = Budget(self.usd_cap, self.seconds_cap)
        base = RunContext(url, firm_name, area, budget, domain=domain, aliases=aliases)

        pool = ThreadPoolExecutor(max_workers=max(len(self.modules), 1))
        futures = {}
        for m in self.modules:
            gctx = replace(base, budget=budget.slice(self.shares.get(m.group_id, 0.1)))
            futures[pool.submit(self._run_group, m, gctx)] = m
        done, _late = wait(futures, timeout=max(budget.remaining_seconds, 0))
        pool.shutdown(wait=False, cancel_futures=True)   # late workers are abandoned

        results: dict[str, GroupResult] = {}
        for fut, m in futures.items():
            if fut in done:
                try:
                    results[m.group_id] = fut.result()
                except Exception as e:                   # noqa: BLE001
                    results[m.group_id] = _failed(m, "error", f"{type(e).__name__}: {e}")
            else:
                results[m.group_id] = _failed(m, "timeout", "late; result discarded")

        card = build_scorecard(results)
        record = {
            "run_id": uuid.uuid4().hex, "domain": domain, "url": url,
            "firm_name": firm_name, "area": area, "created_at": now, "cached": False,
            "duration_s": round(budget.seconds_cap - budget.remaining_seconds, 3),
            "spent_usd": round(budget.spent_usd, 6),
            "scorecard": card, "delta": score_delta(prev["scorecard"] if prev else None, card),
            "results": {g: asdict(r) for g, r in results.items()},
            **self._review_gate(results),
        }
        # internal list: includes held compliance items, marked held. Client output re-filters.
        record["fixes"] = build_fixes(record, include_held=True)
        record["incidents"] = self._log_run(record)
        self.store.append(record)
        return record

    # -- KEDB hooks: logging must never break a run -----------------------------------
    def _log_run(self, record: dict) -> list[dict]:
        if not self.kedb:
            return []
        try:
            return self.kedb.record_run(record)
        except Exception:                                  # noqa: BLE001
            logging.getLogger("advisorgro").exception("KEDB logging failed")
            return []

    def _log_incident(self, inc: Incident) -> None:
        if self.kedb:
            try:
                self.kedb.record_incident(inc)
            except Exception:                              # noqa: BLE001
                logging.getLogger("advisorgro").exception("KEDB logging failed")

    # -- Glynac gate: material compliance flags wait for the CCO ----------------------
    @staticmethod
    def _review_gate(results: dict[str, GroupResult]) -> dict:
        g5 = results.get("g05_compliance")
        held = list((g5.evidence.get("material_flags") if g5 else None) or [])
        if held:
            status = "pending_cco_review"
        elif g5 is None or g5.partial:
            status = "compliance_unverified"      # a scan that did not run is not a clean scan
        else:
            status = "releasable"
        return {"held_flags": held, "release_status": status}