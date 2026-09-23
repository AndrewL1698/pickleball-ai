"""What actually reaches Redis when a job is dispatched.

`RecordingJobQueue` covers the routes; these tests cover the real RQ adapter,
against an in-memory Redis so the suite still needs nothing running.
"""

import uuid

import pytest
from fakeredis import FakeStrictRedis
from pydantic import SecretStr
from rq import Queue

from pickleball_api.config import Settings
from pickleball_api.queue import TASK_PATH, JobQueue, QueueUnavailable, RedisJobQueue


@pytest.fixture
def redis() -> FakeStrictRedis:
    return FakeStrictRedis()


def test_only_the_job_id_is_put_on_the_queue(redis: FakeStrictRedis) -> None:
    """Nothing the worker could be tricked by: no paths, no filenames, no
    configuration. The worker reads everything else from PostgreSQL."""
    queue = RedisJobQueue(redis, "analysis", job_timeout_seconds=60)
    job_id = uuid.uuid4()
    queue.enqueue(job_id)

    enqueued = Queue("analysis", connection=redis).jobs
    assert len(enqueued) == 1
    assert enqueued[0].func_name == TASK_PATH
    assert enqueued[0].args == (str(job_id),)
    assert enqueued[0].kwargs == {}


def test_the_configured_queue_name_and_timeout_are_used(redis: FakeStrictRedis) -> None:
    RedisJobQueue(redis, "other", job_timeout_seconds=123).enqueue(uuid.uuid4())
    assert Queue("analysis", connection=redis).jobs == []
    assert Queue("other", connection=redis).jobs[0].timeout == 123


def test_the_task_path_is_the_worker_module_the_api_never_imports() -> None:
    assert TASK_PATH == "pickleball_worker.tasks.run_analysis_job"


def test_a_dead_redis_becomes_queue_unavailable() -> None:
    # A port nothing is listening on, so the connection genuinely fails.
    queue = RedisJobQueue.from_settings(_settings_pointing_at("redis://127.0.0.1:1/0"))
    with pytest.raises(QueueUnavailable):
        queue.enqueue(uuid.uuid4())
    assert queue.ping() is False


def test_a_reachable_redis_pings(redis: FakeStrictRedis) -> None:
    assert RedisJobQueue(redis, "analysis", job_timeout_seconds=60).ping() is True


def test_both_queues_satisfy_the_interface(redis: FakeStrictRedis) -> None:
    from pickleball_api.queue import RecordingJobQueue

    assert isinstance(RedisJobQueue(redis, "analysis", 60), JobQueue)
    assert isinstance(RecordingJobQueue(), JobQueue)


def _settings_pointing_at(url: str) -> Settings:
    return Settings(redis_url=SecretStr(url), environment="test")
