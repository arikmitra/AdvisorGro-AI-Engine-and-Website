"""Report generator: reads one run record and writes both reports.

client_report.html  plain language, ranked fix list. Never contains held compliance text.
evidence_report.md  INTERNAL. Every check, raw and source-traced, including held flags.
run_record.json     the full record

Ranking is computed here from the findings (points at stake divided by effort). The model
only writes the wording, and its output is validated against the facts it was given.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .fixes import (DEFAULT_EFFORT, EFFORT, EFFORT_WORD, GROUPS, build_fixes, fixes_markdown,  # noqa: F401
                   gate, passed_checks, unverified, coverage_notes)
from .llm import AnthropicClient, LLMError, parse_json
from .reportjson import check_label

PILL = {"High": "bad", "Medium": "warn", "Low": "ok"}

SYSTEM = ("You write short, plain-English audit explanations for the principal of a small "
          "investment advisory firm. Use only the facts you are given. Do not promise rankings, "
          "leads or results. Do not give legal or compliance advice.")
PROMPT = """Firm: {firm}. Overall: {total} of {max_total} points ({measured}).

Write:
1. "summary": at most 90 words, plain English, what the audit found overall.
2. "fixes": for each item below, 1-2 sentences saying what is wrong and what to do about it.

Items:
{items}

