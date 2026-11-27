"""Bearer-token verification (OIDC from Keycloak in prod; HS256 in dev/test) and step-up checks."""
from __future__ import annotations

import time
from typing import Any

import jwt

from ..core.config import Settings
from ..core.errors import StepUpRequired, Unauthorized
from .principal import Principal


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.s = settings
        self._jwks = jwt.PyJWKClient(settings.jwks_url) if settings.jwt_mode == "jwks" and settings.jwks_url else None

    def verify(self, token: str) -> Principal:
        try:
            if self.s.jwt_mode == "jwks":
                if not self._jwks:
                    raise Unauthorized("jwks not configured")
                key = self._jwks.get_signing_key_from_jwt(token).key
                claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=self.s.jwt_audience,
                                    issuer=self.s.jwt_issuer, options={"require": ["exp", "iat", "sub"]})
            else:
                claims = jwt.decode(token, self.s.jwt_secret, algorithms=["HS256"], audience=self.s.jwt_audience,
                                    issuer=self.s.jwt_issuer, options={"require": ["exp", "iat", "sub"]})
        except jwt.PyJWTError as e:
            raise Unauthorized("invalid token") from e
        return principal_from_claims(claims)


def principal_from_claims(c: dict[str, Any]) -> Principal:
    """The tenant comes from the signed token, never from a field the caller sends."""
    roles = frozenset(c.get("roles") or c.get("realm_access", {}).get("roles", []))
    amr = c.get("amr") or []
    return Principal(
        user_id=c["sub"], tenant_id=c.get("tenant_id"), roles=roles, firm_id=c.get("firm_id"),
        is_staff=bool(c.get("staff")), mfa=("mfa" in amr or "otp" in amr or bool(c.get("mfa"))),
        auth_time=int(c.get("auth_time", c.get("iat", 0))), email=c.get("email", ""),
        ticket=c.get("ticket"), consent=bool(c.get("firm_consent")),
    )


def require_step_up(p: Principal, max_age_s: int, *, now: int | None = None) -> None:
    """Approving, exporting leads, connecting an ad account and changing roles need a fresh MFA sign-in."""
    now = now or int(time.time())
    if not p.mfa or now - p.auth_time > max_age_s:
        raise StepUpRequired("re-authenticate with MFA to continue")


def mint_dev_token(settings: Settings, **claims: Any) -> str:
    """Tests and local dev only (jwt_mode=hs256)."""
    now = int(time.time())
    base = {"iss": settings.jwt_issuer, "aud": settings.jwt_audience, "iat": now, "auth_time": now,
            "exp": now + 3600, "amr": ["pwd", "mfa"]}
    return jwt.encode({**base, **claims}, settings.jwt_secret, algorithm="HS256")
