"""Advance / reset orchestration with persistence and notifications."""

from __future__ import annotations

from app.config import get_settings
from app.repositories import job_repository, notification_repository, telemetry_repository
from app.schemas.demo import DemoAdvanceResponse
from app.schemas.jobs import JobStatus
from app.services import demo_worker, scheduling_service
from app.services.demo_worker import InvalidAdvanceHours
from app.services.preview_store import clear_previews_for_session
from app.session_lock import session_mutation


def _place_queued_jobs(jobs, sites, current_time):
    """Try to schedule any still-queued jobs against current sites/clock."""
    placed = list(jobs)
    queued = [j for j in placed if j.status == JobStatus.queued]
    if not queued:
        return placed
    # Rebalance queued + scheduled together so utilization stays even.
    for job in queued:
        others = [j for j in placed if j.id != job.id]
        updated, rebalanced = scheduling_service.try_place_submitted_job(
            job, others, sites, current_time
        )
        by_id = {j.id: j for j in rebalanced}
        by_id[updated.id] = updated
        placed = [by_id.get(j.id, j) for j in placed]
    return placed


def advance_session(*, hours: float, expected_revision: int | None) -> DemoAdvanceResponse:
    try:
        hours = demo_worker.validate_advance_hours(hours)
    except InvalidAdvanceHours as exc:
        raise ValueError(str(exc)) from exc

    with session_mutation(expected_revision=expected_revision) as state:
        previous = state.current_time
        new_time = demo_worker.advance_demo_clock(previous, hours)
        state.current_time = new_time
        # Slide site hours with the demo clock when past the seeded horizon.
        sites = telemetry_repository.list_site_profiles()
        # Live blank-slate demos: retry placement for jobs that stayed queued
        # (e.g. submitted while the clock was past telemetry coverage).
        # Synthetic mode keeps intentionally queued fixture jobs for reallocation demos.
        if get_settings().is_live:
            state.jobs = _place_queued_jobs(state.jobs, sites, new_time)

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
    """Reset session for the current DATA_MODE.

    synthetic — restore fixture jobs and clock.
    live — wipe MongoDB session collections to a blank slate.

    Returns (jobs_count, revision).
    """
    with session_mutation() as state:
        settings = get_settings()
        state.execution_epoch += 1
        clear_previews_for_session(state.session_id)

        if settings.is_live:
            count = state.clear_to_blank_slate()
            notification_repository.clear_session_notifications(state.session_id)
            # Re-align Tiger profiles to the restored anchor clock.
            telemetry_repository.list_site_profiles()
            job_repository.persist_session_meta(
                state.session_id,
                current_time=state.current_time,
                revision=state.revision,
                execution_epoch=state.execution_epoch,
            )
            return count, state.revision

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
        telemetry_repository.list_site_profiles()
        job_repository.persist_session_meta(
            state.session_id,
            current_time=state.current_time,
            revision=state.revision,
            execution_epoch=state.execution_epoch,
        )
        return count, state.revision
