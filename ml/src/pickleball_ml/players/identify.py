"""Identify: player tracks in court feet, from the rows a Detect run has finished.

The second half of player tracking (ADR 0001). Detect stores image-space
ground-contact points only; Identify projects them with the calibration it is
given (the match's newest), then resolves who is who with
`pickleball_ml.players.identity`. It takes seconds to minutes, where Detect
takes about an hour, so it re-runs on everything Detect has finished whenever a
chunk is added, after a recalibration, or after a correction.

Track ids restart in every Detect chunk, so every track ends at a chunk boundary.
Players are selected chunk by chunk (`selection_block`), and partner assignment over
the whole period carries each player's number from one chunk to the next.

The output rows are `PlayerTrackPoint`s (`docs/DATA_MODEL.md`). They carry
`player_number` (1-4), not a database id, so this package stays free of the
database; the match's `Player` rows are found by (match, number).
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from importlib.metadata import version as package_version
from typing import Any

import numpy as np
import pandas as pd

from pickleball_ml.court.geometry import FloatArray
from pickleball_ml.players.court_position import project_ground_contact
from pickleball_ml.players.detect import TRACK_ID_STRIDE
from pickleball_ml.players.identity import IdentityConfig, resolve_identities

#: What a Detect row must carry for Identify.
DETECT_INPUT_COLUMNS = [
    "frame_number", "timestamp_ms", "track_id", "x1", "y1", "x2", "y2", "confidence",
    "image_x", "image_y", "truncated", "appearance",
]

#: An Identify run's artifact: one row per player per sampled frame.
PLAYER_TRACK_COLUMNS = [
    "frame_number", "timestamp_ms", "player_number", "team", "side", "slot",
    "track_id", "segment_id", "tracklet_id", "x1", "y1", "x2", "y2", "confidence",
    "image_x", "image_y", "court_x", "court_y", "truncated",
    "identity_confident", "identity_margin", "switch_link_margin",
]

#: The algorithm, versioned with the package: there is no model file to hash.
ALGORITHM = "pickleball_ml.players.identity"


def model_version() -> str:
    """`pickleball_ml.players.identity@<package version>`, for the run record."""
    return f"{ALGORITHM}@{package_version('pickleball-ml')}"


@dataclass(frozen=True)
class IdentifyRunConfig:
    """Everything an Identify run records about how it was made: its `config_json`.

    Besides the identity settings, what it read: how many of the Detect run's
    chunks, how far into the video they reach ("players tracked up to"), the
    steady part and excluded parts copied from the Detect run, and the changeovers
    it split the match at. All of it is hashed into the run's `config_hash`.
    """

    identity: IdentityConfig
    detect_chunks: int
    tracked_until_seconds: float
    steady_part: Mapping[str, Any]
    changeovers_seconds: tuple[float, ...] = field(default=())

    def to_config(self) -> dict[str, Any]:
        return {
            "identity": asdict(self.identity),
            "detect_chunks": self.detect_chunks,
            "tracked_until_seconds": self.tracked_until_seconds,
            "steady_part": dict(self.steady_part),
            "changeovers_seconds": list(self.changeovers_seconds),
        }


@dataclass(frozen=True)
class Identified:
    """Identify's output: the tracks, diagnostics, and the changeovers it used."""

    tracks: pd.DataFrame
    diagnostics: dict[str, Any]
    #: The changeovers inside the tracked range. Any later one is ignored until
    #: Detect reaches it.
    changeovers_seconds: tuple[float, ...]


def identify(
    detections: pd.DataFrame,
    homography: FloatArray,
    config: IdentityConfig,
    *,
    changeovers_seconds: Sequence[float] = (),
) -> Identified:
    """Player tracks from Detect's rows, projected with `homography`.

    `detections` is the concatenation of a Detect run's finished chunks; rows that
    Detect left untracked (`track_id` -1) are ignored. `changeovers_seconds` are the
    times the teams changed ends; one outside the tracked range is ignored.
    """
    rows = project_ground_contact(
        detections[DETECT_INPUT_COLUMNS], np.asarray(homography, dtype=np.float64)
    )
    # Players are selected chunk by chunk: no track crosses a chunk boundary, and
    # selection over a whole match would lose everyone who is ever away for long.
    rows["selection_block"] = rows["track_id"] // TRACK_ID_STRIDE
    used = _changeovers_in_range(rows, changeovers_seconds)
    # `resolve_identities` predates the glossary: its end switches are changeovers.
    changeover_frames = [_first_frame_at(rows, s) for s in used]
    players, diagnostics = resolve_identities(rows, config, end_switch_frames=changeover_frames)
    tracks = (players.rename(columns={"player_id": "player_number"})[PLAYER_TRACK_COLUMNS]
              .astype({"player_number": np.int64, "truncated": bool,
                       "identity_confident": bool}))
    return Identified(tracks.reset_index(drop=True), diagnostics, used)


def _changeovers_in_range(
    rows: pd.DataFrame, changeovers_seconds: Sequence[float]
) -> tuple[float, ...]:
    """The changeovers after the first sample and at or before the last one."""
    if rows.empty:
        return ()
    first_ms, last_ms = rows["timestamp_ms"].min(), rows["timestamp_ms"].max()
    return tuple(sorted(s for s in changeovers_seconds if first_ms < s * 1000.0 <= last_ms))


def _first_frame_at(rows: pd.DataFrame, seconds: float) -> int:
    """The first sampled frame at or after `seconds`."""
    return int(rows.loc[rows["timestamp_ms"] >= seconds * 1000.0, "frame_number"].min())
