from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.repositories import job_repository
from app.services import scheduling_service
from app.schemas.jobs import (
    DEMO_OWNER_ID,
    TEMPLATE_DEFINITIONS,
    Job,
    JobCreateRequest,
    JobStatus,
    JobTemplate,
)
from app.state import get_session_state


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def list_session_jobs() -> tuple[str, int, list[Job]]:
    state = get_session_state()
    jobs = job_repository.list_jobs_for_session()
    return state.session_id, state.revision, jobs


def create_job(payload: JobCreateRequest) -> Job:
    earliest = _ensure_utc(payload.earliest_start)
    deadline = _ensure_utc(payload.deadline)

    if deadline <= earliest:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="deadline must be after earliest_start",
        )

    if payload.template not in TEMPLATE_DEFINITIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown template: {payload.template}",
        )

    definition = TEMPLATE_DEFINITIONS[payload.template]
    state = get_session_state()
    # Align placement with GET /api/sites (Tiger when configured).
    from app.repositories import telemetry_repository

    telemetry_repository.list_site_profiles()

    data_ready: dict[str, datetime] = {}
    for site_id in definition.eligible_site_ids:
        data_ready[site_id] = state.current_time

    job = Job(
        id=job_repository.next_job_id(),
        session_id=state.session_id,
        owner_id=DEMO_OWNER_ID,
        template=JobTemplate(payload.template),
        earliest_start=earliest,
        deadline=deadline,
        gpu_count=definition.gpu_count,
        duration_hours=definition.duration_hours,
        eligible_site_ids=list(definition.eligible_site_ids),
        data_ready_at_by_site=data_ready,
        critical=definition.critical,
        miss_penalty_usd=definition.miss_penalty_usd,
        status=JobStatus.queued,
        assigned_site_id=None,
        scheduled_start=None,
        completed_at=None,
        result_url=None,
    )

    placed = scheduling_service.try_place_submitted_job(
        job,
        state.jobs,
        state.sites,
        state.current_time,
    )
    saved = job_repository.save_job(placed)
    job_repository.bump_revision()
    return saved