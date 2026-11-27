"""Placeholder for a group that has no real data source yet.

It reports itself as not built instead of inventing a score, so a total never
includes fake points.
"""
from __future__ import annotations

from dataclasses import dataclass

from .kernel import Finding, GroupResult, RunContext


@dataclass
class NotImplementedGroup:
    group_id: str
    max_points: float
    needs: str

    def run(self, ctx: RunContext) -> GroupResult:
        return GroupResult(self.group_id, 0.0, self.max_points,
            [Finding("not_implemented", False, 0, self.max_points,
                     f"not built yet; needs {self.needs}", status="unverified")],
            {"reason": "not_implemented", "needs": self.needs}, partial=True)
