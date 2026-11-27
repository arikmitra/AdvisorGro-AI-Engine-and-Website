"""Fake check groups for dev, CI, ephemeral PR environments and shadow mode (the MVP devserver.py pattern).
Real scoring, record, review gate and reports run on top; only the five groups are canned.
Domain keywords steer behaviour so tests can force each path: 'held' (material compliance flag), 'nog5'
(compliance group unverified), 'slow' (misses the time budget), 'boom' (group raises), 'lowtls' (certificate expiring)."""
from __future__ import annotations

import time

from .mvp.kernel import Finding, GroupResult, RunContext


class FakeGroup:
    def __init__(self, gid: str, mx: float, findings: list, evidence: dict | None = None, partial: bool = False,
                 delay: float = 0.0, boom: bool = False):
        self.group_id, self.max_points, self._f, self._ev, self._p = gid, mx, findings, evidence or {}, partial
        self.delay, self.boom = delay, boom

    def run(self, ctx: RunContext) -> GroupResult:
        if self.boom:
            raise RuntimeError("fake provider exploded")
        if self.delay:
            time.sleep(self.delay)
        fs = [Finding(*f[:5], status=f[5] if len(f) > 5 else "ok") for f in self._f]
        return GroupResult(self.group_id, round(sum(f.points for f in fs), 2), self.max_points, fs, dict(self._ev), self._p)


def fake_modules(domain: str = "") -> list[FakeGroup]:
    d = domain.lower()
    days = 9 if "lowtls" in d else 214
    g1 = [("domain_resolves", True, 4, 4, "2 addresses"), ("tls_certificate_valid", True, 4, 4, "TLSv1.3"),
          ("page_renders", True, 4, 4, "HTTP 200"), ("title_canonical_viewport", False, 2.67, 4, "missing: canonical"),
          ("structured_data_jsonld", False, 0, 4, "no JSON-LD found")]
    g2 = [("pages_indexed", True, 5, 6, "40 pages"), ("branded_rank_top3", False, 2, 7, "ranks 6"),
          ("google_business_profile", False, 0, 4, "no profile found"), ("nap_consistent_with_iapd", True, 3, 3, "match")]
    g3 = [("firm_named_in_category_prompts", False, 4, 25, "SoM 15.0% (3/20 answers across 2 engine(s) x 1 repeat(s))")]
    g4 = [("surface:iapd", True, 3.33, 3.33, "ok"), ("surface:napfa", False, 0, 3.33, "no listing"),
          ("surface:paladin", True, 3.33, 3.33, "ok"), ("surface:yelp", False, 0, 3.33, "blocked", "unverified"),
          ("surface:linkedin", False, 0, 3.33, "manual review", "manual"), ("surface:media", False, 0, 3.33, "manual", "manual")]
    if "nog5" in d:
        g5, ev5, p5 = [("no_material_compliance_exposure", False, 0, 15, "could not scan", "unverified")], {}, True
    elif "held" in d:
        g5, ev5, p5 = [("no_material_compliance_exposure", False, 5, 15, "1 material flag")], \
            {"material_flags": ["performance_claims: our 12% annual returns, no disclosure"]}, False
    else:
        g5, ev5, p5 = [("no_material_compliance_exposure", True, 15, 15, "clean")], {}, False
    slow = 30.0 if "slow" in d else 0.0
    return [FakeGroup("g01_technical", 20, g1, {"tls": {"days_left": days, "ok": True}}),
            FakeGroup("g02_search", 20, g2), FakeGroup("g03_ai_visibility", 25, g3, delay=slow, boom="boom" in d),
            FakeGroup("g04_citations", 20, g4), FakeGroup("g05_compliance", 15, g5, ev5, partial=p5)]
