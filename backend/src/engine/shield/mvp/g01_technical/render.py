"""Headless render for Group 01 (Playwright, optional).

If Playwright or its browser is missing, render_page returns unavailable=True and
Group 01 falls back to a static fetch and says so. Verify on your machine with:

    python -m g01_technical.render https://example.com
"""
from __future__ import annotations

import importlib.util
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Rendered:
    ok: bool
    status: Optional[int] = None
    final_url: str = ""
    html: str = ""
    js_errors: list[str] = field(default_factory=list)       # uncaught exceptions on the page
    failed_requests: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    error: str = ""
    transient: bool = False
    unavailable: bool = False        # no browser here: caller should fall back, not fail


_TRANSIENT = ("TIMED_OUT", "INTERNET_DISCONNECTED", "NETWORK_CHANGED", "CONNECTION_RESET")


def render_page(url: str, timeout: float = 20.0) -> Rendered:
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Rendered(False, error="playwright is not installed", unavailable=True)
    js: list[str] = []
    failed: list[str] = []
    console: list[str] = []
    t0 = time.monotonic()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except PWError as e:
                return Rendered(False, unavailable=True,
                                error=f"browser not available: {str(e).splitlines()[0]}")
            try:
                page = browser.new_page()
                page.on("pageerror", lambda e: js.append(str(e)[:200]))
                page.on("console", lambda m: console.append(m.text[:200]) if m.type == "error" else None)
                page.on("requestfailed", lambda r: failed.append(r.url[:200]))
                resp = page.goto(url, wait_until="load", timeout=timeout * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(5000, timeout * 500))
                except PWTimeout:
                    pass                      # long-polling sites never go idle; load was enough
                return Rendered(True, resp.status if resp else None, page.url, page.content(),
                                js, failed, console, round(time.monotonic() - t0, 3))
            finally:
                browser.close()
    except PWTimeout:
        return Rendered(False, error="timed out", transient=True)
    except PWError as e:
        msg = str(e).splitlines()[0]
        return Rendered(False, error=msg, transient=any(k in msg for k in _TRANSIENT))


def default_renderer() -> Optional[Callable[..., Rendered]]:
    """The Playwright renderer if the package is importable, else None (static only)."""
    return render_page if importlib.util.find_spec("playwright") else None


if __name__ == "__main__":
    r = render_page(sys.argv[1] if len(sys.argv) > 1 else "https://example.com")
    print(f"ok={r.ok} status={r.status} unavailable={r.unavailable} error={r.error!r}")
    print(f"html bytes={len(r.html)} js_errors={len(r.js_errors)} "
          f"failed_requests={len(r.failed_requests)} console_errors={len(r.console_errors)}")
