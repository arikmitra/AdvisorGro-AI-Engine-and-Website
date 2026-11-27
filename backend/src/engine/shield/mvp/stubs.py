"""Stand-ins for groups 01, 02, 04, 05 until the real ones exist."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .kernel import Finding, GroupResult, RunContext


@dataclass
class StubGroup:
    group_id: str
    max_points: float
    score: float
    delay_s: float = 0.0
    fail_times: int = 0               # raise this many times before succeeding
    cost_usd: float = 0.0
    evidence: dict = field(default_factory=dict)
    _calls: int = 0

    def run(self, ctx: RunContext) -> GroupResult:
        self._calls += 1
        if self._calls <= self.fail_times:
            raise RuntimeError("stub failure")
        if self.delay_s:
            time.sleep(self.delay_s)
        ctx.budget.charge(self.cost_usd)
        return GroupResult(self.group_id, self.score, self.max_points,
            [Finding("stub", True, self.score, self.max_points)], dict(self.evidence))


def default_stubs() -> list[StubGroup]:
    return [StubGroup("g01_technical", 20, 17), StubGroup("g02_search", 20, 9),
            StubGroup("g04_citations", 20, 13), StubGroup("g05_compliance", 15, 10)]
