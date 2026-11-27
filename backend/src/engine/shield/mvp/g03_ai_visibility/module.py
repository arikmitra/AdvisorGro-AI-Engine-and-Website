"""G03 entry point: run(ctx) -> GroupResult, registered in ACTIVE_MODULES.

Fix in this version: 'partial' now means thin evidence (cut off, call errors, or fewer than
`min_answers` answers). A smaller-but-adequate plan is recorded in the evidence as `degraded`
but no longer marks the group partial, so Shield can compute run-to-run deltas for this group.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..kernel import Finding, GroupResult, RunContext, register
from .collector import FULL_REPEATS, QueryCache, choose_plan, collect
from .engines import AnswerEngine
from .grader import grade
from .planner import build_battery


@dataclass
class Config:
    max_points: float = 25.0
    full_marks_at_som: float = 50.0      # ASSUMPTION, to be confirmed
    battery_size: int = 6
    budget_share: float = 1.0            # fraction of the run budget G03 may use
    min_answers: int = 10                # ASSUMPTION: below this the SoM is too thin to trust
    workers: int = 4                     # concurrent engine calls
    max_engines: int = 3                 # most engines asked in one run (cost control); more can be configured


def margin_points(share_pct: float, n: int) -> float:
    """Rough 95% margin of error, in percentage points, for a share measured from n answers (Wilson interval).
    Approximate: answers to one prompt are not fully independent, so treat it as a guide, not a guarantee."""
    if n <= 0:
        return 100.0
    p, z = min(max(share_pct / 100.0, 0.0), 1.0), 1.96
    return round(100 * z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n), 1)


@dataclass
class AIVisibility:
    engines: list[AnswerEngine]
    config: Config = field(default_factory=Config)
    cache: QueryCache = field(default_factory=QueryCache)
    group_id: str = "g03_ai_visibility"

    @property
    def max_points(self) -> float:
        return self.config.max_points

    def score(self, som: float) -> float:
        c = self.config
        return round(c.max_points * min(1.0, som / c.full_marks_at_som), 2)

    def run(self, ctx: RunContext) -> GroupResult:
        c = self.config
        budget = ctx.budget.slice(c.budget_share)
        prompts = build_battery(ctx.area, c.battery_size)
        plan = choose_plan(self.engines, len(prompts), budget, max_engines=c.max_engines)
        answers, cut_off, errors = collect(prompts, plan, budget, self.cache, c.workers)
        thin = len(answers) < c.min_answers
        partial = cut_off or bool(errors) or thin

        if not answers and errors:
            msg = errors[0].message
            return GroupResult(self.group_id, 0.0, c.max_points,
                [Finding("firm_named_in_category_prompts", False, 0, c.max_points,
                         f"every call failed; first error: {msg}", status="unverified")],
                {"reason": "error", "detail": msg, "errors": len(errors)}, partial=True)
        if not answers:
            return GroupResult(self.group_id, 0.0, c.max_points,
                [Finding("firm_named_in_category_prompts", False, 0, c.max_points,
                         "no answers collected", status="unverified")],
                {"plan": "none"}, partial=True)

        g = grade(answers, ctx.firm_name, ctx.domain, ctx.aliases)
        pts = self.score(g.share_of_model)
        finding = Finding("firm_named_in_category_prompts", pts >= c.max_points,
            pts, c.max_points,
            f"SoM {g.share_of_model:.1f}% ({g.named}/{g.total} answers across {len(plan.engines)} engine(s) x {plan.repeats} repeat(s)); site cited in {g.cited}"
            + (f"; {len(errors)} call(s) failed" if errors else "")
            + ("; too few answers to trust" if thin else ""),
            status="partial" if partial else "ok")
        evidence = {"engines": [e.name for e in plan.engines], "repeats": plan.repeats,
                    "degraded": plan.degraded, "cut_off": cut_off, "thin": thin,
                    "share_of_model": g.share_of_model, "answers": len(answers),
                    "spent_usd": round(budget.spent_usd, 6), "errors": len(errors),
                    "first_error": errors[0].message if errors else "",
                    "cited_in_answers": g.cited,
                    "prompts": prompts,
                    "more_with_budget": self._more_with_budget(ctx, plan, prompts, g, len(answers), cut_off, thin, budget)}
        return GroupResult(self.group_id, pts, c.max_points, [finding], evidence, partial)


    def _more_with_budget(self, ctx: RunContext, plan, prompts: list[str], g, n_answers: int, cut_off: bool, thin: bool,
                          budget) -> dict:
        """What a bigger budget (or a higher engine cap) would add: more AI assistants, more repeats, a tighter margin.
        `applies` is False when this run already used the richest plan."""
        c = self.config
        wanted = tuple(self.engines)                                       # everything configured, asked in full
        in_cap = tuple(self.engines[:c.max_engines])
        not_asked = [e.name for e in in_cap if e not in plan.engines]      # held back by money
        beyond_cap = [e.name for e in self.engines[c.max_engines:]]        # held back by ADVISORGRO_MAX_ENGINES
        full_answers = len(wanted) * len(prompts) * FULL_REPEATS
        reasons = ([f"fewer repeats ({plan.repeats} of {FULL_REPEATS})"] if plan.repeats < FULL_REPEATS else []) \
            + ([f"{len(not_asked)} engine(s) not asked"] if not_asked else []) \
            + (["stopped early by the budget or the clock"] if cut_off else []) \
            + (["engines beyond the cap were not asked"] if beyond_cap else [])
        if not reasons or full_answers <= n_answers:
            return {"applies": False}
        full_cost = round(sum(e.cost_per_call for e in wanted) * len(prompts) * FULL_REPEATS, 4)
        # the group gets a share of the run budget; say what the whole run would need for the full plan
        root, share = ctx.budget, c.budget_share
        while root.parent is not None:
            root = root.parent
        group_share = (ctx.budget.usd_cap / root.usd_cap) * share if root.usd_cap else 0.0
        suggested = round(math.ceil(full_cost / group_share * 100) / 100, 2) if group_share > 0 else None
        m_now, m_full = margin_points(g.share_of_model, n_answers), margin_points(g.share_of_model, full_answers)
        return {"applies": True, "reasons": reasons,
                "current": {"engines": len(plan.engines), "repeats": plan.repeats, "answers": n_answers, "margin_pts": m_now},
                "full": {"engines": len(wanted), "repeats": FULL_REPEATS, "answers": full_answers,
                         "margin_pts": m_full, "est_cost_usd": full_cost},
                "not_asked": not_asked, "beyond_cap": beyond_cap, "suggested_run_budget_usd": suggested}


def make_module(engines: list[AnswerEngine], config: Config | None = None) -> AIVisibility:
    return register(AIVisibility(engines, config or Config()))  # type: ignore[return-value]