Reply with ONLY JSON: {{"summary": "...", "fixes": [{{"check": "<check id>", "text": "..."}}]}}"""


def measured(record: dict) -> tuple[float, float]:
    c = record["scorecard"]
    ok = [v for v in c["by_group"].values() if not v["partial"]]
    return sum(v["score"] for v in ok), sum(v["max"] for v in ok)


def template_text(fix: dict) -> str:
    return f"{fix['detail']} Next step: {fix['action']}"


def write_text(client: Optional[AnthropicClient], record: dict, fixes: list[dict]):
    """Returns (summary, {check: text}, evidence). Falls back to templates on any problem."""
    c = record["scorecard"]
    got, avail = measured(record)
    texts = {f["check"]: template_text(f) for f in fixes}
    summary = (f"The audit scored {record['firm_name']} {c['total']:g} out of {c['max_total']:g}. "
               + (f"{got:g} of {avail:g} points were measured in the groups that completed; "
                  f"the rest could not be verified." if not c["complete"] else "All five groups completed."))
    if not client:
        return summary, texts, {"writer": "template", "reason": "no API key"}
    items = "\n".join(f'- check "{f["check"]}" ({f["area"]}), {f["lost"]:g} points at stake: {f["detail"]} (suggested step: {f["action"]})'
                      for f in fixes[:10]) or "(no fixes found)"
    try:
        res = client.complete(PROMPT.format(firm=record["firm_name"], total=f"{c['total']:g}",
                              max_total=f"{c['max_total']:g}",
                              measured=f"{got:g} of {avail:g} measured" if not c["complete"] else "complete",
                              items=items), system=SYSTEM, max_tokens=900, timeout=60.0)
        d = parse_json(res.text)
        if not isinstance(d, dict) or not isinstance(d.get("summary"), str) or not isinstance(d.get("fixes"), list):
            raise ValueError("unexpected structure")
        known = {f["check"] for f in fixes}
        for x in d["fixes"]:
            if isinstance(x, dict) and x.get("check") in known and isinstance(x.get("text"), str) and x["text"].strip():
                texts[x["check"]] = x["text"].strip()[:500]
        return d["summary"].strip()[:700], texts, {"writer": "llm", "model": res.model, "cost_usd": round(res.cost_usd, 5)}
    except (LLMError, ValueError) as e:
        return summary, texts, {"writer": "template", "reason": f"{type(e).__name__}: {e}"}


CSS = """body{font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#0a1614;max-width:860px;margin:32px auto;padding:0 20px;line-height:1.5}
h1{color:#144b39;margin:0}h2{color:#144b39;border-bottom:2px solid #01dc80;padding-bottom:4px;margin-top:36px}
.sub{color:#6b7878}.score{font-size:56px;font-weight:800;color:#144b39}.score small{font-size:22px;color:#6b7878}
table{border-collapse:collapse;width:100%}td,th{padding:8px 10px;border-bottom:1px solid #dfe2e0;text-align:left;vertical-align:top}
.pill{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:700}.ok{background:#dcfce7;color:#166534}
.warn{background:#fef3c7;color:#854d0e}.bad{background:#fde2e2;color:#9b1c1c}
.note{background:#f4f4f2;border-left:4px solid #01dc80;padding:10px 14px;margin:16px 0;font-size:14px}
.bar{background:#e8ecea;border-radius:6px;height:10px}.bar>i{display:block;height:10px;border-radius:6px;background:#01dc80}"""


def _e(x) -> str:
    return html.escape(str(x))


def client_html(record: dict, summary: str, texts: dict, fixes: list[dict], ev: dict, show_passed: bool = True) -> str:
    c, show = record["scorecard"], gate(record)["show_compliance"]
    got, avail = measured(record)
    when = datetime.fromtimestamp(record["created_at"], timezone.utc).strftime("%d %B %Y")
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Digital audit: {_e(record['firm_name'])}</title>"
         f"<style>{CSS}</style></head><body>",
         f"<h1>Digital audit</h1><div class='sub'>{_e(record['firm_name'])} &middot; {_e(record['domain'])} &middot; {when}</div>",
         f"<div class='score'>{c['total']:g}<small> / {c['max_total']:g}</small></div>"]
    if not c["complete"]:
        h.append(f"<div class='note'>This score is a lower bound. {got:g} of {avail:g} points were measured in the "
                 f"groups that completed. The remaining areas could not be verified and are listed below.</div>")
    h.append(f"<p>{_e(summary)}</p>")
    ok = passed_checks(record) if show_passed else []
    if show_passed:
        h.append("<h2>What is working</h2>")
        h.append("<ul>" + "".join(f"<li><b>{_e(check_label(p['check']))}</b> ({_e(p['area'])}): {_e(p['detail'])}</li>" for p in ok) + "</ul>"
                 if ok else "<p>No check has earned full marks yet.</p>")
    for n in coverage_notes(record):
        h.append(f"<div class='note'>{_e(n['client'])}</div>")
    h.append("<h2>What to fix first, in priority order</h2>")
    if fixes:
        h.append("<table><tr><th>#</th><th>Priority</th><th>Area</th><th>What to do</th><th>Points at stake</th><th>Effort</th></tr>")
        for i, f in enumerate(fixes, 1):
            h.append(f"<tr><td>{i}</td><td><span class='pill {PILL[f['priority']]}'>{f['priority']}</span></td><td>{_e(f['area'])}</td><td>{_e(texts[f['check']])}</td>"
                     f"<td>{f['lost']:g}</td><td>{EFFORT_WORD[f['effort']]}</td></tr>")
        h.append("</table>")
    else:
        h.append("<p>Nothing measured needs fixing.</p>")
    h.append("<h2>Results by area</h2><table>")
    for gid, v in c["by_group"].items():
        if gid == "g05_compliance" and not show:
            h.append(f"<tr><td>{GROUPS[gid]}</td><td colspan='2'><span class='pill warn'>with compliance review</span> "
                     f"Items in this area are held for your compliance team and are not shown here.</td></tr>")
            continue
        pct = 100 * v["score"] / v["max"] if v["max"] else 0
        tag = "<span class='pill warn'>partly verified</span>" if v["partial"] else "<span class='pill ok'>verified</span>"
        h.append(f"<tr><td>{GROUPS.get(gid, gid)}</td><td style='width:35%'><div class='bar'><i style='width:{pct:.0f}%'></i></div></td>"
                 f"<td>{v['score']:g} / {v['max']:g} {tag}</td></tr>")
    h.append("</table>")
    un = unverified(record)
    if un:
        h.append("<h2>What we could not verify</h2><ul>")
        h += [f"<li><b>{_e(u['area'])}</b>: {'needs manual review' if u['manual'] else 'not verified'}. {_e(u['detail'])}</li>" for u in un]
        h.append("</ul>")
    h.append("<div class='note'>This audit reports observable signals from public sources. It does not provide legal or "
             "compliance advice, and flagged items are for human review, not compliance determinations.</div></body></html>")
    return "".join(h)


def evidence_md(record: dict, ev: dict) -> str:
    c = record["scorecard"]
    L = [f"# Evidence report: {record['firm_name']} ({record['domain']})", "",
         "> INTERNAL. Contains held compliance flags. Do not send to the client.", "",
         f"- Run `{record['run_id']}` · {record['duration_s']}s · ${record['spent_usd']} spent · release status: **{record['release_status']}**",
         f"- Score {c['total']:g} / {c['max_total']:g} · complete: {c['complete']} · partial groups: {', '.join(c['partial_groups']) or 'none'}",
         f"- Report wording: {ev.get('writer')}" + (f" ({ev.get('reason')})" if ev.get("reason") else ""), ""]
    notes = coverage_notes(record)
    if notes:
        L += ["## Budget limits: what more budget would add", ""] + [f"- {n['operator']}" for n in notes] + [""]
    L += ["## Prioritized fix list", ""] + fixes_markdown(
        record.get("fixes") or build_fixes(record, include_held=True)) + [""]
    for gid, g in record["results"].items():
        L += [f"## {GROUPS.get(gid, gid)}: {g['score']:g} / {g['max_points']:g}" + (" (partial)" if g["partial"] else ""), ""]
        for f in g["findings"]:
            L.append(f"- [{f['status']}] **{f['check']}** {f['points']:g}/{f['max_points']:g}: {f['detail']}")
        for fl in g["evidence"].get("flags", []):
            L.append(f"  - flag ({fl['severity']}) {fl['category']}: \"{fl['snippet']}\" — {fl['reason']}")
        L += ["", "```json", json.dumps(g["evidence"], indent=2, default=str)[:6000], "```", ""]
    if record.get("held_flags"):
        L += ["## Held for CCO review", ""] + [f"- {x}" for x in record["held_flags"]] + [""]
    if record.get("incidents"):
        L += ["## Incidents logged to the KEDB", ""] + [
            f"- `{i['signature']}` {i['group_id']}/{i['category']} ({i['kind']}): {i['detail']}" for i in record["incidents"]]
    return "\n".join(L)


def build_reports(record: dict, client: Optional[AnthropicClient], out_dir: str, show_passed: bool = True) -> dict:
    record.setdefault("fixes", build_fixes(record, include_held=True))   # internal list, keeps held items
    fixes = build_fixes(record)                                          # client-safe list
    summary, texts, ev = write_text(client, record, fixes)
    slug = re.sub(r"[^a-z0-9.-]", "", record["domain"].lower())
    d = Path(out_dir) / f"{slug}-{record['run_id'][:8]}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "client_report.html").write_text(client_html(record, summary, texts, fixes, ev, show_passed), encoding="utf-8")
    (d / "evidence_report.md").write_text(evidence_md(record, ev), encoding="utf-8")
    (d / "run_record.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return {"dir": str(d), "client": str(d / "client_report.html"), "evidence": str(d / "evidence_report.md"),
            "record": str(d / "run_record.json"), "writer": ev, "fixes": fixes,
            "summary": summary, "texts": texts, "show_passed": show_passed}