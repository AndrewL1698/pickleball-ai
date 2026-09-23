"""Handing an analysis job to the worker.

Only the job's id goes onto the queue. The worker re-reads the job and its
video from PostgreSQL and derives the storage path itself, so nothing on the
queue is trusted: a tampered or replayed message can at worst ask for a real
job to be run again, which the status transitions already refuse once the job
is finished.

The API never imports the worker package. The task is named by its dotted path
so the dependency points one way only: worker -> api.
"""

import logging
from typing import Protocol, runtime_checkable
from uuid import UUID

from redis import Redis
from rq import Queue

from pickleball_api.config import Settings

#: Where the worker's task lives. Resolved by the worker, never imported here.
TASK_PATH = "pickleball_worker.tasks.run_analysis_job"

logger = logging.getLogger(__name__)


class QueueUnavailable(Exception):
    """The job could not be handed to the worker."""


@runtime_checkable
class JobQueue(Protocol):
    """Somewhere to put a job id for a worker to pick up."""

    def enqueue(self, job_id: UUID) -> str:
        """Queue the job and return the queue's own message id.

        Raises `QueueUnavailable` if the job was not accepted.
        """

    def ping(self) -> bool:
        """Whether the queue backend is reachable."""


class RedisJobQueue:
    """An RQ queue on Redis."""

    def __init__(self, redis: Redis, name: str, job_timeout_seconds: int) -> None:
        self._redis = redis
        self._queue = Queue(name, connection=redis, default_timeout=job_timeout_seconds)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RedisJobQueue":
        redis = Redis.from_url(settings.redis_url.get_secret_value())
        return cls(redis, settings.queue_name, settings.job_timeout_seconds)

    def enqueue(self, job_id: UUID) -> str:
        try:
            enqueued = self._queue.enqueue(TASK_PATH, str(job_id))
        except Exception as exc:  # redis is down, or refused the job
            logger.exception("could not enqueue job %s", job_id)
            raise QueueUnavailable(str(job_id)) from exc
        return str(enqueued.id)

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:
            # The reason is logged, never returned: it embeds the Redis URL.
            logger.exception("redis ping failed")
            return False


class RecordingJobQueue:
    """A queue that only remembers what it was asked to run.

    Used by the tests, and by any development run that wants the API up without
    a worker. It is deliberately in this module rather than in the test suite so
    that `Storage` and `JobQueue` have their substitutes in the same place as
    their interfaces.
    """

    def __init__(self, available: bool = True) -> None:
        self.enqueued: list[UUID] = []
        self.available = available

    def enqueue(self, job_id: UUID) -> str:
        if not self.available:
            raise QueueUnavailable(str(job_id))
        self.enqueued.append(job_id)
        return f"recorded-{job_id}"

    def ping(self) -> bool:
        return self.available
