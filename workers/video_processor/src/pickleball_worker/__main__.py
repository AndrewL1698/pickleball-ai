"""`pbworker`: run the RQ worker that consumes analysis jobs.

    uv run pbworker              # work the configured queue until interrupted
    uv run pbworker --burst      # run the queued jobs, then exit
"""

import argparse
import logging
import sys

from redis import Redis
from rq import Queue, SimpleWorker, Worker

from pickleball_api.config import get_settings
from pickleball_api.storage import LocalFileStorage

logger = logging.getLogger("pickleball_worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pbworker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--burst", action="store_true",
                        help="exit once the queue is empty instead of waiting for more")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--fork", action=argparse.BooleanOptionalAction,
                        default=sys.platform != "darwin",
                        help="run each job in a forked child (default: off on macOS)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    # Same reason as the API: a worker started from another directory would
    # look for uploads somewhere else and fail every job with a file it cannot
    # find, so the resolved path goes in the log where it can be compared.
    logger.info("reading uploads from %s", LocalFileStorage(settings.upload_dir).root)
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    queue = Queue(settings.queue_name, connection=redis)
    # RQ forks a child per job by default. On macOS that deadlocks or aborts
    # once a CoreFoundation-backed library is loaded -- which OpenCV and torch
    # both are, so it will bite the moment the real processor lands in Phase 3.
    # SimpleWorker runs the job in-process instead. The cost is that a runaway
    # job cannot be killed on timeout and a crash takes the worker with it,
    # which is acceptable for one developer's laptop and not for a Linux host.
    worker_class = Worker if args.fork else SimpleWorker
    logger.info("worker listening on queue %r (%s)", settings.queue_name, worker_class.__name__)
    worker_class([queue], connection=redis).work(burst=args.burst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
