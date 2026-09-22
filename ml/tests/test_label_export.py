import pytest

from pickleball_ml.evaluation.label_export import sample_frames


def test_samples_every_nth_processed_frame() -> None:
    processed = list(range(100, 200, 2))  # stride-2 frames, as the tracker writes them
    assert sample_frames(processed, 10) == [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]


def test_never_invents_frames_the_pipeline_did_not_process() -> None:
    processed = [100, 102, 140, 142, 180]
    chosen = sample_frames(processed, 10)
    assert chosen == [100, 140, 180]
    assert set(chosen) <= set(processed)


def test_empty_and_invalid_inputs() -> None:
    assert sample_frames([], 5) == []
    with pytest.raises(ValueError, match="every_frames"):
        sample_frames([1, 2], 0)
