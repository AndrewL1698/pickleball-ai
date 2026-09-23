"""Reading the state of an analysis job."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from pickleball_api import jobs as job_service
from pickleball_api.dependencies import SessionDep
from pickleball_api.schemas import JobRead

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRead)
def read_job(session: SessionDep, job_id: UUID) -> JobRead:
    """Status, stage, progress, timestamps, and — if it failed — a safe reason.

    The failure fields are the code and sentence the worker chose from
    `pickleball_api.errors`; the traceback stays in the worker's log.
    """
    job = job_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such job.")
    return JobRead.of(job)
