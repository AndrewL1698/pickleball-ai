"""A run's Parquet artifact, written and read back through `Storage`."""

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from sqlalchemy.orm import Session

from pickleball_api.models import Match, ModelRun, ModelRunStage
from pickleball_api.run_artifacts import (
    ArtifactAlreadyWritten,
    NoArtifact,
    read_run_artifact,
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
    match = Match(name="Saturday doubles")
    run = ModelRun.start(
        match, ModelRunStage.DETECT, model_version="yolo11m", config={"rate_hz": 15}
    )
    session.add(match)
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
