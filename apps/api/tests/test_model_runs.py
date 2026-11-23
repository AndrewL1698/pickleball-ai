"""Model runs through the ORM: what a run may read, and what a match shows."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests_support_api import calibrated_match, recalibrate

from pickleball_api.models import CourtCalibration, Match, ModelRun, ModelRunStage


def detect(
    match: Match,
    config: dict[str, object] | None = None,
    calibration: CourtCalibration | None = None,
) -> ModelRun:
    return ModelRun.start(
        match,
        ModelRunStage.DETECT,
        calibration=calibration or match.calibrations[-1],
        model_version="yolo11m",
        config=config or {"rate_hz": 15},
    )


def identify(
    match: Match, parent: ModelRun | None, calibration: CourtCalibration | None = None
) -> ModelRun:
    return ModelRun.start(
        match,
        ModelRunStage.IDENTIFY,
        calibration=calibration or match.calibrations[-1],
        model_version="identify-1",
        config={},
        parent=parent,
    )


def test_a_match_shows_the_identify_run_it_was_pointed_at(session: Session) -> None:
    match, _ = calibrated_match(session)
    first = identify(match, detect(match))
    match.current_identify_run = first
    session.commit()

    # A recalibration is a new Identify run over the same Detect run; the
    # first is kept.
    second = identify(match, first.parent_run, recalibrate(session, match))
    match.current_identify_run = second
    session.commit()

    session.expire_all()
    assert match.current_identify_run_id == second.id
    assert second.calibration is not first.calibration
    assert [run.stage for run in match.runs] == [
        ModelRunStage.DETECT,
        ModelRunStage.IDENTIFY,
        ModelRunStage.IDENTIFY,
    ]


def test_deleting_a_match_deletes_its_runs_and_calibrations(session: Session) -> None:
    match, _ = calibrated_match(session)
    match.current_identify_run = identify(match, detect(match))
    session.commit()

    session.delete(match)
    session.commit()
    assert session.scalar(select(func.count()).select_from(ModelRun)) == 0
    assert session.scalar(select(func.count()).select_from(CourtCalibration)) == 0


def test_the_same_configuration_hashes_the_same_whatever_its_key_order(
    session: Session,
) -> None:
    match, _ = calibrated_match(session)
    one = detect(match, {"rate_hz": 15, "model": {"name": "yolo11m", "conf": 0.25}})
    two = detect(match, {"model": {"conf": 0.25, "name": "yolo11m"}, "rate_hz": 15})
    other = detect(match, {"rate_hz": 10, "model": {"name": "yolo11m", "conf": 0.25}})
    assert one.config_hash == two.config_hash
    assert one.config_hash != other.config_hash
    assert len(one.config_hash) == 64


def test_runs_that_differ_only_in_calibration_hash_differently(session: Session) -> None:
    """Otherwise a run made under an old calibration looks reusable after a
    recalibration."""
    match, old = calibrated_match(session)
    new = recalibrate(session, match)
    before = identify(match, detect(match), old)
    after = identify(match, before.parent_run, new)
    assert before.config_json == after.config_json
    assert before.config_hash != after.config_hash


def test_a_run_uses_a_calibration_of_its_own_match(session: Session) -> None:
    match, _ = calibrated_match(session)
    _, elsewhere = calibrated_match(session, name="other")
    with pytest.raises(ValueError):
        detect(match, calibration=elsewhere)
    with pytest.raises(ValueError):
        identify(match, detect(match), elsewhere)


def test_a_run_cannot_hash_a_calibration_that_is_not_saved(session: Session) -> None:
    match, saved = calibrated_match(session)
    unsaved = CourtCalibration(reference_frame_id=saved.reference_frame_id)
    match.calibrations.append(unsaved)
    with pytest.raises(ValueError):
        detect(match, calibration=unsaved)


def test_an_identify_run_must_read_a_detect_run_of_its_own_match(session: Session) -> None:
    match, _ = calibrated_match(session)
    elsewhere, _ = calibrated_match(session, name="other")
    with pytest.raises(ValueError):
        identify(match, None)
    with pytest.raises(ValueError):
        identify(match, identify(match, detect(match)))
    with pytest.raises(ValueError):
        identify(match, detect(elsewhere))


def test_a_detect_run_reads_no_other_run(session: Session) -> None:
    match, calibration = calibrated_match(session)
    with pytest.raises(ValueError):
        ModelRun.start(
            match,
            ModelRunStage.DETECT,
            calibration=calibration,
            model_version="v",
            config={},
            parent=detect(match),
        )


def test_a_match_shows_only_an_identify_run_of_its_own(session: Session) -> None:
    """The foreign key only proves the run exists. Pointing at another match's
    run would also stop that match from ever being deleted."""
    match, _ = calibrated_match(session)
    elsewhere, _ = calibrated_match(session, name="other")
    with pytest.raises(ValueError):
        match.current_identify_run = identify(elsewhere, detect(elsewhere))
    with pytest.raises(ValueError):
        match.current_identify_run = detect(match)
    match.current_identify_run = None  # showing nothing is always allowed
