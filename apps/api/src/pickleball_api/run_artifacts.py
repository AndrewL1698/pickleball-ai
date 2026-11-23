"""A model run's output: one Parquet file in `Storage`, named on the run's row.

Dense per-frame tracks are far too many rows for the database (four players at
30 fps for an hour is about 430k), so each `ModelRun` keeps its output as a
Parquet artifact. A run writes its artifact once. A recalibration or a
correction makes a new run with a new artifact, so the original prediction is
never overwritten.

Each artifact gets a fresh random key from `new_storage_key`, never one derived
from the run's id, for the reason `new_storage_key` gives.
"""

import io

import pandas as pd

from pickleball_api.models import ModelRun
from pickleball_api.storage import Storage, StorageError, new_storage_key

EXTENSION = ".parquet"


class ArtifactAlreadyWritten(StorageError):
    """The run already has an artifact, and runs are never overwritten."""


class NoArtifact(StorageError):
    """The run has not written an artifact."""


def write_run_artifact(storage: Storage, run: ModelRun, frame: pd.DataFrame) -> str:
    """Store `frame` as the run's artifact, set `run.artifact_storage_key`, and return the key.

    The object is written before the key is set on the run, and the caller
    commits. If that commit can fail, wrap it in `discard_on_error(storage, key)`
    so a failed commit leaves no object that no row refers to.
    """
    if run.artifact_storage_key is not None:
        raise ArtifactAlreadyWritten(f"run {run.id} already has an artifact")
    buffer = io.BytesIO()
    # The index carries nothing a track needs, and keeping it would round-trip
    # a filtered frame's gappy index as if it meant something.
    frame.to_parquet(buffer, index=False)
    payload = buffer.getvalue()
    key = new_storage_key(EXTENSION)
    # The limit guards streams from outside; this one was made here, so its
    # own length is the limit.
    storage.write(key, [payload], max_bytes=len(payload))
    run.artifact_storage_key = key
    return key


def read_run_artifact(storage: Storage, run: ModelRun) -> pd.DataFrame:
    """The run's artifact as a DataFrame.

    Raises `NoArtifact` if the run never wrote one, and `ObjectNotFound` if its
    object is missing from storage.
    """
    if run.artifact_storage_key is None:
        raise NoArtifact(f"run {run.id} has no artifact")
    with storage.local_path(run.artifact_storage_key) as path:
        return pd.read_parquet(path)
