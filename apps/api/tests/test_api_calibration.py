"""Calibration images and calibrations, through the HTTP API."""

import uuid

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pickleball_api.models import (
    AnalysisJob,
    CourtCalibration,
    JobStatus,
    Match,
    MatchStatus,
    ReferenceFrame,
    ReferenceFrameKind,
    Video,
    utcnow,
)
from pickleball_api.queue import RecordingJobQueue
from pickleball_api.storage import LocalFileStorage, new_storage_key
from pickleball_ml.court.geometry import apply_homography
from pickleball_ml.court.spec import LANDMARKS

WIDTH, HEIGHT = 1920, 1300
JPEG = b"\xff\xd8\xff\xe0 not really a picture"

# A plausible perspective camera behind the near baseline: feet -> pixels.
COURT_TO_IMAGE = np.array([[42.0, 8.0, 960.0], [0.0, -9.0, 700.0], [0.0, 0.012, 1.0]])


def clicks(*names: str) -> dict[str, list[float]]:
    chosen = names or tuple(LANDMARKS)
    projected = apply_homography(
        COURT_TO_IMAGE, np.array([LANDMARKS[name] for name in chosen], dtype=np.float64)
    )
    return {name: [float(x), float(y)] for name, (x, y) in zip(chosen, projected, strict=True)}


def make_match(
    session: Session,
    storage: LocalFileStorage,
    *,
    metadata: bool = True,
    frames: bool = True,
    job_status: JobStatus = JobStatus.READY,
) -> Match:
    match = Match(
        name="match",
        status=MatchStatus.CALIBRATION_REQUIRED if metadata else MatchStatus.UPLOADED,
    )
    match.video = Video(
        original_filename="m.mp4",
        storage_key=new_storage_key(".mp4"),
        content_type="video/mp4",
        byte_size=1,
    )
    if metadata:
        video = match.video
        video.width, video.height, video.rotation_degrees = WIDTH, HEIGHT, 0
        video.average_fps, video.duration_seconds, video.frame_count = 30.0, 10.0, 300
        video.metadata_extracted_at = utcnow()
    match.jobs.append(AnalysisJob(status=job_status))
    if frames:
        key = new_storage_key(".jpg")
        storage.write(key, [JPEG], max_bytes=len(JPEG))
        match.reference_frames.append(
            ReferenceFrame(
                ordinal=0,
                kind=ReferenceFrameKind.BACKGROUND,
                source_frame_count=25,
                storage_key=key,
                width=WIDTH,
                height=HEIGHT,
                byte_size=len(JPEG),
            )
        )
    session.add(match)
    session.commit()
    return match


def post_calibration(
    client: TestClient, match: Match, points: dict[str, list[float]]
) -> httpx.Response:
    return client.post(
        f"/api/matches/{match.id}/calibrations",
        json={"reference_frame_id": str(match.reference_frames[0].id), "image_points": points},
    )


# --- Reference frames -------------------------------------------------------


