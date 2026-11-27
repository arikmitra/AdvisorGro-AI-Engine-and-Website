import time, re
from advisorgro.engine.shield.mvp.kernel import Budget, RunContext, GroupResult, Finding
from advisorgro.engine.shield.mvp.scoring import build_scorecard

# 1 scoring: missing group keeps the full denominator
r = {"g01_technical": GroupResult("g01_technical", 17, 20)}
c = build_scorecard(r); assert c["max_total"] == 100 and not c["complete"], c

# 2 firm: Service-only page is not a firm name; ranked pick
from advisorgro.engine.shield.mvp.firm import extract_details
h = '<title>Shield — Always-On | AdvisorGro</title><script type="application/ld+json">{"@type":"Service","name":"Shield — Always-On"}</script>'
assert extract_details(h, "advisorgro.com")["name"][1] != "JSON-LD"
h = ('<script type="application/ld+json">{"@graph":[{"@type":"Service","name":"Retirement plan"},'
     '{"@type":"FinancialService","name":"Merrow Lane Wealth","address":{"addressLocality":"Austin","addressRegion":"TX"}}]}</script>')
d = extract_details(h, "merrowlane.com"); assert d["name"][0] == "Merrow Lane Wealth" and d["area"][0] == "Austin, TX", d
extract_details('<script type="application/ld+json">{"@type":"Organization","name":123}</script>', "x.com")  # no crash

# 3 config: bad JSON doesn't crash
from advisorgro.engine.shield.mvp.config import Config
cfg = Config.from_env({"ADVISORGRO_EXTRA_ENGINES": "{bad"}); assert cfg.extra_engines == [] and cfg.config_errors

# 4 scanner
from advisorgro.engine.shield.mvp.g05_compliance.scanner import scan, review
for s, expect in [("We cannot guarantee returns on any investment.", 0), ("We don't guarantee performance.", 0),
                  ("We guarantee returns of 10% a year, past performance aside.", 1)]:
    mat = [f for f in review(scan(s), s, "form crs") if f.severity == "material"]
    assert (len(mat) > 0) == bool(expect), (s, mat)

# 5 grader domain
from advisorgro.engine.shield.mvp.g03_ai_visibility.grader import grade
from advisorgro.engine.shield.mvp.g03_ai_visibility.engines import Answer
a = [Answer("e","p",0,"Try myacme.com or acme.com.au"), Answer("e","p",1,"See acme.com for details"), Answer("e","p",2,"Visit acme.com.")]
g = grade(a, "Zzz", "acme.com"); assert g.named == 2, g

# 6 collector: dead engine skipped, others continue; cache ttl
from advisorgro.engine.shield.mvp.g03_ai_visibility.collector import collect, Plan, QueryCache
class Fatal(Exception): fatal = True
class Bad:
    name, cost_per_call = "bad", 0.0
    def ask(self, p, r): raise Fatal("bad key")
class Good:
    name, cost_per_call = "good", 0.0
    def ask(self, p, r): return Answer("good", p, r, "x", (), 0.0)
b = Budget(1.0, 120.0)
ans, cut, errs = collect(["a","b"], Plan((Bad(), Good()), 3, False), b, QueryCache(), workers=2)
assert len(ans) == 6 and not cut and 1 <= len(errs) <= 2, (len(ans), len(errs))
t = [0.0]; qc = QueryCache(ttl_s=10, clock=lambda: t[0]); qc.put(Answer("e","p",0,"x")); assert qc.get("e","p",0); t[0] = 11; assert qc.get("e","p",0) is None

# 7 g03 module: smaller plan is not partial when answers suffice
from advisorgro.engine.shield.mvp.g03_ai_visibility.module import AIVisibility, Config as C3
from advisorgro.engine.shield.mvp.g03_ai_visibility.engines import StubEngine
ctx = RunContext("https://acme.com", "Acme Wealth", "Austin, TX", Budget(0.5, 120.0))
res = AIVisibility([StubEngine("a","Acme Wealth","acme.com"), StubEngine("b","Acme Wealth","acme.com")]).run(ctx)
assert not res.partial and res.findings[0].status == "ok", res
# thin evidence is partial
res = AIVisibility([StubEngine("a","Acme Wealth","acme.com")], C3(battery_size=2)).run(
    RunContext("https://acme.com", "Acme Wealth", "Austin", Budget(0.5, 120.0)))
assert res.partial or res.evidence["answers"] >= 10

# 8 g01: TLS timeout is transient, not blamed on the cert; hung probe doesn't block
from advisorgro.engine.shield.mvp.web import Page
from advisorgro.engine.shield.mvp.g01_technical.module import TechnicalFoundation
from advisorgro.engine.shield.mvp.g01_technical.probes import DnsResult, TlsResult
class F:
    def get(self, url, timeout=8, verify=True):
        return Page(True, 200, url, "text/html", "<title>t</title><link rel=canonical href=/><meta name=viewport content=x>"+"x"*10, 500, 0.1)
g1 = TechnicalFoundation(dns=lambda h,t: DnsResult(True,["1.1.1.1"]), tls=lambda h,t: TlsResult(False, error="timed out", transient=True),
                         fetcher=F(), renderer=None)
