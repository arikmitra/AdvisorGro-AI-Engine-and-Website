"""The six offer templates. ASSUMPTION: names and angles below are placeholders until the product owner confirms the
catalogue; the structure (id, summary, sections, audience hints) is what the pipeline relies on."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    id: str
    name: str
    summary: str
    audiences: tuple[str, ...]
    sections: tuple[str, ...] = ("hero", "benefits", "process", "faq", "cta")


OFFERS: dict[str, Offer] = {o.id: o for o in [
    Offer("retirement_readiness", "Retirement readiness review", "a conversation about whether your plan supports the retirement you want", ("pre-retirees 55-67",)),
    Offer("tax_aware_investing", "Tax-aware investing check-up", "a review of how taxes shape your investment decisions", ("high-income households",)),
    Offer("business_owner_exit", "Business owner exit planning", "planning the financial side of selling or passing on a business", ("business owners 50+",)),
    Offer("inheritance_transition", "Inheritance and wealth transition", "guidance for families receiving or passing on wealth", ("recent heirs", "families")),
    Offer("retirement_income", "Retirement income plan", "turning savings into a steady income plan", ("recent retirees",)),
    Offer("second_opinion", "Portfolio second opinion", "a second look at an existing portfolio and plan", ("investors with an existing advisor",)),
]}


def get(offer_id: str) -> Offer:
    from ...core.errors import ValidationFailed
    if offer_id not in OFFERS:
        raise ValidationFailed(f"unknown offer {offer_id}")
    return OFFERS[offer_id]
