"""Job persistence — in-memory session state with optional MongoDB backing."""

from __future__ import annotations

from datetime import datetime

from app.config import get_settings
from app.integrations.mongodb import get_mongo_job_store
from app.schemas.jobs import Job
from app.state import get_session_state


def _persist_job(job: Job) -> None:
    if get_settings().is_live:
        state = get_session_state()
        get_mongo_job_store().replace_job(state.session_id, job.model_dump(mode="json"))


def persist_session_meta(
    session_id: str,
    *,
    current_time: datetime,
    revision: int,
    execution_epoch: int,
) -> None:
    state = get_session_state()
    state.current_time = current_time
    state.revision = revision
    state.execution_epoch = execution_epoch
    if get_settings().is_live:
        get_mongo_job_store().set_session_meta(
            session_id,
            revision=revision,
            current_time=current_time.isoformat(),
            execution_epoch=execution_epoch,
        )


def list_jobs_for_session() -> list[Job]:
    return list(get_session_state().jobs)


def save_job(job: Job) -> Job:
    state = get_session_state()
    for i, existing in enumerate(state.jobs):
        if existing.id == job.id:
            state.jobs[i] = job
            _persist_job(job)
            return job
    state.jobs.append(job)
    _persist_job(job)
    return job


def next_job_id() -> str:
    state = get_session_state()
    prefix = "job-hackuta"
    max_n = 0
    for job in state.jobs:
        if job.id.startswith(prefix + "-"):
            try:
                max_n = max(max_n, int(job.id.rsplit("-", 1)[-1]))
            except ValueError:
                continue
    return f"{prefix}-{max_n + 1:04d}"


def bump_revision() -> int:
    state = get_session_state()
    state.revision += 1
    persist_session_meta(
        state.session_id,
        current_time=state.current_time,
        revision=state.revision,
        execution_epoch=state.execution_epoch,
    )
    return state.revision