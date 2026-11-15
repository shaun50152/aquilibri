"""Job persistence boundary — fixtures/in-memory today; MongoDB later."""

from __future__ import annotations

from app.integrations.mongodb import MongoJobStore
from app.schemas.jobs import Job
from app.state import get_session_state


def list_jobs_for_session() -> list[Job]:
    state = get_session_state()
    _ = MongoJobStore  # Person 2: swap when DATA_MODE supports mongodb.
    return list(state.jobs)


def save_job(job: Job) -> Job:
    state = get_session_state()
    for i, existing in enumerate(state.jobs):
        if existing.id == job.id:
            state.jobs[i] = job
            return job
    state.jobs.append(job)
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
    return state.revision