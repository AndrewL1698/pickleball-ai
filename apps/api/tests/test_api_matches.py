"""Creating a match by upload, and the read endpoints around it."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from tests_support_api import upload

from pickleball_api.config import Settings
from pickleball_api.models import AnalysisJob, JobStatus, Match, MatchStatus, Video
from pickleball_api.queue import RecordingJobQueue
from pickleball_api.storage import LocalFileStorage, new_storage_key


def test_a_valid_upload_creates_a_match_a_video_a_job_and_a_queue_entry(
    client: TestClient, queue: RecordingJobQueue, session: Session
) -> None:
    response = upload(client, "match.mp4")
    assert response.status_code == 201
    body = response.json()

    assert body["name"] == "match"
    assert body["status"] == MatchStatus.UPLOADED.value
    assert body["recorded_at"] is None
    assert body["video"]["original_filename"] == "match.mp4"
    assert body["video"]["content_type"] == "video/mp4"
    assert body["video"]["byte_size"] == 4096
    assert body["latest_job"]["match_id"] == body["id"]
    assert body["latest_job"]["status"] == JobStatus.QUEUED.value
    assert body["latest_job"]["stage"] == "ingested"
    assert body["latest_job"]["progress"] == 0.0
    assert [j["id"] for j in body["jobs"]] == [body["latest_job"]["id"]]

    stored = session.get(Match, uuid.UUID(body["id"]))
    assert stored is not None
    assert stored.video is not None and str(stored.video.id) == body["video"]["id"]
    assert [job.id for job in stored.jobs] == [uuid.UUID(body["latest_job"]["id"])]
    assert queue.enqueued == [uuid.UUID(body["latest_job"]["id"])]


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Sunday  doubles.mov", "Sunday doubles"),
        ("IMG_0042.MOV", "IMG_0042"),
        ("club.final.2026.mp4", "club.final.2026"),
        ("../../etc/passwd.mp4", "passwd"),
    ],
)
def test_the_match_is_named_after_the_upload_without_its_extension(
    client: TestClient, filename: str, expected: str
) -> None:
    assert upload(client, filename).json()["name"] == expected


def test_the_stored_file_is_named_by_a_generated_key_not_the_upload(
    client: TestClient, session: Session, settings: Settings
) -> None:
    body = upload(client, "my holiday match.mp4").json()
    video = session.get(Video, uuid.UUID(body["video"]["id"]))
    assert video is not None
    assert video.original_filename == "my holiday match.mp4"
    assert video.storage_key != "my holiday match.mp4"
    assert [p.name for p in settings.upload_dir.iterdir()] == [video.storage_key]


def test_the_storage_key_is_never_exposed(client: TestClient) -> None:
    body = upload(client, "match.mp4").json()
    assert "storage_key" not in str(body)
    listed = client.get("/api/matches").json()["matches"][0]
    assert "storage_key" not in str(listed)
    detail = client.get(f"/api/matches/{body['id']}").json()
    assert "storage_key" not in str(detail)
    job = client.get(f"/api/jobs/{body['latest_job']['id']}").json()
    assert "storage_key" not in str(job)


@pytest.mark.parametrize(
    "filename",
    [
        "../../../../etc/passwd.mp4",
        "..\\..\\windows\\system32\\evil.mp4",
        "/absolute/path/match.mp4",
        "....//....//match.mp4",
        ".hidden.mp4",
    ],
)
def test_a_traversing_filename_cannot_escape_the_upload_directory(
    client: TestClient, settings: Settings, session: Session, filename: str
) -> None:
    response = upload(client, filename)
    assert response.status_code == 201
    # Whatever the name claimed, exactly one flat file exists in the store.
    written = list(settings.upload_dir.rglob("*"))
    assert len(written) == 1
    assert written[0].parent == settings.upload_dir.resolve()
    assert ".." not in written[0].name and "/" not in written[0].name


def test_a_traversing_filename_is_recorded_without_its_directory_part(
    client: TestClient, session: Session
) -> None:
    body = upload(client, "../../etc/passwd.mp4").json()
    assert body["video"]["original_filename"] == "passwd.mp4"


@pytest.mark.parametrize(
    "filename", ["match.txt", "match.exe", "match", "match.mp4.txt", "match.MKV"]
)
def test_an_unsupported_extension_is_rejected(
    client: TestClient, settings: Settings, queue: RecordingJobQueue, filename: str
) -> None:
    response = upload(client, filename)
    assert response.status_code == 415
    assert response.json()["error_code"] == "unsupported_file_type"
    assert list(settings.upload_dir.iterdir()) == []
    assert queue.enqueued == []


def test_a_declared_video_content_type_does_not_make_a_text_file_a_video(
    client: TestClient, settings: Settings
) -> None:
    """The client's content type is not evidence. The bytes are."""
    response = client.post(
        "/api/matches",
        files={"file": ("match.mp4", b"this is not a video at all", "video/mp4")},
    )
    assert response.status_code == 415
    assert list(settings.upload_dir.iterdir()) == []


