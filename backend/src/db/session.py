"""Sessions carry the tenant. Three layers: ORM criteria (here), Postgres RLS (migration), policy function."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, false
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from ..core.errors import ImmutableRecord, TenantContextMissing
from .base import Base, InsertOnly, TenantMixin


def make_engine(url: str) -> Engine:
    kw: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("://"):
            from sqlalchemy.pool import StaticPool
            kw["poolclass"] = StaticPool
    return create_engine(url, **kw)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, autoflush=True)


@event.listens_for(Session, "do_orm_execute")
def _tenant_filter(state) -> None:
    info = state.session.info
    # insert-only tables: no bulk UPDATE/DELETE either
    if state.is_update or state.is_delete:
        for m in state.all_mappers:
            if issubclass(m.class_, InsertOnly):
                raise ImmutableRecord(f"{m.class_.__name__} is insert-only")
    if info.get("system") or not (state.is_select or state.is_update or state.is_delete):
        return
    tid = info.get("tenant_id")
    crit = (lambda cls: cls.tenant_id == tid) if tid else (lambda cls: false())  # deny by default
    state.statement = state.statement.options(
        with_loader_criteria(TenantMixin, crit, include_aliases=True, track_closure_variables=False))


@event.listens_for(Session, "before_flush")
def _guard_writes(session: Session, ctx, instances) -> None:
    info = session.info
    for obj in list(session.new):
        if isinstance(obj, TenantMixin):
            tid = info.get("tenant_id")
            if info.get("system"):
                if not getattr(obj, "tenant_id", None):
                    raise TenantContextMissing(f"{type(obj).__name__} needs tenant_id in a system session")
                continue
            if not tid:
                raise TenantContextMissing("no tenant set on session")
            if getattr(obj, "tenant_id", None) in (None, ""):
                obj.tenant_id = tid
            elif obj.tenant_id != tid:
                raise TenantContextMissing("cross-tenant write refused")
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, InsertOnly) and (obj in session.deleted or session.is_modified(obj)):
            raise ImmutableRecord(f"{type(obj).__name__} is insert-only")


@event.listens_for(Session, "after_begin")
def _set_pg_tenant(session: Session, transaction, connection) -> None:
    """Postgres RLS: policies read current_setting('app.tenant_id'); local to the transaction."""
    if connection.dialect.name == "postgresql":
        tid = "" if session.info.get("system") else (session.info.get("tenant_id") or "")
        sysflag = "on" if session.info.get("system") else "off"
        connection.exec_driver_sql("SELECT set_config('app.tenant_id', %s, true), set_config('app.system', %s, true)",
                                   (tid, sysflag))


@contextmanager
def session_scope(sm: sessionmaker[Session], *, tenant_id: str | None = None, system: bool = False) -> Iterator[Session]:
    s = sm()
    s.info["tenant_id"] = tenant_id
    s.info["system"] = system
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def create_all(engine: Engine) -> None:
    Base.metadata.create_all(engine)
