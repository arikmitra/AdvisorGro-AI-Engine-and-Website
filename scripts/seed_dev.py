"""Seed a local database with one firm tenant, one user per role, approved facts/disclosures and a linked (fake) ad
account, then print a dev token per role. Development only: tokens are HS256 and refused in staging/prod.

    cd backend && python ../scripts/seed_dev.py
"""
from __future__ import annotations

import json
import pathlib

from advisorgro.container import build_container
from advisorgro.core.config import get_settings
from advisorgro.core.ids import new_id
from advisorgro.db.models import AdLink, Firm, Membership, Tenant, User
from advisorgro.db.session import session_scope
from advisorgro.engine.gro import knowledge
from advisorgro.security.tokens import mint_dev_token

s = get_settings()
assert s.env in ("dev", "test"), "seed is for development only"
c = build_container(s)
tenant = new_id()
users: dict[str, str] = {}
with session_scope(c.sm, system=True) as db:
    db.add(Tenant(id=tenant, name="Merrow Lane Wealth", plan="gro"))
    for role in ("firm_admin", "cco", "marketing", "advisor", "read_only"):
        u = User(email=f"{role}-{tenant[:6]}@merrowlane.example", name=role)
        db.add(u)
        db.flush()
        users[role] = u.id
        db.add(Membership(tenant_id=tenant, user_id=u.id, role=role))
    # a second customer who only ever used the free audit (plan "audit"): has no Shield subscription
    audit_tenant = new_id()
    db.add(Tenant(id=audit_tenant, name="Audit-only Advisors", plan="audit"))
    au = User(email=f"audit-admin-{audit_tenant[:6]}@auditonly.example", name="audit_admin")
    db.add(au)
    db.flush()
    users["audit_admin"] = au.id
    db.add(Membership(tenant_id=audit_tenant, user_id=au.id, role="firm_admin"))
    staff = User(email=f"reviewer-{tenant[:6]}@advisorgro.example", name="reviewer", is_staff=True, staff_role="reviewer")
    db.add(staff)
    db.flush()
    users["reviewer"] = staff.id
with session_scope(c.sm, tenant_id=tenant) as db:
    f = Firm(name="Merrow Lane Wealth", domain="merrowlane.com", area="Austin, Texas",
             profile={"links": [{"label": "Form CRS", "url": "https://merrowlane.com/crs.pdf"}, {"label": "Form ADV Part 2A", "url": "https://merrowlane.com/adv.pdf"}]})
    db.add(f)
    db.flush()
    knowledge.add(db, f.id, "fact", "Merrow Lane Wealth is a fee-only fiduciary investment adviser in Austin, Texas.")
    knowledge.add(db, f.id, "approved_language", "Investing involves risk, including loss of principal. Past performance is not a guarantee of future results.", key="disclosure.risk")
    knowledge.add(db, f.id, "approved_language", "Merrow Lane Wealth is an SEC-registered investment adviser. Registration does not imply a certain level of skill or training.", key="disclosure.sec")
    db.add(AdLink(firm_id=f.id, platform="google", status="linked", external_account_id="1234567890"))
    firm_id = f.id
with session_scope(c.sm, tenant_id=audit_tenant) as db:
    af = Firm(name="Audit-only Advisors", domain="auditonly.example", area="Denver, Colorado")
    db.add(af)
    db.flush()
    audit_firm_id = af.id
out = pathlib.Path(__file__).resolve().parent.parent / ".dev"
out.mkdir(exist_ok=True)
(out / "seed.json").write_text(json.dumps({"tenant": tenant, "firm_id": firm_id, "audit_only": {"tenant": audit_tenant, "firm_id": audit_firm_id}, "users": users, "database_url": s.database_url}, indent=2))
print(f"tenant {tenant}\nfirm   {firm_id}\nsaved  {out / 'seed.json'}  (mint fresh tokens with: python ../scripts/mint_token.py cco)\n")
for role, uid in users.items():
    kw = {"staff": True, "roles": [role]} if role == "reviewer" else {"tenant_id": audit_tenant, "roles": ["firm_admin"]} if role == "audit_admin" else {"tenant_id": tenant, "roles": [role]}
    print(f"--- {role}\n{mint_dev_token(s, sub=uid, **kw)}\n")
