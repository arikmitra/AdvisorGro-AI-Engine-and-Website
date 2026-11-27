"""Public audit form validation (the MVP contract: field names, messages and error keys are kept)."""
from __future__ import annotations

import re
from collections.abc import Callable

from .mvp.guard import check_public_site
from .mvp.intake import domain_of, normalize_url

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean(v, n: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else "")).strip()[:n]


def validate_audit(p: dict, site_check: Callable[[str], str] = check_public_site, *, strict_names: bool = False) -> tuple[dict, dict]:
    """(clean fields, errors by field). The new site makes firm name and contact name optional; set strict_names for the MVP rules."""
    c = {"firm_name": clean(p.get("firm_name") or p.get("firm"), 120), "area": clean(p.get("area"), 120), "crd": clean(p.get("crd"), 12),
         "contact_name": clean(p.get("contact_name"), 100), "email": clean(p.get("email"), 254).lower(),
         "aum": clean(p.get("aum"), 60), "goal": clean(p.get("goal"), 60)}
    errors: dict[str, str] = {}
    req = [("area", "Please enter the city or area you serve.")]
    if strict_names:
        req += [("firm_name", "Please enter your firm name."), ("contact_name", "Please enter your name.")]
    for k, msg in req:
        if not c[k]:
            errors[k] = msg
    if not EMAIL_RE.match(c["email"]):
        errors["email"] = "Please enter a valid work email."
    if c["crd"] and not re.fullmatch(r"\d{3,10}", c["crd"]):
        errors["crd"] = "A CRD number is digits only. Leave it blank if you are unsure."
    try:
        url = normalize_url(clean(p.get("website"), 300))
        c["url"], c["domain"] = url, domain_of(url)
        bad = site_check(c["domain"])
        if bad:
            errors["website"] = bad
    except ValueError:
        errors["website"] = "Please enter your firm\u2019s website address, for example yourfirm.com."
    return c, errors


PUBLIC_MESSAGE = {
    "queued": "Thanks. Your audit is being prepared. If anything needs a person to check it first, we will say so here.",
    "running": "We are checking your firm now.",
    "pending_review": "Your audit is done and is being reviewed by a person. It will reach your inbox within one business day.",
    "sent": "Your audit has been sent. Please check your inbox.",
    "released": "Your audit is ready. We are emailing it now.",
    "failed": "We could not finish your audit automatically. A member of our team will follow up by email.",
}
