"""Pure placement engine: candidates, running reservations, and MILP feasibility.

This module does not mutate the job queue or site store. It accepts snapshots and
returns structured results for ``scheduling_service`` to consume later.

Individually valid candidates for different jobs may still conflict when selected
together; use ``check_simultaneous_feasibility`` to prove joint assignability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from app.schemas.jobs import Job, JobStatus
from app.schemas.sites import HourlySiteProfile, SiteProfile

ONE_HOUR = timedelta(hours=1)
_EPS = 1e-9


# ---------------------------------------------------------------------------
# Internal models
# ---------------------------------------------------------------------------


class DiagnosticCode(str, Enum):
    timezone_naive = "timezone_naive"
    duplicate_hour = "duplicate_hour"
    profile_gap = "profile_gap"
    inconsistent_profile = "inconsistent_profile"
    missing_data_ready = "missing_data_ready"
    no_eligible_site = "no_eligible_site"
    data_unavailable_before_deadline = "data_unavailable_before_deadline"
    missing_telemetry = "missing_telemetry"
    insufficient_gpus = "insufficient_gpus"
    water_cap_restriction = "water_cap_restriction"
    no_feasible_start = "no_feasible_start"
    running_missing_assignment = "running_missing_assignment"
    running_inconsistent_timestamps = "running_inconsistent_timestamps"
    running_missing_telemetry = "running_missing_telemetry"
    running_gpu_overcapacity = "running_gpu_overcapacity"
    running_water_cap_exceeded = "running_water_cap_exceeded"
    limited_telemetry_horizon = "limited_telemetry_horizon"


@dataclass(frozen=True)
class IntervalContribution:
    """Resource use on a half-open interval [interval_start, interval_end)."""

    site_id: str
    interval_start: datetime
    interval_end: datetime
    gpu_count: int
    # Instantaneous incremental water withdrawal rate (L/h) while the job is active.
    incremental_water_rate_lph: float
    incremental_water_liters: float
    energy_kwh: float
    electricity_cost_usd: float


@dataclass(frozen=True)
class PlacementCandidate:
    job_id: str
    site_id: str
    start: datetime
    finish: datetime
    gpu_count: int
    contributions: tuple[IntervalContribution, ...]
    estimated_incremental_water_liters: float
    estimated_energy_kwh: float
    estimated_electricity_cost_usd: float


@dataclass(frozen=True)
class RunningReservation:
    job_id: str
    site_id: str
    scheduled_start: datetime
    reserved_from: datetime
    finish: datetime
    gpu_count: int
    contributions: tuple[IntervalContribution, ...]
    estimated_incremental_water_liters: float
    estimated_energy_kwh: float
    estimated_electricity_cost_usd: float


@dataclass(frozen=True)
class Diagnostic:
    code: DiagnosticCode
    message: str
    job_id: Optional[str] = None
    site_id: Optional[str] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


class FeasibilityStatus(str, Enum):
    feasible = "feasible"
    infeasible = "infeasible"
    solver_error = "solver_error"
    solver_timeout = "solver_timeout"


@dataclass(frozen=True)
class FeasibilityResult:
    status: FeasibilityStatus
    selected: dict[str, PlacementCandidate] = field(default_factory=dict)
    reason: Optional[str] = None
    validated: bool = False


@dataclass(frozen=True)
class CandidateGenerationResult:
    candidates_by_job: dict[str, list[PlacementCandidate]]
    running_reservations: list[RunningReservation]
    diagnostics: list[Diagnostic]
    reconsider_job_ids: tuple[str, ...]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def require_aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware; got {value!r}")
    return value.astimezone(timezone.utc)


def hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600.0


def finish_at(start: datetime, duration_hours: float) -> datetime:
    return start + timedelta(hours=duration_hours)


def _overlap(
    a0: datetime, a1: datetime, b0: datetime, b1: datetime
) -> Optional[tuple[datetime, datetime]]:
    start = max(a0, b0)
    end = min(a1, b1)
    if end <= start:
        return None
    return start, end


# ---------------------------------------------------------------------------
# Site profile indexing
# ---------------------------------------------------------------------------


@dataclass
class _SiteIndex:
    site: SiteProfile
    by_hour: dict[datetime, HourlySiteProfile]
    sorted_hours: list[datetime]
    issues: list[Diagnostic]


def index_site_profiles(sites: list[SiteProfile]) -> dict[str, _SiteIndex]:
    indexed: dict[str, _SiteIndex] = {}
    for site in sites:
        issues: list[Diagnostic] = []
        by_hour: dict[datetime, HourlySiteProfile] = {}
        for row in site.hourly_profiles:
            hour = require_aware_utc(row.hour_start, f"{site.site_id}.hour_start")
            if hour in by_hour:
                issues.append(
                    Diagnostic(
                        code=DiagnosticCode.duplicate_hour,
                        message=f"Duplicate hourly profile at {hour.isoformat()}",
                        site_id=site.site_id,
                        period_start=hour,
                        period_end=hour + ONE_HOUR,
                    )
                )
                continue
            by_hour[hour] = row.model_copy(update={"hour_start": hour})

        sorted_hours = sorted(by_hour)
        for prev, nxt in zip(sorted_hours, sorted_hours[1:]):
            expected = prev + ONE_HOUR
            if nxt != expected:
                issues.append(
                    Diagnostic(
                        code=DiagnosticCode.profile_gap,
                        message=(
                            f"Gap in hourly profiles between {prev.isoformat()} "
                            f"and {nxt.isoformat()}"
                        ),
                        site_id=site.site_id,
                        period_start=prev + ONE_HOUR,
                        period_end=nxt,
                    )
                )
            prev_row = by_hour[prev]
            nxt_row = by_hour[nxt]
            if (
                prev_row.gpu_capacity < 0
                or nxt_row.gpu_capacity < 0
                or prev_row.fixed_water_liters_per_hour < 0
                or nxt_row.fixed_water_liters_per_hour < 0
            ):
                issues.append(
                    Diagnostic(
                        code=DiagnosticCode.inconsistent_profile,
                        message="Negative capacity or fixed water in site profile",
                        site_id=site.site_id,
                        period_start=prev,
                        period_end=nxt + ONE_HOUR,
                    )
                )

        indexed[site.site_id] = _SiteIndex(
            site=site,
            by_hour=by_hour,
            sorted_hours=sorted_hours,
            issues=issues,
        )
    return indexed


def _hours_covering(start: datetime, finish: datetime) -> list[datetime]:
    """Hour bucket starts that intersect [start, finish)."""
    if finish <= start:
        return []
    first = start.replace(minute=0, second=0, microsecond=0)
    if start != first:
        # start already on the hour → first is correct; else floor is correct.
        pass
    hours: list[datetime] = []
    cursor = first
    while cursor < finish:
        hours.append(cursor)
        cursor += ONE_HOUR
    return hours


def _contiguous_profiles(
    index: _SiteIndex, start: datetime, finish: datetime
) -> tuple[Optional[list[HourlySiteProfile]], Optional[Diagnostic]]:
    needed = _hours_covering(start, finish)
    if not needed:
        return [], None
    rows: list[HourlySiteProfile] = []
    for hour in needed:
        row = index.by_hour.get(hour)
        if row is None:
            return None, Diagnostic(
                code=DiagnosticCode.missing_telemetry,
                message=(
                    f"Missing telemetry for hour starting {hour.isoformat()} "
                    f"on site {index.site.site_id}"
                ),
                site_id=index.site.site_id,
                period_start=hour,
                period_end=hour + ONE_HOUR,
            )
        rows.append(row)
    # Contiguity among needed hours is implied by consecutive hour list;
    # still reject if any intermediate gap exists in the index spanning the run.
    for prev, nxt in zip(needed, needed[1:]):
        if nxt != prev + ONE_HOUR:
            return None, Diagnostic(
                code=DiagnosticCode.profile_gap,
                message="Non-contiguous hour sequence required for placement",
                site_id=index.site.site_id,
                period_start=prev,
                period_end=nxt,
            )
    return rows, None


# ---------------------------------------------------------------------------
# Contribution / capacity evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ActiveLoad:
    gpu_count: int
    incremental_water_rate_lph: float


def _contribution_for_overlap(
    site_id: str,
    job_gpus: int,
    row: HourlySiteProfile,
    overlap_start: datetime,
    overlap_end: datetime,
) -> IntervalContribution:
    duration_h = hours_between(overlap_start, overlap_end)
    rate = job_gpus * row.incremental_water_liters_per_gpu_hour
    energy = job_gpus * row.energy_kwh_per_gpu_hour * duration_h
    cost = energy * row.electricity_usd_per_kwh
    return IntervalContribution(
        site_id=site_id,
        interval_start=overlap_start,
        interval_end=overlap_end,
        gpu_count=job_gpus,
        incremental_water_rate_lph=rate,
        incremental_water_liters=rate * duration_h,
        energy_kwh=energy,
        electricity_cost_usd=cost,
    )


def build_contributions(
    site_id: str,
    job_gpus: int,
    index: _SiteIndex,
    active_start: datetime,
    finish: datetime,
) -> tuple[Optional[tuple[IntervalContribution, ...]], Optional[Diagnostic]]:
    rows, missing = _contiguous_profiles(index, active_start, finish)
    if missing is not None:
        return None, missing
    assert rows is not None
    parts: list[IntervalContribution] = []
    for row in rows:
        hour = require_aware_utc(row.hour_start, "hour_start")
        ov = _overlap(active_start, finish, hour, hour + ONE_HOUR)
        if ov is None:
            continue
        parts.append(_contribution_for_overlap(site_id, job_gpus, row, ov[0], ov[1]))
    return tuple(parts), None


def _event_points(
    start: datetime,
    finish: datetime,
    extra: list[datetime],
) -> list[datetime]:
    points = {start, finish, *extra}
    return sorted(p for p in points if start <= p <= finish)


def _load_from_contributions(
    contributions: list[IntervalContribution],
    instant: datetime,
    instant_end: datetime,
) -> _ActiveLoad:
    gpus = 0
    rate = 0.0
    for c in contributions:
        if c.interval_start < instant_end and c.interval_end > instant:
            # Active on the open-closed atomic slice [instant, instant_end)
            gpus += c.gpu_count
            rate += c.incremental_water_rate_lph
    return _ActiveLoad(gpu_count=gpus, incremental_water_rate_lph=rate)


def evaluate_capacity_against_reservations(
    *,
    site_id: str,
    index: _SiteIndex,
    job_gpus: int,
    placement_start: datetime,
    placement_finish: datetime,
    reserved_contributions: list[IntervalContribution],
    job_id: Optional[str] = None,
) -> tuple[Optional[tuple[IntervalContribution, ...]], list[Diagnostic]]:
    """Return contributions if placement fits with reservations; else diagnostics."""
    contribs, missing = build_contributions(
        site_id, job_gpus, index, placement_start, placement_finish
    )
    if missing is not None:
        missing = Diagnostic(
            code=missing.code,
            message=missing.message,
            job_id=job_id,
            site_id=missing.site_id,
            period_start=missing.period_start,
            period_end=missing.period_end,
        )
        return None, [missing]
    assert contribs is not None

    boundaries: list[datetime] = []
    for row_hour in _hours_covering(placement_start, placement_finish):
        boundaries.extend([row_hour, row_hour + ONE_HOUR])
    for c in reserved_contributions:
        if c.site_id != site_id:
            continue
        boundaries.extend([c.interval_start, c.interval_end])
    boundaries.extend([placement_start, placement_finish])

    points = _event_points(placement_start, placement_finish, boundaries)
    diagnostics: list[Diagnostic] = []

    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        # Sample the open interval with a representative instant.
        mid_load_reserved = _load_from_contributions(reserved_contributions, left, right)
        # Candidate itself is fully active on every slice inside [start, finish).
        total_gpus = mid_load_reserved.gpu_count + job_gpus
        hour = left.replace(minute=0, second=0, microsecond=0)
        row = index.by_hour.get(hour)
        if row is None:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.missing_telemetry,
                    message=f"Missing telemetry at {hour.isoformat()}",
                    job_id=job_id,
                    site_id=site_id,
                    period_start=hour,
                    period_end=hour + ONE_HOUR,
                )
            )
            return None, diagnostics

        if total_gpus > row.gpu_capacity:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.insufficient_gpus,
                    message=(
                        f"GPU demand {total_gpus} exceeds capacity {row.gpu_capacity} "
                        f"on {site_id} during [{left.isoformat()}, {right.isoformat()})"
                    ),
                    job_id=job_id,
                    site_id=site_id,
                    period_start=left,
                    period_end=right,
                )
            )
            return None, diagnostics

        if row.water_cap_liters_per_hour is not None:
            cand_rate = job_gpus * row.incremental_water_liters_per_gpu_hour
            total_rate = (
                row.fixed_water_liters_per_hour
                + mid_load_reserved.incremental_water_rate_lph
                + cand_rate
            )
            if total_rate > row.water_cap_liters_per_hour + _EPS:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.water_cap_restriction,
                        message=(
                            f"Water withdrawal rate {total_rate:.4f} L/h exceeds cap "
                            f"{row.water_cap_liters_per_hour} L/h on {site_id} during "
                            f"[{left.isoformat()}, {right.isoformat()})"
                        ),
                        job_id=job_id,
                        site_id=site_id,
                        period_start=left,
                        period_end=right,
                    )
                )
                return None, diagnostics

    return contribs, []


def validate_running_load_alone(
    *,
    site_id: str,
    index: _SiteIndex,
    reserved_contributions: list[IntervalContribution],
    window_start: datetime,
    window_finish: datetime,
    job_id: str,
) -> list[Diagnostic]:
    """Detect fixed+running overcapacity without a candidate."""
    if window_finish <= window_start:
        return []
    boundaries: list[datetime] = [window_start, window_finish]
    for row_hour in _hours_covering(window_start, window_finish):
        boundaries.extend([row_hour, row_hour + ONE_HOUR])
    for c in reserved_contributions:
        if c.site_id == site_id:
            boundaries.extend([c.interval_start, c.interval_end])
    points = _event_points(window_start, window_finish, boundaries)
    diagnostics: list[Diagnostic] = []
    seen_gpu: set[tuple[datetime, datetime]] = set()
    seen_water: set[tuple[datetime, datetime]] = set()

    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        load = _load_from_contributions(reserved_contributions, left, right)
        hour = left.replace(minute=0, second=0, microsecond=0)
        row = index.by_hour.get(hour)
        if row is None:
            continue
        if load.gpu_count > row.gpu_capacity and (left, right) not in seen_gpu:
            seen_gpu.add((left, right))
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.running_gpu_overcapacity,
                    message=(
                        f"Running GPU load {load.gpu_count} exceeds capacity "
                        f"{row.gpu_capacity} on {site_id}"
                    ),
                    job_id=job_id,
                    site_id=site_id,
                    period_start=left,
                    period_end=right,
                )
            )
        if row.water_cap_liters_per_hour is not None:
            total_rate = row.fixed_water_liters_per_hour + load.incremental_water_rate_lph
            if total_rate > row.water_cap_liters_per_hour + _EPS and (
                left,
                right,
            ) not in seen_water:
                seen_water.add((left, right))
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.running_water_cap_exceeded,
                        message=(
                            f"Fixed plus running water rate {total_rate:.4f} L/h exceeds "
                            f"cap {row.water_cap_liters_per_hour} L/h on {site_id}"
                        ),
                        job_id=job_id,
                        site_id=site_id,
                        period_start=left,
                        period_end=right,
                    )
                )
    return diagnostics


# ---------------------------------------------------------------------------
# Running reservations
# ---------------------------------------------------------------------------


def reserve_running_jobs(
    jobs: list[Job],
    site_index: dict[str, _SiteIndex],
    current_time: datetime,
) -> tuple[list[RunningReservation], list[Diagnostic]]:
    current_time = require_aware_utc(current_time, "current_time")
    reservations: list[RunningReservation] = []
    diagnostics: list[Diagnostic] = []

    for job in jobs:
        if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.unfulfilled}:
            continue
        if job.status != JobStatus.running:
            continue

        if job.assigned_site_id is None or job.scheduled_start is None:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.running_missing_assignment,
                    message=(
                        f"Running job {job.id} lacks assigned_site_id/scheduled_start"
                    ),
                    job_id=job.id,
                )
            )
            continue

        try:
            scheduled_start = require_aware_utc(
                job.scheduled_start, f"{job.id}.scheduled_start"
            )
            deadline = require_aware_utc(job.deadline, f"{job.id}.deadline")
        except ValueError as exc:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.running_inconsistent_timestamps,
                    message=str(exc),
                    job_id=job.id,
                )
            )
            continue

        finish = finish_at(scheduled_start, job.duration_hours)
        if finish <= current_time:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.running_inconsistent_timestamps,
                    message=(
                        f"Running job {job.id} finish {finish.isoformat()} is not after "
                        f"current_time {current_time.isoformat()}"
                    ),
                    job_id=job.id,
                    site_id=job.assigned_site_id,
                    period_start=scheduled_start,
                    period_end=finish,
                )
            )
            continue
        if scheduled_start > current_time + timedelta(seconds=1):
            # Started in the future while marked running — inconsistent.
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.running_inconsistent_timestamps,
                    message=(
                        f"Running job {job.id} scheduled_start "
                        f"{scheduled_start.isoformat()} is after current_time"
                    ),
                    job_id=job.id,
                    site_id=job.assigned_site_id,
                    period_start=scheduled_start,
                    period_end=finish,
                )
            )
            continue

        site_id = job.assigned_site_id
        index = site_index.get(site_id)
        if index is None:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.running_missing_telemetry,
                    message=f"No site profile for running job site {site_id}",
                    job_id=job.id,
                    site_id=site_id,
                )
            )
            continue

        reserved_from = max(scheduled_start, current_time)
        contribs, missing = build_contributions(
            site_id, job.gpu_count, index, reserved_from, finish
        )
        if missing is not None:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.running_missing_telemetry,
                    message=missing.message,
                    job_id=job.id,
                    site_id=site_id,
                    period_start=missing.period_start,
                    period_end=missing.period_end,
                )
            )
            continue

        assert contribs is not None
        water = sum(c.incremental_water_liters for c in contribs)
        energy = sum(c.energy_kwh for c in contribs)
        cost = sum(c.electricity_cost_usd for c in contribs)
        reservation = RunningReservation(
            job_id=job.id,
            site_id=site_id,
            scheduled_start=scheduled_start,
            reserved_from=reserved_from,
            finish=finish,
            gpu_count=job.gpu_count,
            contributions=contribs,
            estimated_incremental_water_liters=water,
            estimated_energy_kwh=energy,
            estimated_electricity_cost_usd=cost,
        )
        reservations.append(reservation)

        # Capacity self-check for this reservation against site caps/capacity.
        diagnostics.extend(
            validate_running_load_alone(
                site_id=site_id,
                index=index,
                reserved_contributions=list(contribs),
                window_start=reserved_from,
                window_finish=finish,
                job_id=job.id,
            )
        )
        _ = deadline  # deadline is not enforced for already-running fixed work

    # Cross-running aggregate capacity (multiple running jobs on one site).
    by_site: dict[str, list[IntervalContribution]] = {}
    for r in reservations:
        by_site.setdefault(r.site_id, []).extend(r.contributions)
    for site_id, contribs in by_site.items():
        index = site_index[site_id]
        if not contribs:
            continue
        window_start = min(c.interval_start for c in contribs)
        window_finish = max(c.interval_end for c in contribs)
        diagnostics.extend(
            validate_running_load_alone(
                site_id=site_id,
                index=index,
                reserved_contributions=contribs,
                window_start=window_start,
                window_finish=window_finish,
                job_id="*",
            )
        )

    reservations.sort(key=lambda r: (r.site_id, r.job_id, r.reserved_from))
    return reservations, diagnostics


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def _permitted_starts(
    index: _SiteIndex,
    *,
    current_time: datetime,
    earliest_start: datetime,
    data_ready: datetime,
    deadline: datetime,
    duration_hours: float,
) -> list[datetime]:
    starts: list[datetime] = []
    for hour in index.sorted_hours:
        if hour < current_time:
            continue
        if hour < earliest_start:
            continue
        if hour < data_ready:
            continue
        finish = finish_at(hour, duration_hours)
        if finish > deadline:
            continue
        starts.append(hour)
    return starts


def _reason_summary(codes: set[DiagnosticCode]) -> DiagnosticCode:
    priority = [
        DiagnosticCode.no_eligible_site,
        DiagnosticCode.missing_data_ready,
        DiagnosticCode.data_unavailable_before_deadline,
        DiagnosticCode.missing_telemetry,
        DiagnosticCode.insufficient_gpus,
        DiagnosticCode.water_cap_restriction,
        DiagnosticCode.no_feasible_start,
    ]
    for code in priority:
        if code in codes:
            return code
    return DiagnosticCode.no_feasible_start


def generate_placement_candidates(
    jobs: list[Job],
    sites: list[SiteProfile],
    current_time: datetime,
    *,
    reconsider_job_ids: Optional[list[str]] = None,
) -> CandidateGenerationResult:
    """Enumerate eligible site/start placements without mutating inputs.

    Running jobs are reserved in place. Queued jobs and scheduled jobs listed in
    ``reconsider_job_ids`` (default: all scheduled) receive candidate lists.
    """
    current_time = require_aware_utc(current_time, "current_time")
    site_index = index_site_profiles(sites)
    diagnostics: list[Diagnostic] = []
    for index in site_index.values():
        diagnostics.extend(index.issues)
        if index.sorted_hours:
            last = index.sorted_hours[-1]
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.limited_telemetry_horizon,
                    message=(
                        f"Site {index.site.site_id} telemetry covers "
                        f"{index.sorted_hours[0].isoformat()} through "
                        f"{(last + ONE_HOUR).isoformat()} "
                        f"({len(index.sorted_hours)} hourly records); "
                        "placements requiring later hours are excluded"
                    ),
                    site_id=index.site.site_id,
                    period_start=index.sorted_hours[0],
                    period_end=last + ONE_HOUR,
                )
            )

    reservations, running_diags = reserve_running_jobs(jobs, site_index, current_time)
    diagnostics.extend(running_diags)
    reserved_by_site: dict[str, list[IntervalContribution]] = {}
    for r in reservations:
        reserved_by_site.setdefault(r.site_id, []).extend(r.contributions)

    if reconsider_job_ids is None:
        reconsider = {
            j.id
            for j in jobs
            if j.status in {JobStatus.queued, JobStatus.scheduled}
        }
    else:
        reconsider = set(reconsider_job_ids)

    reconsider_ordered = tuple(
        j.id for j in jobs if j.id in reconsider and j.status != JobStatus.running
    )

    candidates_by_job: dict[str, list[PlacementCandidate]] = {}

    for job in jobs:
        if job.id not in reconsider:
            continue
        if job.status not in {JobStatus.queued, JobStatus.scheduled}:
            continue

        earliest = require_aware_utc(job.earliest_start, f"{job.id}.earliest_start")
        deadline = require_aware_utc(job.deadline, f"{job.id}.deadline")
        job_candidates: list[PlacementCandidate] = []
        site_codes: set[DiagnosticCode] = set()
        site_messages: list[str] = []

        if not job.eligible_site_ids:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.no_eligible_site,
                    message=f"Job {job.id} has no eligible sites",
                    job_id=job.id,
                )
            )
            candidates_by_job[job.id] = []
            continue

        for site_id in sorted(job.eligible_site_ids):
            index = site_index.get(site_id)
            if index is None:
                site_codes.add(DiagnosticCode.missing_telemetry)
                site_messages.append(f"No profile for eligible site {site_id}")
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.missing_telemetry,
                        message=f"No site profile for eligible site {site_id}",
                        job_id=job.id,
                        site_id=site_id,
                    )
                )
                continue

            if site_id not in job.data_ready_at_by_site:
                site_codes.add(DiagnosticCode.missing_data_ready)
                msg = (
                    f"Job {job.id} missing data_ready_at_by_site entry for {site_id}"
                )
                site_messages.append(msg)
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.missing_data_ready,
                        message=msg,
                        job_id=job.id,
                        site_id=site_id,
                    )
                )
                continue

            data_ready = require_aware_utc(
                job.data_ready_at_by_site[site_id],
                f"{job.id}.data_ready_at_by_site[{site_id}]",
            )
            # Data must arrive early enough that some start can still finish on time.
            if finish_at(max(current_time, earliest, data_ready), job.duration_hours) > deadline:
                # Still try enumerated starts; may already be impossible.
                if data_ready >= deadline or finish_at(data_ready, job.duration_hours) > deadline:
                    site_codes.add(DiagnosticCode.data_unavailable_before_deadline)
                    diagnostics.append(
                        Diagnostic(
                            code=DiagnosticCode.data_unavailable_before_deadline,
                            message=(
                                f"Data at {site_id} ready {data_ready.isoformat()} "
                                f"cannot finish by deadline {deadline.isoformat()}"
                            ),
                            job_id=job.id,
                            site_id=site_id,
                        )
                    )

            starts = _permitted_starts(
                index,
                current_time=current_time,
                earliest_start=earliest,
                data_ready=data_ready,
                deadline=deadline,
                duration_hours=job.duration_hours,
            )
            if not starts and index.sorted_hours:
                # Distinguish missing horizon vs readiness vs capacity.
                any_hour_after_ready = any(
                    h >= max(current_time, earliest, data_ready) for h in index.sorted_hours
                )
                if not any_hour_after_ready:
                    site_codes.add(DiagnosticCode.missing_telemetry)
                elif finish_at(
                    max(current_time, earliest, data_ready), job.duration_hours
                ) > deadline:
                    site_codes.add(DiagnosticCode.data_unavailable_before_deadline)

            for start in starts:
                finish = finish_at(start, job.duration_hours)
                # Allow completion exactly at deadline (finish <= deadline already).
                contribs, caps_diags = evaluate_capacity_against_reservations(
                    site_id=site_id,
                    index=index,
                    job_gpus=job.gpu_count,
                    placement_start=start,
                    placement_finish=finish,
                    reserved_contributions=reserved_by_site.get(site_id, []),
                    job_id=job.id,
                )
                if contribs is None:
                    for d in caps_diags:
                        site_codes.add(d.code)
                        diagnostics.append(d)
                    continue

                water = sum(c.incremental_water_liters for c in contribs)
                energy = sum(c.energy_kwh for c in contribs)
                cost = sum(c.electricity_cost_usd for c in contribs)
                job_candidates.append(
                    PlacementCandidate(
                        job_id=job.id,
                        site_id=site_id,
                        start=start,
                        finish=finish,
                        gpu_count=job.gpu_count,
                        contributions=contribs,
                        estimated_incremental_water_liters=water,
                        estimated_energy_kwh=energy,
                        estimated_electricity_cost_usd=cost,
                    )
                )

        job_candidates.sort(key=lambda c: (c.site_id, c.start, c.finish))
        candidates_by_job[job.id] = job_candidates
        if not job_candidates:
            code = _reason_summary(site_codes) if site_codes else DiagnosticCode.no_feasible_start
            diagnostics.append(
                Diagnostic(
                    code=code,
                    message=(
                        f"Job {job.id} has no feasible placement candidates"
                        + (f" ({'; '.join(site_messages[:3])})" if site_messages else "")
                    ),
                    job_id=job.id,
                )
            )

    return CandidateGenerationResult(
        candidates_by_job=candidates_by_job,
        running_reservations=reservations,
        diagnostics=diagnostics,
        reconsider_job_ids=reconsider_ordered,
    )


# ---------------------------------------------------------------------------
# Simultaneous feasibility (SciPy MILP)
# ---------------------------------------------------------------------------


def _atomic_slices(
    site_id: str,
    candidates: list[PlacementCandidate],
    reservations: list[RunningReservation],
    site_index: dict[str, _SiteIndex],
) -> list[tuple[datetime, datetime]]:
    index = site_index[site_id]
    points: set[datetime] = set()
    for hour in index.sorted_hours:
        points.add(hour)
        points.add(hour + ONE_HOUR)
    for c in candidates:
        if c.site_id != site_id:
            continue
        points.add(c.start)
        points.add(c.finish)
        for part in c.contributions:
            points.add(part.interval_start)
            points.add(part.interval_end)
    for r in reservations:
        if r.site_id != site_id:
            continue
        points.add(r.reserved_from)
        points.add(r.finish)
        for part in r.contributions:
            points.add(part.interval_start)
            points.add(part.interval_end)
    ordered = sorted(points)
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if b > a]


def _candidate_active(
    candidate: PlacementCandidate, left: datetime, right: datetime
) -> bool:
    return candidate.start < right and candidate.finish > left


def _reserved_load_on_slice(
    reservations: list[RunningReservation],
    site_id: str,
    left: datetime,
    right: datetime,
) -> _ActiveLoad:
    contribs: list[IntervalContribution] = []
    for r in reservations:
        if r.site_id == site_id:
            contribs.extend(r.contributions)
    return _load_from_contributions(contribs, left, right)


def validate_selection(
    selected: dict[str, PlacementCandidate],
    running_reservations: list[RunningReservation],
    sites: list[SiteProfile],
) -> list[Diagnostic]:
    """Re-check GPU and water rates for a concrete selection."""
    site_index = index_site_profiles(sites)
    diagnostics: list[Diagnostic] = []
    by_site_candidates: dict[str, list[PlacementCandidate]] = {}
    for cand in selected.values():
        by_site_candidates.setdefault(cand.site_id, []).append(cand)

    for site_id, cands in by_site_candidates.items():
        index = site_index.get(site_id)
        if index is None:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.missing_telemetry,
                    message=f"Missing site {site_id} while validating selection",
                    site_id=site_id,
                )
            )
            continue
        for left, right in _atomic_slices(
            site_id, cands, running_reservations, site_index
        ):
            hour = left.replace(minute=0, second=0, microsecond=0)
            row = index.by_hour.get(hour)
            if row is None:
                continue
            reserved = _reserved_load_on_slice(running_reservations, site_id, left, right)
            gpus = reserved.gpu_count
            rate = reserved.incremental_water_rate_lph
            for cand in cands:
                if _candidate_active(cand, left, right):
                    gpus += cand.gpu_count
                    rate += cand.gpu_count * row.incremental_water_liters_per_gpu_hour
            if gpus > row.gpu_capacity:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.insufficient_gpus,
                        message=(
                            f"Selection uses {gpus} GPUs > capacity {row.gpu_capacity} "
                            f"on {site_id}"
                        ),
                        site_id=site_id,
                        period_start=left,
                        period_end=right,
                    )
                )
            if row.water_cap_liters_per_hour is not None:
                total = row.fixed_water_liters_per_hour + rate
                if total > row.water_cap_liters_per_hour + _EPS:
                    diagnostics.append(
                        Diagnostic(
                            code=DiagnosticCode.water_cap_restriction,
                            message=(
                                f"Selection water rate {total:.4f} L/h exceeds cap "
                                f"{row.water_cap_liters_per_hour} on {site_id}"
                            ),
                            site_id=site_id,
                            period_start=left,
                            period_end=right,
                        )
                    )
    return diagnostics


def check_simultaneous_feasibility(
    candidates_by_job: dict[str, list[PlacementCandidate]],
    running_reservations: list[RunningReservation],
    sites: list[SiteProfile],
    *,
    job_ids: Optional[list[str]] = None,
    time_limit_sec: float = 10.0,
) -> FeasibilityResult:
    """Zero-cost MILP: pick exactly one candidate per job if jointly feasible.

    Scheduled jobs present in ``candidates_by_job`` are treated as reconsidered —
    their prior placement is not reserved. Running reservations stay fixed.

    Returns a feasible selection, proven infeasibility, or solver timeout/error.
    """
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    site_index = index_site_profiles(sites)
    if job_ids is None:
        target_jobs = sorted(candidates_by_job)
    else:
        target_jobs = list(job_ids)

    flat: list[PlacementCandidate] = []
    job_of_var: list[str] = []
    for job_id in target_jobs:
        opts = candidates_by_job.get(job_id, [])
        if not opts:
            return FeasibilityResult(
                status=FeasibilityStatus.infeasible,
                reason=f"Job {job_id} has no candidates",
            )
        for cand in opts:
            flat.append(cand)
            job_of_var.append(job_id)

    n = len(flat)
    if n == 0:
        return FeasibilityResult(
            status=FeasibilityStatus.feasible,
            selected={},
            validated=True,
        )

    # Equality: exactly one candidate per job.
    unique_jobs = list(dict.fromkeys(job_of_var))
    a_eq_rows: list[list[float]] = []
    b_eq: list[float] = []
    for job_id in unique_jobs:
        row = [1.0 if job_of_var[i] == job_id else 0.0 for i in range(n)]
        a_eq_rows.append(row)
        b_eq.append(1.0)

    # Inequality: GPU and water on atomic slices per site.
    a_ub_rows: list[list[float]] = []
    b_ub: list[float] = []
    sites_touched = {c.site_id for c in flat} | {r.site_id for r in running_reservations}
    for site_id in sorted(sites_touched):
        if site_id not in site_index:
            return FeasibilityResult(
                status=FeasibilityStatus.infeasible,
                reason=f"Missing site profile {site_id}",
            )
        index = site_index[site_id]
        for left, right in _atomic_slices(site_id, flat, running_reservations, site_index):
            hour = left.replace(minute=0, second=0, microsecond=0)
            row_prof = index.by_hour.get(hour)
            if row_prof is None:
                continue
            reserved = _reserved_load_on_slice(running_reservations, site_id, left, right)

            gpu_coeffs = [
                float(flat[i].gpu_count)
                if flat[i].site_id == site_id and _candidate_active(flat[i], left, right)
                else 0.0
                for i in range(n)
            ]
            gpu_rhs = row_prof.gpu_capacity - reserved.gpu_count
            if any(gpu_coeffs):
                a_ub_rows.append(gpu_coeffs)
                b_ub.append(float(gpu_rhs))

            if row_prof.water_cap_liters_per_hour is not None:
                water_coeffs = []
                for i in range(n):
                    if flat[i].site_id == site_id and _candidate_active(
                        flat[i], left, right
                    ):
                        water_coeffs.append(
                            float(
                                flat[i].gpu_count
                                * row_prof.incremental_water_liters_per_gpu_hour
                            )
                        )
                    else:
                        water_coeffs.append(0.0)
                water_rhs = (
                    row_prof.water_cap_liters_per_hour
                    - row_prof.fixed_water_liters_per_hour
                    - reserved.incremental_water_rate_lph
                )
                if any(water_coeffs):
                    a_ub_rows.append(water_coeffs)
                    b_ub.append(float(water_rhs))

    constraints = []
    if a_eq_rows:
        a_eq = np.asarray(a_eq_rows, dtype=float)
        b_eq_arr = np.asarray(b_eq, dtype=float)
        constraints.append(LinearConstraint(a_eq, b_eq_arr, b_eq_arr))
    if a_ub_rows:
        a_ub = np.asarray(a_ub_rows, dtype=float)
        # lb = -inf, ub = b_ub  →  A x <= b
        lb = np.full(len(b_ub), -np.inf)
        ub = np.asarray(b_ub, dtype=float)
        constraints.append(LinearConstraint(a_ub, lb, ub))

    c = np.zeros(n)
    integrality = np.ones(n, dtype=int)
    bounds = Bounds(lb=np.zeros(n), ub=np.ones(n))

    try:
        result = milp(
            c,
            integrality=integrality,
            bounds=bounds,
            constraints=constraints,
            options={"time_limit": time_limit_sec},
        )
    except Exception as exc:  # noqa: BLE001 — surface solver failures distinctly
        return FeasibilityResult(
            status=FeasibilityStatus.solver_error,
            reason=f"SciPy milp raised: {exc}",
        )

    if result.status == 1:
        # timelimit (HiGHS status); scipy uses status codes
        pass

    # SciPy milp: 0 success; 1 iteration/time limit; others failure.
    status_code = int(result.status)
    if status_code == 0 and result.x is not None:
        selected: dict[str, PlacementCandidate] = {}
        for i, val in enumerate(result.x):
            if val >= 0.5:
                selected[job_of_var[i]] = flat[i]
        if set(selected) != set(unique_jobs):
            return FeasibilityResult(
                status=FeasibilityStatus.solver_error,
                reason="Solver returned an incomplete selection",
            )
        violations = validate_selection(selected, running_reservations, sites)
        if violations:
            return FeasibilityResult(
                status=FeasibilityStatus.solver_error,
                reason=(
                    "Solver selection failed post-validation: "
                    + violations[0].message
                ),
                selected=selected,
                validated=False,
            )
        return FeasibilityResult(
            status=FeasibilityStatus.feasible,
            selected=selected,
            validated=True,
        )

    message = getattr(result, "message", "") or ""
    if status_code == 1 or "time" in message.lower():
        return FeasibilityResult(
            status=FeasibilityStatus.solver_timeout,
            reason=message or "MILP time limit reached",
        )

    # Proven infeasible or other failure — distinguish when message indicates infeasibility.
    lower = message.lower()
    if status_code in {2, 3, 4} or "infeas" in lower:
        return FeasibilityResult(
            status=FeasibilityStatus.infeasible,
            reason=message or "No feasible joint assignment of candidates",
        )
    return FeasibilityResult(
        status=FeasibilityStatus.solver_error,
        reason=message or f"MILP failed with status={status_code}",
    )


# ---------------------------------------------------------------------------
# Shared-fixture demo helper
# ---------------------------------------------------------------------------


def demo_shared_fixture_candidates() -> CandidateGenerationResult:
    """Load fixtures/jobs.json + sites.json and generate candidates at demo clock."""
    import json

    from app.config import DEMO_CLOCK_ANCHOR_ISO, FIXTURES_DIR

    with (FIXTURES_DIR / "jobs.json").open(encoding="utf-8") as f:
        jobs_raw = json.load(f)
    with (FIXTURES_DIR / "sites.json").open(encoding="utf-8") as f:
        sites_raw = json.load(f)

    jobs = [Job.model_validate(j) for j in jobs_raw["jobs"]]
    sites = [SiteProfile.model_validate(s) for s in sites_raw["sites"]]
    current_time = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)
    return generate_placement_candidates(jobs, sites, current_time)


def _format_demo(result: CandidateGenerationResult) -> str:
    lines: list[str] = []
    lines.append("AquaLibri placement engine - shared fixture demo")
    lines.append(f"Reconsider jobs: {', '.join(result.reconsider_job_ids) or '(none)'}")
    lines.append(f"Running reservations: {len(result.running_reservations)}")
    for r in result.running_reservations:
        lines.append(
            f"  reserve {r.job_id} @ {r.site_id} "
            f"[{r.reserved_from.isoformat()} → {r.finish.isoformat()}] "
            f"gpus={r.gpu_count}"
        )
    for job_id, cands in result.candidates_by_job.items():
        lines.append(f"Candidates for {job_id}: {len(cands)}")
        for c in cands[:10]:
            lines.append(
                f"  {c.site_id} start={c.start.isoformat()} finish={c.finish.isoformat()} "
                f"water_L={c.estimated_incremental_water_liters:.3f} "
                f"energy_kWh={c.estimated_energy_kwh:.3f} "
                f"elec_USD={c.estimated_electricity_cost_usd:.4f}"
            )
    horizon = [
        d
        for d in result.diagnostics
        if d.code
        in {
            DiagnosticCode.limited_telemetry_horizon,
            DiagnosticCode.missing_telemetry,
            DiagnosticCode.running_missing_telemetry,
            DiagnosticCode.no_feasible_start,
            DiagnosticCode.data_unavailable_before_deadline,
        }
    ]
    lines.append("Coverage / exclusion diagnostics:")
    for d in horizon:
        loc = d.site_id or d.job_id or ""
        lines.append(f"  [{d.code.value}] {loc}: {d.message}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(_format_demo(demo_shared_fixture_candidates()))
