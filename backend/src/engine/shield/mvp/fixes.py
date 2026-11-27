"""Prioritised fix list, built from one run record. Pure: no network, no model.

Priority = points that could be won back divided by the effort to win them, so a cheap fix
worth a lot ranks first. The label is a band over that number:

    High    >= 2.0   (for example 4 points for low or medium effort)
    Medium  >= 1.0
    Low     <  1.0

Only checks that were actually measured appear (status ok or partial). Checks that could not
be verified are not fixes: they are listed separately as not scored.

Compliance items are held back from client output unless the run is releasable. The internal
list (include_held=True) keeps them and marks them `held`.
"""
from __future__ import annotations

GROUPS = {"g01_technical": "Technical foundation", "g02_search": "Search visibility",
          "g03_ai_visibility": "AI visibility", "g04_citations": "Citation surfaces",
          "g05_compliance": "Compliance exposure"}

# ASSUMPTION: relative effort to fix, 1 low .. 3 high. Tune with real delivery data.
EFFORT = {"domain_resolves": 3, "tls_certificate_valid": 2, "page_renders": 3,
          "title_canonical_viewport": 1, "structured_data_jsonld": 1, "pages_indexed": 3,
          "branded_rank_top3": 3, "google_business_profile": 2, "nap_consistent_with_iapd": 2,
          "firm_named_in_category_prompts": 3, "firm_named_in_category_searches": 3,
          "no_material_compliance_exposure": 2}
DEFAULT_EFFORT = 2
EFFORT_WORD = {1: "low", 2: "medium", 3: "high"}

HIGH_AT, MEDIUM_AT = 2.0, 1.0

# What to do, per check. Static on purpose: the fix list must not depend on a model.
ACTIONS = {
    "domain_resolves": "Fix the domain's DNS records (A/AAAA) or renew the domain registration.",
    "tls_certificate_valid": "Install or renew a valid TLS certificate and redirect HTTP to HTTPS.",
    "page_renders": "Fix the server errors or JavaScript errors that stop the homepage loading cleanly.",
    "title_canonical_viewport": "Add a unique page title, a canonical link and a mobile viewport tag to the homepage.",
    "structured_data_jsonld": "Add valid JSON-LD (Organization or FinancialService) with the firm's name, address and phone.",
    "pages_indexed": "Submit an XML sitemap in Google Search Console and remove crawl blocks so more pages get indexed.",
    "branded_rank_top3": "Strengthen branded search: a consistent firm name, a complete Google Business Profile and accurate listings.",
    "google_business_profile": "Claim and complete the Google Business Profile, with the firm's website and details.",
    "nap_consistent_with_iapd": "Make the firm's name, address and phone identical on the website, Google profile and Form ADV.",
    "firm_named_in_category_prompts": "Publish clear service and location pages and earn third-party mentions (directories, reviews, media) so AI assistants name the firm.",
    "firm_named_in_category_searches": "Publish clear service and location pages and earn third-party mentions (directories, reviews, media) so the firm appears for the questions buyers search.",
    "no_material_compliance_exposure": "Have the CCO review the flagged language before it stays published. This is a flag for review, not a ruling.",
}
SURFACE_ACTION = "Claim or correct this listing so its name, address and phone match the firm's Form ADV."
GENERIC_ACTION = "Review this check and address the finding."


def gate(record: dict) -> dict:
    """What a client may see. Compliance detail is withheld unless the run is releasable."""
    return {"show_compliance": record.get("release_status") == "releasable"}


def priority_label(priority: float) -> str:
    return "High" if priority >= HIGH_AT else "Medium" if priority >= MEDIUM_AT else "Low"


def effort_of(check: str) -> int:
    return 2 if check.startswith("surface:") else EFFORT.get(check, DEFAULT_EFFORT)


def action_for(check: str) -> str:
    if check.startswith("surface:"):
        return SURFACE_ACTION
    return ACTIONS.get(check, GENERIC_ACTION)


def build_fixes(record: dict, include_held: bool = False) -> list[dict]:
    """Ranked fixes, best first. Each item: rank, priority (High/Medium/Low), priority_score,
    check, group, area, lost (points), effort (1-3), detail, action, held."""
    release = gate(record)["show_compliance"]
    out = []
    for gid, g in record["results"].items():
        held = gid == "g05_compliance" and not release
        if held and not include_held:
            continue
        for f in g["findings"]:
            lost = f["max_points"] - f["points"]
            if f["status"] in ("ok", "partial") and lost > 0.01:
                effort = effort_of(f["check"])
                score = round(lost / effort, 3)
                out.append({"check": f["check"], "group": gid, "area": GROUPS.get(gid, gid),
                            "lost": round(lost, 2), "effort": effort, "detail": f["detail"],
                            "action": action_for(f["check"]), "priority_score": score,
                            "priority": priority_label(score), "held": held})
    out.sort(key=lambda x: (-x["priority_score"], -x["lost"], x["check"]))
    for i, f in enumerate(out, 1):
        f["rank"] = i
    return out


def passed_checks(record: dict, include_held: bool = False) -> list[dict]:
    """Checks that were measured and earned full marks. Independent of everything else in the run: a failed,
    unverified or timed-out check anywhere never hides these. Held compliance items stay hidden until released."""
    release = gate(record)["show_compliance"]
    out = []
    for gid, g in record["results"].items():
        if gid == "g05_compliance" and not release and not include_held:
            continue
        for f in g["findings"]:
            if f["status"] == "ok" and f["max_points"] > 0 and f["points"] >= f["max_points"] - 0.01:
                out.append({"check": f["check"], "group": gid, "area": GROUPS.get(gid, gid),
                            "points": round(f["points"], 2), "max": f["max_points"], "detail": f["detail"]})
    return out


