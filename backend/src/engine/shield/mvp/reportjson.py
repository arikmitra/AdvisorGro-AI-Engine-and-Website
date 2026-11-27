"""The display-ready JSON the website renders (advisorgro-api.js renderReport reads exactly this shape).

Built from a run record plus what report.build_reports returned. Held compliance detail never enters it:
the compliance group is shown as "with your CCO" with no score, checks or fixes unless the run is releasable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .fixes import EFFORT_WORD, GROUPS, build_fixes, coverage_notes, gate, passed_checks, unverified

GREEN_AT, AMBER_AT = 0.90, 0.50           # same bands as shield.py
ORDER = tuple(GROUPS)
CHECKS = {
    "domain_resolves": "Domain resolves", "tls_certificate_valid": "Security certificate",
    "page_renders": "Homepage loads", "title_canonical_viewport": "Title, canonical link and mobile tag",
    "structured_data_jsonld": "Structured data (JSON-LD)", "pages_indexed": "Pages indexed by Google",
    "branded_rank_top3": "Firm name ranks in the top three", "google_business_profile": "Google Business Profile",
    "nap_consistent_with_iapd": "Name, address and phone match Form ADV",
    "firm_named_in_category_prompts": "Named in AI answers about advisors",
    "firm_named_in_category_searches": "Appears in web searches buyers make (stand-in for AI answers)",
    "no_material_compliance_exposure": "No material compliance exposure",
}
SURFACES = {"iapd": "IAPD (Form ADV)", "napfa": "NAPFA", "paladin": "Paladin Registry", "yelp": "Yelp",
            "linkedin": "LinkedIn", "media": "Media mentions"}


def check_label(check: str) -> str:
    if check.startswith("surface:"):
        name = check.split(":", 1)[1]
        return "Listing: " + SURFACES.get(name, name.replace("_", " ").title())
    return CHECKS.get(check) or check.replace("_", " ").capitalize()


def period_label(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else datetime.now(timezone.utc).timestamp(), timezone.utc).strftime("%B %Y")


def _state(score: float, mx: float) -> str:
    r = score / mx if mx else 0
    return "green" if r >= GREEN_AT else "amber" if r >= AMBER_AT else "red"


def _check_state(f: dict) -> str:
    if f["status"] == "manual":
        return "manual"
    if f["status"] == "unverified":
        return "unverified"
    if f["points"] >= f["max_points"] - 0.01:
        return "pass"
    return "partial" if f["points"] > 0 else "fail"


def _group(gid: str, record: dict, show_compliance: bool) -> dict:
    name, g = GROUPS[gid], record["results"].get(gid)
    if gid == "g05_compliance" and not show_compliance and g is not None:
        return {"id": gid, "name": name, "state": "review", "held": True, "partial": False, "score": 0, "max": 0,
                "delta": None, "checks": [],
                "note": "Items in this area are with your compliance team for review and are not shown here."}
    if g is None:
        return {"id": gid, "name": name, "state": "not_run", "held": False, "partial": True, "score": 0, "max": 0,
                "delta": None, "checks": [], "note": ""}
    st = "unverified" if g["partial"] and g["score"] <= 0 else _state(g["score"], g["max_points"])
    return {"id": gid, "name": name, "state": st, "held": False, "partial": bool(g["partial"]), "note": "",
            "score": round(g["score"], 2), "max": g["max_points"],
            "delta": (record.get("delta") or {}).get("by_group", {}).get(gid),
            "checks": [{"id": f["check"], "state": _check_state(f), "label": check_label(f["check"]), "detail": f["detail"]}
                       for f in g["findings"]]}


def build_payload(record: dict, built: dict, rows: list[dict], *, firm: str, kind: str = "audit",
                  show_passed: bool = True) -> dict:
    show = gate(record)["show_compliance"]
    card = record["scorecard"]
    fixes = built.get("fixes") if built.get("fixes") is not None else build_fixes(record)
    texts = built.get("texts") or {}
    ups = unverified(record)
    passed = [{"label": check_label(p["check"]), "area": p["area"], "detail": p["detail"], "points": p["points"], "max": p["max"]}
              for p in passed_checks(record)]
    counts = {"passed": len(passed), "needs_work": len(fixes), "unverified": len(ups)}
    return {
        "kind": kind, "firm": firm, "domain": record["domain"], "period": period_label(record.get("created_at")),
        "score": {"total": card["total"], "max": card["max_total"], "complete": bool(card["complete"]),
                  "delta": (record.get("delta") or {}).get("total")},
        "summary": built.get("summary", ""),
        "groups": [_group(g, record, show) for g in ORDER],
        "rows": rows,
        "fixes": [{"priority": f["priority"], "title": check_label(f["check"]), "area": f["area"], "lost": f["lost"],
                   "effort": EFFORT_WORD[f["effort"]], "found": f["detail"], "next": f["action"],
                   "text": texts.get(f["check"], "")} for f in fixes],
        "unverified": [{"label": check_label(u["check"]), "area": u["area"], "manual": u["manual"], "detail": u["detail"]}
                       for u in ups],
        # what is working, shown whatever else failed (ADVISORGRO_ALWAYS_SHOW_PASSED=0 turns it off)
        "coverage": [{"area": n["area"], "text": n["client"]} for n in coverage_notes(record)],
        "passed": passed if show_passed else None,
        "counts": counts if show_passed else None,
        "preliminary": False,
    }


def safe_payload(record: dict, built: dict, rows: list[dict], *, firm: str, kind: str = "audit", show_passed: bool = True) -> dict:
    """build_payload that cannot fail: if a part is broken the page still gets the checks that did succeed.
    Falls back to the record without the report writer's output, then to a bare score and the passed list."""
    try:
        return build_payload(record, built, rows, firm=firm, kind=kind, show_passed=show_passed)
    except Exception:                                                         # noqa: BLE001
        pass
    try:
        out = build_payload(record, {}, [], firm=firm, kind=kind, show_passed=show_passed)
        out["degraded"] = True
        return out
    except Exception:                                                         # noqa: BLE001
        card = record.get("scorecard") or {}
        try:
            passed = [{"label": check_label(p["check"]), "area": p["area"], "detail": p["detail"], "points": p["points"],
                       "max": p["max"]} for p in passed_checks(record)] if show_passed else None
        except Exception:                                                     # noqa: BLE001
            passed = [] if show_passed else None
        return {"kind": kind, "firm": firm, "domain": record.get("domain", ""), "period": period_label(record.get("created_at")),
                "score": {"total": card.get("total", 0), "max": card.get("max_total", 100), "complete": False, "delta": None},
                "summary": "", "groups": [], "rows": [], "fixes": [], "unverified": [], "coverage": [], "passed": passed,
                "counts": None, "preliminary": False, "degraded": True}