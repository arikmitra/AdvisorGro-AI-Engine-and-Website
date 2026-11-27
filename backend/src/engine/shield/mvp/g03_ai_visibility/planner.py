"""Planner: builds the buyer-intent prompt battery. Knows nothing about grading."""
from __future__ import annotations

TEMPLATES = (
    "Who are the best fee-only financial advisors in {area}?",
    "Which registered investment advisors in {area} work with retirees?",
    "Recommend a wealth management firm in {area} for a $2M portfolio.",
    "Top fiduciary financial planners near {area}?",
    "Which RIA in {area} is good for business owners planning an exit?",
    "Who should I hire for retirement planning in {area}?",
)


def build_battery(area: str, size: int = 6) -> list[str]:
    return [t.format(area=area) for t in TEMPLATES[:size]]
