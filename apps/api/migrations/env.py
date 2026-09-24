"""Alembic environment.

The database URL comes from the application settings rather than alembic.ini,
so there is one source of truth and no credential in a tracked file. Set
`PICKLEBALL_DATABASE_URL` (or put it in `.env`) to migrate a different database.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from pickleball_api.config import get_settings
from pickleball_api.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Only when the caller has not chosen one. A test, or `alembic -x`, may point
# the migration at a different database, and silently overriding that would
# migrate the wrong one.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option(
        "sqlalchemy.url", get_settings().database_url.get_secret_value().replace("%", "%%")
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A caller (the test suite, or the migration-drift check) can hand in its
    # own connection. That is the only way to migrate an in-memory SQLite
    # database, which exists only for as long as its connection does.
    existing = config.attributes.get("connection")
    if existing is not None:
        _run(existing)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run(connection)


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite cannot ALTER most things in place; the test suite migrates a
        # SQLite database, so later migrations need batch mode there.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
