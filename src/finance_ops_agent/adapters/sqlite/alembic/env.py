"""Alembic environment. The engine is passed in via config.attributes["engine"]
(see migrations.py); render_as_batch is on because SQLite cannot alter tables
in place.
"""

from alembic import context
from sqlalchemy import Engine

from finance_ops_agent.adapters.sqlite.schema import Base

engine = context.config.attributes.get("engine")
if not isinstance(engine, Engine):
    raise RuntimeError("run migrations through finance_ops_agent.adapters.sqlite.migrations")

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