def test_the_stored_content_type_comes_from_the_extension_not_the_client(
    client: TestClient
) -> None:
    body = upload(client, "match.mov", content_type="application/octet-stream").json()
    assert body["video"]["content_type"] == "video/quicktime"


def test_an_oversize_upload_is_rejected_and_leaves_nothing_behind(
    client: TestClient, settings: Settings, queue: RecordingJobQueue, session: Session
) -> None:
    response = upload(client, "huge.mp4", size=settings.max_upload_bytes + 1)
    assert response.status_code == 413
    assert response.json()["error_code"] == "upload_too_large"
    assert list(settings.upload_dir.iterdir()) == []
    assert queue.enqueued == []
    assert session.query(Video).count() == 0
    assert session.query(Match).count() == 0


def test_an_upload_with_no_filename_is_rejected(client: TestClient) -> None:
    response = client.post("/api/matches", files={"file": ("", b"x" * 100, "video/mp4")})
    assert response.status_code in (415, 422)


def test_nothing_is_queued_when_the_queue_is_down_and_the_job_says_so(
    client: TestClient, queue: RecordingJobQueue, session: Session, settings: Settings
) -> None:
    queue.available = False
    response = upload(client, "match.mp4")
    assert response.status_code == 503
    assert response.json()["error_code"] == "dependency_unavailable"
    # Nothing about the infrastructure: no Redis URL, no filesystem path.
    assert "redis" not in response.text.lower()
    assert str(settings.upload_dir) not in response.text

    # The upload really happened, so the rows and the file stay -- but neither
    # the job nor the match is left claiming that a worker has it.
    match = session.query(Match).one()
    assert match.status is MatchStatus.FAILED
    assert match.video is not None
    assert [p.name for p in settings.upload_dir.iterdir()] == [match.video.storage_key]
    assert match.latest_job is not None
    assert match.latest_job.status is JobStatus.FAILED
    assert match.latest_job.error_code == "enqueue_failed"
    assert "enqueue" not in (match.latest_job.error_message or "")

    # And the failure is visible over HTTP, where the web app will look.
    detail = client.get(f"/api/matches/{match.id}").json()
    assert detail["status"] == "failed"
    assert detail["latest_job"]["error_code"] == "enqueue_failed"


