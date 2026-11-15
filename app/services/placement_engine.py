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

# Multi-objective placement weights (tunable for demos).
# Priority: water/hardware feasibility (hard) >> equilibrium >> cost >> earliness.
PLACEMENT_WEIGHT_UTILIZATION = 10.0  # W1 — (jobs_at_site + 1)^2
PLACEMENT_WEIGHT_COST = 1.0  # W2 — projected electricity USD
PLACEMENT_WEIGHT_TIME = 0.1  # W3 — hours delayed from earliest_start


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


@dataclass(frozen=True)
class WaterCapOverride:
    """Temporary water cap applied to one site over [start, end)."""

    site_id: str
    start: datetime
    end: datetime
    water_cap_liters_per_hour: float


class OptimizeObjective(str, Enum):
    min_cost = "min_cost"
    min_water = "min_water"
    # Greedy multi-objective: equilibrium + cost + earliness under hard caps.
    balanced = "balanced"


@dataclass(frozen=True)
class OptimizeResult:
    status: FeasibilityStatus
    selected: dict[str, PlacementCandidate] = field(default_factory=dict)
    unfulfilled_job_ids: tuple[str, ...] = ()
    objective_value: Optional[float] = None
    reason: Optional[str] = None
    validated: bool = False


@dataclass(frozen=True)
class JobDeadlineResult:
    job_id: str
    met_deadline: bool
    projected_completion: Optional[datetime]
    unfulfilled: bool
    unfulfilled_reason: Optional[str]


@dataclass(frozen=True)
class SiteEvaluation:
    site_id: str
    water_liters: float
    water_cap_violations: int
    energy_kwh: float
    energy_cost_usd: float
    peak_withdrawal_lph: float
    peak_withdrawal_at: Optional[datetime]
    within_cap: bool
    active_cap_at_peak: Optional[float]


@dataclass(frozen=True)
class ScheduleEvaluation:
    horizon_start: datetime
    horizon_end: datetime
    total_water_liters: float
    total_energy_kwh: float
    energy_cost_usd: float
    water_cost_usd: float
    miss_penalties_usd: float
    total_cost_usd: float
    jobs_on_time: int
    jobs_late: int
    jobs_unfulfilled: int
    per_site: tuple[SiteEvaluation, ...]
    deadline_outcomes: tuple[JobDeadlineResult, ...]
    diagnostics: tuple[Diagnostic, ...]


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


