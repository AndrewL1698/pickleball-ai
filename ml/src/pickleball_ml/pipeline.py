"""Running experiment stages end to end.

`experiment.py` holds the manifest and the bookkeeping; this module does the
work, importing the heavy CV modules only when a stage actually runs. Each stage
writes its artifact plus a small run record, and is skipped when that record's
fingerprint still matches its inputs and configuration.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from pickleball_ml.experiment import (
    CameraSegment,
    Clip,
    Experiment,
    StageResult,
    StageRunner,
    Window,
    copy_calibration,
    fingerprint,
    to_frame,
    window_frames,
    window_record,
    write_json,
)

Progress = Callable[..., None] | None


def settings_for(config_class: type, settings: dict[str, Any]) -> dict[str, Any]:
    """Manifest entries that are actually fields of a stage config.

    The manifest keeps presentation-only keys (`render_seconds`) and nested
    sections (`identity`, `track`) next to the stage's own settings.
    """
    names = {f.name for f in fields(config_class)}
    return {k: v for k, v in settings.items() if k in names}


def run_clip(
    experiment: Experiment, clip: Clip, runner: StageRunner, stages: set[str],
    drift_step_s: float = 20.0, progress: Progress = None,
) -> dict[str, Any]:
    """Clip-level stages: metadata, calibration per camera segment, camera characteristics."""

    outputs = experiment.clip_outputs(clip.id)
    metadata_path = outputs.metadata
    if "metadata" in stages and not runner.up_to_date(
        f"metadata:{clip.id}", metadata_path, fingerprint(str(clip.video)), [metadata_path]
    ):
        runner.run(f"metadata:{clip.id}", lambda: _write_metadata(clip, metadata_path))
    elif "metadata" in stages:
        runner.skip(f"metadata:{clip.id}")
    metadata: dict[str, Any] = json.loads(metadata_path.read_text())

    for segment in clip.segments:
        if "calibrate" in stages:
            _run_calibrate(experiment, clip, segment, runner)
        if "camera" in stages:
            _run_camera(experiment, clip, segment, metadata, runner, drift_step_s, progress)
    return metadata


def _write_metadata(clip: Clip, path: Path) -> dict[str, Any]:
    from pickleball_ml.video.reader import read_metadata

    metadata = read_metadata(clip.video).to_dict()
    write_json(path, {**metadata, "fingerprint": fingerprint(str(clip.video))})
    return {"fps": metadata["fps"], "frames": metadata["frame_count"]}


def _run_calibrate(
    experiment: Experiment, clip: Clip, segment: CameraSegment, runner: StageRunner
) -> StageResult:
    stage = f"calibrate:{clip.id}:{segment.id}"
    path = experiment.calibration_path(clip.id, segment.id)
    overlay = path.with_name(f"calibration_{segment.id}_overlay.png")
    mark = fingerprint({"points": segment.points.read_text(),
                        "background": segment.background, "video": str(clip.video)})
    if runner.up_to_date(stage, path, mark, [path, overlay]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        import cv2

        from pickleball_ml.court.calibration import Calibration, draw_court_overlay
        from pickleball_ml.court.camera import sample_frames_by_time
        from pickleball_ml.court.click_tool import median_background

        start_s, end_s = segment.background
        samples = 61
        frames = [image for _, image in
                  sample_frames_by_time(clip.video, start_s, end_s, (end_s - start_s) / samples)]
        if not frames:
            raise ValueError(f"no frames in background window {segment.background}")
        image = median_background(frames[:samples])
        points = {name: (float(xy[0]), float(xy[1]))
                  for name, xy in json.loads(segment.points.read_text()).items()}
        calibration = Calibration.fit(
            str(clip.video), to_frame(start_s, 30.0), points,
            reference_image=f"median of {min(samples, len(frames))} frames in "
                            f"[{start_s}, {end_s}) seconds",
        )
        calibration.save(path)
        cv2.imwrite(str(overlay), draw_court_overlay(image, calibration))
        data = json.loads(path.read_text())
        data["fingerprint"] = mark
        data["camera_segment"] = segment.id
        data["points_file"] = str(segment.points)
        write_json(path, data)
        return {"landmarks": len(points),
                "mean_error_ft": round(calibration.mean_error_ft, 3),
                "max_error_ft": round(calibration.max_error_ft, 3)}

    return runner.run(stage, work)


def _run_camera(
    experiment: Experiment, clip: Clip, segment: CameraSegment, metadata: dict[str, Any],
    runner: StageRunner, drift_step_s: float, progress: Progress,
) -> StageResult:
    stage = f"camera:{clip.id}:{segment.id}"
    path = experiment.camera_path(clip.id, segment.id)
    calibration_path = experiment.calibration_path(clip.id, segment.id)
    mark = fingerprint({"calibration": calibration_path.read_text() if
                        calibration_path.exists() else "", "step": drift_step_s,
                        "segment": asdict(segment)})
    if runner.up_to_date(stage, path, mark, [path]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        from pickleball_ml.court.calibration import Calibration
        from pickleball_ml.court.camera import (
            camera_drift,
            camera_placement,
            court_sharpness,
            court_visibility,
            sample_frames_by_time,
            summarize_drift,
        )
        from pickleball_ml.court.click_tool import median_background

        calibration = Calibration.load(calibration_path)
        width, height = int(metadata["width"]), int(metadata["height"])
        end_s = segment.end_s if segment.end_s is not None else float(metadata["duration_seconds"])
        drift = summarize_drift(list(camera_drift(
            sample_frames_by_time(clip.video, segment.start_s, end_s, drift_step_s),
            progress=progress,
        )))
        sharpness_frames = [image for _, image in sample_frames_by_time(
            clip.video, segment.start_s, min(end_s, segment.start_s + 60.0), 6.0)]
        sharpness = [round(court_sharpness(image, calibration), 1) for image in sharpness_frames]
        background = median_background(sharpness_frames) if sharpness_frames else None
        record = {
            "clip": clip.id,
            "camera_segment": segment.id,
            "video": str(clip.video),
            "resolution": [width, height],
            "fps": metadata["fps"],
            "duration_seconds": metadata["duration_seconds"],
            "quality_label": clip.quality,
            "placement": camera_placement(calibration, width, height).to_dict(),
            "visibility": court_visibility(calibration, width, height),
            "calibration": {
                "landmarks": len(calibration.image_points),
                "mean_error_ft": round(calibration.mean_error_ft, 3),
                "max_error_ft": round(calibration.max_error_ft, 3),
            },
            "stability": drift,
            "court_sharpness": {
                "samples": sharpness,
                "median": round(float(sorted(sharpness)[len(sharpness) // 2]), 1)
                if sharpness else None,
                "background_median_sharpness": round(court_sharpness(background, calibration), 1)
                if background is not None else None,
            },
            "fingerprint": mark,
        }
        write_json(path, record)
        return {"median_drift_px": drift["median_drift_px"], "jumps": len(drift["jumps"])}

    return runner.run(stage, work)


def run_window(
    experiment: Experiment, window: Window, runner: StageRunner, stages: set[str],
    metadata: dict[str, Any], progress_factory: Callable[[int, int, int], Any] | None = None,
) -> dict[str, Any]:
    """Every window-level stage, in order, writing `window_run.json` at the end."""

    clip = experiment.clips[window.clip]
    outputs = experiment.outputs(window)
    fps = float(metadata["fps"])
    start_frame, end_frame = window_frames(window, fps)
    calibration_source = experiment.calibration_path(clip.id, window.camera)
    outputs.root.mkdir(parents=True, exist_ok=True)
    if calibration_source.exists():
        copy_calibration(calibration_source, outputs)
    calibration_info = json.loads(outputs.calibration.read_text()) if \
        outputs.calibration.exists() else {}
    stride = window.stride or int(experiment.tracking.get("stride", 2))

    if "track" in stages:
        _run_track(experiment, window, outputs, runner, start_frame, end_frame, stride,
                   clip, metadata, progress_factory)
    if "identify" in stages:
        _run_identify(experiment, window, outputs, runner, fps, stride, metadata)
    if "ball" in stages:
        _run_ball(experiment, window, outputs, runner, fps, clip, progress_factory)
    if "render" in stages:
        _run_render(experiment, window, outputs, runner, clip, metadata, start_frame, end_frame,
                    stride, progress_factory)
    if "ball_render" in stages:
        _run_ball_render(experiment, window, outputs, runner, clip, fps, progress_factory)
    if "evaluate" in stages:
        _run_evaluate(experiment, window, outputs, runner)

    record = window_record(
        experiment, window, metadata, (start_frame, end_frame),
        {
            "source": str(calibration_source),
            "mean_error_ft": calibration_info.get("mean_reprojection_error_ft"),
            "max_error_ft": calibration_info.get("max_reprojection_error_ft"),
            "landmarks": len(calibration_info.get("image_points", {})),
            "camera_segment": window.camera,
        },
        runner.results,
    )
    write_json(outputs.window_record, record)
    return record


def _run_track(
    experiment: Experiment, window: Window, outputs: Any, runner: StageRunner,
    start_frame: int, end_frame: int, stride: int, clip: Clip, metadata: dict[str, Any],
    progress_factory: Callable[[int, int, int], Any] | None,
) -> StageResult:
    stage = "track"
    mark = fingerprint({
        "video": str(clip.video), "start": start_frame, "end": end_frame, "stride": stride,
        "tracking": experiment.tracking,
        "calibration": outputs.calibration.read_text() if outputs.calibration.exists() else "",
    })
    if runner.up_to_date(stage, outputs.detections_run, mark,
                         [outputs.detections_raw, outputs.detections_run]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        from pickleball_ml.court.calibration import Calibration
        from pickleball_ml.players.tracking import TrackingConfig, track_people

        config = TrackingConfig(
            **{k: v for k, v in settings_for(TrackingConfig, experiment.tracking).items()
               if k not in ("stride", "start_frame", "end_frame")},
            stride=stride, start_frame=start_frame, end_frame=end_frame,
        )
        total = (end_frame - start_frame) // stride
        progress = progress_factory(total, start_frame, stride) if progress_factory else None
        detections, run = track_people(clip.video, Calibration.load(outputs.calibration), config,
                                       progress)
        detections.to_parquet(outputs.detections_raw, index=False)
        run["fingerprint"] = mark
        write_json(outputs.detections_run, run)
        return {"detections": len(detections), "frames": run["frames_processed"],
                "frames_per_second": round(run["frames_processed"]
                                           / max(run["elapsed_seconds"], 1e-9), 2)}

    return runner.run(stage, work)


def _run_identify(
    experiment: Experiment, window: Window, outputs: Any, runner: StageRunner, fps: float,
    stride: int, metadata: dict[str, Any],
) -> StageResult:
    stage = "identify"
    if not outputs.detections_run.exists():
        return runner.record(StageResult(stage, "failed", error="no detections to identify"))
    detections_run = json.loads(outputs.detections_run.read_text())
    mark = fingerprint({"detections": detections_run.get("created_at"),
                        "switches": list(window.end_switches_s),
                        "identity": experiment.tracking.get("identity", {})})
    if runner.up_to_date(stage, outputs.players_run, mark,
                         [outputs.players_court, outputs.players_run]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        import pandas as pd

        from pickleball_ml.players.identity import IdentityConfig, resolve_identities, run_record

        config = IdentityConfig(
            **settings_for(IdentityConfig, experiment.tracking.get("identity", {})))
        switches = [to_frame(s, fps) for s in window.end_switches_s]
        players, diagnostics = resolve_identities(
            pd.read_parquet(outputs.detections_raw), fps, stride, int(metadata["height"]),
            config, end_switch_frames=switches,
        )
        players.to_parquet(outputs.players_court, index=False)
        record = run_record(config, players, diagnostics, detections_run["frames_processed"])
        record["end_switch_frames"] = switches
        record["fingerprint"] = mark
        write_json(outputs.players_run, record)
        return {"frames_with_4_players_rate": record["frames_with_4_players_rate"],
                "rows": record["rows"], "tracklets": record["tracklets"]}

    return runner.run(stage, work)


def _run_ball(
    experiment: Experiment, window: Window, outputs: Any, runner: StageRunner, fps: float,
    clip: Clip, progress_factory: Callable[[int, int, int], Any] | None,
) -> StageResult:
    stage = "ball"
    if window.ball is None:
        return runner.skip(stage, {"reason": "no ball range in the manifest"})
    settings = dict(experiment.ball)
    detector_settings = {k: v for k, v in settings.items() if k not in ("track", "stride")}
    track_settings = dict(settings.get("track", {}))
    start_frame = to_frame(window.ball.start_s, fps)
    end_frame = to_frame(window.ball.end_s, fps)
    gate_settings = dict(settings.get("gate", {}))
    mark = fingerprint({"video": str(clip.video), "start": start_frame, "end": end_frame,
                        "detector": detector_settings, "track": track_settings,
                        "gate": gate_settings, "stride": settings.get("stride"),
                        "calibration": outputs.calibration.read_text()
                        if outputs.calibration.exists() else ""})
    if runner.up_to_date(stage, outputs.ball_run, mark,
                         [outputs.ball_raw, outputs.ball_track, outputs.ball_run]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        from pickleball_ml.ball.court_gate import BallGate, gate_polygon, inside
        from pickleball_ml.ball.detector import (
            BallDetectorConfig,
            detect_ball,
            processed_frames,
            stride_for_target_fps,
        )
        from pickleball_ml.ball.track import BallTrackConfig, run_record, select_track
        from pickleball_ml.court.calibration import Calibration

        detector_config = BallDetectorConfig(
            **settings_for(BallDetectorConfig, detector_settings))
        stride = int(settings.get("stride") or
                     stride_for_target_fps(fps, detector_config.target_fps))
        total = (end_frame - start_frame) // stride
        progress = progress_factory(total, start_frame, stride) if progress_factory else None
        candidates, detector_run = detect_ball(clip.video, detector_config, start_frame,
                                               end_frame, stride, progress)
        gate = BallGate(**settings_for(BallGate, gate_settings))
        polygon = gate_polygon(Calibration.load(outputs.calibration), gate)
        candidates["in_gate"] = inside(polygon, candidates[["x", "y"]].to_numpy(dtype=float))
        candidates.to_parquet(outputs.ball_raw, index=False)
        frames, timestamps = processed_frames(candidates)
        track_config = BallTrackConfig(**settings_for(BallTrackConfig, track_settings))
        track = select_track(candidates, frames, timestamps, fps / stride, track_config)
        track.to_parquet(outputs.ball_track, index=False)
        record = run_record(track_config, detector_run, track, candidates)
        record["gate"] = {**asdict(gate), "polygon": [[round(v, 1) for v in point]
                                                      for point in polygon.tolist()]}
        record["stride"] = stride
        record["effective_fps"] = round(fps / stride, 2)
        record["fingerprint"] = mark
        write_json(outputs.ball_run, record)
        return {"coverage": record["coverage"], "frames": record["frames"],
                "frames_per_second": detector_run["frames_per_second"]}

    return runner.run(stage, work)


def _run_render(
    experiment: Experiment, window: Window, outputs: Any, runner: StageRunner, clip: Clip,
    metadata: dict[str, Any], start_frame: int, end_frame: int, stride: int,
    progress_factory: Callable[[int, int, int], Any] | None,
) -> StageResult:
    stage = "render"
    if not outputs.players_run.exists():
        return runner.record(StageResult(stage, "failed", error="no players to render"))
    players_run = json.loads(outputs.players_run.read_text())
    seconds = float(experiment.tracking.get("render_seconds", 60.0))
    mark = fingerprint({"players": players_run.get("fingerprint"), "seconds": seconds})
    if runner.up_to_date(stage, outputs.root / "render.run.json", mark, [outputs.topdown_video]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        import pandas as pd

        from pickleball_ml.court.calibration import Calibration
        from pickleball_ml.render.topdown import render_topdown_video

        fps = float(metadata["fps"])
        limit = min(end_frame, start_frame + to_frame(seconds, fps))
        total = (limit - start_frame) // stride
        progress = progress_factory(total, start_frame, stride) if progress_factory else None
        render_topdown_video(
            clip.video, outputs.topdown_video, Calibration.load(outputs.calibration),
            raw=pd.read_parquet(outputs.detections_raw),
            players=pd.read_parquet(outputs.players_court),
            start_frame=start_frame, end_frame=limit, stride=stride, fps=fps, progress=progress,
        )
        write_json(outputs.root / "render.run.json",
                   {"stage": "render", "seconds": seconds, "fingerprint": mark,
                    "output": str(outputs.topdown_video)})
        return {"seconds": seconds, "output": str(outputs.topdown_video)}

    return runner.run(stage, work)


def _run_ball_render(
    experiment: Experiment, window: Window, outputs: Any, runner: StageRunner, clip: Clip,
    fps: float, progress_factory: Callable[[int, int, int], Any] | None,
) -> StageResult:
    stage = "ball_render"
    if not outputs.ball_run.exists():
        return runner.skip(stage, {"reason": "no ball track"})
    ball_run = json.loads(outputs.ball_run.read_text())
    seconds = float(experiment.ball.get("render_seconds", 30.0))
    mark = fingerprint({"ball": ball_run.get("fingerprint"), "seconds": seconds})
    if runner.up_to_date(stage, outputs.root / "ball_render.run.json", mark,
                         [outputs.ball_video]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        import pandas as pd

        from pickleball_ml.ball.render import render_ball_video

        assert window.ball is not None
        stride = int(ball_run["stride"])
        start_frame = to_frame(window.ball.start_s, fps)
        end_frame = min(to_frame(window.ball.end_s, fps),
                        start_frame + to_frame(seconds, fps))
        total = (end_frame - start_frame) // stride
        progress = progress_factory(total, start_frame, stride) if progress_factory else None
        render_ball_video(
            clip.video, outputs.ball_video, pd.read_parquet(outputs.ball_track),
            pd.read_parquet(outputs.ball_raw), start_frame, end_frame, stride, fps,
            progress=progress,
        )
        write_json(outputs.root / "ball_render.run.json",
                   {"stage": "ball_render", "seconds": seconds, "fingerprint": mark,
                    "output": str(outputs.ball_video)})
        return {"seconds": seconds, "output": str(outputs.ball_video)}

    return runner.run(stage, work)


def _run_evaluate(
    experiment: Experiment, window: Window, outputs: Any, runner: StageRunner
) -> StageResult:
    stage = "evaluate"
    labels = [p for p in (window.player_labels, window.ball_labels) if p is not None]
    if not labels:
        return runner.skip(stage, {"reason": "no label files"})
    mark = fingerprint([p.read_text() if p.exists() else "" for p in labels])
    path = outputs.root / "evaluation.json"
    if runner.up_to_date(stage, path, mark, [path]):
        return runner.skip(stage)

    def work() -> dict[str, Any]:
        import pandas as pd

        report: dict[str, Any] = {"window": window.id, "split": window.split,
                                  "fingerprint": mark}
        if window.player_labels is not None and window.player_labels.exists():
            from pickleball_ml.evaluation.players import evaluate_players, load_player_labels

            header, player_labels, frames = load_player_labels(window.player_labels)
            players = pd.read_parquet(outputs.players_court)
            report["players"] = {
                "labels": str(window.player_labels),
                "annotator": header.get("annotator", ""),
                "metrics": evaluate_players(players, player_labels, frames),
            }
        if window.ball_labels is not None and window.ball_labels.exists():
            from pickleball_ml.evaluation.ball import evaluate_ball, load_ball_labels

            header, ball_labels = load_ball_labels(window.ball_labels)
            track = pd.read_parquet(outputs.ball_track)
            report["ball"] = {
                "labels": str(window.ball_labels),
                "annotator": header.get("annotator", ""),
                "metrics": evaluate_ball(track, ball_labels),
            }
        write_json(path, report)
        summary: dict[str, Any] = {}
        if "players" in report:
            everything = report["players"]["metrics"]["all"]
            summary["player_coverage"] = everything["coverage"]
            summary["identity_accuracy"] = everything["identity_accuracy_when_detected"]
            summary["id_switches"] = everything["id_switches"]
        if "ball" in report:
            summary["ball_coverage"] = report["ball"]["metrics"]["all"]["coverage"]
            summary["ball_median_error_px"] = report["ball"]["metrics"]["all"]["median_error_px"]
        return summary

    return runner.run(stage, work)
