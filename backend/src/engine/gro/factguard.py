"""Stage 3 fact guard (deck 9.5). Every number, year, award, return or testimonial in generated copy must match an
approved fact or approved language for that firm. Matching is deterministic code; the LLM only helps *find* claims,
so a missed extraction cannot approve a made-up fact (rules extraction runs regardless)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...core.errors import ProviderError
from ...llm.registry import data_block
from .schemas import Claim, ClaimList, CreativeSet

_RULES = [
    ("number", re.compile(r"\$\s?\d[\d,.]*\s?(?:[kKmMbB]|million|billion)?|\b\d[\d,.]*\s?%|\b\d[\d,]*\+?\s+(?:clients|families|households|years|offices|advisors|advisers)\b")),
    ("year", re.compile(r"\b(?:since|founded in|established in|est\.)\s+(?:19|20)\d{2}\b", re.I)),
    ("award", re.compile(r"\b(?:award[- ]winning|ranked|named|recognized|rated|top[- ]\w+|best[- ]in|#\s?1|number one|forbes|barron'?s|cfp|cfa|certified)\b[^.\n]{0,40}", re.I)),
    ("testimonial", re.compile(r"\b(?:testimonial|clients say|5[- ]star|five[- ]star)\b", re.I)),
    ("return", re.compile(r"\b(?:returns?|outperform\w*|beat the market|guarantee\w*)\b[^.\n]{0,30}", re.I)),
]


def creative_strings(c: CreativeSet) -> list[str]:
    out: list[str] = []
    if c.google:
        out += c.google.headlines + c.google.descriptions
    if c.meta:
        for v in c.meta.variants:
            out += [v.primary_text, v.headline]
    for s in c.page.sections:
        out += [s.heading, *s.body_blocks, s.cta]
    for e in c.followup.emails:
        out += [e.subject, e.body]
    out += c.followup.sms
    return [x for x in out if x]


def rule_claims(text: str) -> list[Claim]:
    seen, out = set(), []
    for kind, rx in _RULES:
        for m in rx.finditer(text):
            t = re.sub(r"\s+", " ", m.group(0)).strip()
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(Claim(text=t, kind=kind))
    return out


async def extract_claims(gateway, prompts, text: str, meter=None) -> list[Claim]:
    claims = rule_claims(text)
    if gateway is None:
        return claims
    try:
        p, _ = prompts.render("claims", text_block=data_block("copy", text, 12000))
        extra = await gateway.complete_json("You list factual claims.", p, ClaimList, meter=meter, tags={"stage": "factguard"})
        known = {c.text.lower() for c in claims}
        claims += [c for c in extra.claims if c.text.lower() not in known]
    except ProviderError:
        pass  # rules extraction already ran
    return claims


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%$ ]", " ", s.lower())


_STOP = {"since", "founded", "established", "est", "the", "and", "for", "our", "with", "over", "more", "than", "of", "in", "a", "an"}


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if len(t) > 2 or t.isdigit()}


def _content(s: str) -> set[str]:
    """Words that carry meaning beyond the number itself (years, clients, awards...)."""
    return {t for t in _tokens(s) if not any(ch.isdigit() for ch in t) and t not in _STOP}


def is_supported(claim: Claim, facts: list[str]) -> bool:
    """A claim is supported when a fact contains it verbatim, or contains every number in it AND the words that say what
    the number counts (so '20 years' is not supported by '20 clients')."""
    c = _norm(claim.text).strip()
    nums = set(re.findall(r"\d[\d,.]*", claim.text))
    words = _content(claim.text)
    for f in facts:
        if c and c in _norm(f):
            return True
        if nums and all(n in f for n in nums) and (not words or len(words & _tokens(f)) / len(words) >= 0.5):
            return True
    return False


@dataclass
class FactGuardResult:
    ok: bool
    supported: list[str] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)


async def check(gateway, prompts, creative: CreativeSet, facts: list[str], approved_language: list[str], meter=None) -> FactGuardResult:
    text = "\n".join(creative_strings(creative))
    # approved language is copied in verbatim by the pipeline, so claims inside it are already approved
    stripped = text
    for a in approved_language:
        stripped = stripped.replace(a, " ")
    claims = await extract_claims(gateway, prompts, stripped, meter)
    allowed = facts + approved_language
    bad = [c for c in claims if not is_supported(c, allowed)]
    return FactGuardResult(not bad, [c.text for c in claims if c not in bad], [c.model_dump() for c in bad])
