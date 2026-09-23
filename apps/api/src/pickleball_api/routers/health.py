"""Liveness and readiness."""

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from pickleball_api.dependencies import QueueDep, SessionDep, SettingsDep
from pickleball_api.schemas import HealthResponse, ReadyResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(settings: SettingsDep) -> HealthResponse:
    """Whether this process is running. Touches nothing else, so a restart loop
    caused by a slow database stays impossible."""
    return HealthResponse(environment=settings.environment)


@router.get("/ready", response_model=ReadyResponse)
def ready(session: SessionDep, queue: QueueDep, response: Response) -> ReadyResponse:
    """Whether the database and the queue are both reachable.

    Returns 503 when either is not, so a deployment can wait for them, but the
    body says only which one failed: the exception text contains the URL the
    connection was attempted on, password included.
    """
    database_ok = True
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("database readiness check failed")
        database_ok = False

    redis_ok = queue.ping()
    everything = database_ok and redis_ok
    if not everything:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(ready=everything, database=database_ok, redis=redis_ok)
