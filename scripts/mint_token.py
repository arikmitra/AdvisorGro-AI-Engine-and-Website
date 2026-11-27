"""Mint a FRESH dev token for a seeded role (development only). Approvals, ad-account linking and review releases need
a sign-in within the last 5 minutes (step-up), so mint a new token right before those actions.

    cd backend
    python ../scripts/mint_token.py cco                # prints the token
    python ../scripts/mint_token.py cco --stale        # signed in 2 hours ago: use to see the step-up refusal
    python ../scripts/mint_token.py marketing --no-mfa # no second factor
Roles: firm_admin cco marketing advisor read_only reviewer audit_admin
(audit_admin = firm admin of a second firm on the free-audit-only plan: no Shield subscription)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from advisorgro.core.config import get_settings
from advisorgro.security.tokens import mint_dev_token

ap = argparse.ArgumentParser()
ap.add_argument("role")
ap.add_argument("--stale", action="store_true")
ap.add_argument("--no-mfa", action="store_true")
ap.add_argument("--other-tenant", action="store_true", help="same user id but a different tenant claim (isolation check)")
a = ap.parse_args()
seed_file = pathlib.Path(__file__).resolve().parent.parent / ".dev" / "seed.json"
if not seed_file.exists():
    sys.exit("No .dev/seed.json: run `python ../scripts/seed_dev.py` first (from the backend folder).")
seed = json.loads(seed_file.read_text())
s = get_settings()
assert s.env in ("dev", "test"), "dev tokens are for development only"
now = int(time.time())
claims = {"sub": seed["users"][a.role], "roles": [a.role], "auth_time": now - (7200 if a.stale else 0), "exp": now + 8 * 3600,
          "amr": ["pwd"] if a.no_mfa else ["pwd", "mfa"]}
if a.role == "reviewer":
    claims["staff"] = True
elif a.role == "audit_admin":
    claims.update(tenant_id=seed["audit_only"]["tenant"], roles=["firm_admin"])
else:
    claims["tenant_id"] = "00000000-0000-0000-0000-0000000000ff" if a.other_tenant else seed["tenant"]
print(mint_dev_token(s, **claims))
