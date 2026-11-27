"""Verify the hash chain of the activity log (tamper-evidence). Prints OK or the first broken row.

    cd backend && python ../scripts/verify_activity_chain.py
"""
import sys

from sqlalchemy import select

from advisorgro.container import build_container
from advisorgro.core.config import get_settings
from advisorgro.db.models import ActivityLog
from advisorgro.db.session import session_scope
from advisorgro.security.activity import verify_chain

c = build_container(get_settings())
with session_scope(c.sm, system=True) as s:
    n = len(s.execute(select(ActivityLog.id)).all())
    ok = verify_chain(s)
print(f"{n} activity rows: chain {'OK' if ok else 'BROKEN (a row was edited or removed)'}")
sys.exit(0 if ok else 1)
