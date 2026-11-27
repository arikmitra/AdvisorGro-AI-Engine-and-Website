from alembic import context
from sqlalchemy import create_engine

from advisorgro.core.config import get_settings
from advisorgro.db import models  # noqa: F401
from advisorgro.db.base import Base

target = Base.metadata


def run() -> None:
    url = get_settings().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    with create_engine(url).connect() as conn:
        context.configure(connection=conn, target_metadata=target)
        with context.begin_transaction():
            context.run_migrations()


run()
