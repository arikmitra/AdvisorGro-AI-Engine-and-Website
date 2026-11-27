"""Group 01: technical foundation. Five checks x 4 pts. Deterministic by design
(per the check-group slide) so there is no planner, reviewer or model call.

DNS, TLS and the headless render run in parallel and feed one findings builder.
The render uses Playwright when available; otherwise it falls back to a static
fetch and the evidence says which was used.

Fixes in this version:
- a TLS probe that timed out (transient) is no longer blamed on the firm's certificate;
- a hung probe can no longer block the group: results are awaited with a timeout and the
  pool is abandoned rather than joined.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..kernel import Finding, GroupResult, RunContext, register
from ..web import Page, SharedFetcher, parse_page
from .probes import DnsResult, TlsResult, resolve, tls_probe
from .render import Rendered, default_renderer

CHECKS = ("domain_resolves", "tls_certificate_valid", "page_renders",
          "title_canonical_viewport", "structured_data_jsonld")


@dataclass
class TechnicalFoundation:
    dns: Callable = resolve
    tls: Callable = tls_probe
    fetcher: SharedFetcher = field(default_factory=SharedFetcher)
    renderer: Optional[Callable[..., Rendered]] = field(default_factory=default_renderer)
    group_id = "g01_technical"
    max_points = 20.0

    def _render(self, url: str, timeout: float) -> Optional[Rendered]:
        if not self.renderer:
            return None
        try:
            return self.renderer(url, timeout)
        except Exception as e:                              # noqa: BLE001 - never sink the group
            return Rendered(False, error=f"renderer crashed: {type(e).__name__}: {e}", unavailable=True)

    def run(self, ctx: RunContext) -> GroupResult:
        host, url = ctx.domain, f"https://{ctx.domain}/"
        cap = lambda n: max(0.5, min(n, ctx.budget.remaining_seconds))   # noqa: E731
        findings: list[Finding] = []
        ev: dict = {"host": host}

        def add(check, pts, detail, unverified=False):
            findings.append(Finding(check, pts >= 4.0, round(pts, 2), 4.0, detail,
                                    "unverified" if unverified else "ok"))

        def done() -> GroupResult:
            return GroupResult(self.group_id, round(sum(f.points for f in findings), 2),
                               self.max_points, findings, ev,
                               partial=any(f.status == "unverified" for f in findings))

        # the probes run side by side; a probe that never returns is treated as timed out
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            f_dns = pool.submit(self.dns, host, cap(5))
            f_tls = pool.submit(self.tls, host, cap(5))
            f_page = pool.submit(self.fetcher.get, url, cap(8), True)
            f_rend = pool.submit(self._render, url, cap(20)) if self.renderer else None

            def got(fut, wait, fallback):
                try:
                    return fut.result(timeout=cap(wait))
                except FutureTimeout:
                    return fallback

            dns = got(f_dns, 8, DnsResult(False, [], "lookup timed out", True))
            tls = got(f_tls, 8, TlsResult(False, error="timed out", transient=True))
            page = got(f_page, 12, Page(False, error="timed out", transient=True))
            rend = got(f_rend, 25, Rendered(False, error="timed out", transient=True)) if f_rend else None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        # ---- 1. domain resolves
        ev["dns"] = {"ok": dns.ok, "addresses": dns.addresses, "error": dns.error,
                     "records": dns.records}
        if not dns.ok:
            add(CHECKS[0], 0, f"did not resolve: {dns.error}", dns.transient)
            for c in CHECKS[1:]:
                add(c, 0, "skipped: domain did not resolve", dns.transient)
            return done()
        add(CHECKS[0], 4, f"{len(dns.addresses)} address(es): {', '.join(dns.addresses[:3])}")

        # ---- 2. TLS certificate
        ev["tls"] = {"ok": tls.ok, "protocol": tls.protocol, "issuer": tls.issuer,
                     "subject": tls.subject, "not_after": tls.not_after,
                     "days_left": tls.days_left, "error": tls.error}
        if tls.ok:
            soon = " (renew soon)" if tls.days_left is not None and tls.days_left < 14 else ""
            add(CHECKS[1], 4, f"{tls.protocol}, issued by {tls.issuer or 'unknown'}, "
                              f"expires in {tls.days_left} days{soon}")
        else:
            add(CHECKS[1], 0, tls.error, tls.transient)

        if not page.ok and "certificate" in page.error:        # still inspect what visitors get
            page = self.fetcher.get(url, cap(8), verify=False)
        # a TLS probe that merely timed out says nothing about the certificate
        insecure = page.insecure or (not tls.ok and not tls.transient)
        use_render = rend is not None and not rend.unavailable and (tls.ok or tls.transient)
        ev["page"] = {"ok": page.ok, "status": page.status, "final_url": page.final_url,
                      "bytes": page.size, "elapsed_s": page.elapsed_s,
                      "insecure_fetch": insecure, "error": page.error}
        if rend is not None:
            ev["render"] = {"available": not rend.unavailable, "ok": rend.ok, "status": rend.status,
                            "js_errors": rend.js_errors[:5], "console_errors": len(rend.console_errors),
                            "failed_requests": len(rend.failed_requests), "error": rend.error}

        # ---- 3. page renders without error
        html, source = page.html, "static HTML"
        if use_render and rend.ok:
            ok2xx = rend.status is not None and 200 <= rend.status < 300
            if not ok2xx:
                add(CHECKS[2], 0, f"headless render got HTTP {rend.status}")
            elif rend.js_errors:
                add(CHECKS[2], 2, f"loaded, but {len(rend.js_errors)} uncaught JavaScript error(s); "
                                  f"first: {rend.js_errors[0][:120]}")
            else:
                add(CHECKS[2], 4, f"headless render HTTP {rend.status} in {rend.elapsed_s}s, "
                                  f"no JavaScript errors")
            if ok2xx:
                html, source = rend.html, "rendered DOM"
        elif use_render:                                        # the browser could not load it
            add(CHECKS[2], 0, f"headless render failed: {rend.error}", rend.transient)
        elif insecure and page.ok:
            add(CHECKS[2], 0, "page loads only with certificate checks off; browsers would warn")
        elif not page.ok:
            add(CHECKS[2], 0, page.error or "page did not load", page.transient)
        elif page.size == 0 or "html" not in page.content_type.lower():
            add(CHECKS[2], 0, f"unexpected response: {page.content_type or 'no content type'}, "
                              f"{page.size} bytes")
        else:
            meta0 = parse_page(page.html)
            note = "; static fetch only, no JavaScript executed"
            if len(meta0.text) < 200 and meta0.script_count >= 3:
                note += (", and the page looks JavaScript-built so later checks may be "
                         "understated")
                ev["js_heavy_suspected"] = True
            add(CHECKS[2], 4, f"HTTP {page.status}, {page.size} bytes in {page.elapsed_s}s{note}")
        ev["content_source"] = source

        # ---- 4 and 5 read the rendered DOM when there is one, else the static HTML
        meta = parse_page(html) if html else None
        if meta is None:
            why = (rend.error if use_render and not rend.ok else page.error) or "empty page body"
            transient = rend.transient if use_render and not rend.ok else page.transient
            add(CHECKS[3], 0, f"no page to inspect: {why}", transient)
            add(CHECKS[4], 0, f"no page to inspect: {why}", transient)
            return done()

        have = {"title": bool(meta.title), "canonical": bool(meta.canonical),
                "viewport": bool(meta.viewport)}
        missing = [k for k, v in have.items() if not v]
        add(CHECKS[3], 4 * sum(have.values()) / 3,
            f"all three present ({source})" if not missing else
            f"missing: {', '.join(missing)} ({source})")
        ev["meta"] = {"title": meta.title[:120], "canonical": meta.canonical,
                      "viewport": meta.viewport}

        types = meta.json_ld_types
        ev["json_ld"] = {"valid_blocks": len(meta.json_ld), "invalid_blocks": meta.json_ld_invalid,
                         "types": types}
        if meta.json_ld:
            add(CHECKS[4], 4, f"{len(meta.json_ld)} valid block(s); types: {', '.join(types) or 'none'}")
        elif meta.json_ld_invalid:
            add(CHECKS[4], 0, f"{meta.json_ld_invalid} JSON-LD block(s) found but not valid JSON")
        else:
            add(CHECKS[4], 0, "no JSON-LD found")
        return done()


def make_module(**kw) -> TechnicalFoundation:
    """Create and register in ACTIVE_MODULES, as the check-group slide specifies."""
    return register(TechnicalFoundation(**kw))  # type: ignore[return-value]