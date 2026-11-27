"""Group 05 scan and triage, rule-based. No model calls.

scan()   over-inclusive: finds anything that might be a risky claim
review() rates each candidate, dismisses obvious false positives, and flags
         material ones for the CCO

These are flags for human review. Nothing here is a compliance determination.

Fix in this version: standard disclosure wording such as "cannot guarantee returns" or
"doesn't guarantee performance" is no longer read as a guarantee claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

I = re.I
PERFORMANCE = [
    r"\b\d{1,3}(?:\.\d+)?\s?%\s+(?:\w+\s+){0,2}(?:returns?|growth|gains?)\b",
    r"\boutperform\w*",
    r"\bbeat(?:s|ing)? the (?:market|s&p|index)",
    r"(?<!no )(?<!not )(?<!not a )(?<!cannot )(?<!can not )(?<!never )(?<!n't )(?<!n’t )"
    r"\bguarantee[sd]?\b[^.]{0,40}\b(?:returns?|income|growth|profits?|performance)\b",
    r"\b(?:track record|historical returns?)\b",
]
SUPERLATIVES = [
    r"\b(?:best|number one|top[- ](?:rated|ranked|performing)|leading|premier|unmatched|world[- ]class|award[- ]winning)\b",
    r"(?<!\w)#\s?1\b",
]
TESTIMONIALS = [
    r"\btestimonials?\b",
    r"★{3,}|⭐{2,}|\b[45](?:\.\d)?[- ]star\b",
    r"[“\"][^”\"]{20,200}[”\"]\s*[—–-]\s*[A-Z][a-z]+",
    r"\bwhat (?:our )?clients (?:are )?say(?:ing)?\b|\bour clients say\b",
]
DISC_PERFORMANCE = r"past performance|no guarantee|not (?:a )?guarantee|cannot guarantee|loss of (?:principal|capital)|may lose"
DISC_TESTIMONIAL = r"compensat|not indicative|not (?:necessarily )?representative|promoter|disclosure"
FORM_REFERENCE = (r"form crs|customer relationship summary|form adv|part 2a|brochure|"
                  r"adviserinfo\.sec\.gov|brokercheck|investor\.gov/crs")
FALSE_POSITIVE = r"best[- ]interests?|best practices?|leading to\b"


@dataclass(frozen=True)
class Flag:
    category: str
    snippet: str
    severity: str = "unrated"       # material | minor | dismissed
    reason: str = ""
    match: str = ""                 # exactly what the pattern matched
    after: str = ""                 # the few characters that follow it


def scan(text: str) -> list[Flag]:
    out: list[Flag] = []
    for category, patterns in (("performance_claims", PERFORMANCE),
                               ("superlatives", SUPERLATIVES),
                               ("testimonials", TESTIMONIALS)):
        hits = sorted((m.start(), m.end()) for p in patterns for m in re.finditer(p, text, I))
        last = -10**9
        for s, e in hits:
            if s - last < 80:                    # same passage, one flag
                continue
            last = s
            out.append(Flag(category, re.sub(r"\s+", " ", text[max(0, s - 50): e + 50]).strip(),
                            match=text[s:e], after=text[e:e + 20]))
    return out


def review(flags: list[Flag], text: str, html: str) -> list[Flag]:
    perf_disc = bool(re.search(DISC_PERFORMANCE, text, I))
    test_disc = bool(re.search(DISC_TESTIMONIAL, text, I))
    out: list[Flag] = []
    for f in flags:
        if f.category == "performance_claims":
            if re.search(r"guarantee", f.match, I):
                sev, why = "material", "language that could read as a guarantee"
            elif not perf_disc:
                sev, why = "material", "performance claim and no past-performance or risk disclosure on the page"
            else:
                sev, why = "minor", "performance claim; disclosure language exists on the page, check it sits near the claim"
        elif f.category == "superlatives":
            if re.search(FALSE_POSITIVE, f.match + f.after, I):
                sev, why = "dismissed", "common phrase, not a superlative claim"
            else:
                sev, why = "minor", "superlative or ranking claim; needs support if it is a claim of fact"
        else:
            if not test_disc:
                sev, why = "material", "testimonial or client-review language and no disclosure on the page"
            else:
                sev, why = "minor", "testimonial language; disclosure exists on the page, check it is adequate"
        out.append(Flag(f.category, f.snippet, sev, why, f.match, f.after))
    return out + missing_reference_flag(html)


def missing_reference_flag(html: str) -> list[Flag]:
    if re.search(FORM_REFERENCE, html, I):
        return []
    return [Flag("missing_disclosure",
                 "no Form CRS, Form ADV or adviserinfo.sec.gov reference found on the page",
                 "minor", "expected regulatory disclosure reference not found on this page")]