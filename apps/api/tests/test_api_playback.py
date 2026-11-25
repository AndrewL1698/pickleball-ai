"""Streaming a match's playback copy, with HTTP range requests."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pickleball_api.models import Match, MatchStatus, Video
from pickleball_api.storage import LocalFileStorage, new_storage_key

#: 10,000 bytes, each different from its neighbours, so a wrong range shows.
COPY = bytes(i % 251 for i in range(10_000))


def make_match(
    session: Session, storage: LocalFileStorage, *, copy: bool = True, video: bool = True
) -> Match:
    match = Match(name="match", status=MatchStatus.CALIBRATION_REQUIRED)
    if video:
        match.video = Video(
            original_filename="m.mov",
            storage_key=new_storage_key(".mov"),
            content_type="video/quicktime",
            byte_size=1,
        )
        if copy:
            key = new_storage_key(".mp4")
            storage.write(key, [COPY], max_bytes=len(COPY))
            match.video.playback_storage_key = key
    session.add(match)
    session.commit()
    return match


def get_copy(client: TestClient, match: Match, range_header: str | None = None) -> httpx.Response:
    headers = {} if range_header is None else {"Range": range_header}
    return client.get(f"/api/matches/{match.id}/playback", headers=headers)


def test_the_whole_copy_is_served_without_a_range(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    response = get_copy(client, make_match(session, storage))
    assert response.status_code == 200
    assert response.content == COPY
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"


def test_the_copy_cannot_be_sniffed_run_or_shared_between_users(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    response = get_copy(client, make_match(session, storage))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    cache = response.headers["cache-control"]
    assert "private" in cache and "immutable" in cache
    assert response.headers["content-disposition"] == 'inline; filename="playback.mp4"'


@pytest.mark.parametrize(
    ("range_header", "start", "end"),
    [
        ("bytes=0-99", 0, 99),  # the first bytes, where a browser starts
        ("bytes=5000-5099", 5000, 5099),  # the middle, as after a seek
        ("bytes=-10", 9990, 9999),  # the last bytes
    ],
)
def test_a_byte_range_is_served_as_partial_content(
    client: TestClient,
    session: Session,
    storage: LocalFileStorage,
    range_header: str,
    start: int,
    end: int,
) -> None:
    response = get_copy(client, make_match(session, storage), range_header)
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{end}/{len(COPY)}"
    assert response.content == COPY[start : end + 1]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_range_past_the_end_is_unsatisfiable(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    response = get_copy(client, make_match(session, storage), "bytes=20000-30000")
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(COPY)}"


def test_a_match_without_a_copy_is_a_404(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    assert get_copy(client, make_match(session, storage, copy=False)).status_code == 404
    assert get_copy(client, make_match(session, storage, video=False)).status_code == 404
    assert client.get(f"/api/matches/{uuid.uuid4()}/playback").status_code == 404


def test_a_copy_gone_from_storage_is_a_404_without_its_key(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    match = make_match(session, storage)
    assert match.video is not None and match.video.playback_storage_key is not None
    storage.delete(match.video.playback_storage_key)
    response = get_copy(client, match)
    assert response.status_code == 404
    assert match.video.playback_storage_key not in response.text


def test_the_match_says_whether_it_can_be_played_without_naming_the_file(
    client: TestClient, session: Session, storage: LocalFileStorage
) -> None:
    with_copy, without = make_match(session, storage), make_match(session, storage, copy=False)
    assert with_copy.video is not None
    detail = client.get(f"/api/matches/{with_copy.id}").json()
    assert detail["video"]["playback_available"] is True
    assert "playback_storage_key" not in detail["video"]
    assert str(with_copy.video.playback_storage_key) not in str(detail)
    assert client.get(f"/api/matches/{without.id}").json()["video"]["playback_available"] is False
    listed = {m["id"]: m["video"] for m in client.get("/api/matches").json()["matches"]}
    assert listed[str(with_copy.id)]["playback_available"] is True
    assert listed[str(without.id)]["playback_available"] is False