def scrape_notes(record: dict) -> list[dict]:
    """Groups whose results came from reading public web pages instead of a search or model API. The client is told, in plain
    words, that those results are indicative; the operator line says what was used and why."""
    out = []
    for gid, g in record["results"].items():
        if gid == "g05_compliance":
            continue
        ev = g.get("evidence") or {}
        surfaces = ev.get("surfaces") or {}
        used_surfaces = sorted(k for k, v in surfaces.items() if isinstance(v, dict) and v.get("scrape"))
        sc = ev.get("scrape") or {}
        if not (sc.get("used") or used_surfaces):
            continue
        proxy = bool(sc.get("proxy"))
        client = (("We could not ask AI assistants directly, so this shows how often the firm appears in ordinary web searches for the same "
                   "questions. That is a related signal, not the same measurement, and it is scored lower for that reason.") if proxy else
                  "Some results were gathered by reading public web pages directly rather than through a search data service, "
                  "so treat them as indicative. A person confirms them before the final report.")
        what = ", ".join([*(sorted((sc.get("checks") or {}).keys())), *used_surfaces]) or "the whole group"
        out.append({"area": GROUPS.get(gid, gid), "client": f"{GROUPS.get(gid, gid)}: {client}",
                    "operator": f"Scraping fallback used in {GROUPS.get(gid, gid)} ({what}): {sc.get('why') or ev.get('primary_failure') or 'API missing or failing'}."})
    return out


def coverage_notes(record: dict) -> list[dict]:
    """Where the AI-visibility check was limited by budget, and what more budget would add.
    `client` is wording for the client; `operator` adds the cost and the setting to change (reviewer only)."""
    notes = scrape_notes(record)
    ev = ((record["results"].get("g03_ai_visibility") or {}).get("evidence")) or {}
    m = ev.get("more_with_budget") or {}
    if not m.get("applies"):
        return notes
    cur, full = m["current"], m["full"]
    client = (f"The AI-visibility result is based on {cur['answers']} answers from {cur['engines']} AI assistant setting(s). "
              f"A larger sample (up to {full['answers']} answers across {full['engines']} settings) would narrow the margin of "
              f"error from about \u00b1{cur['margin_pts']:g} to about \u00b1{full['margin_pts']:g} percentage points.")
    if m.get("not_asked"):
        client += " It would also ask: " + ", ".join(m["not_asked"]) + "."
    op = (f"AI visibility was limited ({'; '.join(m['reasons'])}). This run: {cur['engines']} engine(s) x {cur['repeats']} repeat(s) "
          f"= {cur['answers']} answers, margin about \u00b1{cur['margin_pts']:g} points. Full plan: {full['engines']} engine(s) x "
          f"{full['repeats']} repeats = {full['answers']} answers, about \u00b1{full['margin_pts']:g} points, roughly "
          f"${full['est_cost_usd']:.2f} for this group")
    if m.get("suggested_run_budget_usd"):
        op += f" (set ADVISORGRO_BUDGET_USD to about ${m['suggested_run_budget_usd']:.2f} to afford it)"
    op += "."
    if m.get("not_asked"):
        op += " Not asked because of budget: " + ", ".join(m["not_asked"]) + "."
    if m.get("beyond_cap"):
        op += " Never asked because of ADVISORGRO_MAX_ENGINES: " + ", ".join(m["beyond_cap"]) + " (raise it to include them)."
    return notes + [{"area": GROUPS["g03_ai_visibility"], "client": client, "operator": op}]


def unverified(record: dict, include_held: bool = False) -> list[dict]:
    """Checks that earned nothing because they could not be verified, or need a human."""
    release = gate(record)["show_compliance"]
    out = []
    for gid, g in record["results"].items():
        for f in g["findings"]:
            if f["status"] in ("unverified", "manual"):
                if gid == "g05_compliance" and not release and not include_held:
                    continue
                out.append({"area": GROUPS.get(gid, gid), "check": f["check"],
                            "manual": f["status"] == "manual", "detail": f["detail"]})
    return out


def format_fix_list(fixes: list[dict]) -> str:
    """Plain-text table for the command line."""
    if not fixes:
        return "Fix list\n  Nothing measured needs fixing."
    L = ["Fix list, highest priority first",
         f"  {'#':>2}  {'priority':<8} {'pts':>5}  {'effort':<6}  area: check"]
    for f in fixes:
        held = "   [HELD for CCO review]" if f.get("held") else ""
        L.append(f"  {f['rank']:>2}  {f['priority'].upper():<8} {f['lost']:>5g}  "
                 f"{EFFORT_WORD[f['effort']]:<6}  {f['area']}: {f['check']}{held}")
        L.append(f"        found: {f['detail'][:140]}")
        L.append(f"        next:  {f['action']}")
    return "\n".join(L)


def fixes_markdown(fixes: list[dict]) -> list[str]:
    """Lines for the internal evidence report."""
    if not fixes:
        return ["Nothing measured needs fixing."]
    return [f"{f['rank']}. **{f['priority']}** · {f['area']} · `{f['check']}` · {f['lost']:g} pts at stake · "
            f"effort {EFFORT_WORD[f['effort']]}" + (" · *held for CCO review, not shown to the client*" if f.get("held") else "")
            + f"\n   - Found: {f['detail']}\n   - Next step: {f['action']}" for f in fixes]