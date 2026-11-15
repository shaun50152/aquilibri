from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from app.schemas.jobs import DEMO_OWNER_ID, Job, JobCreateRequest, JobListResponse, JobStatus
from app.services import job_service
from app.services.result_service import render_result_csv
from app.state import get_session_state

router = APIRouter(tags=["jobs"])


@router.get("/api/jobs", response_model=JobListResponse)
def list_jobs():
    session_id, revision, jobs = job_service.list_session_jobs()
    return JobListResponse(session_id=session_id, revision=revision, jobs=jobs)


@router.post("/api/jobs", response_model=Job, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreateRequest):
    return job_service.create_job(payload)


@router.get("/api/jobs/{job_id}/result")
def download_job_result(job_id: str):
    state = get_session_state()
    job = next((j for j in state.jobs if j.id == job_id), None)
    if job is None or job.session_id != state.session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.owner_id != DEMO_OWNER_ID:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status != JobStatus.completed or not job.result_snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not available")
    csv_text = render_result_csv(job)
    filename = f"{job_id}-simulated-result.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )