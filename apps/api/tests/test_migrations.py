"""Migrations against the database they are actually for.

The rest of the suite migrates SQLite, which proves the migration runs but not
that it runs on PostgreSQL, and not that it still matches the models. This does
both -- when PostgreSQL is up, which is why it is marked `integration`:

    docker compose up -d postgres
    uv run pytest -m integration
"""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text
from tests_support_api import (
    ALEMBIC_INI,
    MATCHES,
    PHASE_1,
    assert_at_0004,
    assert_downgraded,
    assert_upgraded,
    seed_phase_1,
)

from pickleball_api.models import Base

pytestmark = pytest.mark.integration

def alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    # env.py leaves this alone once it is set, so the migration really does run
    # against the throwaway database rather than the development one.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def test_migrations_apply_to_a_clean_postgres_and_match_the_models(
    fresh_database: str,
) -> None:
    command.upgrade(alembic_config(fresh_database), "head")
    engine: Engine = create_engine(fresh_database)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        difference = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert difference == [], f"the models and the migrations disagree: {difference}"


def test_migrations_can_be_rolled_back(fresh_database: str) -> None:
    config = alembic_config(fresh_database)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(fresh_database)
    with engine.connect() as connection:
        tables = connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).scalars().all()
    engine.dispose()
    assert not {"matches", "videos", "analysis_jobs"} & set(tables)


def test_phase_1_rows_survive_the_match_migration_both_ways(fresh_database: str) -> None:
    """Migrations 0003 and 0004 on real data: every video becomes a match,
    every job follows it, nothing is falsely marked decoded, and the downgrade
    gives all of them back."""
    config = alembic_config(fresh_database)
    command.upgrade(config, PHASE_1)
    engine = create_engine(fresh_database)
    try:
        with engine.begin() as connection:
            seeded = seed_phase_1(connection)

        command.upgrade(config, MATCHES)
        with engine.connect() as connection:
            assert_upgraded(connection, seeded)

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert_at_0004(connection, seeded)

        command.downgrade(config, PHASE_1)
        with engine.connect() as connection:
            assert_downgraded(connection, seeded)

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert_at_0004(connection, seeded)
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()
