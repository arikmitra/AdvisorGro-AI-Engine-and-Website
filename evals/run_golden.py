"""Deterministic golden evals (no model, no network): the scanner floor and the fact guard against labelled cases.
Run in CI on every PR; a prompt or rule change that flips a golden case fails the build (guide: prompts are code).
    python evals/run_golden.py
Model-graded evals (drafting quality, claim extraction recall) live in promptfooconfig.yaml and run nightly with keys."""
from __future__ import annotations

import json
import pathlib
import sys

from advisorgro.engine.gro.factguard import is_supported, rule_claims
from advisorgro.engine.shield.mvp.g05_compliance.scanner import review, scan

cases = json.loads((pathlib.Path(__file__).parent / "golden" / "cases.json").read_text())
bad = 0
for c in cases["scanner"]:
    got = sorted({f.category for f in review(scan(c["text"]), c["text"], c["text"] + " form crs form adv") if f.severity == "material"})
    ok = got == sorted(c["expect_material"])
    bad += not ok
    print(("PASS" if ok else "FAIL"), "scanner", repr(c["text"][:50]), got)
for c in cases["factguard"]:
    unsupported = [x.text for x in rule_claims(c["text"]) if not is_supported(x, c["facts"])]
    ok = bool(unsupported) == c["expect_blocked"]
    bad += not ok
    print(("PASS" if ok else "FAIL"), "factguard", repr(c["text"][:50]), unsupported)
sys.exit(1 if bad else 0)
