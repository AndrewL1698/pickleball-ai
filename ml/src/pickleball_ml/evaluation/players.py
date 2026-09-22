"""Player tracking metrics against fully labeled sample frames.

Label file (`player_labels.json`, schema "pbml.player_labels/v1"): every sampled
frame lists *all* court players visible in it, so coverage and misses are
measurable, not just identity agreement:

    {
      "schema": "pbml.player_labels/v1",
      "clip": "buzz", "window": "buzz_a", "video": "data/raw/...mp4",
      "annotator": "...", "people": {"A_white_cap": "white shirt, black cap", ...},
      "frames": [
        {"frame_number": 3600, "split": "tune",
         "players": [
           {"person": "A_white_cap", "box": [x1, y1, x2, y2]},
           {"person": "B_red", "candidate": 3},      # box of exported candidate 3
           {"person": "B_blue", "box": null}          # visible, but no detection box
         ]}
      ]
    }

`candidate` refers to the numbered boxes of `pbml label-players` exports and is
resolved to a box when the file is loaded. A person not listed in a frame was
not visible (out of frame or fully hidden).

Metrics, per split and for all frames:

- coverage: labeled visible players matched by a predicted player box (IoU >= 0.5)
- missed: labeled visible players with no predicted box
- identity accuracy when detected: predicted player IDs are mapped one-to-one onto
  people with the assignment that agrees with the most labels; a matched label is
  correct when its predicted ID maps to that person
- all-four frames: labeled frames with four visible players, and how many of them
  had all four detected / all four correctly identified
- extra predictions: predicted player boxes in labeled frames that match no label
  (a spectator, a player on another court, a duplicate)
- ID switches: for each person, walking through labeled frames in time order,
  the number of times the matched predicted ID differs from the previous matched
  ID. Independent of the global mapping. Swaps shorter than the label spacing
  are not visible to this count.
"""

import json
from collections import Counter
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
from typing import Any, cast

import pandas as pd

from pickleball_ml.evaluation.identity import box_iou

SCHEMA = "pbml.player_labels/v1"
MIN_IOU = 0.5
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class PlayerLabel:
    frame_number: int
    split: str
    person: str
    box: Box | None


def load_player_labels(
    path: Path,
) -> tuple[dict[str, Any], list[PlayerLabel], dict[int, str]]:
    """File header, one label per visible player, and the split of every labeled frame."""
    data = json.loads(path.read_text())
    return (data, *parse_player_labels(data))


def parse_player_labels(data: dict[str, Any]) -> tuple[list[PlayerLabel], dict[int, str]]:
    if data.get("schema") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA!r}, got {data.get('schema')!r}")
    labels: list[PlayerLabel] = []
    frames: dict[int, str] = {}
    for frame in data["frames"]:
        if frame.get("skip"):
            continue
        number = int(frame["frame_number"])
        if number in frames:
            raise ValueError(f"frame {number} is labeled twice")
        split = str(frame.get("split", "unspecified"))
        frames[number] = split
        candidates = {int(c["id"]): c["box"] for c in frame.get("candidates", [])}
        seen: set[str] = set()
        for player in frame["players"]:
            person = str(player["person"])
            if person in seen:
                raise ValueError(f"{person} is labeled twice in frame {number}")
            seen.add(person)
            box = player.get("box")
            if "candidate" in player:
                if int(player["candidate"]) not in candidates:
                    raise ValueError(f"frame {number}: unknown candidate {player['candidate']}")
                box = candidates[int(player["candidate"])]
            labels.append(PlayerLabel(
                frame_number=number, split=split,
                person=person,
                box=None if box is None else (float(box[0]), float(box[1]), float(box[2]),
                                              float(box[3])),
            ))
    return labels, frames


def match_frame(
    labels: list[PlayerLabel], predicted: list[tuple[int, Box]]
) -> tuple[list[int | None], int]:
    """Predicted player ID matched to each label (greedy by IoU, one-to-one), and the
    number of predicted boxes left unmatched."""
    pairs = []
    for i, label in enumerate(labels):
        if label.box is None:
            continue
        for j, (_, box) in enumerate(predicted):
            iou = box_iou(label.box, box)
            if iou >= MIN_IOU:
                pairs.append((iou, i, j))
    matched: list[int | None] = [None] * len(labels)
    used: set[int] = set()
    for _, i, j in sorted(pairs, reverse=True):
        if matched[i] is None and j not in used:
            matched[i] = predicted[j][0]
            used.add(j)
    return matched, len(predicted) - len(used)


