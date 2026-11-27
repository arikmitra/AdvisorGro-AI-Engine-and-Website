"""Offer recommendation from a run record (vendored from the MVP emailer)."""
from __future__ import annotations

from typing import Optional


def recommend(record: dict, goal: str = "") -> tuple[str, str]:
    """(offer, reason). ASSUMPTION: these thresholds are a first rule set, not product policy.
    Only groups that completed count: an unverified area is never read as a weak one."""
    by = record["scorecard"]["by_group"]

    def pct(g: str) -> Optional[float]:
        v = by.get(g)
        return None if not v or v["partial"] or not v["max"] else v["score"] / v["max"]

    def mean(gs: tuple[str, ...]) -> Optional[float]:
        xs = [p for p in map(pct, gs) if p is not None]
        return sum(xs) / len(xs) if xs else None

    vis, prot = mean(("g02_search", "g03_ai_visibility")), mean(("g01_technical", "g04_citations", "g05_compliance"))
    if vis is None and prot is None:
        return "A short call to decide", "too little could be verified automatically to point you to one service"
    g = (goal or "").lower()
    if prot is not None and prot < 0.70:
        return "Shield", "the website, listings or disclosures need fixing first; ads and search work harder on a sound base"
    if vis is not None and vis < 0.60:
        return "Gro Search", "your firm is hard to find on Google and in AI answers compared with what a prospect searches for"
    if "new clients" in g:
        return "Gro Ads", "your foundation is sound and you want more new prospects, which is what managed ads are for"
    if "protect" in g or "fix" in g:
        return "Shield", "you asked to protect what you have, and monthly monitoring keeps it that way"
    return "Nothing yet", "the measured areas look healthy; we would re-run the audit in a few months"


# ---- plain text --------------------------------------------------------------------------
