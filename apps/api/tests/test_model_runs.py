"""Model runs through the ORM: what a run may read, and what a match shows."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pickleball_api.models import Match, ModelRun, ModelRunStage


def detect(match: Match, config: dict[str, object] | None = None) -> ModelRun:
    return ModelRun.start(
        match, ModelRunStage.DETECT, model_version="yolo11m", config=config or {"rate_hz": 15}
    )


def identify(match: Match, parent: ModelRun | None) -> ModelRun:
    return ModelRun.start(
        match, ModelRunStage.IDENTIFY, model_version="identify-1", config={}, parent=parent
    )


def test_a_match_shows_the_identify_run_it_was_pointed_at(session: Session) -> None:
    match = Match(name="Saturday doubles")
    first = identify(match, detect(match))
    match.current_identify_run = first
    session.add(match)
    session.commit()

    # A correction is a new run; the first is kept.
    second = identify(match, first.parent_run)
    match.current_identify_run = second
    session.commit()

    session.expire_all()
    assert match.current_identify_run_id == second.id
    assert [run.stage for run in match.runs] == [
        ModelRunStage.DETECT,
        ModelRunStage.IDENTIFY,
        ModelRunStage.IDENTIFY,
    ]


def test_deleting_a_match_deletes_the_runs_it_shows(session: Session) -> None:
    match = Match(name="Saturday doubles")
    match.current_identify_run = identify(match, detect(match))
    session.add(match)
    session.commit()

    session.delete(match)
    session.commit()
    assert session.scalar(select(func.count()).select_from(ModelRun)) == 0


def test_the_same_configuration_hashes_the_same_whatever_its_key_order() -> None:
    match = Match(name="m")
    one = detect(match, {"rate_hz": 15, "model": {"name": "yolo11m", "conf": 0.25}})
    two = detect(match, {"model": {"conf": 0.25, "name": "yolo11m"}, "rate_hz": 15})
    other = detect(match, {"rate_hz": 10, "model": {"name": "yolo11m", "conf": 0.25}})
    assert one.config_hash == two.config_hash
    assert one.config_hash != other.config_hash
    assert len(one.config_hash) == 64


def test_an_identify_run_must_read_a_detect_run_of_its_own_match() -> None:
    match, elsewhere = Match(name="m"), Match(name="other")
    with pytest.raises(ValueError):
        identify(match, None)
    with pytest.raises(ValueError):
        identify(match, identify(match, detect(match)))
    with pytest.raises(ValueError):
        identify(match, detect(elsewhere))


def test_a_detect_run_reads_no_other_run() -> None:
    match = Match(name="m")
    with pytest.raises(ValueError):
        ModelRun.start(
            match, ModelRunStage.DETECT, model_version="v", config={}, parent=detect(match)
        )


def test_a_match_shows_only_an_identify_run_of_its_own() -> None:
    """The foreign key only proves the run exists. Pointing at another match's
    run would also stop that match from ever being deleted."""
    match, elsewhere = Match(name="m"), Match(name="other")
    with pytest.raises(ValueError):
        match.current_identify_run = identify(elsewhere, detect(elsewhere))
    with pytest.raises(ValueError):
        match.current_identify_run = detect(match)
    match.current_identify_run = None  # showing nothing is always allowed
