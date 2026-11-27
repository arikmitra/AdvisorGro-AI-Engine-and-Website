import sys, pathlib, ssl, shutil, subprocess, threading, http.server
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest

from advisorgro.engine.shield.mvp.kernel import ACTIVE_MODULES, Budget, RunContext
from advisorgro.engine.shield.mvp.web import Page
from advisorgro.engine.shield.mvp.g01_technical.module import TechnicalFoundation, make_module
from advisorgro.engine.shield.mvp.g01_technical.probes import DnsResult, TlsResult, resolve, tls_probe
from advisorgro.engine.shield.mvp.g01_technical.render import Rendered, render_page

GOOD = """<html><head><title>Firm</title><link rel="canonical" href="https://f.com/">
<meta name="viewport" content="width=device-width"><script type="application/ld+json">
{"@type":"FinancialService"}</script></head><body>""" + "Hello world. " * 30 + "</body></html>"
SHELL = ("<html><head><script>1</script><script>2</script><script>3</script></head>"
         "<body><div id=app></div></body></html>")


def ctx():
    return RunContext("https://f.com", "Firm", "Austin", Budget())


def page(html=GOOD, **kw):
    d = dict(ok=True, status=200, final_url="https://f.com/", content_type="text/html",
             html=html, size=len(html))
    d.update(kw)
    return Page(**d)


class FakeFetcher:
    """Returns `secure` when certificate checks are on, `insecure` when they are off."""
    def __init__(self, secure, insecure=None):
        self.secure, self.insecure, self.calls = secure, insecure, []
    def get(self, url, timeout=8.0, verify=True):
        self.calls.append(verify)
        return self.secure if verify else (self.insecure or self.secure)


def rendered(html=GOOD, **kw):
    d = dict(ok=True, status=200, final_url="https://f.com/", html=html, elapsed_s=1.2)
    d.update(kw)
    return Rendered(**d)


def module(dns=None, tls=None, p=None, insecure=None, render=None):
    return TechnicalFoundation(
        dns=lambda h, t: dns or DnsResult(True, ["1.2.3.4"], records={"A": ["1.2.3.4"]}),
        tls=lambda h, t: tls or TlsResult(True, "TLSv1.3", "Let's Encrypt", "f.com", "x", 80),
        fetcher=FakeFetcher(p or page(), insecure),
        renderer=(lambda u, t: render) if render is not None else None)


def by(r): return {f.check: f for f in r.findings}


# ---- static fallback path (no renderer) -------------------------------------------------
def test_all_pass_scores_20_with_static_fetch():
    r = module().run(ctx())
    assert r.score == 20 and not r.partial and r.max_points == 20
    assert r.evidence["content_source"] == "static HTML"
    assert "no JavaScript executed" in by(r)["page_renders"].detail
    assert r.evidence["dns"]["records"] == {"A": ["1.2.3.4"]}


def test_dns_failure_is_a_real_zero_not_partial():
    r = module(dns=DnsResult(False, [], "Name or service not known")).run(ctx())
    assert r.score == 0 and not r.partial
    assert "skipped" in by(r)["page_renders"].detail


def test_dns_transient_is_unverified_and_partial():
    r = module(dns=DnsResult(False, [], "Temporary failure", transient=True)).run(ctx())
    assert r.partial and all(f.status == "unverified" for f in r.findings)


def test_bad_certificate_still_scores_the_page_but_fails_render():
    m = module(tls=TlsResult(False, error="certificate verification failed: expired"),
               p=Page(False, error="certificate verification failed: expired"), insecure=page())
    r = m.run(ctx())
    assert m.fetcher.calls == [True, False]                # retried with checks off
    assert by(r)["tls_certificate_valid"].points == 0
    assert by(r)["page_renders"].points == 0 and "warn" in by(r)["page_renders"].detail
    assert by(r)["title_canonical_viewport"].points == 4   # content still inspected
    assert r.score == 4 + 4 + 4 and not r.partial


def test_expiring_certificate_is_noted_but_passes():
    r = module(tls=TlsResult(True, "TLSv1.3", "CA", "f.com", "x", 9)).run(ctx())
    assert by(r)["tls_certificate_valid"].points == 4 and "renew soon" in by(r)["tls_certificate_valid"].detail


def test_http_404_fails_render_and_content_checks():
    r = module(p=Page(False, 404, error="HTTP 404")).run(ctx())
    assert by(r)["page_renders"].points == 0 and by(r)["title_canonical_viewport"].points == 0
    assert not r.partial


def test_http_403_is_unverified_not_a_failure():
    r = module(p=Page(False, 403, error="HTTP 403 (server refused the probe; possible bot protection)",
                      transient=True)).run(ctx())
    assert r.partial and by(r)["page_renders"].status == "unverified"
    assert "bot protection" in by(r)["page_renders"].detail


def test_server_unreachable_is_unverified():
    r = module(p=Page(False, error="timed out", transient=True)).run(ctx())
    assert r.partial and by(r)["page_renders"].status == "unverified"


