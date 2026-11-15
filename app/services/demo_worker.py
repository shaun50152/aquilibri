"""Simulated clock and deterministic job execution (Person 1)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from app.schemas.jobs import Job, JobStatus
from app.services.result_service import attach_completed_result, finish_time


class InvalidAdvanceHours(ValueError):
    pass


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_advance_hours(hours: float) -> float:
    if not math.isfinite(hours) or hours <= 0:
        raise InvalidAdvanceHours("hours must be a finite number greater than zero")
    return float(hours)


def advance_demo_clock(current: datetime, hours: float) -> datetime:
    validate_advance_hours(hours)
    return _ensure_utc(current) + timedelta(hours=hours)


def _assignment_valid(job: Job) -> bool:
    return bool(job.assigned_site_id and job.scheduled_start is not None)


def _normalize_running_at(jobs: list[Job], t: datetime) -> list[Job]:
    t = _ensure_utc(t)
    out: list[Job] = []
    for job in jobs:
        if job.status == JobStatus.scheduled and job.scheduled_start and _ensure_utc(job.scheduled_start) <= t:
            if not _assignment_valid(job):
                out.append(
                    job.model_copy(
                        update={"status": JobStatus.failed, "completed_at": t},
                    )
                )
            else:
                out.append(job.model_copy(update={"status": JobStatus.running}))
        else:
            out.append(job)
    return out


def _scheduled_start_in_open_interval(job: Job, t_from: datetime, t_to: datetime) -> bool:
    if job.scheduled_start is None:
        return False
    s = _ensure_utc(job.scheduled_start)
    return t_from < s <= t_to


def _finish_in_open_interval(job: Job, t_from: datetime, t_to: datetime) -> datetime | None:
    fin = finish_time(job)
    if fin is None:
        return None
    fin = _ensure_utc(fin)
    if t_from < fin <= t_to:
        return fin
    return None


def process_jobs_between(
    jobs: list[Job],
    t_from: datetime,
    t_to: datetime,
) -> tuple[list[Job], list[str], list[str], list[str]]:
    """Apply starts and finishes chronologically on (t_from, t_to]."""
    t_from = _ensure_utc(t_from)
    t_to = _ensure_utc(t_to)
    if t_to <= t_from:
        return jobs, [], [], []

    jobs = _normalize_running_at(jobs, t_from)
    by_id = {j.id: j for j in jobs}
    started: list[str] = []
    completed: list[str] = []
    failed: list[str] = []

    events: list[tuple[datetime, int, str, str]] = []
    for job in by_id.values():
        if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.unfulfilled, JobStatus.queued}:
            continue
        if not _assignment_valid(job):
            continue
        if _scheduled_start_in_open_interval(job, t_from, t_to) and job.status == JobStatus.scheduled:
            events.append((_ensure_utc(job.scheduled_start), 0, "start", job.id))
        fin = _finish_in_open_interval(job, t_from, t_to)
        if fin is not None and job.status in {JobStatus.running, JobStatus.scheduled}:
            events.append((fin, 1, "complete", job.id))

    events.sort(key=lambda e: (e[0], e[1], e[3]))

    for at, _prio, kind, jid in events:
        job = by_id.get(jid)
        if job is None:
            continue
        if kind == "start" and job.status == JobStatus.scheduled:
            if not _assignment_valid(job):
                by_id[jid] = job.model_copy(update={"status": JobStatus.failed, "completed_at": at})
                failed.append(jid)
                continue
            by_id[jid] = job.model_copy(update={"status": JobStatus.running})
            started.append(jid)
        elif kind == "complete":
            if job.status == JobStatus.scheduled:
                by_id[jid] = job.model_copy(update={"status": JobStatus.running})
                job = by_id[jid]
            if job.status != JobStatus.running:
                continue
            if not _assignment_valid(job):
                by_id[jid] = job.model_copy(update={"status": JobStatus.failed, "completed_at": at})
                failed.append(jid)
                continue
            done = attach_completed_result(
                job.model_copy(
                    update={
                        "status": JobStatus.completed,
                        "completed_at": at,
                    }
                ),
                at,
            )
            by_id[jid] = done
            completed.append(jid)

    jobs = list(by_id.values())
    jobs = _normalize_running_at(jobs, t_to)
    for job in jobs:
        if (
            job.id not in started
            and job.status == JobStatus.running
            and job.scheduled_start
            and _ensure_utc(job.scheduled_start) == t_to
        ):
            started.append(job.id)

    return jobs, sorted(set(started)), sorted(set(completed)), sorted(set(failed))


def planned_advance_delta() -> timedelta:
    return timedelta(hours=1)