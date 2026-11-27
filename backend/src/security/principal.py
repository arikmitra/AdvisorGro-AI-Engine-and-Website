from __future__ import annotations

from dataclasses import dataclass

FIRM_ROLES = {"firm_admin", "cco", "marketing", "advisor", "read_only"}
STAFF_ROLES = {"support", "reviewer", "engineer", "platform_admin", "security"}


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str | None
    roles: frozenset[str]
    firm_id: str | None = None
    is_staff: bool = False
    mfa: bool = False
    auth_time: int = 0  # epoch seconds of the last interactive authentication
    email: str = ""
    ticket: str | None = None  # support ticket id (support access needs ticket + firm consent)
    consent: bool = False

    def has(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))
