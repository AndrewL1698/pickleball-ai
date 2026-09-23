import json
from pathlib import Path

import pytest

from pickleball_ml.experiment import (
    StageRunner,
    fingerprint,
    load_experiment,
    smoke_window,
    to_frame,
    window_frames,
)

MANIFEST = {
    "name": "t",
    "tracking": {"stride": 2},
    "clips": {
        "c": {
            "video": "data/raw/c.mp4",
            "segments": [
                {"id": "cam1", "points": "points.json", "background": [0, 10], "start_s": 0,
                 "end_s": 100},
                {"id": "cam2", "points": "points.json", "background": [110, 120],
                 "start_s": 105, "end_s": 300},
            ],
        }
    },
    "windows": [
        {"id": "w1", "clip": "c", "camera": "cam1", "start_s": 10, "end_s": 40,
         "split": "tune", "ball": {"start_s": 20, "end_s": 30}},
        {"id": "w2", "clip": "c", "camera": "cam2", "start_s": 120, "end_s": 200,
         "split": "test", "end_switches_s": [160]},
    ],
}


def write(tmp_path: Path, manifest: dict[str, object]) -> Path:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest))
    (tmp_path / "points.json").write_text("{}")
    return path


def test_loads_clips_windows_and_output_layout(tmp_path: Path) -> None:
    experiment = load_experiment(write(tmp_path, MANIFEST))
    assert [w.id for w in experiment.windows] == ["w1", "w2"]
    assert experiment.window("w2").end_switches_s == (160.0,)
    first, second = experiment.windows
    assert experiment.outputs(first).root != experiment.outputs(second).root
    assert experiment.outputs(first).root.name == "w1"
    assert experiment.calibration_path("c", "cam1").name == "calibration_cam1.json"


def test_window_outside_its_camera_segment_is_rejected(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["windows"][0]["end_s"] = 150  # crosses the camera move
    with pytest.raises(ValueError, match="outside camera segment"):
        load_experiment(write(tmp_path, manifest))


def test_duplicate_window_ids_are_rejected(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["windows"][1]["id"] = "w1"
    with pytest.raises(ValueError, match="duplicate window id"):
        load_experiment(write(tmp_path, manifest))


def test_end_switch_and_ball_range_must_be_inside_the_window(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["windows"][1]["end_switches_s"] = [900]
    with pytest.raises(ValueError, match="end switch"):
        load_experiment(write(tmp_path, manifest))
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["windows"][0]["ball"] = {"start_s": 5, "end_s": 30}
    with pytest.raises(ValueError, match="ball range"):
        load_experiment(write(tmp_path, manifest))


def test_frame_conversion_uses_the_source_frame_rate() -> None:
    manifest_fps = 59.94
    assert to_frame(10.0, 30.0) == 300
    window = type("W", (), {"start_s": 60.0, "end_s": 120.0})()
    assert window_frames(window, manifest_fps) == (3596, 7193)  # type: ignore[arg-type]


def test_smoke_window_shortens_everything_and_writes_elsewhere(tmp_path: Path) -> None:
    experiment = load_experiment(write(tmp_path, MANIFEST))
    smoke = smoke_window(experiment.window("w2"), 20.0)
    assert smoke.id == "w2_smoke"
    assert (smoke.start_s, smoke.end_s) == (120.0, 140.0)
    assert smoke.end_switches_s == ()  # the switch is past the shortened end
    assert smoke.player_labels is None


def test_stage_is_skipped_only_when_output_and_fingerprint_match(tmp_path: Path) -> None:
    marker = tmp_path / "run.json"
    artifact = tmp_path / "out.parquet"
    runner = StageRunner()
    assert not runner.up_to_date("track", marker, "abc", [artifact])
    marker.write_text(json.dumps({"fingerprint": "abc"}))
    assert not runner.up_to_date("track", marker, "abc", [artifact])  # artifact missing
    artifact.write_text("x")
    assert runner.up_to_date("track", marker, "abc", [artifact])
    assert not runner.up_to_date("track", marker, "different", [artifact])
    assert not StageRunner(force=["track"]).up_to_date("track", marker, "abc", [artifact])
    assert not StageRunner(force=["all"]).up_to_date("track", marker, "abc", [artifact])


def test_fingerprint_is_stable_and_order_independent() -> None:
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_failing_stage_is_recorded_not_raised() -> None:
    runner = StageRunner()

    def boom() -> dict[str, object]:
        raise ValueError("nope")

    result = runner.run("track", boom)
    assert result.status == "failed"
    assert "nope" in result.error
    assert runner.results == [result]
