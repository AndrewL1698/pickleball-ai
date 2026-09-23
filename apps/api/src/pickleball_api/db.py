"""Database engine and session handling.

Sessions are synchronous. The RQ worker is a synchronous process and Alembic is
simpler without an async driver, so one style across both avoids maintaining
two connection paths for a workload that is a handful of small queries per
request.

Tables are created by Alembic, never by `create_all` at startup: a process that
silently creates whatever its models happen to say would hide a missing
migration until production.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from pickleball_api.config import Settings, get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def create_db_engine(settings: Settings) -> Engine:
    """An engine for these settings.

    `pool_pre_ping` costs one round trip per checkout and saves the first
    request after Postgres restarts, which during development it does often.
    """
    return create_engine(
        settings.database_url.get_secret_value(),
        echo=settings.database_echo,
        pool_pre_ping=True,
        future=True,
    )


def get_engine() -> Engine:
    """The process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        _engine = create_db_engine(get_settings())
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back on error.

    The route commits explicitly when it has written something, so a read-only
    route never holds a transaction open past its last query.
    """
    with get_session_factory()() as session:
        try:
            yield session
        except BaseException:
            session.rollback()
            raise


@contextmanager
def session_scope() -> Iterator[Session]:
    """A committed unit of work, for code outside a request (the worker)."""
    with get_session_factory()() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