def test_a_reference_frame_is_served_as_a_fixed_type_that_cannot_run(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    frame = match.reference_frames[0]
    response = client.get(f"/api/matches/{match.id}/reference-frames/{frame.id}")
    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_a_reference_frame_is_only_found_under_its_own_match(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match, other = make_match(session, storage), make_match(session, storage)
    frame = match.reference_frames[0]
    assert client.get(f"/api/matches/{other.id}/reference-frames/{frame.id}").status_code == 404
    assert client.get(f"/api/matches/{match.id}/reference-frames/{uuid.uuid4()}").status_code == 404


def test_a_reference_frame_gone_from_storage_is_a_404_without_its_key(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    frame = match.reference_frames[0]
    storage.delete(frame.storage_key)
    response = client.get(f"/api/matches/{match.id}/reference-frames/{frame.id}")
    assert response.status_code == 404
    assert frame.storage_key not in response.text


def test_match_detail_lists_reference_frames_without_storage_keys(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    response = client.get(f"/api/matches/{match.id}")
    frames = response.json()["reference_frames"]
    assert frames[0]["kind"] == "background"
    assert (frames[0]["width"], frames[0]["height"]) == (WIDTH, HEIGHT)
    assert frames[0]["position_seconds"] is None
    assert match.reference_frames[0].storage_key not in response.text


def test_an_older_match_can_queue_a_job_for_its_calibration_images(
    client: TestClient, session: Session, storage: LocalFileStorage, queue: RecordingJobQueue
) -> None:
    match = make_match(session, storage, frames=False)
    detail = client.get(f"/api/matches/{match.id}").json()
    assert detail["can_extract_reference_frames"] is True
    assert detail["can_extract_metadata"] is False
    assert detail["can_calibrate"] is False

    response = client.post(f"/api/matches/{match.id}/reference-frame-jobs")
    assert response.status_code == 202
    body = response.json()
    assert len(body["jobs"]) == 2
    assert body["latest_job"]["status"] == "queued"
    assert body["can_extract_reference_frames"] is False
    assert queue.enqueued == [uuid.UUID(body["latest_job"]["id"])]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"metadata": False, "frames": False}, "metadata has not been extracted"),
        ({"frames": True}, "already has calibration images"),
        ({"frames": False, "job_status": JobStatus.RUNNING}, "already queued or running"),
    ],
)
def test_a_job_for_calibration_images_is_refused_when_it_makes_no_sense(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    queue: RecordingJobQueue,
    options: dict[str, object],
    message: str,
) -> None:
    match = make_match(session, storage, **options)  # type: ignore[arg-type]
    response = client.post(f"/api/matches/{match.id}/reference-frame-jobs")
    assert response.status_code == 409
    assert message in response.json()["detail"]
    assert queue.enqueued == []


def test_a_job_for_calibration_images_needs_a_match_that_exists(client: TestClient) -> None:
    assert client.post(f"/api/matches/{uuid.uuid4()}/reference-frame-jobs").status_code == 404


# --- Calibrations -----------------------------------------------------------


@pytest.mark.parametrize(
    "jumps",
    [
        None,
        [],
        [{"from_seconds": 349.9, "to_seconds": 386.9, "shift_px": 107.4}],
    ],
)
def test_match_detail_keeps_not_checked_apart_from_no_camera_move(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    jumps: list[dict[str, float]] | None,
) -> None:
    match = make_match(session, storage)
    assert match.video is not None
    match.video.camera_jumps = jumps
    session.commit()
    body = client.get(f"/api/matches/{match.id}").json()
    assert body["video"]["camera_jumps"] == jumps


