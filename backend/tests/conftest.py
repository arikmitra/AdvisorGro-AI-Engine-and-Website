
import pytest

from advisorgro.container import build_container
from advisorgro.core.config import Settings
from advisorgro.core.ids import new_id
from advisorgro.db.models import Firm, Membership, Tenant, User
from advisorgro.db.session import session_scope
from advisorgro.security.principal import Principal


@pytest.fixture
def c(tmp_path):
    s = Settings(env="test", database_url=f"sqlite+pysqlite:///{tmp_path}/t.db", object_store_dir=str(tmp_path / "obj"), use_fakes=True,
                 shield_budget_seconds=6, report_page_url="https://site.test/shield")
    return build_container(s)


class World:
    """A firm tenant with a CCO, a marketer, an admin, and a firm with approved facts and disclosures."""

    def __init__(self, c):
        self.c = c
        self.tenant = new_id()
        with session_scope(c.sm, system=True) as s:
            s.add(Tenant(id=self.tenant, name="Merrow Lane Wealth", plan="gro"))
        self.users = {}
        with session_scope(c.sm, system=True) as s:
            for role in ("cco", "marketing", "firm_admin", "advisor", "read_only"):
                u = User(email=f"{role}-{self.tenant[:6]}@merrow.test", name=role)
                s.add(u)
                s.flush()
                self.users[role] = u.id
                s.add(Membership(tenant_id=self.tenant, user_id=u.id, role=role))
        with session_scope(c.sm, tenant_id=self.tenant) as s:
            f = Firm(name="Merrow Lane Wealth", domain="merrowlane.com", area="Austin, Texas",
                     profile={"links": [{"label": "Form CRS", "url": "https://merrowlane.com/crs.pdf"}, {"label": "Form ADV Part 2A", "url": "https://merrowlane.com/adv.pdf"}]})
            s.add(f)
            s.flush()
            self.firm_id = f.id
            from advisorgro.engine.gro import knowledge
            knowledge.add(s, f.id, "fact", "Merrow Lane Wealth is a fee-only fiduciary investment adviser in Austin, Texas.")
            knowledge.add(s, f.id, "approved_language", "Investing involves risk, including loss of principal. Past performance is not a guarantee of future results.", key="disclosure.risk")
            knowledge.add(s, f.id, "approved_language", "Merrow Lane Wealth is an SEC-registered investment adviser. Registration does not imply a certain level of skill or training.", key="disclosure.sec")

    def principal(self, role: str, *, mfa=True, age=0, **kw) -> Principal:
        import time
        return Principal(user_id=self.users[role], tenant_id=self.tenant, roles=frozenset({role}), mfa=mfa, auth_time=int(time.time()) - age, email=f"{role}@merrow.test", **kw)


@pytest.fixture
def world(c):
    return World(c)
