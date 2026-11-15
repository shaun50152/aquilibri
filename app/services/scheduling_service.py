"""Reallocation preview orchestration (Person 2).

Uses the placement engine for candidates, reservations, MILP optimization, and
schedule evaluation. Does not mutate jobs, clock, or revision. Gemini NL parsing
is deferred — structured fields are required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.schemas.jobs import Job, JobStatus
from app.schemas.scheduling import (
    AggregateMetrics,
    CostBreakdown,
    DeadlineOutcome,
    MetricsSourceLabel,
    PlacementSnapshot,
    PreviewStatus,
    ProposedAssignment,
    ProposedJobChange,
    ReallocationApplyRequest,
    ReallocationApplyResponse,
    ReallocationMode,
    ReallocationPreviewRequest,
    ReallocationPreviewResponse,
    SiteScheduleMetrics,
    SiteWaterMetrics,
    SolverOutcome,
)
from app.schemas.sites import SiteProfile
from app.services.placement_engine import (
    DiagnosticCode,
    FeasibilityStatus,
    OptimizeObjective,
    PlacementCandidate,
    ScheduleEvaluation,
    WaterCapOverride,
    default_evaluation_horizon,
    evaluate_schedule,
    generate_placement_candidates,
    optimize_placements,
    require_aware_utc,
)
from app.services.preview_store import StoredPreview, new_preview_id, store_preview
from app.state import SessionState


class PreviewRequestError(ValueError):
    """Invalid structured preview request (maps to HTTP 422)."""


@dataclass(frozen=True)
class SchedulingSnapshot:
    session_id: str
    revision: int
    current_time: datetime
    jobs: list[Job]
    sites: list[SiteProfile]
    sites_fixture_name: str


def capture_snapshot(state: SessionState) -> SchedulingSnapshot:
    return SchedulingSnapshot(
        session_id=state.session_id,
        revision=state.revision,
        current_time=state.current_time,
        jobs=[j.model_copy(deep=True) for j in state.jobs],
        sites=[s.model_copy(deep=True) for s in state.sites],
        sites_fixture_name=state.sites_fixture_name,
    )


def _empty_metrics() -> AggregateMetrics:
    return AggregateMetrics(
        total_water_liters=0.0,
        total_energy_kwh=0.0,
        total_cost_usd=0.0,
        jobs_on_time=0,
        jobs_late=0,
        jobs_unfulfilled=0,
    )


def _agg(ev: ScheduleEvaluation) -> AggregateMetrics:
    return AggregateMetrics(
        total_water_liters=ev.total_water_liters,
        total_energy_kwh=ev.total_energy_kwh,
        total_cost_usd=ev.total_cost_usd,
        jobs_on_time=ev.jobs_on_time,
        jobs_late=ev.jobs_late,
        jobs_unfulfilled=ev.jobs_unfulfilled,
    )


def _cost_breakdown(ev: ScheduleEvaluation) -> CostBreakdown:
    return CostBreakdown(
        energy_usd=ev.energy_cost_usd,
        water_usd=ev.water_cost_usd,
        miss_penalties_usd=ev.miss_penalties_usd,
        total_usd=ev.total_cost_usd,
    )


def _site_water(ev: ScheduleEvaluation) -> list[SiteWaterMetrics]:
    return [
        SiteWaterMetrics(
            site_id=s.site_id,
            water_liters=s.water_liters,
            water_cap_violations=s.water_cap_violations,
        )
        for s in ev.per_site
    ]


def _site_schedule(ev: ScheduleEvaluation) -> list[SiteScheduleMetrics]:
    return [
        SiteScheduleMetrics(
            site_id=s.site_id,
            water_liters=s.water_liters,
            water_cap_violations=s.water_cap_violations,
            energy_kwh=s.energy_kwh,
            energy_cost_usd=s.energy_cost_usd,
            peak_withdrawal_lph=s.peak_withdrawal_lph,
            peak_withdrawal_at=s.peak_withdrawal_at,
            within_cap=s.within_cap,
            active_cap_lph=s.active_cap_at_peak,
        )
        for s in ev.per_site
    ]


def _deadlines(ev: ScheduleEvaluation) -> list[DeadlineOutcome]:
    return [
        DeadlineOutcome(
            job_id=o.job_id,
            met_deadline=o.met_deadline,
            projected_completion=o.projected_completion,
            unfulfilled=o.unfulfilled,
            unfulfilled_reason=o.unfulfilled_reason,
        )
        for o in ev.deadline_outcomes
    ]


def _override_from_request(request: ReallocationPreviewRequest) -> Optional[WaterCapOverride]:
    if request.emergency_restriction is None:
        return None
    er = request.emergency_restriction
    return WaterCapOverride(
        site_id=er.site_id,
        start=require_aware_utc(er.start, "emergency_restriction.start"),
        end=require_aware_utc(er.end, "emergency_restriction.end"),
        water_cap_liters_per_hour=er.water_cap_liters_per_hour,
    )


def _validate_emergency(request: ReallocationPreviewRequest, sites: list[SiteProfile]) -> None:
    if request.mode != ReallocationMode.emergency:
        return
    if request.emergency_restriction is None:
        raise PreviewRequestError(
            "emergency mode requires emergency_restriction with site_id, "
            "water_cap_liters_per_hour, start, and end."
        )
    er = request.emergency_restriction
    require_aware_utc(er.start, "emergency_restriction.start")
    require_aware_utc(er.end, "emergency_restriction.end")
    known = {s.site_id for s in sites}
    if er.site_id not in known:
        raise PreviewRequestError(
            f"emergency_restriction.site_id={er.site_id!r} is not a known site. "
            f"Known: {sorted(known)}"
        )


def _nl_only_without_structure(request: ReallocationPreviewRequest) -> Optional[str]:
    """Gemini is not wired yet — require structured fields."""
    if request.natural_language_instruction and request.mode == ReallocationMode.emergency:
        if request.emergency_restriction is None:
            return (
                "Natural-language-only emergency requests are not interpreted yet "
                "(Gemini integration is the next milestone). Provide structured "
                "emergency_restriction: site_id, water_cap_liters_per_hour, start, end."
            )
    return None


def _prefer_from_saved(jobs: list[Job]) -> dict[str, tuple[str, datetime]]:
    prefs: dict[str, tuple[str, datetime]] = {}
    for job in jobs:
        if (
            job.status == JobStatus.scheduled
            and job.assigned_site_id
            and job.scheduled_start is not None
        ):
            prefs[job.id] = (
                job.assigned_site_id,
                require_aware_utc(job.scheduled_start, "scheduled_start"),
            )
    return prefs


def _proposed_assignments(
    jobs: list[Job],
    selected: dict[str, PlacementCandidate],
    unfulfilled: set[str],
    unfulfilled_reasons: dict[str, str],
    running_by_id: dict[str, object],
) -> list[ProposedAssignment]:
    out: list[ProposedAssignment] = []
    for job in jobs:
        if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.unfulfilled}:
            continue
        if job.status == JobStatus.running:
            r = running_by_id.get(job.id)
            out.append(
                ProposedAssignment(
                    job_id=job.id,
                    assigned_site_id=job.assigned_site_id,
                    scheduled_start=job.scheduled_start,
                    projected_completion=getattr(r, "finish", None) if r else None,
                    unfulfilled=False,
                )
            )
            continue
        if job.id in unfulfilled:
            out.append(
                ProposedAssignment(
                    job_id=job.id,
                    assigned_site_id=None,
                    scheduled_start=None,
                    projected_completion=None,
                    unfulfilled=True,
                    unfulfilled_reason=unfulfilled_reasons.get(
                        job.id, "left_unfulfilled_by_optimizer"
                    ),
                )
            )
            continue
        cand = selected.get(job.id)
        if cand is None:
            continue
        out.append(
            ProposedAssignment(
                job_id=job.id,
                assigned_site_id=cand.site_id,
                scheduled_start=cand.start,
                projected_completion=cand.finish,
                unfulfilled=False,
            )
        )
    return out


def _job_changes(
    jobs: list[Job],
    selected: dict[str, PlacementCandidate],
    unfulfilled: set[str],
) -> list[ProposedJobChange]:
    """Diff against actual saved placements (not the reference schedule)."""
    changes: list[ProposedJobChange] = []
    for job in jobs:
        if job.status not in {JobStatus.queued, JobStatus.scheduled}:
            continue
        old = PlacementSnapshot(
            job_id=job.id,
            assigned_site_id=job.assigned_site_id,
            scheduled_start=job.scheduled_start,
        )
        if job.id in unfulfilled:
            new = PlacementSnapshot(
                job_id=job.id, assigned_site_id=None, scheduled_start=None
            )
            if old.assigned_site_id is not None or old.scheduled_start is not None:
                changes.append(
                    ProposedJobChange(job_id=job.id, old_placement=old, new_placement=new)
                )
            elif job.status == JobStatus.queued:
                # Queued → explicitly unfulfilled is a meaningful change to surface.
                changes.append(
                    ProposedJobChange(job_id=job.id, old_placement=old, new_placement=new)
                )
            continue
        cand = selected.get(job.id)
        if cand is None:
            continue
        new = PlacementSnapshot(
            job_id=job.id,
            assigned_site_id=cand.site_id,
            scheduled_start=cand.start,
        )
        same_site = old.assigned_site_id == new.assigned_site_id
        same_start = old.scheduled_start == new.scheduled_start
        if not (same_site and same_start):
            changes.append(
                ProposedJobChange(job_id=job.id, old_placement=old, new_placement=new)
            )
    return changes


def _infeasible_response(
    *,
    snapshot: SchedulingSnapshot,
    preview_id: str,
    reason: str,
    solver_outcome: SolverOutcome,
    explanation: str,
    reference_ev: Optional[ScheduleEvaluation] = None,
    reference_cost: Optional[float] = None,
    cost_ceiling: Optional[float] = None,
    max_additional: Optional[float] = None,
) -> ReallocationPreviewResponse:
    return ReallocationPreviewResponse(
        preview_id=preview_id,
        input_revision=snapshot.revision,
        status=PreviewStatus.infeasible,
        proposed_job_changes=[],
        metrics_before=_agg(reference_ev) if reference_ev else None,
        metrics_after=None,
        per_site_water=[],
        cost_breakdown=None,
        deadline_outcomes=_deadlines(reference_ev) if reference_ev else [],
        explanation=explanation,
        infeasibility_reason=reason,
        simulated=True,
        metrics_before_label=(
            MetricsSourceLabel.computed_reference_schedule
            if reference_ev
            else MetricsSourceLabel.unavailable
        ),
        reference_cost_usd=reference_cost,
        cost_ceiling_usd=cost_ceiling,
        max_additional_cost_usd=max_additional,
        evaluation_start=reference_ev.horizon_start if reference_ev else None,
        evaluation_end=reference_ev.horizon_end if reference_ev else None,
        per_site_before=_site_schedule(reference_ev) if reference_ev else [],
        per_site_after=[],
        proposed_assignments=[],
        solver_outcome=solver_outcome,
        session_id=snapshot.session_id,
        sites_fixture_name=snapshot.sites_fixture_name,
    )


def _solver_outcome(status: FeasibilityStatus) -> SolverOutcome:
    if status == FeasibilityStatus.infeasible:
        return SolverOutcome.infeasible
    if status == FeasibilityStatus.solver_timeout:
        return SolverOutcome.solver_timeout
    if status == FeasibilityStatus.solver_error:
        return SolverOutcome.solver_error
    return SolverOutcome.ok


def compute_proposed_placements(
    jobs_snapshot: list[Job],
    site_profiles: list[SiteProfile],
    current_time: datetime,
    objective: dict,
) -> ReallocationPreviewResponse:
    """Lower-level entry used by tests; prefer preview_reallocation for API."""
    mode = ReallocationMode(objective.get("mode", "routine"))
    request = ReallocationPreviewRequest(
        mode=mode,
        max_additional_cost_usd=objective.get("max_additional_cost_usd"),
        emergency_restriction=objective.get("emergency_restriction"),
        natural_language_instruction=objective.get("natural_language_instruction"),
    )
    snapshot = SchedulingSnapshot(
        session_id=str(objective.get("session_id", "compute")),
        revision=int(objective.get("revision", 0)),
        current_time=current_time,
        jobs=list(jobs_snapshot),
        sites=list(site_profiles),
        sites_fixture_name=str(objective.get("sites_fixture_name", "inline")),
    )
    return _compute_preview(request, snapshot, persist=False)


def preview_reallocation(
    request: ReallocationPreviewRequest,
    state: SessionState,
) -> ReallocationPreviewResponse:
    """Compute a preview from the live session snapshot without mutating state."""
    snapshot = capture_snapshot(state)
    response = _compute_preview(request, snapshot, persist=True)
    # Stale detection: inputs changed while solving.
    if state.revision != snapshot.revision:
        response = response.model_copy(
            update={
                "status": PreviewStatus.infeasible,
                "solver_outcome": SolverOutcome.incomplete_input,
                "infeasibility_reason": (
                    f"Scheduling inputs changed during solve "
                    f"(snapshot revision {snapshot.revision}, current {state.revision}). "
                    "Preview is not a current plan."
                ),
                "explanation": "Discarded because the session revision advanced during optimization.",
                "metrics_after": None,
                "proposed_job_changes": [],
                "proposed_assignments": [],
            }
        )
        return response
    return response


def _compute_preview(
    request: ReallocationPreviewRequest,
    snapshot: SchedulingSnapshot,
    *,
    persist: bool,
) -> ReallocationPreviewResponse:
    preview_id = new_preview_id()

    nl_block = _nl_only_without_structure(request)
    if nl_block:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=nl_block,
            solver_outcome=SolverOutcome.incomplete_input,
            explanation=nl_block,
        )

    try:
        _validate_emergency(request, snapshot.sites)
    except (PreviewRequestError, ValueError) as exc:
        raise PreviewRequestError(str(exc)) from exc

    overrides: list[WaterCapOverride] = []
    ov = _override_from_request(request)
    if ov is not None:
        overrides.append(ov)

    jobs = snapshot.jobs
    sites = snapshot.sites
    now = require_aware_utc(snapshot.current_time, "current_time")
    jobs_by_id = {j.id: j for j in jobs}

    gen = generate_placement_candidates(
        jobs,
        sites,
        now,
        water_cap_overrides=overrides or None,
    )

    # Incomplete telemetry / running reservation problems.
    running_missing = [
        d
        for d in gen.diagnostics
        if d.code
        in {
            DiagnosticCode.running_missing_telemetry,
            DiagnosticCode.running_missing_assignment,
            DiagnosticCode.running_inconsistent_timestamps,
        }
    ]
    if running_missing:
        detail = "; ".join(d.message for d in running_missing[:3])
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=detail,
            solver_outcome=SolverOutcome.incomplete_input,
            explanation=(
                "Cannot build a reliable schedule: running-job telemetry or assignment "
                f"data is incomplete. {detail}"
            ),
        )

    running_cap = [
        d
        for d in gen.diagnostics
        if d.code
        in {
            DiagnosticCode.running_water_cap_exceeded,
            DiagnosticCode.running_gpu_overcapacity,
        }
    ]
    if running_cap:
        detail = "; ".join(d.message for d in running_cap[:3])
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=detail,
            solver_outcome=SolverOutcome.infeasible,
            explanation=(
                "Fixed facility withdrawal plus unavoidable running load exceeds capacity. "
                f"{detail}"
            ),
        )

    reconsider = list(gen.reconsider_job_ids)
    prefs = _prefer_from_saved(jobs)

    if request.mode == ReallocationMode.routine:
        return _routine_preview(
            request=request,
            snapshot=snapshot,
            preview_id=preview_id,
            gen=gen,
            reconsider=reconsider,
            jobs_by_id=jobs_by_id,
            prefs=prefs,
            overrides=overrides,
            persist=persist,
        )

    return _emergency_preview(
        request=request,
        snapshot=snapshot,
        preview_id=preview_id,
        gen=gen,
        reconsider=reconsider,
        jobs_by_id=jobs_by_id,
        prefs=prefs,
        overrides=overrides,
        persist=persist,
    )


def _routine_preview(
    *,
    request: ReallocationPreviewRequest,
    snapshot: SchedulingSnapshot,
    preview_id: str,
    gen,
    reconsider: list[str],
    jobs_by_id: dict[str, Job],
    prefs: dict[str, tuple[str, datetime]],
    overrides: list[WaterCapOverride],
    persist: bool,
) -> ReallocationPreviewResponse:
    max_extra = (
        0.0
        if request.max_additional_cost_usd is None
        else float(request.max_additional_cost_usd)
    )

    # Every queued/scheduled job must be placeable in routine mode.
    missing = [jid for jid in reconsider if not gen.candidates_by_job.get(jid)]
    if missing:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=(
                "Routine mode requires the full workload on time; no candidates for: "
                + ", ".join(missing)
            ),
            solver_outcome=SolverOutcome.infeasible,
            explanation=(
                "Routine optimization is infeasible: one or more jobs lack any feasible "
                "placement under current telemetry, eligibility, and capacity limits. "
                "Jobs are not dropped to create apparent savings."
            ),
            max_additional=max_extra,
        )

    # Reference: minimize modeled cost; all jobs on time.
    ref = optimize_placements(
        gen.candidates_by_job,
        gen.running_reservations,
        snapshot.sites,
        jobs_by_id,
        objective=OptimizeObjective.min_cost,
        job_ids=reconsider,
        allow_unfulfilled=set(),
        water_cap_overrides=overrides or None,
        prefer_placements=prefs,
        secondary_change_weight=1e-6,
        secondary_cost_weight=0.0,
    )
    if ref.status != FeasibilityStatus.feasible:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=ref.reason or "Reference schedule infeasible",
            solver_outcome=_solver_outcome(ref.status),
            explanation=(
                "Could not construct a complete on-time reference schedule for routine mode. "
                f"{ref.reason or ''}"
            ),
            max_additional=max_extra,
        )

    horizon_start, horizon_end = default_evaluation_horizon(
        snapshot.sites,
        snapshot.current_time,
        ref.selected,
        gen.running_reservations,
    )
    ref_ev = evaluate_schedule(
        snapshot.jobs,
        snapshot.sites,
        snapshot.current_time,
        selected=ref.selected,
        running_reservations=gen.running_reservations,
        unfulfilled_job_ids=set(),
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        water_cap_overrides=overrides or None,
    )
    # All reconsidered jobs must meet deadlines in the reference.
    late_ref = [
        o
        for o in ref_ev.deadline_outcomes
        if o.job_id in reconsider and (not o.met_deadline or o.unfulfilled)
    ]
    if late_ref:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason="Reference schedule has late or unfulfilled jobs",
            solver_outcome=SolverOutcome.infeasible,
            explanation="Routine mode requires every queued/scheduled job to finish on time.",
            reference_ev=ref_ev,
            reference_cost=ref_ev.total_cost_usd,
            max_additional=max_extra,
        )

    reference_cost = ref_ev.total_cost_usd
    ceiling = reference_cost + max_extra

    prop = optimize_placements(
        gen.candidates_by_job,
        gen.running_reservations,
        snapshot.sites,
        jobs_by_id,
        objective=OptimizeObjective.min_water,
        job_ids=reconsider,
        allow_unfulfilled=set(),
        cost_ceiling=ceiling,
        water_cap_overrides=overrides or None,
        prefer_placements=prefs,
        secondary_change_weight=1e-6,
        secondary_cost_weight=1e-4,
    )
    if prop.status != FeasibilityStatus.feasible:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=prop.reason or "Water-minimizing schedule infeasible under cost ceiling",
            solver_outcome=_solver_outcome(prop.status),
            explanation=(
                f"No water-minimizing schedule found within cost ceiling "
                f"{ceiling:.4f} USD (reference {reference_cost:.4f} + allowance {max_extra:.4f}). "
                f"{prop.reason or ''}"
            ),
            reference_ev=ref_ev,
            reference_cost=reference_cost,
            cost_ceiling=ceiling,
            max_additional=max_extra,
        )

    # Extend horizon to cover both schedules.
    horizon_start, horizon_end = default_evaluation_horizon(
        snapshot.sites,
        snapshot.current_time,
        {**ref.selected, **prop.selected},
        gen.running_reservations,
    )
    ref_ev = evaluate_schedule(
        snapshot.jobs,
        snapshot.sites,
        snapshot.current_time,
        selected=ref.selected,
        running_reservations=gen.running_reservations,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        water_cap_overrides=overrides or None,
    )
    prop_ev = evaluate_schedule(
        snapshot.jobs,
        snapshot.sites,
        snapshot.current_time,
        selected=prop.selected,
        running_reservations=gen.running_reservations,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        water_cap_overrides=overrides or None,
    )

    late_prop = [
        o
        for o in prop_ev.deadline_outcomes
        if o.job_id in reconsider and (not o.met_deadline or o.unfulfilled)
    ]
    if late_prop:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason="Proposed routine schedule misses deadlines",
            solver_outcome=SolverOutcome.solver_error,
            explanation="Internal error: proposed routine schedule failed deadline validation.",
            reference_ev=ref_ev,
            reference_cost=reference_cost,
            cost_ceiling=ceiling,
            max_additional=max_extra,
        )

    changes = _job_changes(snapshot.jobs, prop.selected, set())
    assignments = _proposed_assignments(
        snapshot.jobs,
        prop.selected,
        set(),
        {},
        {r.job_id: r for r in gen.running_reservations},
    )

    moved = [c.job_id for c in changes]
    explanation = (
        f"Routine optimization: computed min-cost reference schedule "
        f"(cost={reference_cost:.4f} USD, label=computed_reference_schedule), "
        f"then minimized fleet water under ceiling {ceiling:.4f} USD "
        f"(reference + max_additional_cost_usd={max_extra:.4f}; omitted allowance means 0). "
        f"Proposed water={prop_ev.total_water_liters:.3f} L vs reference "
        f"{ref_ev.total_water_liters:.3f} L. "
        f"Assignment changes vs saved queue: {len(changes)}"
        + (f" ({', '.join(moved)})." if moved else ".")
    )

    response = ReallocationPreviewResponse(
        preview_id=preview_id,
        input_revision=snapshot.revision,
        status=PreviewStatus.feasible,
        proposed_job_changes=changes,
        metrics_before=_agg(ref_ev),
        metrics_after=_agg(prop_ev),
        per_site_water=_site_water(prop_ev),
        cost_breakdown=_cost_breakdown(prop_ev),
        deadline_outcomes=_deadlines(prop_ev),
        explanation=explanation,
        infeasibility_reason=None,
        simulated=True,
        metrics_before_label=MetricsSourceLabel.computed_reference_schedule,
        reference_cost_usd=reference_cost,
        cost_ceiling_usd=ceiling,
        max_additional_cost_usd=max_extra,
        evaluation_start=horizon_start,
        evaluation_end=horizon_end,
        per_site_before=_site_schedule(ref_ev),
        per_site_after=_site_schedule(prop_ev),
        proposed_assignments=assignments,
        solver_outcome=SolverOutcome.ok,
        session_id=snapshot.session_id,
        sites_fixture_name=snapshot.sites_fixture_name,
    )

    if persist:
        store_preview(
            StoredPreview(
                preview_id=preview_id,
                session_id=snapshot.session_id,
                input_revision=snapshot.revision,
                simulation_time=snapshot.current_time,
                sites_fixture_name=snapshot.sites_fixture_name,
                proposed_assignments=assignments,
                unfulfilled_job_ids=(),
                response=response,
            )
        )
    return response


def _emergency_preview(
    *,
    request: ReallocationPreviewRequest,
    snapshot: SchedulingSnapshot,
    preview_id: str,
    gen,
    reconsider: list[str],
    jobs_by_id: dict[str, Job],
    prefs: dict[str, tuple[str, datetime]],
    overrides: list[WaterCapOverride],
    persist: bool,
) -> ReallocationPreviewResponse:
    critical_ids = {jid for jid in reconsider if jobs_by_id[jid].critical}
    noncritical_ids = {jid for jid in reconsider if not jobs_by_id[jid].critical}

    missing_critical = [jid for jid in critical_ids if not gen.candidates_by_job.get(jid)]
    if missing_critical:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=(
                "Critical jobs have no feasible candidates under the emergency restriction: "
                + ", ".join(sorted(missing_critical))
            ),
            solver_outcome=SolverOutcome.infeasible,
            explanation=(
                "Emergency optimization cannot silently drop critical jobs. "
                "No eligible on-time placement exists for one or more critical jobs."
            ),
        )

    # Candidates dict: critical must have options; noncritical may be empty → unfulfilled only.
    candidates = {
        jid: list(gen.candidates_by_job.get(jid, []))
        for jid in reconsider
        if jid in critical_ids or gen.candidates_by_job.get(jid)
    }
    # Ensure critical keys present.
    for jid in critical_ids:
        candidates.setdefault(jid, [])
    for jid in noncritical_ids:
        candidates.setdefault(jid, list(gen.candidates_by_job.get(jid, [])))

    allow_unfulfilled = set(noncritical_ids)

    # Reference under emergency constraints: min cost (may unfulfill noncritical).
    ref = optimize_placements(
        candidates,
        gen.running_reservations,
        snapshot.sites,
        jobs_by_id,
        objective=OptimizeObjective.min_cost,
        job_ids=reconsider,
        allow_unfulfilled=allow_unfulfilled,
        water_cap_overrides=overrides or None,
        prefer_placements=prefs,
        secondary_change_weight=1e-6,
    )
    if ref.status != FeasibilityStatus.feasible:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason=ref.reason or "Emergency schedule infeasible",
            solver_outcome=_solver_outcome(ref.status),
            explanation=(
                "No feasible emergency schedule: temporary and normal water caps, GPU "
                f"capacity, eligibility, readiness, and critical deadlines cannot all be met. "
                f"{ref.reason or ''}"
            ),
        )

    # Proposed = same min-cost result for emergency (objective is modeled cost).
    prop = ref
    unfulfilled = set(prop.unfulfilled_job_ids)
    reasons = {
        jid: "noncritical_unfulfilled_under_emergency_restriction"
        for jid in unfulfilled
    }

    # Critical must not be unfulfilled.
    if unfulfilled & critical_ids:
        return _infeasible_response(
            snapshot=snapshot,
            preview_id=preview_id,
            reason="Critical job marked unfulfilled",
            solver_outcome=SolverOutcome.solver_error,
            explanation="Internal error: critical jobs must never be dropped.",
        )

    horizon_start, horizon_end = default_evaluation_horizon(
        snapshot.sites,
        snapshot.current_time,
        prop.selected,
        gen.running_reservations,
    )
    # For emergency, metrics_before is still a computed reference under the same
    # restriction (min-cost). There is no separate water-minimizing second pass.
    ref_ev = evaluate_schedule(
        snapshot.jobs,
        snapshot.sites,
        snapshot.current_time,
        selected=prop.selected,
        running_reservations=gen.running_reservations,
        unfulfilled_job_ids=unfulfilled,
        unfulfilled_reasons=reasons,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        water_cap_overrides=overrides or None,
    )
    prop_ev = ref_ev

    # Critical deadline check.
    for o in prop_ev.deadline_outcomes:
        if o.job_id in critical_ids and (not o.met_deadline or o.unfulfilled):
            return _infeasible_response(
                snapshot=snapshot,
                preview_id=preview_id,
                reason=f"Critical job {o.job_id} does not meet deadline",
                solver_outcome=SolverOutcome.infeasible,
                explanation="Emergency mode requires all critical jobs to finish on time.",
            )

    changes = _job_changes(snapshot.jobs, prop.selected, unfulfilled)
    assignments = _proposed_assignments(
        snapshot.jobs,
        prop.selected,
        unfulfilled,
        reasons,
        {r.job_id: r for r in gen.running_reservations},
    )

    er = request.emergency_restriction
    assert er is not None
    explanation = (
        f"Emergency optimization under temporary water cap {er.water_cap_liters_per_hour} L/h "
        f"on {er.site_id} from {er.start.isoformat()} to {er.end.isoformat()} "
        f"(stricter of temporary and normal caps during overlap; partial hours honored). "
        f"Minimized modeled cost while keeping running jobs fixed and critical jobs on time. "
        f"Unfulfilled noncritical jobs: {sorted(unfulfilled) or 'none'}. "
        f"Assignment changes vs saved queue: {len(changes)}."
    )

    response = ReallocationPreviewResponse(
        preview_id=preview_id,
        input_revision=snapshot.revision,
        status=PreviewStatus.feasible,
        proposed_job_changes=changes,
        metrics_before=_agg(ref_ev),
        metrics_after=_agg(prop_ev),
        per_site_water=_site_water(prop_ev),
        cost_breakdown=_cost_breakdown(prop_ev),
        deadline_outcomes=_deadlines(prop_ev),
        explanation=explanation,
        infeasibility_reason=None,
        simulated=True,
        metrics_before_label=MetricsSourceLabel.computed_reference_schedule,
        reference_cost_usd=ref_ev.total_cost_usd,
        cost_ceiling_usd=None,
        max_additional_cost_usd=None,
        evaluation_start=horizon_start,
        evaluation_end=horizon_end,
        per_site_before=_site_schedule(ref_ev),
        per_site_after=_site_schedule(prop_ev),
        proposed_assignments=assignments,
        solver_outcome=SolverOutcome.ok,
        session_id=snapshot.session_id,
        sites_fixture_name=snapshot.sites_fixture_name,
    )

    if persist:
        store_preview(
            StoredPreview(
                preview_id=preview_id,
                session_id=snapshot.session_id,
                input_revision=snapshot.revision,
                simulation_time=snapshot.current_time,
                sites_fixture_name=snapshot.sites_fixture_name,
                proposed_assignments=assignments,
                unfulfilled_job_ids=tuple(sorted(unfulfilled)),
                response=response,
            )
        )
    return response


def apply_reallocation(request: ReallocationApplyRequest) -> ReallocationApplyResponse:
    raise NotImplementedError(
        "POST /api/reallocation/apply is owned by Person 1. "
        "Use app.services.preview_store.get_stored_preview(preview_id) and reject when "
        "session_id or input_revision do not match the live session."
    )
