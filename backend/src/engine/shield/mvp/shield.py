"""Shield rules vendored from the MVP (pure functions only: probes, the eight RAG rows, alerts, text).
The MVP SQLite store, monitor and CLI are replaced by the platform (Postgres, Temporal schedules).

Shield: monitoring on top of the audit engine.

One engine, two front doors. The free audit runs all five groups once. Shield re-runs the same
checks every month with the same weights, so a score means the same thing month to month, adds
two probes the audit scorecard does not carry (email authentication and domain expiry), turns
the results into the eight red/amber/green rows shown on the Shield page, and raises alerts.

    python shield.py snapshot example.com           live rows for the Shield page, no API keys needed
    python shield.py enroll example.com --firm ... --area ... --name ... --email ...
    python shield.py run-due [--force]              the monthly job; run it daily from cron
    python shield.py list

Rules carried over from the engine:
- an unverified check is shown as "not verified", never as red, and never raises an alert;
- compliance detail goes to the reviewer only; a client sees "with your CCO", not the flagged text;
- Shield alerts the client about technical faults (site down, certificate, domain) and tells the
  reviewer about anything compliance-related.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Callable, Optional
from urllib.parse import quote


from .fixes import build_fixes, gate
from .reportjson import build_payload, check_label, period_label
from .intake import domain_of, normalize_url
from .iapd import IapdError, http_get_json

DAY = 86400.0
MONTH_S = 30 * DAY
QUARTER_EVERY = 3                    # every third monthly run also produces the quarterly CCO report
ALERT_QUIET_S = 7 * DAY              # the same alert is not repeated inside this window
# ASSUMPTION: thresholds below are first values to tune with real data, not product policy.
CERT_AMBER_DAYS, CERT_RED_DAYS = 30, 14
DOMAIN_AMBER_DAYS, DOMAIN_RED_DAYS = 30, 14
SCORE_DROP_ALERT = -3.0              # points lost in one group since the last run
GREEN_AT, AMBER_AT = 0.90, 0.50
DKIM_SELECTORS = ("google", "selector1", "selector2", "default", "k1", "s1", "s2", "dkim", "mail", "smtp")
UA = {"User-Agent": "Mozilla/5.0 (compatible; AdvisorGro-shield/0.1)"}

AREAS = ("Google Business Profile", "Google search", "AI answers", "Listings and reputation",
         "Disclosures and wording", "LinkedIn", "Domain and security", "Email delivery")
RANK = {"red": 4, "amber": 3, "review": 2, "green": 1, "unverified": 0, "not_run": -1}
LABEL = {"red": "Red", "amber": "Amber", "green": "Green", "review": "With a person",
         "unverified": "Not verified", "not_run": "In the free audit"}


# ---- extra probes: email authentication and domain expiry ----------------------------------------
def doh_txt(name: str, timeout: float = 4.0) -> Optional[list[str]]:
    """TXT records over DNS-over-HTTPS (stdlib has no TXT lookup). [] = none exist, None = could not ask."""
    try:
        code, d = http_get_json(f"https://cloudflare-dns.com/dns-query?name={quote(name)}&type=TXT",
                                {**UA, "Accept": "application/dns-json"}, timeout)
    except IapdError:
        return None
    if code != 200:
        return None
    if d.get("Status") == 3:                                   # NXDOMAIN
        return []
    return ["".join(re.findall(r'"((?:[^"\\]|\\.)*)"', a["data"])) or a["data"].strip('"')
            for a in d.get("Answer", []) if a.get("type") == 16]


def email_auth(domain: str, txt: Callable = doh_txt) -> dict:
    """SPF, DMARC and (best effort) DKIM. status is red / amber / green / unverified."""
    spf_recs, dmarc_recs = txt(domain), txt(f"_dmarc.{domain}")
    if spf_recs is None and dmarc_recs is None:
        return {"status": "unverified", "detail": "DNS over HTTPS was not reachable, so email settings were not checked"}
    out: dict = {"spf": None, "dmarc": None, "dkim": None}
    notes: list[str] = []
    status = "green"
    if spf_recs is not None:
        spf = next((r for r in spf_recs if r.lower().startswith("v=spf1")), "")
        out["spf"] = spf or "missing"
        if not spf:
            status = "red"; notes.append("no SPF record, so mail from your domain can be spoofed or filtered")
        elif re.search(r"[+?]all\b", spf.lower()):
            status = "amber" if status != "red" else status; notes.append("SPF allows any sender")
    if dmarc_recs is not None:
        dm = next((r for r in dmarc_recs if r.lower().startswith("v=dmarc1")), "")
        out["dmarc"] = dm or "missing"
        pol = re.search(r"\bp=(\w+)", dm.lower())
        if not dm:
            status = "amber" if status != "red" else status; notes.append("no DMARC record")
        elif pol and pol.group(1) == "none":
            status = "amber" if status != "red" else status; notes.append("DMARC is set to monitor only (p=none)")

    def one(sel: str) -> str:
        r = txt(f"{sel}._domainkey.{domain}") or []
        return sel if any("v=dkim1" in x.lower() or "p=" in x.lower() for x in r) else ""
    with ThreadPoolExecutor(max_workers=5) as pool:
        found = [s for s in pool.map(one, DKIM_SELECTORS) if s]
    out["dkim"] = found[0] if found else "not detected"
    # DKIM selectors are arbitrary names, so "not detected" is a note, never a failure
    notes.append(f"DKIM found ({found[0]})" if found else "DKIM not detected on common selectors")
    if spf_recs is None or dmarc_recs is None:
        notes.append("part of the lookup could not be completed")
    out.update(status=status, detail="; ".join(notes).capitalize() + ".")
    return out


def registrable(domain: str) -> str:
    """Approximate registrable domain (no public-suffix list in the stdlib)."""
    p = domain.lower().split(".")
    if len(p) >= 3 and len(p[-1]) == 2 and p[-2] in {"co", "com", "org", "net", "gov", "edu", "ac"}:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def domain_expiry(domain: str, get_json: Callable = http_get_json, now: Callable = time.time) -> dict:
    """Days until the domain registration expires, from RDAP. days is None when it could not be read."""
    try:
        code, d = get_json(f"https://rdap.org/domain/{registrable(domain)}", UA, 6.0)
    except IapdError as e:
        return {"days": None, "detail": f"registry lookup failed: {e}"}
    if code != 200:
        return {"days": None, "detail": f"registry lookup returned HTTP {code}"}
    for ev in d.get("events", []):
        if str(ev.get("eventAction", "")).lower() == "expiration" and ev.get("eventDate"):
            try:
                end = datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                break
            return {"days": int((end - now()) // DAY), "expires": ev["eventDate"][:10], "detail": ""}
    return {"days": None, "detail": "the registry did not publish an expiry date"}


def collect_extras(domain: str, txt: Callable = doh_txt, get_json: Callable = http_get_json) -> dict:
    return {"email": email_auth(domain, txt), "domain": domain_expiry(domain, get_json)}


# ---- classification: audit record -> the eight rows ------------------------------------------------
def _finding(record: dict, gid: str, check: str) -> Optional[dict]:
    g = record["results"].get(gid)
    return next((f for f in (g["findings"] if g else []) if f["check"] == check), None)


def _status(f: Optional[dict]) -> str:
    if f is None:
        return "not_run"
    if f["status"] == "unverified":
        return "unverified"
    if f["status"] == "manual":
        return "review"
    r = f["points"] / f["max_points"] if f["max_points"] else 0
    return "green" if r >= GREEN_AT else "amber" if r >= AMBER_AT else "red"


def _worst(statuses: list[str]) -> str:
    live = [s for s in statuses if s != "not_run"]
    return max(live, key=RANK.get) if live else "not_run"


def _row(area: str, status: str, detail: str, live: bool = True) -> dict:
    return {"area": area, "status": status, "label": LABEL[status], "detail": detail, "live": live}


def _days_status(days: Optional[int], amber: int, red: int) -> str:
    return "unverified" if days is None else "red" if days < red else "amber" if days < amber else "green"


def classify(record: dict, extras: Optional[dict] = None) -> list[dict]:
    """Eight rows for the Shield page. Detail never contains held compliance text."""
    extras = extras or {}
    F = lambda g, c: _finding(record, g, c)                    # noqa: E731
    rows = []
    f = F("g02_search", "google_business_profile")
    rows.append(_row(AREAS[0], _status(f), f["detail"] if f else "Included in the free audit", f is not None))

    fs = [F("g02_search", "branded_rank_top3"), F("g02_search", "pages_indexed")]
    fs = [x for x in fs if x]
    rows.append(_row(AREAS[1], _worst([_status(x) for x in fs]), "; ".join(x["detail"] for x in fs)[:240]
                     or "Included in the free audit", bool(fs)))

    f = F("g03_ai_visibility", "firm_named_in_category_prompts")
    m = re.search(r"(\d+)/(\d+) answers", f["detail"]) if f else None
    rows.append(_row(AREAS[2], _status(f), f"Named in {m.group(1)} of {m.group(2)} AI answers" if m
                     else (f["detail"] if f else "Included in the free audit"), f is not None))

    surf = [x for x in (record["results"].get("g04_citations") or {"findings": []})["findings"]
            if x["check"].startswith("surface:") and x["check"] != "surface:linkedin" and x["check"] != "surface:media"]
    nap = F("g02_search", "nap_consistent_with_iapd")
    live = [x for x in surf + ([nap] if nap else []) if x]
    ok = [x for x in live if x["status"] != "unverified"]
    good = [x for x in ok if x["points"] >= x["max_points"] * GREEN_AT]
    bad = [check_label(x["check"]).removeprefix("Listing: ") for x in ok if x not in good and x["check"].startswith("surface:")]
    rows.append(_row(AREAS[3], _worst([_status(x) for x in live]),
                     f"{len(good)} of {len(ok)} checked listings match" + (f"; needs work: {', '.join(bad)}" if bad else "")
                     if ok else ("Not verified from here" if live else "Included in the free audit"), bool(live)))

    f = F("g05_compliance", "no_material_compliance_exposure")
    if f is None:
        rows.append(_row(AREAS[4], "not_run", "Included in the free audit", False))
    elif f["status"] == "unverified":
        rows.append(_row(AREAS[4], "unverified", "The scan did not complete"))
    elif record.get("held_flags"):
        rows.append(_row(AREAS[4], "review", "Items are with your compliance team for review"))
    else:
        rows.append(_row(AREAS[4], "green", "No wording flagged for review"))

    f = F("g04_citations", "surface:linkedin")
    rows.append(_row(AREAS[5], "review" if f else "not_run",
                     "Reviewed by a person each quarter" if f else "Included in the free audit", f is not None))

    dns, tls = F("g01_technical", "domain_resolves"), F("g01_technical", "tls_certificate_valid")
    ev = (record["results"].get("g01_technical") or {"evidence": {}})["evidence"]
    days = (ev.get("tls") or {}).get("days_left")
    dom = (extras.get("domain") or {}).get("days")
    st = [_status(dns), _status(tls)]
    if tls and tls["status"] != "unverified" and tls["points"] >= tls["max_points"] * GREEN_AT:
        st.append(_days_status(days, CERT_AMBER_DAYS, CERT_RED_DAYS))
    if "domain" in extras:
        st.append(_days_status(dom, DOMAIN_AMBER_DAYS, DOMAIN_RED_DAYS))
    bits = []
    if days is not None:
        bits.append(f"Certificate valid for {days} days")
    elif tls:
        bits.append(tls["detail"][:100])
    if dom is not None:
        bits.append(f"domain renews in {dom} days")
    elif "domain" in extras:
        bits.append("domain expiry not verified")
    rows.append(_row(AREAS[6], _worst(st), "; ".join(bits).capitalize() or "Included in the free audit",
                     dns is not None or "domain" in extras))

    em = extras.get("email")
    rows.append(_row(AREAS[7], em["status"], em["detail"]) if em
                else _row(AREAS[7], "not_run", "Included in the free audit", False))
    return rows


# ---- live snapshot for the Shield page ------------------------------------------------------------
def snapshot(url: str, *, fetcher=None, extras_fn: Callable = collect_extras, tech=None) -> dict:
    """What the Shield page shows for a visitor: the checks that need no keys and no budget.
    Domain, certificate, page load and email settings run live; the rest is the free audit's job."""
    from .g01_technical.module import TechnicalFoundation
    from .kernel import Budget, RunContext
    from .web import SharedFetcher
    url = normalize_url(url)
    domain = domain_of(url)
    # renderer=None: a static fetch is enough here and keeps the snapshot to a few seconds
    tech = tech or TechnicalFoundation(fetcher=fetcher or SharedFetcher(), renderer=None)
    res = tech.run(RunContext(url, domain, "", Budget(0.01, 25.0), domain=domain))
    record = {"results": {"g01_technical": asdict(res)}, "held_flags": []}
    rows = classify(record, extras_fn(domain))
    return {"domain": domain, "checked_at": int(time.time()), "rows": rows,
            "live_rows": sum(1 for r in rows if r["live"])}


