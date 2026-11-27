"""initial schema (expand-only; later migrations follow expand/contract so deploys are reversible)

Revision ID: 0001
Revises:
"""
from alembic import op

from advisorgro.db import models  # noqa: F401
from advisorgro.db.base import Base

revision = "0001"
down_revision = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
