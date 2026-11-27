import sys, pathlib, threading, http.server, socket
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from advisorgro.engine.shield.mvp.web import Page, SharedFetcher, fetch_page, parse_page

HTML = """<html><head><title> Merrow Lane </title>
<link rel="canonical" href="https://merrowlanewealth.com/">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="application/ld+json">{"@type":"FinancialService","name":"Merrow","address":{"@type":"PostalAddress"}}</script>
<script type="application/ld+json">{not json</script>
<style>.x{color:red}</style><script>var hidden = 1;</script></head>
<body><p>We plan   retirements.</p><noscript>enable js</noscript></body></html>"""


def serve(status=200, body=HTML):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_parse_page():
    m = parse_page(HTML)
    assert m.title == "Merrow Lane"
    assert m.canonical == "https://merrowlanewealth.com/"
    assert m.viewport.startswith("width=device-width")
    assert len(m.json_ld) == 1 and m.json_ld_invalid == 1
    assert m.json_ld_types == ["FinancialService", "PostalAddress"]
    assert m.text == "We plan retirements."          # no script, style or noscript text
    assert m.script_count == 3


def test_fetch_ok_and_404():
    srv = serve()
    try:
        p = fetch_page(f"http://127.0.0.1:{srv.server_port}/")
        assert p.ok and p.status == 200 and "html" in p.content_type and "Merrow" in p.html
    finally:
        srv.shutdown()
    srv = serve(403)
    try:
        p = fetch_page(f"http://127.0.0.1:{srv.server_port}/")
        assert not p.ok and p.status == 403 and p.transient and "bot protection" in p.error
    finally:
        srv.shutdown()
    srv = serve(404)
    try:
        p = fetch_page(f"http://127.0.0.1:{srv.server_port}/")
        assert not p.ok and p.status == 404 and not p.transient
    finally:
        srv.shutdown()


def test_connection_refused_is_not_transient():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = fetch_page(f"http://127.0.0.1:{port}/", timeout=2)
    assert not p.ok and not p.transient and "refused" in p.error


def test_shared_fetcher_caches_but_not_transient_failures():
    calls = []
    results = iter([Page(False, error="timed out", transient=True),
                    Page(True, 200, html="x"), Page(True, 200, html="y")])
    f = SharedFetcher(fetch=lambda url, timeout, verify: (calls.append(url), next(results))[1])
    assert not f.get("https://a.com/").ok          # transient: not cached
    assert f.get("https://a.com/").html == "x"     # fetched again
    assert f.get("https://a.com/").html == "x"     # now cached
    assert len(calls) == 2
    assert f.get("https://a.com/", verify=False).html == "y"   # different key
