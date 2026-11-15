"""Job persistence — in-memory session state with optional MongoDB backing."""

from __future__ import annotations

from app.config import get_settings
from app.integrations.mongodb import get_mongo_job_store
from app.schemas.jobs import Job
from app.state import get_session_state


def _persist_job(job: Job) -> None:
    if get_settings().data_mode == "mongodb":
        state = get_session_state()
        get_mongo_job_store().replace_job(state.session_id, job.model_dump(mode="json"))


def _persist_revision(revision: int) -> None:
    if get_settings().data_mode == "mongodb":
        state = get_session_state()
        store = get_mongo_job_store()
        store.set_revision(state.session_id, revision)
        store.set_session_meta(
            state.session_id,
            revision=revision,
            current_time=state.current_time.isoformat(),
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
    _persist_revision(state.revision)
    return state.revision