# ---- alerts ----------------------------------------------------------------------------------------
def build_alerts(record: dict, prev: Optional[dict], extras: dict) -> list[dict]:
    """Each alert: key, severity (critical|warning), audience (client|internal), title, detail."""
    out: list[dict] = []

    def add(key, sev, aud, title, detail):
        out.append({"key": key, "severity": sev, "audience": aud, "title": title, "detail": detail})

    dns, tls = _finding(record, "g01_technical", "domain_resolves"), _finding(record, "g01_technical", "tls_certificate_valid")
    page = _finding(record, "g01_technical", "page_renders")
    if dns and dns["status"] == "ok" and dns["points"] == 0:
        add("domain_down", "critical", "client", "Your website address is not resolving", dns["detail"])
    elif page and page["status"] == "ok" and page["points"] == 0:
        add("site_down", "critical", "client", "Your website is not loading", page["detail"])
    if tls and tls["status"] == "ok" and tls["points"] == 0:
        add("cert_invalid", "critical", "client", "Your website's security certificate is not valid", tls["detail"])
    days = ((record["results"].get("g01_technical") or {"evidence": {}})["evidence"].get("tls") or {}).get("days_left")
    if days is not None and days < CERT_AMBER_DAYS and not any(a["key"] == "cert_invalid" for a in out):
        add("cert_expiring", "critical" if days < 7 else "warning", "client",
            f"Your security certificate expires in {days} days", "Renew it before it lapses so visitors never see a warning.")
    dom = (extras.get("domain") or {}).get("days")
    if dom is not None and dom < DOMAIN_AMBER_DAYS:
        add("domain_expiring", "critical" if dom < 7 else "warning", "client",
            f"Your domain registration expires in {dom} days", "Renew it with your registrar; an expired domain takes the site and email down.")
    em = extras.get("email") or {}
    if em.get("status") == "red":
        add("email_spf", "warning", "client", "Your email settings need attention", em.get("detail", ""))
    for gid, d in ((prev and record.get("delta")) or {"by_group": {}})["by_group"].items():
        if d is not None and d <= SCORE_DROP_ALERT:                  # None = a gap in coverage: never alert
            internal = gid == "g05_compliance"
            add(f"drop:{gid}", "warning", "internal" if internal else "client",
                f"{gid.split('_', 1)[1].replace('_', ' ').capitalize()} score dropped {abs(d):g} points",
                "Compare this month's findings with last month's in the evidence report.")
    held, before = set(record.get("held_flags") or []), set((prev or {}).get("held_flags") or [])
    if held - before:
        add("held_flags", "warning", "internal", f"{len(held - before)} new compliance item(s) held for CCO review",
            "None of this is shown to the client. A flag is a prompt for review, not a determination.")
    return out