def effective_water_cap(
    row_cap: Optional[float],
    site_id: str,
    left: datetime,
    right: datetime,
    overrides: Optional[list[WaterCapOverride]],
) -> Optional[float]:
    """Stricter of profile cap and any overlapping temporary overrides."""
    caps: list[float] = []
    if row_cap is not None:
        caps.append(row_cap)
    if overrides:
        for ov in overrides:
            if ov.site_id != site_id:
                continue
            if ov.start < right and ov.end > left:
                caps.append(ov.water_cap_liters_per_hour)
    if not caps:
        return None
    return min(caps)


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
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
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
    if water_cap_overrides:
        for ov in water_cap_overrides:
            if ov.site_id == site_id:
                boundaries.extend([ov.start, ov.end])

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

        cap = effective_water_cap(
            row.water_cap_liters_per_hour, site_id, left, right, water_cap_overrides
        )
        if cap is not None:
            cand_rate = job_gpus * row.incremental_water_liters_per_gpu_hour
            total_rate = (
                row.fixed_water_liters_per_hour
                + mid_load_reserved.incremental_water_rate_lph
                + cand_rate
            )
            if total_rate > cap + _EPS:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.water_cap_restriction,
                        message=(
                            f"Water withdrawal rate {total_rate:.4f} L/h exceeds cap "
                            f"{cap} L/h on {site_id} during "
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
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
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
    if water_cap_overrides:
        for ov in water_cap_overrides:
            if ov.site_id == site_id:
                boundaries.extend([ov.start, ov.end])
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
        cap = effective_water_cap(
            row.water_cap_liters_per_hour, site_id, left, right, water_cap_overrides
        )
        if cap is not None:
            total_rate = row.fixed_water_liters_per_hour + load.incremental_water_rate_lph
            if total_rate > cap + _EPS and (left, right) not in seen_water:
                seen_water.add((left, right))
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.running_water_cap_exceeded,
                        message=(
                            f"Fixed plus running water rate {total_rate:.4f} L/h exceeds "
                            f"cap {cap} L/h on {site_id}"
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
    *,
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
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
                water_cap_overrides=water_cap_overrides,
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
                water_cap_overrides=water_cap_overrides,
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
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
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

    reservations, running_diags = reserve_running_jobs(
        jobs,
        site_index,
        current_time,
        water_cap_overrides=water_cap_overrides,
    )
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
                    water_cap_overrides=water_cap_overrides,
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
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
    extra_bounds: Optional[list[datetime]] = None,
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
    if water_cap_overrides:
        for ov in water_cap_overrides:
            if ov.site_id == site_id:
                points.add(ov.start)
                points.add(ov.end)
    if extra_bounds:
        points.update(extra_bounds)
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
    *,
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
) -> list[Diagnostic]:
    """Re-check GPU and water rates for a concrete selection."""
    site_index = index_site_profiles(sites)
    diagnostics: list[Diagnostic] = []
    by_site_candidates: dict[str, list[PlacementCandidate]] = {}
    for cand in selected.values():
        by_site_candidates.setdefault(cand.site_id, []).append(cand)

    sites_to_check = set(by_site_candidates) | {
        r.site_id for r in running_reservations
    }
    for site_id in sites_to_check:
        index = site_index.get(site_id)
        cands = by_site_candidates.get(site_id, [])
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
            site_id,
            cands,
            running_reservations,
            site_index,
            water_cap_overrides=water_cap_overrides,
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
            cap = effective_water_cap(
                row.water_cap_liters_per_hour, site_id, left, right, water_cap_overrides
            )
            if cap is not None:
                total = row.fixed_water_liters_per_hour + rate
                if total > cap + _EPS:
                    diagnostics.append(
                        Diagnostic(
                            code=DiagnosticCode.water_cap_restriction,
                            message=(
                                f"Selection water rate {total:.4f} L/h exceeds cap "
                                f"{cap} on {site_id}"
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
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
) -> FeasibilityResult:
    """Zero-cost MILP: pick exactly one candidate per job if jointly feasible.

    Scheduled jobs present in ``candidates_by_job`` are treated as reconsidered —
    their prior placement is not reserved. Running jobs remain fixed.

    Returns a feasible selection, proven infeasibility, or solver timeout/error.
    """
    result = optimize_placements(
        candidates_by_job,
        running_reservations,
        sites,
        jobs_by_id={},
        objective=OptimizeObjective.min_cost,
        job_ids=job_ids,
        allow_unfulfilled=set(),
        cost_ceiling=None,
        water_cap_overrides=water_cap_overrides,
        prefer_placements=None,
        time_limit_sec=time_limit_sec,
        secondary_change_weight=0.0,
        secondary_cost_weight=0.0,
    )
    return FeasibilityResult(
        status=result.status,
        selected=result.selected,
        reason=result.reason,
        validated=result.validated,
    )


def score_placement_candidate(
    candidate: PlacementCandidate,
    *,
    jobs_already_at_site: int,
    earliest_start: datetime,
    prefer: Optional[tuple[str, datetime]] = None,
    change_weight: float = 1e-6,
    w_utilization: float = PLACEMENT_WEIGHT_UTILIZATION,
    w_cost: float = PLACEMENT_WEIGHT_COST,
    w_time: float = PLACEMENT_WEIGHT_TIME,
) -> float:
    """Weighted score — lower is better. Caller must ensure hard-cap feasibility."""
    utilization_penalty = float(jobs_already_at_site + 1) ** 2
    cost_penalty = float(candidate.estimated_electricity_cost_usd)
    earliest = require_aware_utc(earliest_start, "earliest_start")
    start = require_aware_utc(candidate.start, "candidate.start")
    hours_delayed = max(0.0, (start - earliest).total_seconds() / 3600.0)
    time_penalty = hours_delayed
    score = (
        w_utilization * utilization_penalty
        + w_cost * cost_penalty
        + w_time * time_penalty
    )
    if prefer is not None and (candidate.site_id, candidate.start) != prefer:
        score += change_weight
    return score


def optimize_placements_balanced(
    candidates_by_job: dict[str, list[PlacementCandidate]],
    running_reservations: list[RunningReservation],
    sites: list[SiteProfile],
    jobs_by_id: dict[str, Job],
    *,
    job_ids: Optional[list[str]] = None,
    allow_unfulfilled: Optional[set[str]] = None,
    cost_ceiling: Optional[float] = None,
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
    prefer_placements: Optional[dict[str, tuple[str, datetime]]] = None,
    secondary_change_weight: float = 1e-6,
    w_utilization: float = PLACEMENT_WEIGHT_UTILIZATION,
    w_cost: float = PLACEMENT_WEIGHT_COST,
    w_time: float = PLACEMENT_WEIGHT_TIME,
) -> OptimizeResult:
    """Greedy multi-objective placement under hard water/GPU caps.

    For each unassigned job, score every valid (site, start) candidate:

        score = W1*(jobs_at_site+1)^2 + W2*cost + W3*hours_delayed

    then lock in the minimum score and update site utilization. Candidates that
    would violate joint GPU/water caps with the partial selection are skipped
    (hard infeasible / infinite score).
    """
    allow_unfulfilled = set(allow_unfulfilled or ())
    prefer_placements = prefer_placements or {}

    if job_ids is None:
        target_jobs = sorted(candidates_by_job)
    else:
        target_jobs = list(job_ids)

    target_set = set(target_jobs)
    running_ids = {r.job_id for r in running_reservations}

    site_job_counts: dict[str, int] = {}
    for res in running_reservations:
        site_job_counts[res.site_id] = site_job_counts.get(res.site_id, 0) + 1
    # Count fixed scheduled jobs that are not being re-placed (utilization seed).
    for jid, job in jobs_by_id.items():
        if jid in target_set or jid in running_ids:
            continue
        if job.assigned_site_id and job.status == JobStatus.scheduled:
            site_job_counts[job.assigned_site_id] = (
                site_job_counts.get(job.assigned_site_id, 0) + 1
            )

    running_elec = sum(
        float(r.estimated_electricity_cost_usd) for r in running_reservations
    )

    def _job_sort_key(job_id: str) -> tuple:
        job = jobs_by_id.get(job_id)
        critical = 0 if (job and job.critical) else 1
        n_opts = len(candidates_by_job.get(job_id, []))
        deadline = job.deadline if job else datetime.max.replace(tzinfo=timezone.utc)
        return (critical, n_opts, deadline, job_id)

    selected: dict[str, PlacementCandidate] = {}
    unfulfilled: list[str] = []
    objective_total = 0.0

    for job_id in sorted(target_jobs, key=_job_sort_key):
        opts = list(candidates_by_job.get(job_id, []))
        job = jobs_by_id.get(job_id)
        earliest = (
            job.earliest_start
            if job is not None
            else datetime.min.replace(tzinfo=timezone.utc)
        )
        prefer = prefer_placements.get(job_id)

        best: Optional[PlacementCandidate] = None
        best_score = float("inf")

        for cand in opts:
            trial = dict(selected)
            trial[job_id] = cand
            if validate_selection(
                trial,
                running_reservations,
                sites,
                water_cap_overrides=water_cap_overrides,
            ):
                continue  # hard cap / GPU conflict → skip (infinite)

            if cost_ceiling is not None:
                selected_elec = sum(
                    float(c.estimated_electricity_cost_usd) for c in trial.values()
                )
                # Remaining optional jobs may still pay miss penalties; ignore here.
                if running_elec + selected_elec > float(cost_ceiling) + _EPS:
                    continue

            score = score_placement_candidate(
                cand,
                jobs_already_at_site=site_job_counts.get(cand.site_id, 0),
                earliest_start=earliest,
                prefer=prefer,
                change_weight=secondary_change_weight,
                w_utilization=w_utilization,
                w_cost=w_cost,
                w_time=w_time,
            )
            # Lower score wins; stable tie-break by earlier start, then site id.
            rank = (score, cand.start, cand.site_id)
            best_rank = (
                (best_score, best.start, best.site_id)
                if best is not None
                else None
            )
            if best_rank is None or rank < best_rank:
                best = cand
                best_score = score

        if best is not None:
            selected[job_id] = best
            site_job_counts[best.site_id] = site_job_counts.get(best.site_id, 0) + 1
            objective_total += best_score
            continue

        if job_id in allow_unfulfilled:
            unfulfilled.append(job_id)
            if job is not None:
                objective_total += float(job.miss_penalty_usd)
            continue

        if not opts:
            return OptimizeResult(
                status=FeasibilityStatus.infeasible,
                reason=f"Job {job_id} has no candidates",
                selected=selected,
                unfulfilled_job_ids=tuple(sorted(unfulfilled)),
            )
        return OptimizeResult(
            status=FeasibilityStatus.infeasible,
            reason=(
                f"Job {job_id} has no jointly feasible placement under water/GPU "
                f"caps with the current partial selection"
            ),
            selected=selected,
            unfulfilled_job_ids=tuple(sorted(unfulfilled)),
        )

    violations = validate_selection(
        selected,
        running_reservations,
        sites,
        water_cap_overrides=water_cap_overrides,
    )
    if violations:
        return OptimizeResult(
            status=FeasibilityStatus.solver_error,
            reason="Balanced selection failed post-validation: " + violations[0].message,
            selected=selected,
            unfulfilled_job_ids=tuple(sorted(unfulfilled)),
            validated=False,
        )

    return OptimizeResult(
        status=FeasibilityStatus.feasible,
        selected=selected,
        unfulfilled_job_ids=tuple(sorted(unfulfilled)),
        objective_value=objective_total,
        validated=True,
    )


def optimize_placements(
    candidates_by_job: dict[str, list[PlacementCandidate]],
    running_reservations: list[RunningReservation],
    sites: list[SiteProfile],
    jobs_by_id: dict[str, Job],
    *,
    objective: OptimizeObjective,
    job_ids: Optional[list[str]] = None,
    allow_unfulfilled: Optional[set[str]] = None,
    cost_ceiling: Optional[float] = None,
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
    prefer_placements: Optional[dict[str, tuple[str, datetime]]] = None,
    time_limit_sec: float = 10.0,
    secondary_change_weight: float = 1e-6,
    secondary_cost_weight: float = 1e-4,
) -> OptimizeResult:
    """Assign candidates with cost, water, or balanced multi-objective scoring.

    ``balanced`` uses a greedy equilibrium/cost/earliness heuristic under hard
    caps (and optional cost ceiling). ``min_cost`` / ``min_water`` use SciPy MILP.

    Jobs in ``allow_unfulfilled`` may be left unfulfilled (binary) and pay
    ``miss_penalty_usd``. All other requested jobs must receive exactly one
    candidate. Running reservations stay fixed and are never double-counted.
    """
    if objective == OptimizeObjective.balanced:
        return optimize_placements_balanced(
            candidates_by_job,
            running_reservations,
            sites,
            jobs_by_id,
            job_ids=job_ids,
            allow_unfulfilled=allow_unfulfilled,
            cost_ceiling=cost_ceiling,
            water_cap_overrides=water_cap_overrides,
            prefer_placements=prefer_placements,
            secondary_change_weight=secondary_change_weight,
        )

    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    allow_unfulfilled = set(allow_unfulfilled or ())
    prefer_placements = prefer_placements or {}
    site_index = index_site_profiles(sites)

    if job_ids is None:
        target_jobs = sorted(candidates_by_job)
    else:
        target_jobs = list(job_ids)

    flat: list[PlacementCandidate] = []
    job_of_var: list[str] = []
    for job_id in target_jobs:
        opts = list(candidates_by_job.get(job_id, []))
        if not opts and job_id not in allow_unfulfilled:
            return OptimizeResult(
                status=FeasibilityStatus.infeasible,
                reason=f"Job {job_id} has no candidates",
            )
        for cand in opts:
            flat.append(cand)
            job_of_var.append(job_id)

    n_cand = len(flat)
    optional_jobs = [j for j in target_jobs if j in allow_unfulfilled]
    n_unf = len(optional_jobs)
    n = n_cand + n_unf
    unf_index = {job_id: n_cand + i for i, job_id in enumerate(optional_jobs)}

    if n == 0:
        return OptimizeResult(status=FeasibilityStatus.feasible, validated=True)

    # Assignment equalities / covering constraints.
    a_eq_rows: list[list[float]] = []
    b_eq: list[float] = []
    for job_id in target_jobs:
        row = [0.0] * n
        for i in range(n_cand):
            if job_of_var[i] == job_id:
                row[i] = 1.0
        if job_id in unf_index:
            row[unf_index[job_id]] = 1.0
        a_eq_rows.append(row)
        b_eq.append(1.0)

    a_ub_rows: list[list[float]] = []
    b_ub: list[float] = []

    # Cost ceiling uses electricity + miss penalties (same accounting as evaluation).
    # Running-job electricity is sunk/constant and subtracted from the RHS so the
    # ceiling compares against full schedule cost = running + selected + penalties.
    if cost_ceiling is not None:
        running_elec = sum(
            float(r.estimated_electricity_cost_usd) for r in running_reservations
        )
        cost_row = [0.0] * n
        for i, cand in enumerate(flat):
            cost_row[i] = float(cand.estimated_electricity_cost_usd)
        for job_id, idx in unf_index.items():
            job = jobs_by_id.get(job_id)
            cost_row[idx] = float(job.miss_penalty_usd) if job else 0.0
        a_ub_rows.append(cost_row)
        b_ub.append(float(cost_ceiling) - running_elec)

    sites_touched = {c.site_id for c in flat} | {r.site_id for r in running_reservations}
    for site_id in sorted(sites_touched):
        if site_id not in site_index:
            return OptimizeResult(
                status=FeasibilityStatus.infeasible,
                reason=f"Missing site profile {site_id}",
            )
        index = site_index[site_id]
        for left, right in _atomic_slices(
            site_id,
            flat,
            running_reservations,
            site_index,
            water_cap_overrides=water_cap_overrides,
        ):
            hour = left.replace(minute=0, second=0, microsecond=0)
            row_prof = index.by_hour.get(hour)
            if row_prof is None:
                continue
            reserved = _reserved_load_on_slice(running_reservations, site_id, left, right)

            gpu_coeffs = [0.0] * n
            for i in range(n_cand):
                if flat[i].site_id == site_id and _candidate_active(flat[i], left, right):
                    gpu_coeffs[i] = float(flat[i].gpu_count)
            gpu_rhs = row_prof.gpu_capacity - reserved.gpu_count
            if any(gpu_coeffs):
                a_ub_rows.append(gpu_coeffs)
                b_ub.append(float(gpu_rhs))

            cap = effective_water_cap(
                row_prof.water_cap_liters_per_hour,
                site_id,
                left,
                right,
                water_cap_overrides,
            )
            if cap is not None:
                water_coeffs = [0.0] * n
                for i in range(n_cand):
                    if flat[i].site_id == site_id and _candidate_active(
                        flat[i], left, right
                    ):
                        water_coeffs[i] = float(
                            flat[i].gpu_count
                            * row_prof.incremental_water_liters_per_gpu_hour
                        )
                water_rhs = (
                    cap
                    - row_prof.fixed_water_liters_per_hour
                    - reserved.incremental_water_rate_lph
                )
                if any(water_coeffs) or water_rhs < -_EPS:
                    a_ub_rows.append(water_coeffs)
                    b_ub.append(float(water_rhs))

    # Objective coefficients.
    c = np.zeros(n)
    if objective == OptimizeObjective.min_cost:
        for i, cand in enumerate(flat):
            c[i] = float(cand.estimated_electricity_cost_usd)
            pref = prefer_placements.get(cand.job_id)
            if pref is not None and (cand.site_id, cand.start) != pref:
                c[i] += secondary_change_weight
        for job_id, idx in unf_index.items():
            job = jobs_by_id.get(job_id)
            c[idx] = float(job.miss_penalty_usd) if job else 0.0
    else:
        # Minimize incremental job water; fixed/running water is constant.
        for i, cand in enumerate(flat):
            c[i] = float(cand.estimated_incremental_water_liters)
            c[i] += secondary_cost_weight * float(cand.estimated_electricity_cost_usd)
            pref = prefer_placements.get(cand.job_id)
            if pref is not None and (cand.site_id, cand.start) != pref:
                c[i] += secondary_change_weight
        for job_id, idx in unf_index.items():
            # Unfulfilled adds no incremental water; discourage via large penalty
            # unless the job is allowed — use miss penalty scaled into water units.
            job = jobs_by_id.get(job_id)
            # In routine mode allow_unfulfilled is empty; this branch is for safety.
            c[idx] = 1e6 + (float(job.miss_penalty_usd) if job else 0.0)

    constraints = []
    if a_eq_rows:
        constraints.append(
            LinearConstraint(
                np.asarray(a_eq_rows, dtype=float),
                np.asarray(b_eq, dtype=float),
                np.asarray(b_eq, dtype=float),
            )
        )
    if a_ub_rows:
        constraints.append(
            LinearConstraint(
                np.asarray(a_ub_rows, dtype=float),
                np.full(len(b_ub), -np.inf),
                np.asarray(b_ub, dtype=float),
            )
        )

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
    except Exception as exc:  # noqa: BLE001
        return OptimizeResult(
            status=FeasibilityStatus.solver_error,
            reason=f"SciPy milp raised: {exc}",
        )

    status_code = int(result.status)
    message = getattr(result, "message", "") or ""

    if status_code == 0 and result.x is not None:
        selected: dict[str, PlacementCandidate] = {}
        unfulfilled: list[str] = []
        for i, val in enumerate(result.x[:n_cand]):
            if val >= 0.5:
                selected[job_of_var[i]] = flat[i]
        for job_id, idx in unf_index.items():
            if result.x[idx] >= 0.5:
                unfulfilled.append(job_id)

        covered = set(selected) | set(unfulfilled)
        if covered != set(target_jobs):
            return OptimizeResult(
                status=FeasibilityStatus.solver_error,
                reason="Solver returned an incomplete selection",
            )

        violations = validate_selection(
            selected,
            running_reservations,
            sites,
            water_cap_overrides=water_cap_overrides,
        )
        if violations:
            return OptimizeResult(
                status=FeasibilityStatus.solver_error,
                reason="Solver selection failed post-validation: " + violations[0].message,
                selected=selected,
                unfulfilled_job_ids=tuple(sorted(unfulfilled)),
                validated=False,
            )
        obj = float(np.dot(c, result.x))
        return OptimizeResult(
            status=FeasibilityStatus.feasible,
            selected=selected,
            unfulfilled_job_ids=tuple(sorted(unfulfilled)),
            objective_value=obj,
            validated=True,
        )

    if status_code == 1 or "time" in message.lower():
        return OptimizeResult(
            status=FeasibilityStatus.solver_timeout,
            reason=message or "MILP time limit reached",
        )

    lower = message.lower()
    if status_code in {2, 3, 4} or "infeas" in lower:
        return OptimizeResult(
            status=FeasibilityStatus.infeasible,
            reason=message or "No feasible joint assignment of candidates",
        )
    return OptimizeResult(
        status=FeasibilityStatus.solver_error,
        reason=message or f"MILP failed with status={status_code}",
    )


def default_evaluation_horizon(
    sites: list[SiteProfile],
    current_time: datetime,
    selected: dict[str, PlacementCandidate],
    running_reservations: list[RunningReservation],
    extra_ends: Optional[list[datetime]] = None,
) -> tuple[datetime, datetime]:
    """Shared explicit horizon for reference and proposed schedule metrics."""
    current_time = require_aware_utc(current_time, "current_time")
    ends: list[datetime] = [current_time + ONE_HOUR]
    site_index = index_site_profiles(sites)
    for index in site_index.values():
        if index.sorted_hours:
            ends.append(index.sorted_hours[-1] + ONE_HOUR)
    for cand in selected.values():
        ends.append(cand.finish)
    for r in running_reservations:
        ends.append(r.finish)
    if extra_ends:
        ends.extend(extra_ends)
    return current_time, max(ends)


def evaluate_schedule(
    jobs: list[Job],
    sites: list[SiteProfile],
    current_time: datetime,
    *,
    selected: dict[str, PlacementCandidate],
    running_reservations: list[RunningReservation],
    unfulfilled_job_ids: Optional[set[str]] = None,
    unfulfilled_reasons: Optional[dict[str, str]] = None,
    horizon_start: Optional[datetime] = None,
    horizon_end: Optional[datetime] = None,
    water_cap_overrides: Optional[list[WaterCapOverride]] = None,
) -> ScheduleEvaluation:
    """Evaluate one complete schedule for metrics, deadlines, and cap compliance.

    Cost accounting: electricity from profile fields + miss_penalty_usd for
    explicitly unfulfilled jobs. water_usd is always 0 (no tariff in schema).
    Fixed facility withdrawal is counted once per site-hour in the horizon.
    """
    current_time = require_aware_utc(current_time, "current_time")
    unfulfilled_job_ids = set(unfulfilled_job_ids or ())
    unfulfilled_reasons = dict(unfulfilled_reasons or {})
    site_index = index_site_profiles(sites)

    if horizon_start is None or horizon_end is None:
        horizon_start, horizon_end = default_evaluation_horizon(
            sites, current_time, selected, running_reservations
        )
    else:
        horizon_start = require_aware_utc(horizon_start, "horizon_start")
        horizon_end = require_aware_utc(horizon_end, "horizon_end")

    jobs_by_id = {j.id: j for j in jobs}
    active_job_ids = {
        j.id
        for j in jobs
        if j.status
        in {JobStatus.queued, JobStatus.scheduled, JobStatus.running}
    }

    # Incremental contributions from running + selected placements.
    all_contribs: list[IntervalContribution] = []
    for r in running_reservations:
        all_contribs.extend(r.contributions)
    for cand in selected.values():
        all_contribs.extend(cand.contributions)

    energy_kwh = 0.0
    energy_cost = 0.0
    incremental_water = 0.0
    for c in all_contribs:
        ov = _overlap(c.interval_start, c.interval_end, horizon_start, horizon_end)
        if ov is None:
            continue
        frac = hours_between(ov[0], ov[1]) / max(hours_between(c.interval_start, c.interval_end), _EPS)
        # Contributions already store integrated values for their intervals; scale if clipped.
        full_h = hours_between(c.interval_start, c.interval_end)
        scale = hours_between(ov[0], ov[1]) / full_h if full_h > 0 else 0.0
        energy_kwh += c.energy_kwh * scale
        energy_cost += c.electricity_cost_usd * scale
        incremental_water += c.incremental_water_liters * scale
        _ = frac

    # Fixed facility water once per site per overlapping hour in the horizon.
    fixed_water = 0.0
    per_site_fixed: dict[str, float] = {sid: 0.0 for sid in site_index}
    per_site_incr: dict[str, float] = {sid: 0.0 for sid in site_index}
    per_site_energy: dict[str, float] = {sid: 0.0 for sid in site_index}
    per_site_energy_cost: dict[str, float] = {sid: 0.0 for sid in site_index}
    per_site_violations: dict[str, int] = {sid: 0 for sid in site_index}
    per_site_peak: dict[str, float] = {sid: 0.0 for sid in site_index}
    per_site_peak_at: dict[str, Optional[datetime]] = {sid: None for sid in site_index}
    per_site_peak_cap: dict[str, Optional[float]] = {sid: None for sid in site_index}

    for c in all_contribs:
        ov = _overlap(c.interval_start, c.interval_end, horizon_start, horizon_end)
        if ov is None:
            continue
        full_h = hours_between(c.interval_start, c.interval_end)
        scale = hours_between(ov[0], ov[1]) / full_h if full_h > 0 else 0.0
        per_site_incr[c.site_id] = per_site_incr.get(c.site_id, 0.0) + c.incremental_water_liters * scale
        per_site_energy[c.site_id] = per_site_energy.get(c.site_id, 0.0) + c.energy_kwh * scale
        per_site_energy_cost[c.site_id] = (
            per_site_energy_cost.get(c.site_id, 0.0) + c.electricity_cost_usd * scale
        )

    diagnostics: list[Diagnostic] = []
    for site_id, index in site_index.items():
        for hour in index.sorted_hours:
            hour_end = hour + ONE_HOUR
            ov = _overlap(hour, hour_end, horizon_start, horizon_end)
            if ov is None:
                continue
            row = index.by_hour[hour]
            dur = hours_between(ov[0], ov[1])
            fixed_part = row.fixed_water_liters_per_hour * dur
            fixed_water += fixed_part
            per_site_fixed[site_id] = per_site_fixed.get(site_id, 0.0) + fixed_part

        # Peak withdrawal and cap violations on atomic slices in horizon.
        site_cands = [c for c in selected.values() if c.site_id == site_id]
        for left, right in _atomic_slices(
            site_id,
            site_cands,
            running_reservations,
            site_index,
            water_cap_overrides=water_cap_overrides,
            extra_bounds=[horizon_start, horizon_end],
        ):
            if right <= horizon_start or left >= horizon_end:
                continue
            slice_left = max(left, horizon_start)
            slice_right = min(right, horizon_end)
            if slice_right <= slice_left:
                continue
            hour = slice_left.replace(minute=0, second=0, microsecond=0)
            row = index.by_hour.get(hour)
            if row is None:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.missing_telemetry,
                        message=f"Missing telemetry at {hour.isoformat()} during evaluation",
                        site_id=site_id,
                        period_start=hour,
                        period_end=hour + ONE_HOUR,
                    )
                )
                continue
            reserved = _reserved_load_on_slice(
                running_reservations, site_id, slice_left, slice_right
            )
            rate = reserved.incremental_water_rate_lph
            for cand in site_cands:
                if _candidate_active(cand, slice_left, slice_right):
                    rate += cand.gpu_count * row.incremental_water_liters_per_gpu_hour
            total_rate = row.fixed_water_liters_per_hour + rate
            if total_rate > per_site_peak.get(site_id, 0.0):
                per_site_peak[site_id] = total_rate
                per_site_peak_at[site_id] = slice_left
            cap = effective_water_cap(
                row.water_cap_liters_per_hour,
                site_id,
                slice_left,
                slice_right,
                water_cap_overrides,
            )
            per_site_peak_cap[site_id] = cap
            if cap is not None and total_rate > cap + _EPS:
                per_site_violations[site_id] = per_site_violations.get(site_id, 0) + 1

    miss_penalties = 0.0
    outcomes: list[JobDeadlineResult] = []
    on_time = late = unfulfilled_count = 0

    for job in jobs:
        if job.id not in active_job_ids:
            continue
        if job.status == JobStatus.running:
            res = next((r for r in running_reservations if r.job_id == job.id), None)
            if res is None:
                outcomes.append(
                    JobDeadlineResult(
                        job_id=job.id,
                        met_deadline=False,
                        projected_completion=None,
                        unfulfilled=False,
                        unfulfilled_reason="running_reservation_unavailable",
                    )
                )
                late += 1
                continue
            met = res.finish <= require_aware_utc(job.deadline, "deadline")
            outcomes.append(
                JobDeadlineResult(
                    job_id=job.id,
                    met_deadline=met,
                    projected_completion=res.finish,
                    unfulfilled=False,
                    unfulfilled_reason=None,
                )
            )
            if met:
                on_time += 1
            else:
                late += 1
            continue

        if job.id in unfulfilled_job_ids:
            reason = unfulfilled_reasons.get(job.id, "left_unfulfilled_by_optimizer")
            miss_penalties += job.miss_penalty_usd
            outcomes.append(
                JobDeadlineResult(
                    job_id=job.id,
                    met_deadline=False,
                    projected_completion=None,
                    unfulfilled=True,
                    unfulfilled_reason=reason,
                )
            )
            unfulfilled_count += 1
            continue

        cand = selected.get(job.id)
        if cand is None:
            # Expected to be placed but missing — treat as unfulfilled for honesty.
            outcomes.append(
                JobDeadlineResult(
                    job_id=job.id,
                    met_deadline=False,
                    projected_completion=None,
                    unfulfilled=True,
                    unfulfilled_reason="no_assignment_in_schedule",
                )
            )
            unfulfilled_count += 1
            miss_penalties += job.miss_penalty_usd
            continue

        deadline = require_aware_utc(job.deadline, "deadline")
        met = cand.finish <= deadline
        outcomes.append(
            JobDeadlineResult(
                job_id=job.id,
                met_deadline=met,
                projected_completion=cand.finish,
                unfulfilled=False,
                unfulfilled_reason=None,
            )
        )
        if met:
            on_time += 1
        else:
            late += 1

    total_water = fixed_water + incremental_water
    water_cost = 0.0  # no water tariff in current schema
    total_cost = energy_cost + water_cost + miss_penalties

    site_evals = []
    for site_id in sorted(site_index):
        water = per_site_fixed.get(site_id, 0.0) + per_site_incr.get(site_id, 0.0)
        viol = per_site_violations.get(site_id, 0)
        site_evals.append(
            SiteEvaluation(
                site_id=site_id,
                water_liters=water,
                water_cap_violations=viol,
                energy_kwh=per_site_energy.get(site_id, 0.0),
                energy_cost_usd=per_site_energy_cost.get(site_id, 0.0),
                peak_withdrawal_lph=per_site_peak.get(site_id, 0.0),
                peak_withdrawal_at=per_site_peak_at.get(site_id),
                within_cap=viol == 0,
                active_cap_at_peak=per_site_peak_cap.get(site_id),
            )
        )

    outcomes.sort(key=lambda o: o.job_id)
    return ScheduleEvaluation(
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        total_water_liters=total_water,
        total_energy_kwh=energy_kwh,
        energy_cost_usd=energy_cost,
        water_cost_usd=water_cost,
        miss_penalties_usd=miss_penalties,
        total_cost_usd=total_cost,
        jobs_on_time=on_time,
        jobs_late=late,
        jobs_unfulfilled=unfulfilled_count,
        per_site=tuple(site_evals),
        deadline_outcomes=tuple(outcomes),
        diagnostics=tuple(diagnostics),
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