def test_a_saved_calibration_says_what_a_pixel_is_worth_at_the_far_baseline(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    calibration = post_calibration(client, match, clicks()).json()["calibration"]
    assert calibration["far_baseline_ft_per_pixel"] > 0


def test_saving_a_calibration_recovers_the_camera_and_readies_the_court(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    response = post_calibration(client, match, clicks())
    assert response.status_code == 201

    body = response.json()
    assert body["status"] == "court_ready"
    calibration = body["calibration"]
    assert calibration["source"] == "manual"
    assert calibration["reference_frame_id"] == str(match.reference_frames[0].id)
    assert set(calibration["image_points"]) == set(LANDMARKS)
    assert calibration["court_points"]["near_left_kitchen"] == [-10.0, -7.0]
    assert calibration["mean_reprojection_error_ft"] < 0.01
    assert calibration["warnings"] == []

    # The saved homography takes a known pixel to the court point it came from.
    pixel = np.array([[960.0, 700.0]])  # court (0, 0)
    assert apply_homography(calibration["homography"], pixel)[0] == pytest.approx([0, 0], abs=1e-6)
    # The overlay's near baseline starts where that corner was placed.
    near_baseline = calibration["projected_lines"]["near_baseline"]
    assert near_baseline[0] == pytest.approx(
        calibration["image_points"]["near_left_baseline_corner"], abs=1e-3
    )
    assert set(calibration["projected_lines"]) >= {"left_sideline", "far_baseline", "net"}


def test_a_new_calibration_is_in_force_and_the_old_one_is_kept(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    first = post_calibration(client, match, clicks()).json()["calibration"]
    second = post_calibration(client, match, clicks()).json()["calibration"]
    assert second["id"] != first["id"]
    assert client.get(f"/api/matches/{match.id}").json()["calibration"]["id"] == second["id"]
    session.expire_all()
    assert session.query(CourtCalibration).filter_by(match_id=match.id).count() == 2


def test_a_list_of_matches_does_not_need_the_calibration(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    post_calibration(client, match, clicks())
    listed = client.get("/api/matches").json()["matches"][0]
    assert listed["status"] == "court_ready"
    assert "calibration" not in listed


@pytest.mark.parametrize(
    ("points", "message"),
    [
        (clicks("near_left_kitchen", "near_right_kitchen", "far_left_kitchen"), "at least 4"),
        (
            clicks(
                "near_left_baseline_corner", "near_center_baseline",
                "near_right_baseline_corner", "near_center_kitchen",
            ),
            "not along one line",
        ),
        (
            {
                **clicks("near_left_kitchen", "near_right_kitchen", "far_left_kitchen"),
                "far_right_kitchen": [-1.0, 300.0],
            },
            "inside the image",
        ),
    ],
)
def test_landmarks_that_cannot_make_a_court_are_refused_with_a_reason(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    points: dict[str, list[float]],
    message: str,
) -> None:
    match = make_match(session, storage)
    response = post_calibration(client, match, points)
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_request"
    assert message in response.json()["detail"]
    session.expire_all()
    assert session.get(Match, match.id).status is MatchStatus.CALIBRATION_REQUIRED


def test_swapped_left_and_right_are_refused_even_though_they_fit_perfectly(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    swapped = {
        name.replace("left", "@").replace("right", "left").replace("@", "right"): point
        for name, point in clicks().items()
    }
    response = post_calibration(client, match, swapped)
    assert response.status_code == 422
    assert "swapped" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"image_points": {"near_left_kitchen": [1.0, 2.0]}},
        {"reference_frame_id": str(uuid.uuid4()), "image_points": {"the_net_post": [1.0, 2.0]}},
        {"reference_frame_id": str(uuid.uuid4()), "image_points": {"near_left_kitchen": [1.0]}},
        {"reference_frame_id": str(uuid.uuid4()), "image_points": {}, "homography": []},
    ],
)
def test_a_malformed_calibration_request_is_refused(
    client: TestClient, session: Session, storage: LocalFileStorage, body: dict[str, object]
) -> None:
    match = make_match(session, storage)
    response = client.post(f"/api/matches/{match.id}/calibrations", json=body)
    assert response.status_code == 422


def test_a_coordinate_that_is_not_a_finite_number_is_refused(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    """`json.loads` accepts NaN, so the schema has to be the one that refuses it."""
    match = make_match(session, storage)
    frame = match.reference_frames[0]
    content = f"""{{"reference_frame_id": "{frame.id}",
                    "image_points": {{"near_left_kitchen": [NaN, 1.0]}}}}"""
    response = client.post(
        f"/api/matches/{match.id}/calibrations",
        content=content,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_a_frame_from_another_match_is_refused(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match, other = make_match(session, storage), make_match(session, storage)
    response = client.post(
        f"/api/matches/{match.id}/calibrations",
        json={"reference_frame_id": str(other.reference_frames[0].id), "image_points": clicks()},
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]


def test_calibration_waits_for_images_and_for_active_jobs(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    without_images = make_match(session, storage, frames=False)
    detail = client.get(f"/api/matches/{without_images.id}").json()
    assert detail["can_calibrate"] is False
    response = client.post(
        f"/api/matches/{without_images.id}/calibrations",
        json={"reference_frame_id": str(uuid.uuid4()), "image_points": clicks()},
    )
    assert response.status_code == 409
    assert "no calibration images" in response.json()["detail"]

    busy = make_match(session, storage, job_status=JobStatus.RUNNING)
    assert client.get(f"/api/matches/{busy.id}").json()["can_calibrate"] is False
    assert post_calibration(client, busy, clicks()).status_code == 409


def test_calibrating_a_match_that_does_not_exist_is_a_404(client: TestClient) -> None:
    response = client.post(
        f"/api/matches/{uuid.uuid4()}/calibrations",
        json={"reference_frame_id": str(uuid.uuid4()), "image_points": clicks()},
    )
    assert response.status_code == 404
