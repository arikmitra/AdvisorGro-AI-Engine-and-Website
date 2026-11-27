"""Group 05: compliance exposure, 15 pts. Starts at 15, -5 per material flag,
-1 per minor flag, floor 0. Material flags are held for the CCO by the orchestrator.

Scope: the homepage only. Flags are for review, not compliance determinations.

Fixes in this version:
- a homepage with almost no static text (JavaScript-built) is 'unverified', not a clean scan;
- the rules are a floor under the model: the LLM reviewer cannot dismiss a flag the rules
  rated material (it is kept as minor so a human still sees it).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from typing import Optional

from ..kernel import Finding, GroupResult, RunContext
from ..llm import AnthropicClient, LLMError
from ..web import SharedFetcher, parse_page
from .llm_review import llm_review
from .scanner import missing_reference_flag, review, scan

CHECK = "no_material_compliance_exposure"
THIN_PAGE_CHARS = 200


@dataclass
class ComplianceExposure:
    fetcher: SharedFetcher = field(default_factory=SharedFetcher)
    client: Optional[AnthropicClient] = None      # LLM reviewer; without it the rules decide
    group_id = "g05_compliance"
    max_points = 15.0

    def _unverified(self, why: str) -> GroupResult:
        return GroupResult(self.group_id, 0.0, self.max_points,
            [Finding(CHECK, False, 0, self.max_points, f"could not scan: {why}", status="unverified")],
            {"reason": "page_unavailable", "detail": why}, partial=True)

    def run(self, ctx: RunContext) -> GroupResult:
        url = f"https://{ctx.domain}/"
        t = max(0.5, min(8.0, ctx.budget.remaining_seconds))
        page = self.fetcher.get(url, t)
        if not page.ok and "certificate" in page.error:       # still scan what visitors would see
            page = self.fetcher.get(url, t, verify=False)
        if not page.ok or not page.html:
            return self._unverified(page.error or "empty page body")

        text = parse_page(page.html).text
        if len(text) < THIN_PAGE_CHARS:                        # nothing to scan is not a clean scan
            return self._unverified("page has almost no static text; likely JavaScript-built")
        cands = scan(text)
        review_ev = {"reviewer": "rules"}
        if self.client:
            try:
                rated, review_ev = llm_review(self.client, cands, text, ctx.budget)
                # the rules are a floor: the model may not dismiss what the rules call material
                rules = {(f.category, f.snippet): f.severity for f in review(cands, text, page.html)}
                rated = [replace(f, severity="minor",
                                 reason=f.reason + " (rules rated this material; kept for human review)")
                         if f.severity == "dismissed" and rules.get((f.category, f.snippet)) == "material"
                         else f for f in rated]
                rated += missing_reference_flag(page.html)
            except (LLMError, ValueError) as e:
                rated = review(cands, text, page.html)
                review_ev = {"reviewer": "rules", "llm_fallback_reason": f"{type(e).__name__}: {e}"}
        else:
            rated = review(cands, text, page.html)
        material = [f for f in rated if f.severity == "material"]
        minor = [f for f in rated if f.severity == "minor"]
        score = max(0.0, self.max_points - 5 * len(material) - len(minor))
        detail = (f"{len(material)} material and {len(minor)} minor flag(s) for review "
                  f"(homepage only; not a compliance determination)")
        return GroupResult(self.group_id, score, self.max_points,
            [Finding(CHECK, not material, score, self.max_points, detail)],
            {"scope": "homepage only", "page_chars": len(text), **review_ev,
             "flags": [{"category": f.category, "severity": f.severity,
                        "reason": f.reason, "snippet": f.snippet} for f in rated],
             "material_flags": [f"{f.category}: {f.snippet}" for f in material],
             "minor_count": len(minor),
             "dismissed_count": sum(f.severity == "dismissed" for f in rated)})