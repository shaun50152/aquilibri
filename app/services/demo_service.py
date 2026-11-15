"""Advance / reset orchestration with persistence and notifications."""

from __future__ import annotations

from app.repositories import job_repository, notification_repository
from app.schemas.demo import DemoAdvanceResponse
from app.services import demo_worker
from app.services.demo_worker import InvalidAdvanceHours
from app.services.preview_store import clear_previews_for_session
from app.session_lock import session_mutation


def advance_session(*, hours: float, expected_revision: int | None) -> DemoAdvanceResponse:
    try:
        hours = demo_worker.validate_advance_hours(hours)
    except InvalidAdvanceHours as exc:
        raise ValueError(str(exc)) from exc

    with session_mutation(expected_revision=expected_revision) as state:
        previous = state.current_time
        new_time = demo_worker.advance_demo_clock(previous, hours)
        jobs, started, completed, failed = demo_worker.process_jobs_between(
            state.jobs, previous, new_time
        )
        for job in jobs:
            job_repository.save_job(job)
            if job.id in completed:
                notification_repository.ensure_completion_notification(job)

        state.jobs = job_repository.list_jobs_for_session()
        state.current_time = new_time
        revision = job_repository.bump_revision()
        job_repository.persist_session_meta(
            state.session_id,
            current_time=new_time,
            revision=revision,
            execution_epoch=state.execution_epoch,
        )

        return DemoAdvanceResponse(
            session_id=state.session_id,
            revision=revision,
            previous_time=previous,
            current_time=new_time,
            hours_advanced=hours,
            jobs_started=started,
            jobs_completed=completed,
            jobs_failed=failed,
        )


def reset_demo_session() -> tuple[int, int]:
    """Reset jobs/clock for current session; returns (jobs_restored, revision)."""
    with session_mutation() as state:
        from app.config import get_settings

        settings = get_settings()
        state.execution_epoch += 1
        for j in state._jobs_fixture:
            j["session_id"] = settings.demo_session_id
            j["result_url"] = None
            j["result_snapshot"] = None
            j["completed_at"] = None
            j["execution_epoch"] = state.execution_epoch
        count = state.reload_from_fixtures()
        for i, job in enumerate(state.jobs):
            state.jobs[i] = job.model_copy(update={"execution_epoch": state.execution_epoch})
            job_repository.save_job(state.jobs[i])
        notification_repository.clear_session_notifications(state.session_id)
        clear_previews_for_session(state.session_id)
        job_repository.persist_session_meta(
            state.session_id,
            current_time=state.current_time,
            revision=state.revision,
            execution_epoch=state.execution_epoch,
        )
        return count, state.revision