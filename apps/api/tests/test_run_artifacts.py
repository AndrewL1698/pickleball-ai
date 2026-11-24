"""A run's Parquet artifact, written and read back through `Storage`."""

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from sqlalchemy.orm import Session
from tests_support_api import calibrated_match

from pickleball_api.models import ModelRun, ModelRunChunk, ModelRunStage
from pickleball_api.run_artifacts import (
    ArtifactAlreadyWritten,
    ChunkOutOfOrder,
    NoArtifact,
    NotADetectRun,
    RunAlreadyComplete,
    read_detect_chunks,
    read_run_artifact,
    write_detect_chunk,
    write_run_artifact,
)
from pickleball_api.storage import LocalFileStorage


def detections() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "frame": pd.Series([0, 0, 2], dtype="int64"),
            "track_id": pd.Series([7, 9, 7], dtype="int32"),
            "ground_x": [612.5, 1301.0, 615.25],
            "ground_y": [880.0, 402.5, 884.0],
            "confidence": pd.Series([0.91, 0.42, None], dtype="float32"),
            "cut_off": [False, True, False],
        }
    )


def detect_run(session: Session) -> ModelRun:
    match, calibration = calibrated_match(session)
    run = ModelRun.start(
        match,
        ModelRunStage.DETECT,
        calibration=calibration,
        model_version="yolo11m",
        config={"rate_hz": 15},
    )
    session.commit()
    return run


def test_an_artifact_round_trips_a_dataframe(session: Session, storage: LocalFileStorage) -> None:
    run = detect_run(session)
    write_run_artifact(storage, run, detections())
    session.commit()

    session.expire_all()
    stored = session.get(ModelRun, run.id)
    assert stored is not None
    assert_frame_equal(read_run_artifact(storage, stored), detections())


def test_each_run_gets_its_own_key(session: Session, storage: LocalFileStorage) -> None:
    first, second = detect_run(session), detect_run(session)
    write_run_artifact(storage, first, detections())
    write_run_artifact(storage, second, detections().head(1))
    assert first.artifact_storage_key != second.artifact_storage_key
    assert len(read_run_artifact(storage, first)) == 3
    assert len(read_run_artifact(storage, second)) == 1


def test_a_run_artifact_is_never_overwritten(session: Session, storage: LocalFileStorage) -> None:
    run = detect_run(session)
    write_run_artifact(storage, run, detections())
    with pytest.raises(ArtifactAlreadyWritten):
        write_run_artifact(storage, run, detections().head(1))
    assert len(read_run_artifact(storage, run)) == 3


def test_reading_a_run_without_an_artifact_says_so(
    session: Session, storage: LocalFileStorage
) -> None:
    with pytest.raises(NoArtifact):
        read_run_artifact(storage, detect_run(session))


# --- Detect chunks ------------------------------------------------------------


def chunk_rows(frame: int) -> pd.DataFrame:
    return pd.DataFrame(
        {"frame_number": [frame, frame], "track_id": [1, -1], "image_x": [10.0, 900.0]}
    )


def write_chunk(
    storage: LocalFileStorage, run: ModelRun, index: int, *, is_last: bool = False
) -> ModelRunChunk:
    return write_detect_chunk(
        storage,
        run,
        index=index,
        start_seconds=index * 300.0,
        end_seconds=(index + 1) * 300.0,
        frame=chunk_rows(index * 9000),
        is_last=is_last,
    )


def test_finished_chunks_read_back_in_order_as_one_frame(
    session: Session, storage: LocalFileStorage
) -> None:
    run = detect_run(session)
    for index in range(3):
        write_chunk(storage, run, index)
        session.commit()

    session.expire_all()
    stored = session.get(ModelRun, run.id)
    assert stored is not None
    assert [c.index for c in stored.chunks] == [0, 1, 2]
    assert (stored.chunks[1].start_seconds, stored.chunks[1].end_seconds) == (300.0, 600.0)
    assert read_detect_chunks(storage, stored)["frame_number"].tolist() == [
        0, 0, 9000, 9000, 18000, 18000
    ]
    # An unfinished run can be read: Identify reads partial runs.
    assert stored.completed_at is None


def test_the_last_chunk_completes_the_run_without_a_single_artifact(
    session: Session, storage: LocalFileStorage
) -> None:
    run = detect_run(session)
    write_chunk(storage, run, 0)
    last = write_chunk(storage, run, 1, is_last=True)
    session.commit()
    assert run.completed_at == last.completed_at
    assert run.artifact_storage_key is None


def test_chunks_are_saved_in_order_and_once(session: Session, storage: LocalFileStorage) -> None:
    run = detect_run(session)
    with pytest.raises(ChunkOutOfOrder):
        write_chunk(storage, run, 1)
    write_chunk(storage, run, 0)
    with pytest.raises(ChunkOutOfOrder):
        write_chunk(storage, run, 0)
    assert len(run.chunks) == 1


def test_a_complete_run_takes_no_more_chunks(session: Session, storage: LocalFileStorage) -> None:
    run = detect_run(session)
    write_chunk(storage, run, 0, is_last=True)
    with pytest.raises(RunAlreadyComplete):
        write_chunk(storage, run, 1)


def test_only_a_detect_run_has_chunks(session: Session, storage: LocalFileStorage) -> None:
    detect = detect_run(session)
    identify = ModelRun.start(
        detect.match,
        ModelRunStage.IDENTIFY,
        calibration=detect.calibration,
        model_version="identify-1",
        config={},
        parent=detect,
    )
    with pytest.raises(NotADetectRun):
        write_chunk(storage, identify, 0)


def test_a_run_with_no_chunks_reads_as_an_empty_frame(
    session: Session, storage: LocalFileStorage
) -> None:
    assert read_detect_chunks(storage, detect_run(session)).empty
