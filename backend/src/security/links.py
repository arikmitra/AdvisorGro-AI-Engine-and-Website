"""Short-lived signed download links for object storage; report tokens are separate (hashed in the DB)."""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from ..core.errors import Unauthorized


def sign_link(secret: str, key: str, ttl_s: int = 300, *, now: int | None = None) -> str:
    exp = (now or int(time.time())) + ttl_s
    sig = hmac.new(secret.encode(), f"{key}|{exp}".encode(), hashlib.sha256).hexdigest()
    return f"/v1/files/{quote(key, safe='/')}?exp={exp}&sig={sig}"


def verify_link(secret: str, key: str, exp: int, sig: str, *, now: int | None = None) -> None:
    if (now or int(time.time())) > exp:
        raise Unauthorized("link expired")
    good = hmac.new(secret.encode(), f"{key}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, sig):
        raise Unauthorized("bad link")
