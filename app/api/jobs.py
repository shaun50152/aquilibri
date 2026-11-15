from fastapi import APIRouter, status

from app.schemas.jobs import Job, JobCreateRequest, JobListResponse
from app.services import job_service

router = APIRouter(tags=["jobs"])


@router.get("/api/jobs", response_model=JobListResponse)
def list_jobs():
    session_id, revision, jobs = job_service.list_session_jobs()
    return JobListResponse(session_id=session_id, revision=revision, jobs=jobs)


@router.post("/api/jobs", response_model=Job, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreateRequest):
    return job_service.create_job(payload)