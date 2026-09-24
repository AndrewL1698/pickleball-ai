"""Test helpers that both suites need.

Shipped with the package rather than kept in a test directory, for the same
reason `RecordingJobQueue` sits next to `JobQueue`: the worker's tests live in
a different pytest rootdir and cannot import the API's `tests/`, but they can
import `pickleball_api`. Two copies of the SQLite setup had already drifted
apart once, and the foreign-key PRAGMA is the dangerous half -- without it a
test suite is more permissive than production, which is the one direction that
is never acceptable.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

#: `pickleball_api` lives at apps/api/src/pickleball_api, so the root is four up.
REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"

#: A real ISO base-media header, so an upload gets past the format sniff.
FTYP_HEADER = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


def video_bytes(size: int = 4096) -> bytes:
    """A fake video: a genuine `ftyp` box followed by filler."""
    return FTYP_HEADER + b"\x00" * max(size - len(FTYP_HEADER), 0)


@contextmanager
def migrated_sqlite_engine() -> Iterator[Engine]:
    """An in-memory SQLite database with every migration applied.

    Migrated rather than created from the models, so a migration that does not
    produce the schema the models expect fails in the test suite rather than on
    somebody's first `docker compose up`.

    `StaticPool` plus `check_same_thread=False` keeps every connection pointed
    at the same database: `sqlite://` otherwise hands out a fresh empty one per
    connection, and FastAPI runs synchronous routes on a worker thread.
    """
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: object, _record: object) -> None:
        # Off by default in SQLite, which would make the tests more permissive
        # than production.
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.connect() as connection:
        config = Config(str(ALEMBIC_INI))
        # Alembic is handed the open connection because an in-memory database
        # exists only for as long as its connection does.
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()

    try:
        yield engine
    finally:
        engine.dispose()
