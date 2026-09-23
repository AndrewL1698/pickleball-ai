"""`pbml` command line.

Single-video stages, reading the previous stage's output from
data/processed/<video_name>/:

    pbml metadata  VIDEO
    pbml calibrate VIDEO [--time S | --background START END] [--points FILE]
    pbml track     VIDEO --start S --end S
    pbml identify  VIDEO [--end-switch S ...]
    pbml render    VIDEO
    pbml evaluate  VIDEO --labels FILE

Multi-clip, multi-window experiments (Phase 0b), reading a manifest and writing
data/processed/<clip>/<window>/:

    pbml experiment run     MANIFEST [--window ID ...] [--stage S ...] [--smoke S]
    pbml experiment summary MANIFEST

Labeling helpers, which export sampled frames and a template to fill in:

    pbml label-players MANIFEST --window ID [--every-seconds S]
    pbml label-ball    MANIFEST --window ID [--every-frames N] [--export]
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

from pickleball_ml.court.calibration import Calibration, draw_court_overlay
from pickleball_ml.court.click_tool import collect_landmarks, median_background
from pickleball_ml.evaluation.identity import evaluate_identity
from pickleball_ml.experiment import (
    ALL_STAGES,
    CLIP_STAGES,
    WINDOW_STAGES,
    StageRunner,
    load_experiment,
    smoke_window,
    write_json,
)
from pickleball_ml.paths import DEFAULT_PROCESSED_ROOT, StageOutputs
from pickleball_ml.players.identity import IdentityConfig, resolve_identities, run_record
from pickleball_ml.players.tracking import TrackingConfig, track_people
from pickleball_ml.render.topdown import render_topdown_video
from pickleball_ml.video.reader import Frame, iter_frames, read_frame, read_metadata

BACKGROUND_SAMPLES = 61


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pbml", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--processed-root", type=Path, default=DEFAULT_PROCESSED_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("metadata", help="extract video metadata")
    p.add_argument("video", type=Path)
    p.set_defaults(handler=cmd_metadata)

    p = commands.add_parser("calibrate", help="manual court calibration")
    p.add_argument("video", type=Path)
    source = p.add_mutually_exclusive_group()
    source.add_argument("--time", type=float, default=0.0,
                        help="calibrate on the frame at S seconds")
    source.add_argument("--background", type=float, nargs=2, metavar=("START", "END"),
                        help="calibrate on the median of frames in [START, END] seconds "
                             "(removes moving players)")
    p.add_argument("--points", type=Path,
                   help="JSON of {landmark_name: [x, y]} instead of clicking interactively")
    p.set_defaults(handler=cmd_calibrate)

    p = commands.add_parser("track", help="detect people, gate by court position, and track")
    p.add_argument("video", type=Path)
    p.add_argument("--start", type=float, default=0.0, help="window start, seconds")
    p.add_argument("--end", type=float, help="window end, seconds (default: end of video)")
    defaults = TrackingConfig()
    p.add_argument("--stride", type=int, default=defaults.stride)
    p.add_argument("--model", default=defaults.model)
    p.add_argument("--imgsz", type=int, default=defaults.imgsz)
    p.add_argument("--conf", type=float, default=defaults.conf)
    p.add_argument("--device", default=defaults.device)
    p.set_defaults(handler=cmd_track)

    p = commands.add_parser("identify", help="link track fragments into four player identities")
    p.add_argument("video", type=Path)
    p.add_argument("--end-switch", type=float, action="append", default=[], metavar="SECONDS",
                   help="time at which the teams have changed ends (repeatable)")
    p.set_defaults(handler=cmd_identify)

    p = commands.add_parser("render", help="render side-by-side top-down debug video")
    p.add_argument("video", type=Path)
    p.add_argument("--max-seconds", type=float,
                   help="only render the first S seconds of the window")
    p.set_defaults(handler=cmd_render)

    p = commands.add_parser("evaluate", help="identity accuracy against labeled person boxes")
    p.add_argument("video", type=Path)
    p.add_argument("--labels", type=Path, required=True,
                   help="identity_samples.json (see data/annotations)")
    p.add_argument("--split", help="only samples from this split")
    p.set_defaults(handler=cmd_evaluate)

    p = commands.add_parser("experiment", help="run or summarize a multi-window experiment")
    actions = p.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", help="run experiment stages")
    run.add_argument("manifest", type=Path)
    run.add_argument("--window", action="append", default=[], help="only these windows")
    run.add_argument("--clip", action="append", default=[], help="only these clips")
    run.add_argument("--stage", action="append", default=[], choices=list(ALL_STAGES),
                     help="only these stages (default: all)")
    run.add_argument("--force", action="append", default=[],
                     help="rerun these stages even when up to date ('all' for everything)")
    run.add_argument("--smoke", type=float, metavar="SECONDS",
                     help="process only the first S seconds of each window, into <window>_smoke")
    run.add_argument("--drift-step", type=float, default=20.0,
                     help="seconds between camera-stability samples")
    run.set_defaults(handler=cmd_experiment_run)
    summary = actions.add_parser("summary", help="collect window records and metrics")
    summary.add_argument("manifest", type=Path)
    summary.set_defaults(handler=cmd_experiment_summary)

    p = commands.add_parser("label-players", help="export frames for player identity labeling")
    p.add_argument("manifest", type=Path)
    p.add_argument("--window", required=True)
    p.add_argument("--every-seconds", type=float, default=5.0)
    p.add_argument("--out", type=Path, help="output directory (default: alongside the window)")
    p.set_defaults(handler=cmd_label_players)

    p = commands.add_parser("label-ball", help="label ball centres, interactively or by export")
    p.add_argument("manifest", type=Path)
    p.add_argument("--window", required=True)
    p.add_argument("--every-frames", type=int, default=6,
                   help="label every Nth processed frame")
    p.add_argument("--export", action="store_true",
                   help="write frames and a template instead of opening the click tool")
    p.add_argument("--out", type=Path)
    p.add_argument("--annotator", default="")
    p.set_defaults(handler=cmd_label_ball)

    args = parser.parse_args(argv)
    if getattr(args, "video", None) is None:
        try:
            result_no_video: int = args.handler(args, None)
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return result_no_video
    outputs = StageOutputs.for_video(args.video, args.processed_root)
    try:
        result: int = args.handler(args, outputs)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return result


def cmd_metadata(args: argparse.Namespace, outputs: StageOutputs) -> int:
    metadata = read_metadata(args.video)
    _write_json(outputs.metadata, metadata.to_dict())
    print(json.dumps(metadata.to_dict(), indent=2))
    return 0


def cmd_calibrate(args: argparse.Namespace, outputs: StageOutputs) -> int:
    metadata = read_metadata(args.video)
    if args.background:
        start, end = (_to_frame(s, metadata.fps) for s in args.background)
        image = _background(args.video, start, end)
        frame_number = start
        reference = f"median of {BACKGROUND_SAMPLES} frames in [{start}, {end})"
    else:
        frame_number = _to_frame(args.time, metadata.fps)
        image = read_frame(args.video, frame_number)
        reference = "frame"

    if args.points:
        clicked = json.loads(args.points.read_text())
        points = {name: (float(xy[0]), float(xy[1])) for name, xy in clicked.items()}
    else:
        collected = collect_landmarks(image)
        if collected is None:
            print("calibration aborted")
            return 1
        points = collected

    calibration = Calibration.fit(str(args.video), frame_number, points, reference_image=reference)
    calibration.save(outputs.calibration)
    cv2.imwrite(str(outputs.calibration_overlay), draw_court_overlay(image, calibration))
    print(f"landmarks: {len(calibration.image_points)}")
    for name, error in calibration.reprojection_error_ft.items():
        print(f"  {name:28s} {error:.3f} ft")
    print(f"mean reprojection error: {calibration.mean_error_ft:.3f} ft "
          f"(max {calibration.max_error_ft:.3f} ft)")
    print(f"wrote {outputs.calibration} and {outputs.calibration_overlay}")
    return 0


def cmd_track(args: argparse.Namespace, outputs: StageOutputs) -> int:
    metadata = read_metadata(args.video)
    calibration = Calibration.load(outputs.calibration)
    config = TrackingConfig(
        model=args.model, imgsz=args.imgsz, conf=args.conf, device=args.device, stride=args.stride,
        start_frame=_to_frame(args.start, metadata.fps),
        end_frame=_to_frame(args.end, metadata.fps) if args.end is not None else None,
    )
    total = ((config.end_frame or metadata.frame_count) - config.start_frame) // config.stride
    detections, run = track_people(args.video, calibration, config,
                                   _progress_printer(total, config))
    outputs.root.mkdir(parents=True, exist_ok=True)
    detections.to_parquet(outputs.detections_raw, index=False)
    _write_json(outputs.detections_run, run)
    print(f"\n{len(detections)} detections over {run['frames_processed']} frames "
          f"in {run['elapsed_seconds']}s -> {outputs.detections_raw}")
    return 0


def cmd_identify(args: argparse.Namespace, outputs: StageOutputs) -> int:
    metadata = read_metadata(args.video)
    raw = pd.read_parquet(outputs.detections_raw)
    raw_run = json.loads(outputs.detections_run.read_text())
    config = IdentityConfig()
    switches = [_to_frame(s, metadata.fps) for s in args.end_switch]
    players, diagnostics = resolve_identities(
        raw, metadata.fps, raw_run["config"]["stride"], metadata.height, config,
        end_switch_frames=switches,
    )
    players.to_parquet(outputs.players_court, index=False)
    record = run_record(config, players, diagnostics, raw_run["frames_processed"])
    record["source_run_created_at"] = raw_run["created_at"]
    record["end_switch_frames"] = switches
    _write_json(outputs.players_run, record)
    print(json.dumps(record, indent=2))
    return 0


def cmd_evaluate(args: argparse.Namespace, outputs: StageOutputs) -> int:
    samples = json.loads(args.labels.read_text())["samples"]
    players = pd.read_parquet(outputs.players_court)
    frames = set(players["frame_number"])
    in_window = [s for s in samples if s["frame_number"] in frames]
    print(json.dumps(evaluate_identity(players, in_window, args.split), indent=2))
    return 0


def cmd_render(args: argparse.Namespace, outputs: StageOutputs) -> int:
    metadata = read_metadata(args.video)
    calibration = Calibration.load(outputs.calibration)
    raw_run = json.loads(outputs.detections_run.read_text())
    config = raw_run["config"]
    start, end, stride = config["start_frame"], config["end_frame"], config["stride"]
    if args.max_seconds is not None:
        limit = start + _to_frame(args.max_seconds, metadata.fps)
        end = limit if end is None else min(end, limit)
    total = ((end or metadata.frame_count) - start) // stride
    render_topdown_video(
        args.video, outputs.topdown_video, calibration,
        raw=pd.read_parquet(outputs.detections_raw),
        players=pd.read_parquet(outputs.players_court),
        start_frame=start, end_frame=end, stride=stride, fps=metadata.fps,
        progress=_progress_printer(total, TrackingConfig(start_frame=start, stride=stride)),
    )
    print(f"\nwrote {outputs.topdown_video}")
    return 0


def cmd_experiment_run(args: argparse.Namespace, _outputs: object) -> int:
    from pickleball_ml.pipeline import run_clip, run_window

    experiment = load_experiment(args.manifest)
    stages = set(args.stage) if args.stage else set(ALL_STAGES)
    windows = [w for w in experiment.windows
               if (not args.window or w.id in args.window)
               and (not args.clip or w.clip in args.clip)]
    if args.window and len(windows) != len(set(args.window)):
        missing = set(args.window) - {w.id for w in windows}
        raise ValueError(f"unknown windows: {sorted(missing)}")
    clips = [experiment.clips[c] for c in dict.fromkeys(w.clip for w in windows)]

    metadata: dict[str, dict[str, Any]] = {}
    for clip in clips:
        print(f"\n== clip {clip.id}: {clip.video.name}")
        runner = StageRunner(force=args.force)
        metadata[clip.id] = run_clip(experiment, clip, runner, stages & set(CLIP_STAGES),
                                     drift_step_s=args.drift_step,
                                     progress=_counter(f"  {clip.id} camera"))
        for result in runner.results:
            _print_stage(result)

    window_stages = stages & set(WINDOW_STAGES)
    if not window_stages:
        return 0
    failures = 0
    for window in windows:
        target = smoke_window(window, args.smoke) if args.smoke else window
        print(f"\n== window {target.id} ({target.clip} {target.start_s:.0f}-{target.end_s:.0f}s, "
              f"{target.split})")
        runner = StageRunner(force=args.force)
        record = run_window(experiment, target, runner, window_stages, metadata[window.clip],
                            progress_factory=_stage_progress)
        for result in runner.results:
            _print_stage(result)
        failures += record["status"] == "failed"
    print(f"\n{len(windows) - failures}/{len(windows)} windows ok")
    return 1 if failures else 0


def cmd_experiment_summary(args: argparse.Namespace, _outputs: object) -> int:
    experiment = load_experiment(args.manifest)
    windows = []
    for window in experiment.windows:
        outputs = experiment.outputs(window)
        if not outputs.window_record.exists():
            continue
        record = json.loads(outputs.window_record.read_text())
        entry: dict[str, Any] = {
            "window": window.id, "clip": window.clip, "split": window.split,
            "camera": window.camera, "purpose": window.purpose,
            "frames": record["frames"], "status": record["status"],
            "stages": {s["stage"]: {"status": s["status"], "seconds": s["seconds"],
                                    **({"detail": s["detail"]} if s.get("detail") else {})}
                       for s in record["stages"]},
            "calibration": record["calibration"],
        }
        if outputs.players_run.exists():
            players = json.loads(outputs.players_run.read_text())
            entry["players"] = {k: players.get(k) for k in
                                ("frames", "frames_with_4_players_rate", "tracklets",
                                 "low_confidence_identity_rate", "end_switch_frames")}
        if outputs.ball_run.exists():
            ball = json.loads(outputs.ball_run.read_text())
            entry["ball"] = {k: ball.get(k) for k in
                             ("frames", "frames_with_ball", "coverage", "median_score",
                              "longest_gap_frames", "effective_fps")}
            entry["ball"]["detector"] = ball["detector_run"]["model_name"]
        evaluation = outputs.root / "evaluation.json"
        if evaluation.exists():
            entry["evaluation"] = json.loads(evaluation.read_text())
        windows.append(entry)

    cameras = {}
    for clip in experiment.clips.values():
        for segment in clip.segments:
            path = experiment.camera_path(clip.id, segment.id)
            if path.exists():
                cameras[f"{clip.id}:{segment.id}"] = json.loads(path.read_text())
    summary = {"experiment": experiment.name, "description": experiment.description,
               "cameras": cameras, "windows": windows,
               "totals": _totals(windows)}
    write_json(experiment.summary_path(), summary)
    print(json.dumps(summary["totals"], indent=2))
    print(f"wrote {experiment.summary_path()}")
    return 0


def _totals(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Label counts summed over windows, per split: metrics pooled, not averaged."""
    totals: dict[str, Any] = {"windows": len(windows),
                              "failed": sum(w["status"] == "failed" for w in windows)}
    for split in ("tune", "test"):
        counters = {"labeled_frames": 0, "visible_player_labels": 0, "detected": 0,
                    "correct_identity": 0, "wrong_identity": 0, "missed": 0,
                    "id_switches": 0, "id_switch_opportunities": 0,
                    "frames_with_4_visible": 0, "frames_all_4_correct": 0,
                    "extra_predictions": 0}
        ball = {"visible_labels": 0, "reported_on_visible": 0, "absent_labels": 0,
                "reported_on_absent": 0, "hits_10px": 0, "hits_20px": 0}
        for window in windows:
            evaluation = window.get("evaluation", {})
            players = evaluation.get("players", {}).get("metrics", {}).get(split)
            if players:
                for key in counters:
                    counters[key] += players.get(key) or 0
            ball_metrics = evaluation.get("ball", {}).get("metrics", {}).get(split)
            if ball_metrics:
                for key in ("visible_labels", "reported_on_visible", "absent_labels",
                            "reported_on_absent"):
                    ball[key] += ball_metrics.get(key) or 0
                ball["hits_10px"] += ball_metrics["at_tolerance"].get("10px", {}).get("hits", 0)
                ball["hits_20px"] += ball_metrics["at_tolerance"].get("20px", {}).get("hits", 0)
        totals[split] = {
            "players": {
                **counters,
                "coverage": _ratio(counters["detected"], counters["visible_player_labels"]),
                "identity_accuracy_when_detected": _ratio(counters["correct_identity"],
                                                          counters["detected"]),
                "id_switch_rate": _ratio(counters["id_switches"],
                                         counters["id_switch_opportunities"]),
                "frames_all_4_correct_rate": _ratio(counters["frames_all_4_correct"],
                                                    counters["frames_with_4_visible"]),
            },
            "ball": {
                **ball,
                "coverage": _ratio(ball["reported_on_visible"], ball["visible_labels"]),
                "recall_10px": _ratio(ball["hits_10px"], ball["visible_labels"]),
                "recall_20px": _ratio(ball["hits_20px"], ball["visible_labels"]),
                "precision_20px": _ratio(ball["hits_20px"], ball["reported_on_visible"]
                                         + ball["reported_on_absent"]),
            },
        }
    return totals


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def cmd_label_players(args: argparse.Namespace, _outputs: object) -> int:
    import pandas as pd

    from pickleball_ml.evaluation.label_export import export_player_frames, sample_frames

    experiment = load_experiment(args.manifest)
    window = experiment.window(args.window)
    outputs = experiment.outputs(window)
    metadata = json.loads(experiment.clip_outputs(window.clip).metadata.read_text())
    detections = pd.read_parquet(outputs.detections_raw)
    every = max(1, int(round(args.every_seconds * float(metadata["fps"]))))
    frames = sample_frames(sorted({int(f) for f in detections["frame_number"]}), every)
    out_dir = args.out or (outputs.root / "label_players")
    result = export_player_frames(
        experiment.clips[window.clip].video, detections, frames, out_dir,
        clip=window.clip, window=window.id, split=window.split,
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_label_ball(args: argparse.Namespace, _outputs: object) -> int:
    import pandas as pd

    from pickleball_ml.evaluation.label_export import export_ball_frames, sample_frames

    experiment = load_experiment(args.manifest)
    window = experiment.window(args.window)
    outputs = experiment.outputs(window)
    if not outputs.ball_track.exists():
        raise FileNotFoundError(f"run the ball stage for {window.id} first")
    track = pd.read_parquet(outputs.ball_track)
    frames = sample_frames(sorted({int(f) for f in track["frame_number"]}), args.every_frames)
    video = experiment.clips[window.clip].video
    if args.export:
        out_dir = args.out or (outputs.root / "label_ball")
        print(json.dumps(export_ball_frames(video, frames, out_dir, clip=window.clip,
                                            window=window.id, split=window.split), indent=2))
        return 0

    from pickleball_ml.ball.click_tool import label_ball_frames

    output = args.out or (outputs.root / "ball_labels.json")
    count = label_ball_frames(video, frames, output, clip=window.clip, window=window.id,
                              split=window.split, annotator=args.annotator)
    print(f"labeled {count}/{len(frames)} frames -> {output}")
    return 0


def _print_stage(result: Any) -> None:
    detail = "  ".join(f"{k}={v}" for k, v in result.detail.items()) if result.detail else ""
    line = f"  {result.stage:28s} {result.status:8s} {result.seconds:6.1f}s  {detail}"
    print(line + (f"\n    {result.error}" if result.error else ""))


def _stage_progress(total: int, start_frame: int, stride: int) -> Callable[..., None]:
    return _progress_printer(total, TrackingConfig(start_frame=start_frame, stride=stride))


def _counter(label: str) -> Callable[..., None]:
    last = [0.0]

    def report(index: int) -> None:
        now = time.monotonic()
        if now - last[0] < 2.0:
            return
        last[0] = now
        print(f"\r  {label}: {index} samples   ", end="", flush=True)

    return report


def _background(video: Path, start_frame: int, end_frame: int) -> Frame:
    stride = max(1, (end_frame - start_frame) // BACKGROUND_SAMPLES)
    frames = [image for _, _, image in iter_frames(video, start_frame, end_frame, stride)]
    if not frames:
        raise ValueError("background window contains no frames")
    return median_background(frames[:BACKGROUND_SAMPLES])


def _to_frame(seconds: float, fps: float) -> int:
    # Assumes constant frame rate; true for the Phase 0 test clip.
    return int(round(seconds * fps))


def _progress_printer(total: int, config: TrackingConfig) -> Callable[..., None]:
    last = [0.0]

    def report(frame_number: int, rate: float | None = None) -> None:
        now = time.monotonic()
        if now - last[0] < 2.0:
            return
        last[0] = now
        done = (frame_number - config.start_frame) // config.stride + 1
        suffix = f" {rate:.1f} frames/s" if rate is not None else ""
        print(f"\r  {done}/{total} frames ({100 * done / max(total, 1):.0f}%){suffix}   ",
              end="", flush=True)

    return report


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=_json_default) + "\n")


def _json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
