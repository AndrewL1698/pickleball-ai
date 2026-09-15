"""`pbml` command line: run each Phase 0 pipeline stage on one video.

Stages read the previous stage's output from data/processed/<video_name>/:

    pbml metadata  VIDEO
    pbml calibrate VIDEO [--time S | --background START END] [--points FILE]
    pbml track     VIDEO --start S --end S
    pbml identify  VIDEO
    pbml render    VIDEO
    pbml evaluate  VIDEO --labels FILE
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from pickleball_ml.court.calibration import Calibration, draw_court_overlay
from pickleball_ml.court.click_tool import collect_landmarks, median_background
from pickleball_ml.evaluation.identity import evaluate_identity
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

    args = parser.parse_args(argv)
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
    players, diagnostics = resolve_identities(
        raw, metadata.fps, raw_run["config"]["stride"], metadata.height, config
    )
    players.to_parquet(outputs.players_court, index=False)
    record = run_record(config, players, diagnostics, raw_run["frames_processed"])
    record["source_run_created_at"] = raw_run["created_at"]
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
