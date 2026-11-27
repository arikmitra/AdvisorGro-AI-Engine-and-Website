"""Deterministic fake LLM for dev, CI, shadow mode and contract tests (the devserver.py pattern).
Behaviour is keyed on markers in the prompt so each pipeline stage gets schema-valid output."""
from __future__ import annotations

import json
import re

from .gateway import LLMResult


class FakeProvider:
    name = "fake"

    def __init__(self, name: str = "fake", fail: bool = False, handlers: dict | None = None):
        self.name, self.fail, self.handlers, self.calls = name, fail, handlers or {}, []

    async def complete(self, system, prompt, *, model, max_tokens, temperature, json_mode, timeout) -> LLMResult:
        from ..core.errors import ProviderUnavailable
        self.calls.append(prompt)
        if self.fail:
            raise ProviderUnavailable(f"{self.name} down")
        for marker, fn in self.handlers.items():
            if marker in prompt:
                return LLMResult(fn(prompt), self.name, "fake-1", 10, 10)
        return LLMResult(_default(prompt), self.name, "fake-1", 10, 10)


def _default(p: str) -> str:
    if "TASK:brief_angles" in p:
        return json.dumps({"angles": ["Plan for the next chapter", "Clarity on your retirement income"],
                           "keyword_themes": ["retirement planning advisor", "fiduciary financial advisor"]})
    if "TASK:google_ads" in p:
        return json.dumps({"headlines": [f"Fiduciary Advisor {i}" for i in range(1, 7)],
                           "descriptions": ["Book a no-obligation call with a fee-only fiduciary advisor.",
                                            "Clear retirement planning for local families."]})
    if "TASK:meta_ads" in p:
        return json.dumps({"variants": [{"primary_text": "Planning for retirement? Talk with a local fiduciary advisor.",
                                         "headline": "Retirement planning, explained", "cta": "Learn more"}]})
    if "TASK:page_copy" in p:
        return json.dumps({"sections": [{"type": "hero", "heading": "Retirement planning with a fiduciary",
                                         "body_blocks": ["We help families plan the next chapter."], "cta": "Book a call"}]})
    if "TASK:emails" in p:
        return json.dumps({"emails": [{"subject": f"Following up ({i})", "body": "Thanks for your interest. Book a time with us."} for i in range(1, 6)],
                           "sms": ["Thanks for reaching out. Reply STOP to opt out. Book here: {link}"]})
    if "TASK:claims" in p:
        return json.dumps({"claims": []})
    if "TASK:compliance_review" in p:
        return json.dumps({"findings": []})
    if "TASK:grade_ai_answer" in p:
        m = re.search(r'firm_name="([^"]+)"', p)
        name = (m.group(1) if m else "").lower()
        ans = p.split("<data label=\"answer\">")[-1].lower()
        return json.dumps({"mentioned": bool(name) and name in ans, "recommended": False})
    if "TASK:ai_answer" in p:
        return "Several local advisors are commonly mentioned; check SEC IAPD records before choosing."
    if "TASK:weekly_narrative" in p:
        return json.dumps({"narrative": "Performance held steady this week."})
    if "TASK:optimiser_explain" in p:
        return json.dumps({"explanation": "The rule fired because the ad underperformed against the cap."})
    return "{}"
