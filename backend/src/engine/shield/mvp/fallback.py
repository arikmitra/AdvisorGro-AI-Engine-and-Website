"""Wraps a check group so a scraping version runs when the real one produced nothing usable.

A group is 'blank' when it measured nothing: it raised, or every one of its findings is unverified (a missing key, a
dead provider, every call failing). A group that measured something, even thinly, is kept as it is: partial real data
beats a substitute. The substitute's result is marked in its evidence (`scrape`), so the report can say how it was made.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .kernel import GroupResult, RunContext


def is_blank(res: GroupResult) -> bool:
    return not res.findings or all(f.status == "unverified" for f in res.findings)


def why_blank(res: GroupResult) -> str:
    ev = res.evidence or {}
    return str(ev.get("detail") or ev.get("reason") or (res.findings[0].detail if res.findings else "no findings"))[:300]


@dataclass
class FallbackGroup:
    primary: Any
    fallback: Any

    @property
    def group_id(self) -> str:
        return self.primary.group_id

    @property
    def max_points(self) -> float:
        return self.primary.max_points

    def run(self, ctx: RunContext) -> GroupResult:
        first: GroupResult | None = None
        reason = ""
        try:
            first = self.primary.run(ctx)
        except Exception as e:                                   # noqa: BLE001 - the substitute gets its chance
            reason = f"{type(e).__name__}: {e}"
        if first is not None:
            if not is_blank(first):
                return first
            reason = why_blank(first)
        try:
            alt = self.fallback.run(ctx)
        except Exception as e:                                   # noqa: BLE001
            if first is None:
                raise
            first.evidence.setdefault("scrape", {"used": False, "why": reason, "failed": f"{type(e).__name__}: {e}"})
            return first
        if is_blank(alt) and first is not None:
            first.evidence["scrape"] = {"used": False, "why": reason, "failed": why_blank(alt)}
            return first                                          # the primary's reason is the actionable one
        alt.evidence["primary_failure"] = reason
        sc = alt.evidence.setdefault("scrape", {"used": True})
        sc.setdefault("why", reason)
        return alt
