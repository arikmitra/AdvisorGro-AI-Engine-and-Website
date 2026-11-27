"""Known Error Database (KEDB) for failed and faulty runs.

Every run record is inspected for problems. Each problem becomes an incident
with a stable signature, so the same fault seen on many firms collapses into
one known-error row with an occurrence count. A human then confirms the root
cause and workaround; until then the entry carries only a *suggested*,
unconfirmed cause.

failed  = a group produced nothing usable (error, timeout, no data, missing)
faulty  = a group produced a result, but not a trustworthy or full one
          (degraded plan, budget cut-off, out-of-range score)

Library:  KEDB(path).record_run(run_record)
CLI:      python log.py list | show SIG | annotate SIG ... | report
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger("advisorgro.kedb")

FAILED, FAULTY = "failed", "faulty"
STATUSES = ("open", "known", "resolved", "reopened")

# category -> (kind, symptom, SUGGESTED cause. A hypothesis, not a finding)
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "group_error": (FAILED, "Group raised an unhandled exception",
        "Bug or unexpected input in the group's code or a dependency it calls. "
        "The exception type in the detail points at where to look."),
    "group_timeout": (FAILED, "Group did not return before the run clock",
        "A probe or third-party call is slower than the 2-minute clock, or the "
        "probe ignores budget.remaining_seconds and keeps running."),
    "no_data": (FAILED, "Group collected nothing",
        "Budget slice was empty or exhausted before the first call, or every "
        "call failed."),
    "missing_group": (FAILED, "Group returned no result at all",
        "The group is not registered in ACTIVE_MODULES or was left out of the "
        "orchestrator's module list."),
    "not_implemented": (FAILED, "Group is not built or configured yet",
        "No real data source is connected for this group. See the 'needs' in the detail."),
    "invalid_input": (FAILED, "Request rejected at intake",
        "The URL failed validation (no host, or not a site URL)."),
    "budget_cutoff": (FAULTY, "Budget ran out mid-collection",
        "Calls x per-call cost exceed the group's budget slice. Review "
        "DEFAULT_SHARES, the battery size, or the per-call cost."),
    "degraded": (FAULTY, "Group ran a reduced plan",
        "The full plan was unaffordable at the current per-call cost, so the "
        "group fell back to fewer engines and repeats."),
    "score_out_of_range": (FAULTY, "Group returned a score outside 0..max",
        "Bug in the group's scoring function. The scoring engine clamped it, "
        "so the total is not wrong but the group is."),
    "unverified_checks": (FAULTY, "Some checks could not be verified",
        "A data source is missing, blocked or unreachable. The check names in the detail say which."),
    "scrape_fallback": (FAULTY, "Group used the scraping fallback",
        "A search, model or listing API is missing, rejected (bad key), out of quota or failing, so the group read public "
        "web pages instead. Results are real but weaker; fix the API to restore the full measurement."),
    "partial_unspecified": (FAULTY, "Group flagged partial with no recorded reason",
        "The group set partial=True without evidence saying why. Add a reason "
        "to its evidence."),
}


# --- incidents --------------------------------------------------------------------------
def _normalize(detail: str) -> str:
    d = detail.lower()
    d = re.sub(r"https?://\S+", "<url>", d)
    d = re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{8,}\b", "<id>", d)
    d = re.sub(r"\d+(\.\d+)?", "<n>", d)
    return re.sub(r"\s+", " ", d).strip()[:200]


def make_signature(group_id: str, category: str, detail: str) -> str:
    return hashlib.sha1(f"{group_id}|{category}|{_normalize(detail)}".encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Incident:
    run_id: str
    domain: str
    group_id: str
    category: str
    detail: str

    @property
    def kind(self) -> str:
        return CATEGORIES[self.category][0]

    @property
    def signature(self) -> str:
        return make_signature(self.group_id, self.category, self.detail)


def classify(record: dict) -> list[Incident]:
    """Inspect one run record and return every problem found in it."""
    rid, dom = record.get("run_id", ""), record.get("domain", "")
    out: list[Incident] = []

    def add(g, c, d):
        out.append(Incident(rid, dom, g, c, d))

    for gid, r in (record.get("results") or {}).items():
        ev, before = r.get("evidence") or {}, len(out)
        if ev.get("reason") == "error":
            add(gid, "group_error", ev.get("detail") or "unknown error")
        elif ev.get("reason") == "timeout":
            add(gid, "group_timeout", ev.get("detail") or "no result before clock")
        elif ev.get("reason") == "not_implemented":
            add(gid, "not_implemented", f"needs {ev.get('needs', 'a data source')}")
        elif ev.get("reason") == "page_unavailable":
            add(gid, "no_data", f"page unavailable: {ev.get('detail', '')}")
        if ev.get("plan") == "none":
            add(gid, "no_data", "no answers collected")
        if ev.get("cut_off"):
            add(gid, "budget_cutoff", f"stopped after {ev.get('answers', '?')} answers")
        if ev.get("degraded"):
            add(gid, "degraded",
                f"{len(ev.get('engines') or [])} engine(s) x {ev.get('repeats', '?')} repeats")
        s, m = r.get("score", 0), r.get("max_points", 0)
        if not 0 <= s <= m:
            add(gid, "score_out_of_range", f"score {s} outside 0..{m}")
        fs = r.get("findings") or []
        unv = sorted(f["check"] for f in fs if f.get("status") == "unverified")
        if unv and len(out) == before:
            add(gid, "unverified_checks", ", ".join(unv))
        elif (r.get("partial") and len(out) == before and not ev.get("scrape")
              and not any(f.get("status") == "manual" for f in fs)):
            add(gid, "partial_unspecified", "partial with no recorded reason")   # never pass unexplained
        sc = ev.get("scrape") or {}
        used = sorted((sc.get("checks") or {}).keys()) + sorted(
            k for k, v in (ev.get("surfaces") or {}).items() if isinstance(v, dict) and v.get("scrape"))
        if sc.get("used") or used:
            why = sc.get("why") or ev.get("primary_failure") or ", ".join(
                sorted({str(v.get("scrape_reason")) for v in (ev.get("surfaces") or {}).values()
                        if isinstance(v, dict) and v.get("scrape_reason")})) or "API missing or failing"
            add(gid, "scrape_fallback", f"{why}" + (f" ({', '.join(used)})" if used else ""))
    for gid in (record.get("scorecard") or {}).get("missing_groups", []):
        add(gid, "missing_group", "no result returned")
    return out


# --- the database -----------------------------------------------------------------------
class KEDB:
    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time,
                 dedupe_window_s: float = 3600.0):
        """`dedupe_window_s`: the same problem on the same site is one log entry, however many times it is re-run
        inside this window (default one hour, the same as the run cache). After it, a recurrence is a new occurrence."""
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock, self._clock, self.dedupe_window_s = threading.Lock(), clock, dedupe_window_s
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS known_errors(
                signature TEXT PRIMARY KEY, group_id TEXT, category TEXT, kind TEXT,
                symptom TEXT, example_detail TEXT, suggested_cause TEXT,
                root_cause TEXT DEFAULT '', workaround TEXT DEFAULT '',
                status TEXT DEFAULT 'open', first_seen REAL, last_seen REAL,
                occurrences INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS occurrences(
                id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT, run_id TEXT,
                domain TEXT, seen_at REAL, detail TEXT);
            CREATE INDEX IF NOT EXISTS ix_occ ON occurrences(signature);""")

    def record_incident(self, inc: Incident) -> dict:
        sig, now = inc.signature, self._clock()
        with self._lock:
            row = self._db.execute("SELECT * FROM known_errors WHERE signature=?",
                                   (sig,)).fetchone()
            is_new = row is None
            duplicate = False
            if not is_new and row["status"] != "resolved":
                # never log the same thing twice: the same run, or the same problem on the same site inside the window.
                # (A problem that was marked resolved and comes back is new information, so it is always logged.)
                duplicate = bool(self._db.execute(
                    "SELECT 1 FROM occurrences WHERE signature=? AND (run_id=? OR (domain=? AND seen_at>=?)) LIMIT 1",
                    (sig, inc.run_id, inc.domain, now - self.dedupe_window_s)).fetchone())
            if duplicate:
                self._db.execute("UPDATE known_errors SET last_seen=? WHERE signature=?", (now, sig))
            elif is_new:
                kind, symptom, cause = CATEGORIES[inc.category]
                self._db.execute(
                    "INSERT INTO known_errors(signature,group_id,category,kind,symptom,"
                    "example_detail,suggested_cause,first_seen,last_seen,occurrences)"
                    " VALUES (?,?,?,?,?,?,?,?,?,1)",
                    (sig, inc.group_id, inc.category, kind, symptom, inc.detail, cause, now, now))
            else:
                status = "reopened" if row["status"] == "resolved" else row["status"]
                self._db.execute("UPDATE known_errors SET occurrences=occurrences+1,"
                                 "last_seen=?, status=? WHERE signature=?", (now, status, sig))
            if not duplicate:
                self._db.execute("INSERT INTO occurrences(signature,run_id,domain,seen_at,detail)"
                                 " VALUES (?,?,?,?,?)", (sig, inc.run_id, inc.domain, now, inc.detail))
            self._db.commit()
            row = self._db.execute("SELECT * FROM known_errors WHERE signature=?",
                                   (sig,)).fetchone()
        if is_new:
            logger.warning("new known error %s: %s/%s: %s", sig, inc.group_id,
                           inc.category, inc.detail)
        return {"signature": sig, "group_id": inc.group_id, "category": inc.category,
                "kind": inc.kind, "detail": inc.detail, "is_new": is_new, "duplicate": duplicate,
                "status": row["status"], "root_cause": row["root_cause"],
                "workaround": row["workaround"]}

    def record_run(self, record: dict) -> list[dict]:
        if record.get("cached"):
            return []                                    # a replayed record is not a new observation
        seen, out = set(), []
        for i in classify(record):
            if i.signature not in seen:                  # two findings that normalise to one problem are one entry
                seen.add(i.signature)
                out.append(self.record_incident(i))
        return out

    def annotate(self, signature: str, *, root_cause: Optional[str] = None,
                 workaround: Optional[str] = None, status: Optional[str] = None) -> dict:
        cur = self.get(signature)
        if cur is None:
            raise KeyError(f"unknown signature {signature!r}")
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        new_cause = cur["root_cause"] if root_cause is None else root_cause
        new_work = cur["workaround"] if workaround is None else workaround
        if status == "known" and not (new_cause.strip() and new_work.strip()):
            raise ValueError("'known' needs a confirmed root_cause and a workaround")
        with self._lock:
            self._db.execute("UPDATE known_errors SET root_cause=?, workaround=?, status=?"
                             " WHERE signature=?",
                             (new_cause, new_work, status or cur["status"], signature))
            self._db.commit()
        return self.get(signature)  # type: ignore[return-value]

    def get(self, signature: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM known_errors WHERE signature=?",
                                   (signature,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["domains"] = self._db.execute(
                "SELECT COUNT(DISTINCT domain) FROM occurrences WHERE signature=?",
                (signature,)).fetchone()[0]
        return d

    def list(self, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        q = "SELECT signature FROM known_errors" + (" WHERE status=?" if status else "")
        q += " ORDER BY occurrences DESC, last_seen DESC LIMIT ?"
        with self._lock:
            sigs = [r[0] for r in self._db.execute(q, (status, limit) if status else (limit,))]
        return [self.get(s) for s in sigs]  # type: ignore[misc]

    def occurrences(self, signature: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT run_id,domain,seen_at,detail FROM occurrences"
                                    " WHERE signature=? ORDER BY id DESC LIMIT ?",
                                    (signature, limit)).fetchall()
        return [dict(r) for r in rows]

    def report_markdown(self) -> str:
        items = self.list()
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = ["# Known Error Database", ""]
        if not items:
            return "\n".join(lines + ["No known errors recorded."])
        lines += ["| Signature | Group | Category | Kind | Status | Seen | Firms |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| `{e['signature']}` | {e['group_id']} | {e['category']} | {e['kind']} | "
                  f"{e['status']} | {e['occurrences']} | {e['domains']} |" for e in items]
        for e in items:
            lines += ["", f"### `{e['signature']}` {e['group_id']} / {e['category']} ({e['status']})",
                      f"- **Symptom:** {e['symptom']}",
                      f"- **Example:** {e['example_detail']}",
                      f"- **Seen:** {e['occurrences']}x across {e['domains']} firm(s); "
                      f"first {iso(e['first_seen'])}, last {iso(e['last_seen'])}",
                      f"- **Confirmed root cause:** {e['root_cause'] or '_not yet confirmed_'}",
                      f"- **Suggested cause (unconfirmed):** {e['suggested_cause']}",
                      f"- **Workaround:** {e['workaround'] or '_none documented_'}"]
        return "\n".join(lines)


# --- CLI --------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inspect and annotate the KEDB")
    ap.add_argument("--db", default="kedb.sqlite")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--status", choices=STATUSES)
    sub.add_parser("show").add_argument("signature")
    an = sub.add_parser("annotate"); an.add_argument("signature")
    an.add_argument("--cause"); an.add_argument("--workaround"); an.add_argument("--status", choices=STATUSES)
    sub.add_parser("report")
    a = ap.parse_args(argv)
    db = KEDB(a.db)
    try:
        if a.cmd == "list":
            for e in db.list(a.status):
                print(f"{e['signature']}  {e['status']:<9}{e['kind']:<7}{e['occurrences']:>4}x  "
                      f"{e['group_id']}/{e['category']}")
        elif a.cmd == "show":
            e = db.get(a.signature)
            if not e:
                print("not found", file=sys.stderr); return 1
            for k, v in e.items():
                print(f"{k:<16}{v}")
            for o in db.occurrences(a.signature, 5):
                print(f"  seen on {o['domain']} (run {o['run_id'][:8]}): {o['detail']}")
        elif a.cmd == "annotate":
            db.annotate(a.signature, root_cause=a.cause, workaround=a.workaround, status=a.status)
            print("updated")
        else:
            print(db.report_markdown())
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())