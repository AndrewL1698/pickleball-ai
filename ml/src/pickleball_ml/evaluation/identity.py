"""Player identity accuracy against manually labeled person boxes.

Labels name the real person in a box ("near_blue", "far_black", ...). Predicted
player IDs are mapped one-to-one onto people with the assignment that agrees
with the most labels, then each labeled sample counts as:

- correct: a predicted player box overlaps the label box and maps to that person
- wrong: a predicted player box overlaps it but maps to someone else (identity swap)
- missed: no predicted player box overlaps it
"""

from collections import Counter
from itertools import permutations
from typing import Any

import numpy as np
import pandas as pd

MIN_IOU = 0.5


def box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def match_samples(players: pd.DataFrame, samples: list[dict[str, Any]]) -> list[int | None]:
    """Predicted player_id for each labeled sample, or None when no player box overlaps it."""
    by_frame = {f: g for f, g in players.groupby("frame_number")}
    matched: list[int | None] = []
    for sample in samples:
        frame = by_frame.get(sample["frame_number"])
        best: tuple[float, int | None] = (MIN_IOU, None)
        if frame is not None:
            boxes = frame[["x1", "y1", "x2", "y2"]].to_numpy(dtype=float)
            for player_id, box in zip(frame["player_id"].astype(int), boxes, strict=True):
                iou = box_iou(tuple(sample["box"]), tuple(box))
                if iou >= best[0]:
                    best = (iou, int(player_id))
        matched.append(best[1])
    return matched


def evaluate_identity(
    players: pd.DataFrame, samples: list[dict[str, Any]], split: str | None = None
) -> dict[str, Any]:
    chosen = [s for s in samples if split is None or s["split"] == split]
    predicted = match_samples(players, chosen)
    people = sorted({s["person"] for s in chosen})
    player_ids = sorted({p for p in predicted if p is not None})
    agreements = Counter((p, s["person"]) for s, p in zip(chosen, predicted, strict=True)
                         if p is not None)

    mapping: dict[int, str] = {}
    best_agreement = -1
    slots: list[str | None] = [*people] + [None] * max(0, len(player_ids) - len(people))
    for assignment in permutations(slots, len(player_ids)):
        agreement = sum(agreements[(pid, person)] for pid, person in
                        zip(player_ids, assignment, strict=True) if person is not None)
        if agreement > best_agreement:
            best_agreement = agreement
            mapping = {pid: person for pid, person in zip(player_ids, assignment, strict=True)
                       if person is not None}

    correct = wrong = missed = 0
    for sample, player_id in zip(chosen, predicted, strict=True):
        if player_id is None:
            missed += 1
        elif mapping.get(player_id) == sample["person"]:
            correct += 1
        else:
            wrong += 1
    total = len(chosen)
    matched = correct + wrong
    return {
        "split": split or "all",
        "samples": total,
        "correct": correct,
        "wrong_identity": wrong,
        "missed": missed,
        "identity_accuracy_when_detected": round(correct / matched, 3) if matched else None,
        "overall_accuracy": round(correct / total, 3) if total else None,
        "mapping": {str(k): v for k, v in mapping.items()},
        "player_ids_matched": int(np.unique([p for p in predicted if p is not None]).size),
    }
