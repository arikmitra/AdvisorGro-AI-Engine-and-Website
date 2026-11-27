"""Collector: asks engines (several at once), caches raw answers, and stops cleanly
when the budget or the clock runs out.

Fixes in this version:
- the cache expires (default 15 min), so Shield re-runs are not served frozen answers;
- one engine failing fatally is dropped on its own instead of aborting every engine;
- the clock margin is 20 s, longer than a wave can overrun, so a late wave no longer
  gets the whole group discarded by the orchestrator.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..kernel import Budget
from .engines import Answer, AnswerEngine


class QueryCache:
    """Raw answers land here before grading (private to G03)."""

    def __init__(self, ttl_s: float = 900.0, clock=time.monotonic):
        self._d: dict[tuple[str, str, int], tuple[float, Answer]] = {}
        self.ttl_s, self._clock = ttl_s, clock

    def get(self, engine: str, prompt: str, repeat: int):
        hit = self._d.get((engine, prompt, repeat))
        return hit[1] if hit and self._clock() - hit[0] < self.ttl_s else None

    def put(self, a: Answer) -> None:
        self._d[(a.engine, a.prompt, a.repeat)] = (self._clock(), a)

    def __len__(self):
        return len(self._d)


FULL_REPEATS = 5                      # how many times each prompt is asked in the richest plan


@dataclass(frozen=True)
class Plan:
    engines: tuple[AnswerEngine, ...]
    repeats: int
    degraded: bool


@dataclass(frozen=True)
class CallError:
    engine: str
    message: str
    fatal: bool


def choose_plan(engines: list[AnswerEngine], n_prompts: int, budget: Budget,
                max_engines: int = 3, repeats: int = FULL_REPEATS, fallback_repeats: int = 2) -> Plan:
    """The richest plan the budget allows. Anything below the first tier is degraded."""
    es = tuple(engines[:max_engines])

    def cost(e, r):
        return sum(x.cost_per_call for x in e) * n_prompts * r

    tiers = [(es, repeats), (es, fallback_repeats), (es[:2], fallback_repeats),
             (es[:1], fallback_repeats), (es[:1], 1)]
    seen = set()
    for i, (e, r) in enumerate(tiers):
        if not e or (len(e), r) in seen:
            continue
        seen.add((len(e), r))
        if budget.can_afford(cost(e, r)):
            return Plan(e, r, degraded=i > 0)
    return Plan(es[:1], 1, True)          # nothing fits: collect() stops at the cut-off


def _call(task, budget: Budget, cache: QueryCache):
    e, p, r = task
    try:
        a = e.ask(p, r)
    except Exception as ex:                                     # noqa: BLE001
        return CallError(e.name, f"{type(ex).__name__}: {ex}", bool(getattr(ex, "fatal", False)))
    budget.charge(a.cost_usd)
    cache.put(a)
    return a


def collect(prompts: list[str], plan: Plan, budget: Budget, cache: QueryCache,
            workers: int = 4, margin_s: float = 20.0):
    """Repeat-major order, in waves of `workers` concurrent calls, so a cut-off run still
    covers every prompt and engine. Returns (answers, cut_off, errors)."""
    tasks = [(e, p, r) for r in range(plan.repeats) for p in prompts for e in plan.engines]
    answers: list[Answer] = []
    errors: list[CallError] = []
    dead: set[str] = set()                 # engines that failed fatally (bad key, bad model name)
    cut_off, i = False, 0
    while i < len(tasks) and not cut_off:
        wave, est = [], 0.0
        while i < len(tasks) and len(wave) < workers:
            e, p, r = tasks[i]
            if e.name in dead:
                i += 1
                continue
            hit = cache.get(e.name, p, r)
            if hit:
                answers.append(hit)                               # cache hits cost nothing
                i += 1
                continue
            if budget.remaining_seconds < margin_s or not budget.can_afford(est + e.cost_per_call):
                cut_off = True
                break
            wave.append(tasks[i])
            est += e.cost_per_call
            i += 1
        if wave:
            with ThreadPoolExecutor(len(wave)) as pool:
                for res in pool.map(lambda t: _call(t, budget, cache), wave):
                    (answers if isinstance(res, Answer) else errors).append(res)
            dead.update(x.engine for x in errors if x.fatal)
            if len(dead) >= len(plan.engines):
                break                                              # every engine is dead: stop spending time
    return answers, cut_off, errors