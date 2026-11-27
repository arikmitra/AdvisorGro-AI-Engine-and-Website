"""Stage 4 pre-check. The MVP rule scanner is the floor (the model can add flags but can never dismiss a rule hit).
LLM unavailable -> rules-only report flagged 'AI review missing' for the CCO. Flags only; the CCO decides."""
from __future__ import annotations

import re

from pydantic import BaseModel

from ...core.errors import ProviderError
from ...llm.registry import data_block
from ..shield.mvp.g05_compliance.scanner import review, scan


class _LF(BaseModel):
    severity: str = "minor"
    title: str = ""
    quoted_text: str = ""
    why: str = ""


class _LOut(BaseModel):
    findings: list[_LF] = []


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


async def run(gateway, prompts, text: str, disclosures: list[str], unsupported: list[dict], meter=None) -> dict:
    # scan the creative alone: a disclosure elsewhere on the page does not excuse a claim in an ad or email
    rated = review(scan(text), text, text + " form crs form adv")
    page_like = text
    material = [f for f in rated if f.severity == "material"]
    minor = [f for f in rated if f.severity == "minor"]
    ai = "missing"
    extra: list[dict] = []
    if gateway is not None:
        try:
            p, _ = prompts.render("compliance_review", text_block=data_block("marketing_copy", page_like, 12000))
            out = await gateway.complete_json("You triage marketing copy for a CCO.", p, _LOut, meter=meter, tags={"stage": "precheck"})
            body = _squash(page_like)
            for f in out.findings[:5]:
                if len(f.quoted_text) >= 12 and _squash(f.quoted_text) in body:  # must quote text that really exists
                    extra.append({"severity": "material" if f.severity == "high" else "minor", "title": f.title, "snippet": f.quoted_text[:200], "reason": f.why[:300]})
            ai = "done"
        except ProviderError:
            ai = "missing"
    missing = [] if disclosures else ["no approved disclosure text is on file for this firm"]
    return {
        "status": "flags" if (material or extra or unsupported or missing) else "clear",
        "material": [{"category": f.category, "snippet": f.snippet, "reason": f.reason} for f in material] + [e for e in extra if e["severity"] == "material"],
        "minor": [{"category": f.category, "snippet": f.snippet, "reason": f.reason} for f in minor] + [e for e in extra if e["severity"] == "minor"],
        "unsupported_claims": unsupported, "missing_disclosures": missing, "ai_review": ai,
        "note": "AI review missing: rules-only result, flagged for the CCO" if ai == "missing" else "",
    }