r1 = g1.run(RunContext("https://acme.com","A","x",Budget(0.1,60.0)))
f = {x.check: x for x in r1.findings}
assert f["tls_certificate_valid"].status == "unverified" and f["page_renders"].points == 4 and f["page_renders"].status == "ok", r1.findings
def hang(h, t): time.sleep(30)
g1 = TechnicalFoundation(dns=hang, tls=lambda h,t: TlsResult(True,"TLSv1.3","x","x","",100), fetcher=F(), renderer=None)
t0 = time.time(); r1 = g1.run(RunContext("https://acme.com","A","x",Budget(0.1,6.0)))
assert time.time()-t0 < 12 and r1.partial, time.time()-t0

# 9 g05 thin page unverified; g05 floor
from advisorgro.engine.shield.mvp.g05_compliance.module import ComplianceExposure
class F2:
    def __init__(s, html): s.html = html
    def get(s, url, timeout=8, verify=True): return Page(True, 200, url, "text/html", s.html, len(s.html), 0.1)
r5 = ComplianceExposure(fetcher=F2("<html><body><div id=root></div><script></script></body></html>")).run(RunContext("https://a.com","A","x",Budget()))
assert r5.partial and r5.findings[0].status == "unverified"

# 10 orchestrator: incomplete runs aren't served from cache
from advisorgro.engine.shield.mvp.orchestrator import Orchestrator
from advisorgro.engine.shield.mvp.storage import RunStore
from advisorgro.engine.shield.mvp.stubs import StubGroup
mods = [StubGroup("g01_technical",20,17), StubGroup("g02_search",20,9)]
o = Orchestrator(RunStore(), mods); r1 = o.run("https://a.com","A","x"); r2 = o.run("https://a.com","A","x")
assert not r1["scorecard"]["complete"] and not r2["cached"]

# 11 report keeps partial findings
from advisorgro.engine.shield.mvp.report import build_fixes
rec = {"release_status":"releasable","results":{"g03_ai_visibility":{"findings":[{"check":"firm_named_in_category_prompts","status":"partial","points":5,"max_points":25,"detail":"d","passed":False}]}}}
assert build_fixes(rec)
# 12 fix list: ranking, priority bands, held compliance, unverified excluded
from advisorgro.engine.shield.mvp.fixes import build_fixes as bf, priority_label, format_fix_list, unverified as unv
rec = {"release_status": "pending_cco_review", "results": {
    "g01_technical": {"findings": [
        {"check": "title_canonical_viewport", "status": "ok", "points": 0, "max_points": 4, "detail": "missing", "passed": False},
        {"check": "page_renders", "status": "ok", "points": 3, "max_points": 4, "detail": "js error", "passed": False},
        {"check": "structured_data_jsonld", "status": "unverified", "points": 0, "max_points": 4, "detail": "x", "passed": False}]},
    "g05_compliance": {"findings": [
        {"check": "no_material_compliance_exposure", "status": "ok", "points": 5, "max_points": 15, "detail": "2 material", "passed": False}]}}}
client = bf(rec)
assert [f["check"] for f in client] == ["title_canonical_viewport", "page_renders"], client
assert [f["priority"] for f in client] == ["High", "Low"] and [f["rank"] for f in client] == [1, 2]
internal = bf(rec, include_held=True)
assert [f["check"] for f in internal][0] == "no_material_compliance_exposure" and internal[0]["held"] and internal[0]["rank"] == 1
assert all(f["action"] for f in internal) and not any(f["check"] == "structured_data_jsonld" for f in internal)
assert (priority_label(2.0), priority_label(1.0), priority_label(0.99)) == ("High", "Medium", "Low")
assert "HELD" in format_fix_list(internal) and "Nothing measured" in format_fix_list([])
assert [u["check"] for u in unv(rec)] == ["structured_data_jsonld"]
rec["release_status"] = "releasable"
assert any(f["group"] == "g05_compliance" and not f["held"] for f in bf(rec))

# 13 the orchestrator stores the internal fix list in the run record
o = Orchestrator(RunStore(), [StubGroup("g01_technical", 20, 17), StubGroup("g02_search", 20, 9)])
rr = o.run("https://b.com", "B", "x")
assert rr["fixes"] and rr["fixes"][0]["rank"] == 1 and rr["fixes"][0]["priority"] in ("High", "Medium", "Low")

# 14 client report: shows priority, never shows held compliance text
import tempfile, os
from advisorgro.engine.shield.mvp.report import build_reports
rec2 = dict(rr)
rec2["results"] = dict(rec2["results"])
rec2["results"]["g05_compliance"] = {"group_id": "g05_compliance", "score": 5, "max_points": 15, "partial": False,
    "findings": [{"check": "no_material_compliance_exposure", "status": "ok", "points": 5, "max_points": 15,
                  "detail": "SECRET-HELD-TEXT", "passed": False}], "evidence": {"flags": [], "material_flags": ["x"]}}
rec2["release_status"] = "pending_cco_review"; rec2["held_flags"] = ["x"]
rec2["scorecard"] = dict(rec2["scorecard"]); rec2["scorecard"]["by_group"] = {**rec2["scorecard"]["by_group"], "g05_compliance": {"score": 5, "max": 15, "partial": False}}
rec2.pop("fixes")
with tempfile.TemporaryDirectory() as d:
    rep = build_reports(rec2, None, d)
    html = open(rep["client"]).read(); md = open(rep["evidence"]).read()
    assert "Priority" in html and "SECRET-HELD-TEXT" not in html
    assert "Prioritized fix list" in md and "held for CCO review" in md
    assert any(f["held"] for f in rec2["fixes"])      # internal list kept for the record
print("ALL OK")