def test_partial_credit_for_missing_meta():
    html = "<html><head><title>Only title</title></head><body>" + "text " * 80 + "</body></html>"
    r = module(p=page(html)).run(ctx())
    f = by(r)["title_canonical_viewport"]
    assert f.points == round(4 / 3, 2) and "canonical" in f.detail and "viewport" in f.detail
    assert by(r)["structured_data_jsonld"].points == 0


def test_invalid_jsonld_scores_zero_with_reason():
    html = GOOD.replace('{"@type":"FinancialService"}', "{broken")
    assert "not valid JSON" in by(module(p=page(html)).run(ctx()))["structured_data_jsonld"].detail


def test_js_heavy_static_page_is_called_out():
    r = module(p=page(SHELL)).run(ctx())
    assert r.evidence["js_heavy_suspected"] and "JavaScript-built" in by(r)["page_renders"].detail


# ---- headless render path ---------------------------------------------------------------
def test_render_fixes_what_static_fetch_cannot_see():
    r = module(p=page(SHELL), render=rendered(GOOD)).run(ctx())     # SPA: DOM built in the browser
    assert r.score == 20 and r.evidence["content_source"] == "rendered DOM"
    assert "no JavaScript errors" in by(r)["page_renders"].detail


def test_javascript_errors_cost_half_the_render_check():
    r = module(render=rendered(js_errors=["TypeError: x is undefined"])).run(ctx())
    f = by(r)["page_renders"]
    assert f.points == 2 and "TypeError" in f.detail and not f.passed


def test_render_http_error_scores_zero():
    r = module(render=rendered(status=500)).run(ctx())
    assert by(r)["page_renders"].points == 0 and "HTTP 500" in by(r)["page_renders"].detail


def test_render_timeout_is_unverified_and_falls_back_to_static_content():
    r = module(render=Rendered(False, error="timed out", transient=True)).run(ctx())
    assert by(r)["page_renders"].status == "unverified" and r.partial
    assert by(r)["title_canonical_viewport"].points == 4          # static content still scored
    assert r.evidence["content_source"] == "static HTML"


def test_no_browser_falls_back_to_static_without_penalty():
    r = module(render=Rendered(False, error="browser not available", unavailable=True)).run(ctx())
    assert r.score == 20 and not r.partial and "static fetch only" in by(r)["page_renders"].detail
    assert r.evidence["render"]["available"] is False


def test_renderer_crash_never_sinks_the_group():
    def boom(u, t): raise RuntimeError("chromium died")
    m = module(); m.renderer = boom
    r = m.run(ctx())
    assert r.score == 20 and r.evidence["render"]["available"] is False


def test_all_probes_run_even_though_they_are_independent():
    seen = []
    m = TechnicalFoundation(dns=lambda h, t: (seen.append("dns"), DnsResult(True, ["1.1.1.1"]))[1],
                            tls=lambda h, t: (seen.append("tls"), TlsResult(True, "TLSv1.3", "CA", "x", "", 90))[1],
                            fetcher=FakeFetcher(page()),
                            renderer=lambda u, t: (seen.append("render"), rendered())[1])
    m.run(ctx())
    assert sorted(seen) == ["dns", "render", "tls"]


def test_make_module_registers_in_active_modules():
    before = len(ACTIVE_MODULES)
    m = make_module(renderer=None)
    assert ACTIVE_MODULES[-1] is m and len(ACTIVE_MODULES) == before + 1
    ACTIVE_MODULES.remove(m)


# ---- real network pieces, local only ----------------------------------------------------
def test_name_not_found_is_believed_only_when_the_control_lookup_works(monkeypatch):
    import socket
    from advisorgro.engine.shield.mvp.g01_technical import probes
    def lookup(name, *a, **k):
        if name == "example.com":
            return [(0, 0, 0, "", ("93.184.216.34", 443))]
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    monkeypatch.setattr(probes.socket, "getaddrinfo", lookup)
    r = probes.resolve("missing-firm.com")
    assert not r.ok and not r.transient                       # DNS works, the name is genuinely absent

    def dead(name, *a, **k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    monkeypatch.setattr(probes.socket, "getaddrinfo", dead)
    r = probes.resolve("missing-firm.com")
    assert not r.ok and r.transient and "unavailable from this machine" in r.error
    r = probes.resolve("example.com")                          # the control name itself failing
    assert not r.ok and r.transient


def test_real_dns_probe():
    assert resolve("localhost").ok
    assert not resolve("no-such-host.invalid").ok


def test_real_render_adapter_degrades_when_no_browser_or_runs_when_there_is_one():
    r = render_page("http://127.0.0.1:9/", timeout=3)
    assert isinstance(r, Rendered)
    assert r.unavailable or not r.ok          # nothing listens on port 9, so it must not report success


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl not installed")
def test_real_tls_probe_against_local_self_signed_server(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(cert), "-days", "2", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers()
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain(cert, key)
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        bad = tls_probe("localhost", 3, srv.server_port)
        assert not bad.ok and "verification failed" in bad.error and not bad.transient
        trust = ssl.create_default_context(cafile=str(cert))
        trust.verify_flags &= ~ssl.VERIFY_X509_STRICT
        good = tls_probe("localhost", 3, srv.server_port, trust)
        assert good.ok and good.protocol.startswith("TLS") and good.days_left in (1, 2)
    finally:
        srv.shutdown()
