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
from sqlalchemy.orm import Session
from tests_support_api import (
    ALEMBIC_INI,
    MATCHES,
    PHASE_1,
    assert_at_0004,
    assert_downgraded,
    assert_upgraded,
    calibrated_match,
    seed_phase_1,
)

from pickleball_api.models import Base, Match, ModelRun, ModelRunStage

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


def _match_with_runs(session: Session, name: str) -> Match:
    """A calibrated match showing an Identify run that read a Detect run."""
    match, calibration = calibrated_match(session, name=name)
    detect = ModelRun.start(
        match, ModelRunStage.DETECT, calibration=calibration, model_version="v", config={}
    )
    match.current_identify_run = ModelRun.start(
        match,
        ModelRunStage.IDENTIFY,
        calibration=calibration,
        model_version="v",
        config={},
        parent=detect,
    )
    session.commit()
    return match


def test_deleting_a_match_cascades_to_its_runs_and_calibrations(fresh_database: str) -> None:
    """The match, its runs and its calibrations point at one another, and a run's
    references to its parent and its calibration do not cascade. PostgreSQL has
    to resolve all of that in one statement, and through the ORM."""
    command.upgrade(alembic_config(fresh_database), "head")
    engine = create_engine(fresh_database)
    try:
        with Session(engine) as session:
            session.delete(_match_with_runs(session, "deleted through the ORM"))
            session.commit()
            _match_with_runs(session, "deleted in SQL")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM matches"))
        with engine.connect() as connection:
            for table in ("model_runs", "court_calibrations", "reference_frames"):
                remaining = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                assert remaining == 0, table
    finally:
        engine.dispose()
