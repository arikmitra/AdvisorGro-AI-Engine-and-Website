"""Async fan-out x5 under a time budget (the MVP orchestrator, moved off threads per guide 3.3).

A group that is late, errors or raises becomes *unverified* and the run continues; a group is never "failed".
There is no USD cap: spend is metered per run and group, not a stop. The same group callables run as one
queue/Temporal activity each in production; this function is also the in-process runner for dev and tests.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from .mvp.kernel import Budget, GroupModule, GroupResult, RunContext
from .mvp.orchestrator import _failed

log = logging.getLogger(__name__)
SLICES = {"g01_technical": 0.6, "g02_search": 0.7, "g03_ai_visibility": 1.0, "g04_citations": 0.9, "g05_compliance": 0.8}
NO_CAP_USD = 1e9


@dataclass
class RunOutcome:
    results: dict[str, GroupResult]
    spend_usd: dict[str, float]
    duration_s: float


async def run_groups(modules: list[GroupModule], url: str, firm: str, area: str, *, seconds_cap: float = 120.0,
                     domain: str = "", aliases: tuple[str, ...] = (), retries: int = 1, backoff_s: float = 0.2) -> RunOutcome:
    t0 = time.monotonic()
    budget = Budget(NO_CAP_USD, seconds_cap)
    base = RunContext(url, firm, area, budget, domain=domain, aliases=aliases)
    pool = ThreadPoolExecutor(max_workers=max(len(modules), 1), thread_name_prefix="shield-group")
    loop = asyncio.get_running_loop()
    ctxs = {m.group_id: replace(base, budget=budget.slice(1.0)) for m in modules}

    def attempt(m: GroupModule) -> GroupResult:
        last = ""
        for i in range(retries + 1):
            if ctxs[m.group_id].budget.remaining_seconds <= 0:
                return _failed(m, "timeout", "clock expired before attempt")
            try:
                return m.run(ctxs[m.group_id])
            except Exception as e:  # noqa: BLE001 - isolate groups
                last = f"{type(e).__name__}: {e}"
                if i < retries:
                    time.sleep(min(backoff_s * (i + 1), max(ctxs[m.group_id].budget.remaining_seconds, 0)))
        return _failed(m, "error", last)

    async def one(m: GroupModule) -> GroupResult:
        slice_s = max(1.0, seconds_cap * SLICES.get(m.group_id, 1.0))
        try:
            return await asyncio.wait_for(loop.run_in_executor(pool, attempt, m), timeout=slice_s)
        except TimeoutError:
            return _failed(m, "timeout", "late; result discarded")
        except Exception as e:  # noqa: BLE001
            return _failed(m, "error", f"{type(e).__name__}: {e}")

    try:
        outs = await asyncio.gather(*[one(m) for m in modules])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # late workers are abandoned, as in the MVP
    results = {m.group_id: r for m, r in zip(modules, outs, strict=True)}
    spend = {gid: round(c.budget.spent_usd, 6) for gid, c in ctxs.items()}
    return RunOutcome(results, spend, round(time.monotonic() - t0, 3))
