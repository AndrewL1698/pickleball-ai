"""A model run's output: Parquet files in `Storage`, named on the run's rows.

Dense per-frame tracks are far too many rows for the database (four players at
30 fps for an hour is about 430k), so each `ModelRun` keeps its output as
Parquet. An Identify run writes one artifact, once. A Detect run takes about an
hour over a whole match, so it saves its output a chunk at a time, each a
`ModelRunChunk` with its own object, and a retry continues from the first
unfinished one. A recalibration or a correction makes a new run with new
output, so the original prediction is never overwritten.

Each object gets a fresh random key from `new_storage_key`, never one derived
from the run's id, for the reason `new_storage_key` gives.
"""

import io

import pandas as pd

from pickleball_api.models import ModelRun, ModelRunChunk, ModelRunStage, utcnow
from pickleball_api.storage import Storage, new_storage_key

EXTENSION = ".parquet"


class RunArtifactError(Exception):
    """A run's artifact cannot be written or read, because of the run's state.

    Not a `StorageError`: nothing is wrong with the store. A failure in the store
    itself still raises the store's own errors, such as `ObjectNotFound`.
    """


class ArtifactAlreadyWritten(RunArtifactError):
    """The run already has an artifact, and runs are never overwritten."""


class NoArtifact(RunArtifactError):
    """The run has not written an artifact."""


def write_run_artifact(storage: Storage, run: ModelRun, frame: pd.DataFrame) -> str:
    """Store `frame` as the run's artifact, set `run.artifact_storage_key`, and return the key.

    The object is written before the key is set on the run, and the caller
    commits. If that commit can fail, wrap it in `discard_on_error(storage, key)`
    so a failed commit leaves no object that no row refers to.
    """
    if run.artifact_storage_key is not None:
        raise ArtifactAlreadyWritten(f"run {run.id} already has an artifact")
    key = _write_parquet(storage, frame)
    run.artifact_storage_key = key
    return key


def _write_parquet(storage: Storage, frame: pd.DataFrame) -> str:
    """Store `frame` under a fresh key and return the key."""
    buffer = io.BytesIO()
    # The index carries nothing a track needs, and keeping it would round-trip
    # a filtered frame's gappy index as if it meant something.
    frame.to_parquet(buffer, index=False)
    payload = buffer.getvalue()
    key = new_storage_key(EXTENSION)
    # The limit guards streams from outside; this one was made here, so its
    # own length is the limit.
    storage.write(key, [payload], max_bytes=len(payload))
    return key


class NotADetectRun(RunArtifactError):
    """Only a Detect run saves its output in chunks."""


class ChunkOutOfOrder(RunArtifactError):
    """Chunks are saved once each, in order, so the next one is always `len(run.chunks)`."""


class RunAlreadyComplete(RunArtifactError):
    """The run's last chunk is saved; a complete run is never added to."""


def write_detect_chunk(
    storage: Storage,
    run: ModelRun,
    *,
    index: int,
    start_seconds: float,
    end_seconds: float,
    frame: pd.DataFrame,
    is_last: bool,
) -> ModelRunChunk:
    """Store one finished chunk of a Detect run and add its row to `run.chunks`.

    The last chunk also completes the run. A Detect run's output is its chunk
    set, so its `artifact_storage_key` stays null. As for `write_run_artifact`,
    the caller commits, inside `discard_on_error(storage, chunk.storage_key)`, so
    the object and its row are saved together or not at all.
    """
    if run.stage is not ModelRunStage.DETECT:
        raise NotADetectRun(f"run {run.id} is a {run.stage.value} run")
    if run.completed_at is not None:
        raise RunAlreadyComplete(f"run {run.id} is complete")
    if index != len(run.chunks):
        raise ChunkOutOfOrder(f"run {run.id} expects chunk {len(run.chunks)}, not {index}")
    key = _write_parquet(storage, frame)
    chunk = ModelRunChunk(
        index=index,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        storage_key=key,
        completed_at=utcnow(),
    )
    run.chunks.append(chunk)
    if is_last:
        run.completed_at = chunk.completed_at
    return chunk


def read_detect_chunks(storage: Storage, run: ModelRun) -> pd.DataFrame:
    """A Detect run's finished chunks, in order, as one DataFrame.

    Works on an unfinished run too, which then reads as far as it got. A run
    with no chunks yet is an empty DataFrame. Raises `ObjectNotFound` if a
    chunk's object is missing from storage.
    """
    if run.stage is not ModelRunStage.DETECT:
        raise NotADetectRun(f"run {run.id} is a {run.stage.value} run")
    frames = []
    for chunk in run.chunks:
        with storage.local_path(chunk.storage_key) as path:
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def read_run_artifact(storage: Storage, run: ModelRun) -> pd.DataFrame:
    """The run's artifact as a DataFrame.

    Raises `NoArtifact` if the run never wrote one, and `ObjectNotFound` if its
    object is missing from storage.
    """
    if run.artifact_storage_key is None:
        raise NoArtifact(f"run {run.id} has no artifact")
    with storage.local_path(run.artifact_storage_key) as path:
        return pd.read_parquet(path)
