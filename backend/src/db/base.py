from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import TypeDecorator

from ..core.ids import new_id


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, returns aware UTC. Works the same on SQLite (tests) and Postgres."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime refused; use core.clock.utcnow()")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON, datetime: UTCDateTime}


class TenantMixin:
    """Every row belongs to a firm (the tenant). Tenant comes from the verified token, never from the caller.
    Enforced in three places: ORM loader criteria (session.py), Postgres row-level security (migration 0001)
    and the policy function."""

    @declared_attr
    def tenant_id(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(String(36), index=True, nullable=False)


class InsertOnly:
    """Approvals, versions, alerts, access and activity logs are never updated or deleted."""


def pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)
