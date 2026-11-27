"""Minimal fixtures for the vendored MVP engine tests."""
from advisorgro.engine.shield.mvp.web import Page, SharedFetcher

HTML = "<html><head><title>Merrow Lane Wealth</title></head><body>Planning.</body></html>"


def fetcher():
    return SharedFetcher(fetch=lambda url, timeout, verify: Page(True, 200, url, "text/html", HTML, len(HTML)))
