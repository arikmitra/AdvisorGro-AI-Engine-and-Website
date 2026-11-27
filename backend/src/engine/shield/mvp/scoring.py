"""Scoring engine: group scores -> 0-100, plus run-to-run deltas for Shield."""
from __future__ import annotations

from typing import Optional

from .kernel import GroupResult

EXPECTED_GROUPS = ("g01_technical", "g02_search", "g03_ai_visibility",
                   "g04_citations", "g05_compliance")


EXPECTED_MAX = {"g01_technical": 20.0, "g02_search": 20.0, "g03_ai_visibility": 25.0,
                "g04_citations": 20.0, "g05_compliance": 15.0}


def build_scorecard(results: dict[str, GroupResult],
                    expected: tuple[str, ...] = EXPECTED_GROUPS) -> dict:
    by_group, total, max_total = {}, 0.0, 0.0
    for gid, r in results.items():
        score = max(0.0, min(r.score, r.max_points))      # clamp misbehaving groups
        by_group[gid] = {"score": round(score, 2), "max": r.max_points, "partial": r.partial}
        total += score
        max_total += r.max_points
    missing = [g for g in expected if g not in results]
    # a group that never reported still counts toward the maximum, so 62/80 cannot pass for 62/100
    max_total += sum(EXPECTED_MAX.get(g, 0.0) for g in missing)
    partial = sorted(g for g, v in by_group.items() if v["partial"])
    return {"total": round(total, 2), "max_total": max_total, "by_group": by_group,
            "partial_groups": partial, "missing_groups": missing,
            # a partial or missing group means the total is a lower bound
            "complete": not partial and not missing}


def score_delta(prev: Optional[dict], curr: dict) -> dict:
    """Per-group change since the last run. None where either side is partial,
    so Shield never alerts on a gap in coverage."""
    if not prev:
        return {"total": None, "by_group": {}}
    out = {}
    for gid, c in curr["by_group"].items():
        p = prev["by_group"].get(gid)
        out[gid] = None if (not p or p["partial"] or c["partial"]) \
            else round(c["score"] - p["score"], 2)
    total = None if prev["partial_groups"] or curr["partial_groups"] \
        else round(curr["total"] - prev["total"], 2)
    return {"total": total, "by_group": out}