def test_the_file_is_discarded_when_the_database_write_fails(
    client: TestClient,
    settings: Settings,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(self: Session) -> None:
        raise RuntimeError("postgres went away")

    monkeypatch.setattr(Session, "commit", explode)
    response = client.post(
        "/api/matches", files={"file": ("match.mp4", b"\x00\x00\x00\x20ftypisom" + b"0" * 64,
                                       "video/mp4")}
    )
    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_error"
    assert "postgres went away" not in response.text
    assert list(settings.upload_dir.iterdir()) == []


def test_matches_are_listed_newest_first_with_their_video_and_latest_job(
    client: TestClient,
) -> None:
    first = upload(client, "first.mp4").json()
    second = upload(client, "second.mp4").json()
    body = client.get("/api/matches").json()
    assert body["count"] == 2
    assert [m["id"] for m in body["matches"]] == [second["id"], first["id"]]
    assert body["matches"][0]["name"] == "second"
    assert body["matches"][0]["video"]["original_filename"] == "second.mp4"
    assert body["matches"][0]["latest_job"]["status"] == JobStatus.QUEUED.value
    assert "jobs" not in body["matches"][0]


def test_a_match_can_be_read_back_with_its_video_and_all_of_its_jobs(
    client: TestClient,
) -> None:
    created = upload(client, "match.mp4").json()
    body = client.get(f"/api/matches/{created['id']}").json()
    assert body["id"] == created["id"]
    assert body["video"]["id"] == created["video"]["id"]
    assert body["video"]["original_filename"] == "match.mp4"
    assert [j["id"] for j in body["jobs"]] == [created["latest_job"]["id"]]


def test_the_latest_job_is_the_newest_attempt_and_jobs_are_oldest_first(
    client: TestClient, session: Session
) -> None:
    created = upload(client, "match.mp4").json()
    match = session.get(Match, uuid.UUID(created["id"]))
    assert match is not None
    assert match.latest_job is not None
    match.latest_job.status = JobStatus.FAILED  # only one job may be active
    retry = AnalysisJob(match=match)
    session.add(retry)
    session.commit()

    body = client.get(f"/api/matches/{created['id']}").json()
    assert [j["id"] for j in body["jobs"]] == [created["latest_job"]["id"], str(retry.id)]
    assert body["latest_job"]["id"] == str(retry.id)
    listed = client.get("/api/matches").json()["matches"][0]
    assert listed["latest_job"]["id"] == str(retry.id)


def test_a_missing_match_is_a_clean_404(client: TestClient) -> None:
    response = client.get(f"/api/matches/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"error_code": "not_found", "detail": "No such match."}


def test_the_phase_1_video_endpoints_are_gone(client: TestClient) -> None:
    """One primary API, not two that can drift apart."""
    assert client.get("/api/videos").status_code == 404
    assert client.get(f"/api/videos/{uuid.uuid4()}").status_code == 404
    assert upload(client, "match.mp4").status_code == 201  # via /api/matches
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/api/videos")]


def test_the_openapi_contract_describes_matches(client: TestClient) -> None:
    """The shapes the web app's `lib/types.ts` mirrors, and nothing private."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schemas["MatchSummary"]["properties"]) == {
        "id", "name", "recorded_at", "status", "created_at", "video", "latest_job",
    }
    assert set(schemas["MatchDetail"]["properties"]) == set(
        schemas["MatchSummary"]["properties"]
    ) | {"jobs", "can_extract_metadata"}
    assert set(schemas["MatchList"]["properties"]) == {"matches", "count"}
    assert set(schemas["VideoRead"]["properties"]) == {
        "id", "original_filename", "content_type", "byte_size", "created_at", "metadata",
    }
    assert set(schemas["VideoMetadataRead"]["properties"]) == {
        "width", "height", "rotation_degrees", "average_fps", "duration_seconds",
        "frame_count", "codec", "extracted_at",
    }
    # Named for what it is: an average, not an exact frame rate.
    assert "fps" not in schemas["VideoMetadataRead"]["properties"]
    assert "/api/matches/{match_id}/metadata-jobs" in client.get("/openapi.json").json()["paths"]
    assert "match_id" in schemas["JobRead"]["properties"]
    assert "video_id" not in schemas["JobRead"]["properties"]
    assert schemas["MatchStatus"]["enum"] == [
        "uploaded", "processing", "calibration_required", "court_ready", "failed",
    ]
    assert "ready" not in schemas["MatchStatus"]["enum"]
    assert "storage_key" not in str(schemas)


def test_a_match_id_that_is_not_a_uuid_is_rejected_without_a_stack_trace(
    client: TestClient
) -> None:
    response = client.get("/api/matches/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_request"
    assert "traceback" not in response.text.lower()


def test_a_job_can_be_read_by_id(client: TestClient) -> None:
    created = upload(client, "match.mp4").json()
    body = client.get(f"/api/jobs/{created['latest_job']['id']}").json()
    assert body["match_id"] == created["id"]
    assert body["status"] == JobStatus.QUEUED.value
    assert body["error_code"] is None
    assert body["started_at"] is None and body["finished_at"] is None


def test_a_missing_job_is_a_clean_404(client: TestClient) -> None:
    response = client.get(f"/api/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "No such job."


def test_health_does_not_touch_the_database(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body == {"status": "ok", "environment": "test"}


def test_ready_reports_each_dependency_without_naming_it(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "database": True, "redis": True}


def test_ready_is_503_when_the_queue_is_unreachable(
    client: TestClient, queue: RecordingJobQueue
) -> None:
    queue.available = False
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "database": True, "redis": False}
    # A connection error's text carries the Redis URL, password and all.
    assert "redis://" not in response.text


def test_storage_survives_a_restart_because_keys_are_stable(
    settings: Settings, tmp_path: Path
) -> None:
    storage = LocalFileStorage(settings.upload_dir)
    key = new_storage_key(".mp4")
    storage.write(key, [b"frames"], max_bytes=1000)
    assert LocalFileStorage(settings.upload_dir).exists(key)


def test_an_upload_is_refused_when_the_disk_is_nearly_full(
    client: TestClient, storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A match is gigabytes; running out of room is ordinary, not an attack."""
    monkeypatch.setattr(LocalFileStorage, "free_bytes", lambda self: 0)
    response = upload(client, "match.mp4")
    assert response.status_code == 507
    assert response.json()["error_code"] == "insufficient_storage"
    assert list(storage.root.iterdir()) == []


def test_an_oversize_upload_is_refused_before_its_body_is_read(
    client: TestClient, settings: Settings, storage: LocalFileStorage
) -> None:
    """A declared Content-Length over the limit is rejected up front.

    The route's own counting check would also catch this, but only after the
    ASGI server had already written every byte to a temporary file.
    """
    response = client.post(
        "/api/matches",
        files={"file": ("huge.mp4", b"x" * 100, "video/mp4")},
        headers={"Content-Length": str(settings.max_upload_bytes + 1)},
    )
    assert response.status_code == 413
    assert response.json()["error_code"] == "upload_too_large"
    assert list(storage.root.iterdir()) == []


def test_a_small_upload_is_accepted_when_the_disk_has_room_for_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Free space is measured against this upload, not against the maximum.

    Sizing the check to `max_upload_bytes` refused every upload, however small,
    once free space fell below twice the configured limit.
    """
    monkeypatch.setattr(
        LocalFileStorage, "free_bytes", lambda self: 512 * 1024 * 1024
    )
    assert upload(client, "tiny.mp4", size=4096).status_code == 201


def test_a_long_filename_keeps_its_extension(client: TestClient) -> None:
    """Truncating the whole name would drop the suffix and make a valid file
    look like an unsupported type."""
    response = upload(client, "a" * 250 + ".mp4")
    assert response.status_code == 201
    assert response.json()["video"]["original_filename"].endswith(".mp4")


# --- decoded metadata and the metadata-jobs endpoint --------------------------


def _decode(session: Session, match_id: str, **overrides: object) -> None:
    """Record metadata as a successful job would."""
    match = session.get(Match, uuid.UUID(match_id))
    assert match is not None and match.video is not None
    values: dict[str, object] = {
        "width": 1080, "height": 1920, "rotation_degrees": 90, "average_fps": 29.97,
        "duration_seconds": 12.5, "frame_count": 375, "codec": "avc1",
        "metadata_extracted_at": datetime.now(UTC),
    }
    values.update(overrides)
    for name, value in values.items():
        setattr(match.video, name, value)
    assert match.latest_job is not None
    match.latest_job.status = JobStatus.READY
    session.commit()


def _fail_latest(session: Session, match_id: str) -> None:
    match = session.get(Match, uuid.UUID(match_id))
    assert match is not None and match.latest_job is not None
    match.latest_job.status = JobStatus.FAILED
    match.status = MatchStatus.FAILED
    session.commit()


def test_an_upload_has_no_metadata_until_the_worker_decodes_it(client: TestClient) -> None:
    body = upload(client, "match.mp4").json()
    assert body["video"]["metadata"] is None
    # A job is already queued, so there is nothing to start.
    assert body["can_extract_metadata"] is False


def test_decoded_metadata_is_returned_in_display_orientation(
    client: TestClient, session: Session
) -> None:
    created = upload(client, "match.mp4").json()
    _decode(session, created["id"])
    metadata = client.get(f"/api/matches/{created['id']}").json()["video"]["metadata"]
    assert metadata["width"] == 1080 and metadata["height"] == 1920
    assert metadata["rotation_degrees"] == 90
    assert metadata["average_fps"] == pytest.approx(29.97)
    assert metadata["frame_count"] == 375
    assert metadata["codec"] == "avc1"
    assert metadata["extracted_at"]
    listed = client.get("/api/matches").json()["matches"][0]
    assert listed["video"]["metadata"]["width"] == 1080


def test_a_decoded_video_with_no_codec_name_says_null(
    client: TestClient, session: Session
) -> None:
    created = upload(client, "match.mp4").json()
    _decode(session, created["id"], codec=None)
    metadata = client.get(f"/api/matches/{created['id']}").json()["video"]["metadata"]
    assert metadata["codec"] is None


def test_metadata_can_be_requested_after_a_failure_and_history_is_kept(
    client: TestClient, session: Session, queue: RecordingJobQueue
) -> None:
    created = upload(client, "match.mp4").json()
    _fail_latest(session, created["id"])
    before = client.get(f"/api/matches/{created['id']}").json()
    assert before["can_extract_metadata"] is True

    response = client.post(f"/api/matches/{created['id']}/metadata-jobs")
    assert response.status_code == 202
    body = response.json()
    assert [j["id"] for j in body["jobs"]][0] == created["latest_job"]["id"]
    assert len(body["jobs"]) == 2
    assert body["jobs"][0]["status"] == "failed"  # the old attempt is untouched
    assert body["latest_job"]["status"] == "queued"
    assert body["latest_job"]["match_id"] == created["id"]
    assert body["status"] == MatchStatus.UPLOADED.value
    assert body["can_extract_metadata"] is False
    assert queue.enqueued[-1] == uuid.UUID(body["latest_job"]["id"])


def test_a_backfilled_match_with_a_finished_placeholder_job_can_be_decoded(
    client: TestClient, session: Session, queue: RecordingJobQueue
) -> None:
    """Phase 1 rows: the job reached `ready` but nothing was ever decoded."""
    created = upload(client, "match.mp4").json()
    match = session.get(Match, uuid.UUID(created["id"]))
    assert match is not None and match.latest_job is not None
    match.latest_job.status = JobStatus.READY
    match.status = MatchStatus.UPLOADED
    session.commit()

    response = client.post(f"/api/matches/{created['id']}/metadata-jobs")
    assert response.status_code == 202
    assert len(response.json()["jobs"]) == 2


def test_the_job_is_committed_before_it_is_enqueued(
    client: TestClient,
    session: Session,
    session_factory: sessionmaker[Session],
    queue: RecordingJobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = upload(client, "match.mp4").json()
    _fail_latest(session, created["id"])
    seen: list[bool] = []

    def enqueue(job_id: uuid.UUID) -> str:
        # What a worker would see the moment the message lands.
        with session_factory() as other:
            seen.append(other.get(AnalysisJob, job_id) is not None)
        return "ok"

    monkeypatch.setattr(queue, "enqueue", enqueue)
    assert client.post(f"/api/matches/{created['id']}/metadata-jobs").status_code == 202
    assert seen == [True]


def test_a_second_request_while_a_job_is_active_is_refused(
    client: TestClient, session: Session, queue: RecordingJobQueue
) -> None:
    created = upload(client, "match.mp4").json()
    _fail_latest(session, created["id"])
    assert client.post(f"/api/matches/{created['id']}/metadata-jobs").status_code == 202
    enqueued = list(queue.enqueued)

    response = client.post(f"/api/matches/{created['id']}/metadata-jobs")
    assert response.status_code == 409
    assert response.json() == {
        "error_code": "conflict",
        "detail": "A processing job for this match is already queued or running.",
    }
    assert queue.enqueued == enqueued
    assert len(client.get(f"/api/matches/{created['id']}").json()["jobs"]) == 2


def test_a_request_racing_past_the_check_is_refused_by_the_database(
    client: TestClient,
    session: Session,
    queue: RecordingJobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests can both pass the application's check before either
    commits. The partial unique index is what stops the second."""
    created = upload(client, "match.mp4").json()  # its job is still queued
    monkeypatch.setattr(Match, "metadata_job_refusal", lambda self: None)
    enqueued = list(queue.enqueued)

    response = client.post(f"/api/matches/{created['id']}/metadata-jobs")
    assert response.status_code == 409
    assert response.json()["error_code"] == "conflict"
    assert queue.enqueued == enqueued
    session.expire_all()
    match = session.get(Match, uuid.UUID(created["id"]))
    assert match is not None and len(match.jobs) == 1


def test_metadata_that_already_exists_is_not_extracted_again(
    client: TestClient, session: Session
) -> None:
    created = upload(client, "match.mp4").json()
    _decode(session, created["id"])
    assert client.get(f"/api/matches/{created['id']}").json()["can_extract_metadata"] is False
    response = client.post(f"/api/matches/{created['id']}/metadata-jobs")
    assert response.status_code == 409
    assert "already been extracted" in response.json()["detail"]


def test_a_match_with_no_video_cannot_be_processed(
    client: TestClient, session: Session
) -> None:
    match = Match(name="empty")
    session.add(match)
    session.commit()
    response = client.post(f"/api/matches/{match.id}/metadata-jobs")
    assert response.status_code == 409
    assert response.json()["detail"] == "This match has no video to process."


def test_metadata_jobs_for_an_unknown_match_is_a_404(client: TestClient) -> None:
    response = client.post(f"/api/matches/{uuid.uuid4()}/metadata-jobs")
    assert response.status_code == 404
    assert response.json()["detail"] == "No such match."


def test_a_retry_that_cannot_be_queued_fails_visibly_and_safely(
    client: TestClient, session: Session, queue: RecordingJobQueue
) -> None:
    created = upload(client, "match.mp4").json()
    _fail_latest(session, created["id"])
    queue.available = False
    response = client.post(f"/api/matches/{created['id']}/metadata-jobs")
    assert response.status_code == 503
    assert response.json() == {
        "error_code": "dependency_unavailable",
        "detail": "The job was recorded but could not be queued for processing.",
    }
    detail = client.get(f"/api/matches/{created['id']}").json()
    assert detail["status"] == "failed"
    assert [j["status"] for j in detail["jobs"]] == ["failed", "failed"]
    assert detail["latest_job"]["error_code"] == "enqueue_failed"
    # Nothing is left active, so it can be tried again once the queue is back.
    assert detail["can_extract_metadata"] is True
