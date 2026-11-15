from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.integrations.grok_summary import (
    GrokSummaryError,
    build_job_summary_payload,
    generate_job_summary,
)
from app.schemas.jobs import DEMO_OWNER_ID, Job, JobCreateRequest, JobListResponse, JobStatus
from app.services import job_service
from app.services.result_service import render_result_csv
from app.state import get_session_state

router = APIRouter(tags=["jobs"])


class JobSummaryResponse(BaseModel):
    job_id: str
    summary: str
    model: str
    provider: str = "xai"
    metrics: dict = Field(default_factory=dict)


def _find_session_job(job_id: str) -> Job:
    state = get_session_state()
    job = next((j for j in state.jobs if j.id == job_id), None)
    if job is None or job.session_id != state.session_id:
        # Live mode: session is Mongo-backed; also try a direct Atlas read.
        settings = get_settings()
        if settings.is_live:
            from app.integrations.mongodb import get_mongo_job_store

            docs = get_mongo_job_store().list_jobs(settings.demo_session_id)
            match = next((d for d in docs if d.get("id") == job_id), None)
            if match is not None:
                return Job.model_validate(match)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.owner_id != DEMO_OWNER_ID:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/api/jobs", response_model=JobListResponse)
def list_jobs():
    session_id, revision, jobs = job_service.list_session_jobs()
    return JobListResponse(session_id=session_id, revision=revision, jobs=jobs)


@router.post("/api/jobs", response_model=Job, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreateRequest):
    return job_service.create_job(payload)


@router.get("/api/jobs/{job_id}/result")
def download_job_result(job_id: str):
    job = _find_session_job(job_id)
    if job.status != JobStatus.completed or not job.result_snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not available")
    csv_text = render_result_csv(job)
    filename = f"{job_id}-simulated-result.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/jobs/{job_id}/summary", response_model=JobSummaryResponse)
def job_executive_summary(job_id: str):
    """xAI Grok 2-sentence ops summary for a completed job (sponsor-track feature)."""
    job = _find_session_job(job_id)
    if job.status != JobStatus.completed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Summary is only available for completed jobs",
        )

    # Prefer live site snapshots already used by the dashboard (no Gemini changes).
    try:
        from app.repositories import telemetry_repository

        sites = telemetry_repository.list_site_profiles()
    except Exception:  # noqa: BLE001
        sites = get_session_state().sites
    payload = build_job_summary_payload(job, sites)
    try:
        summary = generate_job_summary(payload)
    except GrokSummaryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    settings = get_settings()
    return JobSummaryResponse(
        job_id=job.id,
        summary=summary,
        model=settings.grok_model,
        provider="xai",
        metrics={
            "site_id": payload.get("site_id"),
            "site_name": payload.get("site_name"),
            "duration_hours": payload.get("duration_hours"),
            "water_liters": payload.get("water_liters"),
            "cost_usd": payload.get("cost_usd"),
        },
    )