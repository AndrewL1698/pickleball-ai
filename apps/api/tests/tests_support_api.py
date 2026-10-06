"""Helpers shared by the API tests.

Named so pytest does not collect it as a test module, matching
`ml/tests/tests_support_camera.py`. The pieces the worker's suite also needs
live in `pickleball_api.testing`, because the two suites are separate pytest
rootdirs and can only share code through the package.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from fastapi.testclient import TestClient

from pickleball_api.testing import ALEMBIC_INI, FTYP_HEADER, video_bytes

__all__ = [
    "ALEMBIC_INI",
    "FTYP_HEADER",
    "MATCHES",
    "PHASE_1",
    "Phase1Rows",
    "assert_at_0004",
    "assert_downgraded",
    "assert_upgraded",
    "seed_phase_1",
    "upload",
    "video_bytes",
]


def upload(
    client: TestClient,
    filename: str,
    *,
    size: int = 4096,
    content_type: str = "video/mp4",
) -> httpx.Response:
    """Create a match by uploading one video."""
    return client.post(
        "/api/matches", files={"file": (filename, video_bytes(size), content_type)}
    )


# --- Phase 1 rows, for testing migration 0003 -------------------------------
#
# Table shapes as they were at revision 0002, written out here rather than taken
# from the models, which no longer describe that schema.

PHASE_1 = "0002_enum_checks"
#: Revision 0003: matches introduced, before metadata columns.
MATCHES = "0003_match_ownership"

_videos_v1 = sa.table(
    "videos",
    sa.column("id", sa.Uuid()),
    sa.column("original_filename", sa.String()),
    sa.column("storage_key", sa.String()),
    sa.column("content_type", sa.String()),
    sa.column("byte_size", sa.BigInteger()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
_jobs_v1 = sa.table(
    "analysis_jobs",
    sa.column("id", sa.Uuid()),
    sa.column("video_id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("stage", sa.String()),
    sa.column("progress", sa.Float()),
    sa.column("error_code", sa.String()),
    sa.column("error_message", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


@dataclass(frozen=True)
class Phase1Rows:
    """What `seed_phase_1` inserted, and what the upgrade should make of it."""

    videos: dict[uuid.UUID, str]  # id -> original filename
    jobs: dict[uuid.UUID, uuid.UUID]  # job id -> video id
    expected_names: dict[uuid.UUID, str]
    expected_status: dict[uuid.UUID, str]


def seed_phase_1(connection: sa.Connection) -> Phase1Rows:
    """Insert Phase 1 videos and jobs covering every case the backfill handles."""
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    cases = [
        # filename, [(job status, minutes after upload)], expected name, expected match status
        ("Sunday  doubles.mov", [("ready", 1)], "Sunday doubles", "calibration_required"),
        ("queued.mp4", [("queued", 1)], "queued", "uploaded"),
        ("running.mp4", [("running", 1)], "running", "processing"),
        # The latest job decides, even though it was inserted first.
        ("retried.mp4", [("ready", 5), ("failed", 1)], "retried", "calibration_required"),
        ("broke.mp4", [("ready", 1), ("failed", 5)], "broke", "failed"),
        ("never picked up.m4v", [], "never picked up", "uploaded"),
    ]
    rows = Phase1Rows({}, {}, {}, {})
    for index, (filename, job_specs, name, status) in enumerate(cases):
        video_id = uuid.uuid4()
        connection.execute(
            _videos_v1.insert().values(
                id=video_id,
                original_filename=filename,
                storage_key=f"{index:032x}.mp4",
                content_type="video/mp4",
                byte_size=100 + index,
                created_at=t0 + timedelta(hours=index),
            )
        )
        rows.videos[video_id] = filename
        rows.expected_names[video_id] = name
        rows.expected_status[video_id] = status
        for job_status, minutes in job_specs:
            job_id = uuid.uuid4()
            connection.execute(
                _jobs_v1.insert().values(
                    id=job_id,
                    video_id=video_id,
                    status=job_status,
                    stage="metadata_ready" if job_status == "ready" else "ingested",
                    progress=1.0 if job_status == "ready" else 0.0,
                    error_code="internal" if job_status == "failed" else None,
                    error_message="Processing failed." if job_status == "failed" else None,
                    created_at=t0 + timedelta(hours=index, minutes=minutes),
                )
            )
            rows.jobs[job_id] = video_id
    return rows


def assert_upgraded(connection: sa.Connection, seeded: Phase1Rows) -> None:
    """Every Phase 1 row survived, under a match named and statused correctly."""
    matches = {
        row.id: row
        for row in connection.execute(sa.text("SELECT id, name, status, recorded_at FROM matches"))
    }
    videos = {
        row.id: row.match_id
        for row in connection.execute(sa.text("SELECT id, match_id FROM videos"))
    }
    jobs = {
        row.id: row.match_id
        for row in connection.execute(sa.text("SELECT id, match_id FROM analysis_jobs"))
    }
    # Raw rows come back as the driver's representation, so normalize.
    matches = {_as_uuid(k): v for k, v in matches.items()}
    videos = {_as_uuid(k): _as_uuid(v) for k, v in videos.items()}
    jobs = {_as_uuid(k): _as_uuid(v) for k, v in jobs.items()}

    assert set(videos) == set(seeded.videos)
    assert set(jobs) == set(seeded.jobs)
    # One match per video, sharing its id, so old links can be redirected.
    assert set(matches) == set(seeded.videos)
    assert all(match_id == video_id for video_id, match_id in videos.items())
    for job_id, video_id in seeded.jobs.items():
        assert jobs[job_id] == video_id
    for video_id in seeded.videos:
        match = matches[video_id]
        assert match.name == seeded.expected_names[video_id]
        assert match.status == seeded.expected_status[video_id]
        assert match.recorded_at is None


def assert_downgraded(connection: sa.Connection, seeded: Phase1Rows) -> None:
    """Back at Phase 1 with every video and job, and jobs pointing at their video."""
    videos = {
        _as_uuid(row.id)
        for row in connection.execute(sa.text("SELECT id FROM videos"))
    }
    jobs = {
        _as_uuid(row.id): _as_uuid(row.video_id)
        for row in connection.execute(sa.text("SELECT id, video_id FROM analysis_jobs"))
    }
    assert videos == set(seeded.videos)
    assert jobs == seeded.jobs


def _as_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def assert_at_0004(connection: sa.Connection, seeded: Phase1Rows) -> None:
    """After 0004: nothing decoded, and nothing claims to be ready for calibration.

    0003 derived `calibration_required` from a finished placeholder job; 0004
    moves those back to `uploaded`, since none of them has metadata.
    """
    rows = connection.execute(
        sa.text(
            "SELECT m.id, m.status, v.width, v.metadata_extracted_at FROM matches m "
            "JOIN videos v ON v.match_id = m.id"
        )
    ).all()
    assert {_as_uuid(row.id) for row in rows} == set(seeded.videos)
    for row in rows:
        expected = seeded.expected_status[_as_uuid(row.id)]
        if expected == "calibration_required":
            expected = "uploaded"
        assert row.status == expected
        assert row.width is None and row.metadata_extracted_at is None
    jobs = connection.execute(sa.text("SELECT id FROM analysis_jobs")).scalars().all()
    assert {_as_uuid(job) for job in jobs} == set(seeded.jobs)
