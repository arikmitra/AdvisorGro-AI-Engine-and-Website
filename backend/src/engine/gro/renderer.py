"""Stage 5 renderer: deterministic. Builds the landing page, ad previews and email set and one content hash over
the *normalised* text, links and disclosures, so the Publisher can fetch the live page, normalise it the same way
and compare. Disclosures come from approved text, injected by code, never by the model."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from jinja2 import Environment, select_autoescape

from ...core.hashing import content_hash, normalise_html, normalise_text
from .schemas import CreativeSet

_env = Environment(autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
CONSENT = {"text_id": "lead-consent", "version": "1",
           "text": "I agree to be contacted by this firm by email, phone or text about my inquiry. Consent is not a condition of any service. Message and data rates may apply. Reply STOP to opt out."}


@dataclass
class Rendered:
    parts: dict
    page_html: str
    content_hash: str


def _page_html(c: CreativeSet, *, firm: dict, offer, disclosures: list[dict], campaign_id: str, version: int, api_base: str, links: list[dict], consent: dict) -> str:
    tpl = _env.from_string(resources.files("advisorgro.engine.gro").joinpath("templates/landing_page.html.j2").read_text())
    return tpl.render(firm=firm, offer=offer, sections=c.page.sections, disclosures=disclosures, links=links, campaign_id=campaign_id, version=version,
                      api_base=api_base, consent=consent, form_heading="Book a no-obligation conversation",
                      cta=next((s.cta for s in c.page.sections if s.cta), "Book a call"))


def norm_google(g: dict | None) -> dict | None:
    return None if g is None else {"headlines": [normalise_text(x) for x in g["headlines"]], "descriptions": [normalise_text(x) for x in g["descriptions"]]}


def norm_meta(m: dict | None) -> dict | None:
    return None if m is None else {"variants": [{"primary_text": normalise_text(v["primary_text"]), "headline": normalise_text(v["headline"]),
                                                 "cta": normalise_text(v.get("cta", "Learn more"))} for v in m["variants"]]}


def parts_for(c: CreativeSet, page_html: str, disclosures: list[dict], consent: dict) -> dict:
    return {
        "google": norm_google(c.google.model_dump()) if c.google else None,
        "meta": norm_meta(c.meta.model_dump()) if c.meta else None,
        "page": normalise_html(page_html),
        "emails": [{"subject": normalise_text(e.subject), "body": normalise_text(e.body)} for e in c.followup.emails],
        "sms": [normalise_text(s) for s in c.followup.sms],
        "disclosures": [normalise_text(d["text"]) for d in sorted(disclosures, key=lambda d: d["id"])],
        "consent": {"text_id": consent["text_id"], "version": consent["version"], "text": normalise_text(consent["text"])},
    }


def render(c: CreativeSet, *, firm: dict, offer, disclosures: list[dict], campaign_id: str, version: int, api_base: str = "",
           links: list[dict] | None = None, consent: dict | None = None) -> Rendered:
    consent = consent or CONSENT
    links = links if links is not None else firm.get("links", [])
    html = _page_html(c, firm=firm, offer=offer, disclosures=disclosures, campaign_id=campaign_id, version=version, api_base=api_base, links=links, consent=consent)
    parts = parts_for(c, html, disclosures, consent)
    return Rendered(parts, html, content_hash(parts))


def hash_live_page(live_html: str, expected_parts: dict) -> str:
    """Hash of what is actually live: expected parts with the page replaced by the normalised live HTML."""
    return content_hash({**expected_parts, "page": normalise_html(live_html)})
