"""Grader: sees raw answers only, never the prompts' intent or the scoring rules.

Fix in this version: the domain must match as a whole token, so acme.com no longer
matches myacme.com.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .engines import Answer


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()


@dataclass(frozen=True)
class Grade:
    total: int
    named: int
    cited: int

    @property
    def share_of_model(self) -> float:      # mentions / answers x 100
        return 100.0 * self.named / self.total if self.total else 0.0


def _cites(url: str, domain: str) -> bool:
    h = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return bool(domain) and (h == domain or h.endswith("." + domain))


def grade(answers: list[Answer], firm: str, domain: str,
          aliases: tuple[str, ...] = ()) -> Grade:
    """Named = the firm name or an alias appears as whole words, or its domain appears as a
    whole token. Names under 4 characters are too ambiguous to match on their own (they rely
    on the domain)."""
    domain = (domain or "").lower()
    names = [n for n in (_norm(x) for x in (firm, *aliases)) if len(n) >= 4]
    pats = [re.compile(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])") for n in names]
    dom_re = re.compile(rf"(?<![a-z0-9.-]){re.escape(domain)}(?![a-z0-9-]|\.[a-z0-9])") if domain else None
    named = cited = 0
    for a in answers:
        text = _norm(a.text)
        if any(p.search(text) for p in pats) or (dom_re and dom_re.search(a.text.lower())):
            named += 1
        if any(_cites(u, domain) for u in a.cited_urls):
            cited += 1
    return Grade(len(answers), named, cited)