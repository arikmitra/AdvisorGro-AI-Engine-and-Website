"""Group 03 stand-in when no AI assistant can be asked (no key, or every call failed).

It cannot measure what AI assistants say. It measures something related and checkable: does the firm appear in
ordinary web search results for the same buyer-intent questions the AI battery asks? AI assistants draw on the same
public web, so a firm that never appears in these results is unlikely to be named by them. It is a proxy and is
labelled as one:

  * its own check id (`firm_named_in_category_searches`), not the AI check's id;
  * scored at no more than PROXY_CAP of the group's points, because it is not the real measurement;
  * the group is marked partial, so the scorecard shows a lower bound and a person reviews it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..kernel import Finding, GroupResult, RunContext
from ..search import SearchError, host_matches
from .engines import Answer
from .grader import grade
from .planner import build_battery

CHECK = "firm_named_in_category_searches"
PROXY_CAP = 0.6               # ASSUMPTION: a search-result proxy may earn at most this share of the group's points
FULL_MARKS_AT_SHARE = 50.0    # same yardstick as the AI check: named in half the searches = full proxy marks


@dataclass
class SerpVisibility:
    search: Any                             # anything with .search(query, count)
    battery_size: int = 6
    group_id: str = "g03_ai_visibility"
    max_points: float = 25.0

    def run(self, ctx: RunContext) -> GroupResult:
        prompts = build_battery(ctx.area, self.battery_size)
        answers: list[Answer] = []
        errors: list[str] = []
        sources: set[str] = set()
        for i, q in enumerate(prompts):
            if ctx.budget.remaining_seconds < 10:
                errors.append("out of time")
                break
            try:
                res = self.search.search(re.sub(r"\?$", "", q), 10)
            except SearchError as e:
                errors.append(str(e))
                continue
            sources.add(getattr(res, "source", "") or "search")
            text = " ".join(f"{r.title}. {r.snippet}" for r in res)
            answers.append(Answer("web search", q, i, text, tuple(r.url for r in res)))
        if not answers:
            msg = errors[0] if errors else "no results"
            return GroupResult(self.group_id, 0.0, self.max_points,
                [Finding(CHECK, False, 0, self.max_points, f"web search could not be read: {msg}", status="unverified")],
                {"reason": "error", "detail": msg, "scrape": {"used": True, "why": "search unavailable"}}, partial=True)
        g = grade(answers, ctx.firm_name, ctx.domain, ctx.aliases)
        listed = sum(any(host_matches((r.split("//")[-1].split("/")[0]).removeprefix("www."), ctx.domain) for r in a.cited_urls)
                     for a in answers)
        share = g.share_of_model
        pts = round(self.max_points * PROXY_CAP * min(1.0, share / FULL_MARKS_AT_SHARE), 2)
        detail = (f"Named or linked in {g.named} of {g.total} web searches for the questions buyers ask about advisors "
                  f"({share:.0f}%); site listed in {listed}. Stand-in for asking AI assistants directly, which needs a model key"
                  + (f"; {len(errors)} search(es) failed" if errors else ""))
        ev = {"share_of_searches": round(share, 1), "searches": g.total, "named": g.named, "site_listed": listed,
              "prompts": prompts, "errors": len(errors), "first_error": errors[0] if errors else "",
              "proxy_cap": PROXY_CAP, "sources": sorted(sources),
              "scrape": {"used": True, "why": "no AI assistant could be asked", "proxy": True}}
        return GroupResult(self.group_id, pts, self.max_points, [Finding(CHECK, False, pts, self.max_points, detail, status="partial")],
                           ev, partial=True)