def best_mapping(pairs: list[tuple[int, str]]) -> dict[int, str]:
    """One-to-one predicted ID -> person assignment agreeing with the most (id, person) pairs."""
    agreements = Counter(pairs)
    ids = sorted({pid for pid, _ in pairs})
    people = sorted({person for _, person in pairs})
    slots: list[str | None] = [*people] + [None] * max(0, len(ids) - len(people))
    best: tuple[int, dict[int, str]] = (-1, {})
    for assignment in permutations(slots, len(ids)):
        score = sum(agreements[(pid, person)] for pid, person in zip(ids, assignment, strict=True)
                    if person is not None)
        if score > best[0]:
            best = (score, {pid: person for pid, person in zip(ids, assignment, strict=True)
                            if person is not None})
    return best[1]


def evaluate_players(
    players: pd.DataFrame, labels: list[PlayerLabel], labeled_frames: dict[int, str]
) -> dict[str, Any]:
    """Metrics for all labeled frames and for each split."""
    by_frame = {cast(int, f): g for f, g in players.groupby("frame_number")}
    extras: dict[int, int] = {}
    labels_by_frame: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        labels_by_frame.setdefault(label.frame_number, []).append(index)
    matched: list[int | None] = [None] * len(labels)
    for frame in sorted(labeled_frames):
        indices = labels_by_frame.get(frame, [])
        rows = by_frame.get(frame)
        predicted: list[tuple[int, Box]] = []
        if rows is not None:
            for pid, x1, y1, x2, y2 in rows[["player_id", "x1", "y1", "x2", "y2"]].to_numpy(
                dtype=float
            ):
                predicted.append((int(pid), (float(x1), float(y1), float(x2), float(y2))))
        result, extra = match_frame([labels[i] for i in indices], predicted)
        for i, pid in zip(indices, result, strict=True):
            matched[i] = pid
        extras[frame] = extra

    mapping = best_mapping([(pid, label.person) for label, pid in zip(labels, matched,
                                                                      strict=True)
                            if pid is not None])
    splits = sorted(set(labeled_frames.values()) | {"all"})
    report: dict[str, Any] = {"mapping": {str(k): v for k, v in sorted(mapping.items())}}
    for split in splits:
        chosen = [i for i, label in enumerate(labels) if split in ("all", label.split)]
        frames = {f for f, s in labeled_frames.items() if split in ("all", s)}
        report[split] = _metrics([labels[i] for i in chosen], [matched[i] for i in chosen],
                                 mapping, frames, extras)
    return report


def _metrics(
    labels: list[PlayerLabel], matched: list[int | None], mapping: dict[int, str],
    frames: set[int], extras: dict[int, int],
) -> dict[str, Any]:
    visible = len(labels)
    detected = sum(pid is not None for pid in matched)
    correct = sum(pid is not None and mapping.get(pid) == label.person
                  for label, pid in zip(labels, matched, strict=True))
    per_frame: dict[int, list[tuple[PlayerLabel, int | None]]] = {}
    for label, pid in zip(labels, matched, strict=True):
        per_frame.setdefault(label.frame_number, []).append((label, pid))
    four = [items for f, items in per_frame.items() if f in frames and len(items) >= 4]
    all_detected = sum(all(pid is not None for _, pid in items) for items in four)
    all_correct = sum(all(pid is not None and mapping.get(pid) == label.person
                          for label, pid in items) for items in four)
    switches, transitions = id_switches(labels, matched)
    return {
        "labeled_frames": len(frames),
        "visible_player_labels": visible,
        "detected": detected,
        "missed": visible - detected,
        "coverage": _rate(detected, visible),
        "correct_identity": correct,
        "wrong_identity": detected - correct,
        "identity_accuracy_when_detected": _rate(correct, detected),
        "frames_with_4_visible": len(four),
        "frames_all_4_detected": all_detected,
        "frames_all_4_detected_rate": _rate(all_detected, len(four)),
        "frames_all_4_correct": all_correct,
        "frames_all_4_correct_rate": _rate(all_correct, len(four)),
        "extra_predictions": sum(extras.get(f, 0) for f in frames),
        "id_switches": switches,
        "id_switch_opportunities": transitions,
        "id_switch_rate": _rate(switches, transitions),
    }


def id_switches(labels: list[PlayerLabel], matched: list[int | None]) -> tuple[int, int]:
    """(switches, consecutive matched pairs) over each person's labels in time order."""
    by_person: dict[str, list[tuple[int, int]]] = {}
    for label, pid in zip(labels, matched, strict=True):
        if pid is not None:
            by_person.setdefault(label.person, []).append((label.frame_number, pid))
    switches = transitions = 0
    for sequence in by_person.values():
        ids = [pid for _, pid in sorted(sequence)]
        transitions += max(0, len(ids) - 1)
        switches += sum(a != b for a, b in zip(ids, ids[1:], strict=False))
    return switches, transitions


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