# ---- text for emails ---------------------------------------------------------------------------------
def rows_text(rows: list[dict]) -> str:
    w = max(len(r["area"]) for r in rows)
    return "\n".join(f"  {r['area']:<{w}}  {r['label']:<14} {r['detail']}" for r in rows)


def shield_text(firm: str, record: dict, rows: list[dict], kind: str = "monthly", link: str = "") -> str:
    c, d = record["scorecard"], record.get("delta") or {}
    when = datetime.fromtimestamp(record["created_at"], timezone.utc).strftime("%d %B %Y")
    head = "QUARTERLY SHIELD REPORT" if kind == "quarterly" else "MONTHLY SHIELD CHECK"
    lines = [f"{head}: {firm}", f"{record['domain']}  |  {when}", "",
             f"Score: {c['total']:g} out of {c['max_total']:g}" + ("" if c["complete"] else "  (lower bound: some checks could not be verified)")]
    if d.get("total") is not None:
        lines.append(f"Change since last month: {d['total']:+g}")
    lines += ["", "WHAT SHIELD WATCHES", rows_text(rows), "", "FIXES, IN PRIORITY ORDER"]
    fixes = build_fixes(record)[:8]
    for f in fixes:
        lines.append(f"  {f['rank']}. [{f['priority'].upper()}] {f['area']}: {f['action']}")
    if not fixes:
        lines.append("  Nothing measured needs fixing.")
    if link:
        lines += ["", f"Full report online, with all five checks and the fix list: {link}"]
    lines += ["", "Items marked 'With a person' are reviewed by our team and, where they concern wording, by your CCO.",
              "Shield reports observable signals from public sources. It is not legal or compliance advice."]
    return "\n".join(lines)


def alert_email(firm: str, a: dict, to: str) -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = f"[Shield alert] {a['title']}"
    m["To"] = to
    m.set_content(f"{firm}\n\n{a['title']}\n\n{a['detail']}\n\nReply to this email and we will help fix it.\n\n"
                  "Shield checks public signals only and is not legal or compliance advice.")
    return m


# ---- subscribers and the monthly job -------------------------------------------------------------------
