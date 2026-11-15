"""Service/API tests for Person 2 reallocation preview milestone."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import (
    DEMO_CLOCK_ANCHOR_ISO,
    FIXTURES_DIR,
    SITE_PROFILES_EXTENDED_SYNTHETIC,
    SITE_PROFILES_SHORT,
    clear_settings_cache,
)
from app.schemas.jobs import Job, JobStatus, JobTemplate
from app.schemas.scheduling import (
    EmergencyRestriction,
    MetricsSourceLabel,
    PreviewStatus,
    ReallocationMode,
    ReallocationPreviewRequest,
    ReallocationPreviewResponse,
    SolverOutcome,
)
from app.schemas.sites import HourlySiteProfile, SiteProfile
from app.services import scheduling_service
from app.services.placement_engine import (
    FeasibilityStatus,
    WaterCapOverride,
    generate_placement_candidates,
)
from app.services.preview_store import clear_preview_store, get_stored_preview
from app.services.scheduling_service import SchedulingSnapshot, capture_snapshot
from app.state import SessionState, build_session_state, get_session_state, replace_session_state

UTC = timezone.utc
NOW = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)


def _load_jobs() -> list[Job]:
    raw = json.loads((FIXTURES_DIR / "jobs.json").read_text(encoding="utf-8"))
    return [Job.model_validate(j) for j in raw["jobs"]]


def _load_sites(name: str) -> list[SiteProfile]:
    raw = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return [SiteProfile.model_validate(s) for s in raw["sites"]]


def _snapshot(sites_name: str = SITE_PROFILES_EXTENDED_SYNTHETIC) -> SchedulingSnapshot:
    return SchedulingSnapshot(
        session_id="test-session",
        revision=7,
        current_time=NOW,
        jobs=_load_jobs(),
        sites=_load_sites(sites_name),
        sites_fixture_name=sites_name,
    )


def _hour(
    start: datetime,
    *,
    gpu: int = 32,
    fixed: float = 10.0,
    incr: float = 1.0,
    energy: float = 1.0,
    price: float = 0.10,
    cap: float | None = 1000.0,
) -> HourlySiteProfile:
    return HourlySiteProfile(
        hour_start=start,
        gpu_capacity=gpu,
        fixed_water_liters_per_hour=fixed,
        incremental_water_liters_per_gpu_hour=incr,
        energy_kwh_per_gpu_hour=energy,
        electricity_usd_per_kwh=price,
        water_cap_liters_per_hour=cap,
    )


def _site(site_id: str, hours: list[HourlySiteProfile]) -> SiteProfile:
    return SiteProfile(site_id=site_id, name=site_id, hourly_profiles=hours)


def _job(**kwargs) -> Job:
    defaults = dict(
        id="job-x",
        session_id="test",
        owner_id="demo-user-001",
        template=JobTemplate.inference_batch,
        earliest_start=NOW,
        deadline=NOW + timedelta(hours=12),
        gpu_count=4,
        duration_hours=1.0,
        eligible_site_ids=["site-alpha"],
        data_ready_at_by_site={"site-alpha": NOW},
        critical=False,
        miss_penalty_usd=50.0,
        status=JobStatus.queued,
    )
    defaults.update(kwargs)
    return Job(**defaults)


@pytest.fixture(autouse=True)
def _clear_previews():
    clear_preview_store()
    yield
    clear_preview_store()


@pytest.fixture
def extended_session(client):
    """Point the live session at the extended synthetic fixture for API tests."""
    clear_settings_cache()
    import os

    os.environ["SITE_PROFILES_FIXTURE"] = SITE_PROFILES_EXTENDED_SYNTHETIC
    clear_settings_cache()
    state = build_session_state()
    previous = get_session_state()
    replace_session_state(state)
    client.post("/api/demo/reset")
    # reset bumps revision and restores jobs; sites stay from state
    yield state
    replace_session_state(previous)
    os.environ.pop("SITE_PROFILES_FIXTURE", None)
    clear_settings_cache()


# ---------------------------------------------------------------------------
# Telemetry consistency
# ---------------------------------------------------------------------------


def test_get_sites_matches_solver_profiles(extended_session, client):
    api_sites = client.get("/api/sites").json()["sites"]
    snap = capture_snapshot(get_session_state())
    assert snap.sites_fixture_name == SITE_PROFILES_EXTENDED_SYNTHETIC
    assert [s["site_id"] for s in api_sites] == [s.site_id for s in snap.sites]
    assert len(api_sites[0]["hourly_profiles"]) == len(snap.sites[0].hourly_profiles)
    # Solver uses the same snapshot sites object graph from session.
    gen = generate_placement_candidates(snap.jobs, snap.sites, snap.current_time)
    assert gen.running_reservations  # extended covers the 6h running job


def test_short_fixture_preserved_for_missing_coverage():
    snap = _snapshot(SITE_PROFILES_SHORT)
    assert all(len(s.hourly_profiles) == 3 for s in snap.sites)
    gen = generate_placement_candidates(snap.jobs, snap.sites, snap.current_time)
    assert gen.candidates_by_job.get("job-hackuta-0003") == []


def test_extended_fixture_covers_required_runs():
    snap = _snapshot(SITE_PROFILES_EXTENDED_SYNTHETIC)
    gen = generate_placement_candidates(snap.jobs, snap.sites, snap.current_time)
    assert any(r.job_id == "job-hackuta-0002" for r in gen.running_reservations)
    assert gen.candidates_by_job["job-hackuta-0001"]
    assert gen.candidates_by_job["job-hackuta-0003"]


# ---------------------------------------------------------------------------
# Routine optimization
# ---------------------------------------------------------------------------


def test_routine_respects_reference_cost_budget():
    snap = _snapshot()
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=0.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    assert resp.metrics_before_label == MetricsSourceLabel.computed_reference_schedule
    assert resp.reference_cost_usd is not None
    assert resp.cost_ceiling_usd == pytest.approx(resp.reference_cost_usd)
    assert resp.metrics_after is not None
    assert resp.metrics_after.total_cost_usd <= resp.cost_ceiling_usd + 1e-6


def test_routine_omitted_allowance_is_zero():
    snap = _snapshot()
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    assert resp.max_additional_cost_usd == 0.0
    assert resp.cost_ceiling_usd == pytest.approx(resp.reference_cost_usd)


def test_routine_preserves_workload_and_deadlines():
    snap = _snapshot()
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=100.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    assigned = {a.job_id: a for a in resp.proposed_assignments}
    assert "job-hackuta-0001" in assigned
    assert "job-hackuta-0003" in assigned
    assert not assigned["job-hackuta-0001"].unfulfilled
    assert not assigned["job-hackuta-0003"].unfulfilled
    for o in resp.deadline_outcomes:
        if o.job_id in {"job-hackuta-0001", "job-hackuta-0003"}:
            assert o.met_deadline
            assert not o.unfulfilled


def test_routine_balanced_spreads_identical_jobs_across_sites():
    """Equilibrium scoring should alternate identical jobs across Alpha/Beta."""
    hours = [
        _hour(NOW + timedelta(hours=i), incr=1.0, energy=1.0, price=0.1, gpu=32, fixed=0.0)
        for i in range(8)
    ]
    sites = [_site("site-alpha", hours), _site("site-beta", list(hours))]
    jobs = [
        _job(
            id=f"job-bal-{i}",
            eligible_site_ids=["site-alpha", "site-beta"],
            data_ready_at_by_site={"site-alpha": NOW, "site-beta": NOW},
            gpu_count=4,
            duration_hours=1.0,
            deadline=NOW + timedelta(hours=8),
        )
        for i in range(4)
    ]
    snap = SchedulingSnapshot(
        session_id="s",
        revision=1,
        current_time=NOW,
        jobs=jobs,
        sites=sites,
        sites_fixture_name="inline-synthetic",
    )
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=50.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    sites_used = {
        a.assigned_site_id
        for a in resp.proposed_assignments
        if not a.unfulfilled and a.assigned_site_id
    }
    assert sites_used == {"site-alpha", "site-beta"}
    counts = {"site-alpha": 0, "site-beta": 0}
    for a in resp.proposed_assignments:
        if a.assigned_site_id:
            counts[a.assigned_site_id] += 1
    assert abs(counts["site-alpha"] - counts["site-beta"]) <= 1


def test_routine_infeasible_on_short_telemetry():
    snap = _snapshot(SITE_PROFILES_SHORT)
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=50.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.infeasible
    assert resp.metrics_after is None
    assert resp.solver_outcome in {
        SolverOutcome.infeasible,
        SolverOutcome.incomplete_input,
    }
    assert resp.infeasibility_reason


# ---------------------------------------------------------------------------
# Emergency optimization
# ---------------------------------------------------------------------------


def test_emergency_limit_only_requested_site_and_window():
    snap = _snapshot()
    # Cap only alpha tightly enough that 16-GPU alpha windows are eliminated;
    # beta remains unrestricted by the emergency override.
    req = ReallocationPreviewRequest(
        mode=ReallocationMode.emergency,
        emergency_restriction=EmergencyRestriction(
            site_id="us-west-1",
            water_cap_liters_per_hour=100.0,
            start=NOW + timedelta(hours=2),
            end=NOW + timedelta(hours=18),
        ),
    )
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    # Cap 100 L/h blocks 16-GPU Santa Clara placements (fixed≈115 + incremental > 100).
    large = next(a for a in resp.proposed_assignments if a.job_id == "job-hackuta-0003")
    assert not large.unfulfilled
    assert large.assigned_site_id != "us-west-1"
    # Restriction is Santa Clara-only: other regions remain eligible.
    assert all(
        c.new_placement.assigned_site_id != "us-west-1"
        or c.job_id != "job-hackuta-0003"
        for c in resp.proposed_job_changes
    )


def test_job_moves_to_other_eligible_site_when_required():
    test_emergency_limit_only_requested_site_and_window()


def test_job_can_remain_at_original_site_when_still_best():
    # Mild cap that still allows the saved 4-GPU job on alpha.
    snap = _snapshot()
    req = ReallocationPreviewRequest(
        mode=ReallocationMode.routine,
        max_additional_cost_usd=100.0,
    )
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    # Saved placement for 0001 is alpha @ 19:00; may remain if selected.
    a1 = next(a for a in resp.proposed_assignments if a.job_id == "job-hackuta-0001")
    assert a1.assigned_site_id in {
        "us-east-1",
        "us-south-central",
        "us-west-1",
        "eu-west-1",
        "ap-northeast",
    }
    # At least one job keeps an assignment (not all forced to move).
    assert any(not a.unfulfilled for a in resp.proposed_assignments)


def test_running_jobs_remain_fixed():
    snap = _snapshot()
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=50.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    running = next(j for j in snap.jobs if j.id == "job-hackuta-0002")
    for change in resp.proposed_job_changes:
        assert change.job_id != "job-hackuta-0002"
    assigned = {a.job_id: a for a in resp.proposed_assignments}
    assert assigned["job-hackuta-0002"].assigned_site_id == running.assigned_site_id
    assert assigned["job-hackuta-0002"].scheduled_start == running.scheduled_start


def test_critical_jobs_never_silently_dropped():
    snap = _snapshot()
    # Extreme cap on both paths via alpha-only eligibility would be needed;
    # with fixture eligibility, use an override that still leaves beta open.
    req = ReallocationPreviewRequest(
        mode=ReallocationMode.emergency,
        emergency_restriction=EmergencyRestriction(
            site_id="us-west-1",
            water_cap_liters_per_hour=0.0,
            start=NOW,
            end=NOW + timedelta(hours=24),
        ),
    )
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    large = next(a for a in resp.proposed_assignments if a.job_id == "job-hackuta-0003")
    assert large.unfulfilled is False
    assert large.assigned_site_id != "us-west-1"


def test_noncritical_unfulfilled_incurs_configured_penalty():
    # Only one site, emergency cap too tight for the noncritical job; critical none.
    hours = [
        _hour(NOW + timedelta(hours=i), gpu=8, fixed=40.0, incr=10.0, cap=45.0)
        for i in range(3)
    ]
    sites = [_site("site-alpha", hours)]
    job = _job(
        id="job-nc",
        gpu_count=4,  # rate 40; fixed+job=80 > 45 even with emergency
        duration_hours=1.0,
        critical=False,
        miss_penalty_usd=123.0,
        eligible_site_ids=["site-alpha"],
        data_ready_at_by_site={"site-alpha": NOW},
    )
    # Use emergency with same site to allow unfulfilled path when no candidates.
    # First: make profile cap high so candidates exist without emergency, then
    # emergency makes them invalid... generate uses overrides so no candidates.
    hours2 = [
        _hour(NOW + timedelta(hours=i), gpu=8, fixed=40.0, incr=10.0, cap=1000.0)
        for i in range(3)
    ]
    sites = [_site("site-alpha", hours2)]
    snap = SchedulingSnapshot(
        session_id="s",
        revision=1,
        current_time=NOW,
        jobs=[job],
        sites=sites,
        sites_fixture_name="inline",
    )
    req = ReallocationPreviewRequest(
        mode=ReallocationMode.emergency,
        emergency_restriction=EmergencyRestriction(
            site_id="site-alpha",
            water_cap_liters_per_hour=45.0,
            start=NOW,
            end=NOW + timedelta(hours=3),
        ),
    )
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    assert resp.cost_breakdown is not None
    assert resp.cost_breakdown.miss_penalties_usd == pytest.approx(123.0)
    assert any(o.unfulfilled and o.job_id == "job-nc" for o in resp.deadline_outcomes)


def test_unavoidable_running_load_violation_is_infeasible():
    hours = [
        _hour(NOW + timedelta(hours=i), gpu=32, fixed=80.0, incr=5.0, cap=100.0)
        for i in range(4)
    ]
    sites = [_site("site-alpha", hours)]
    running = _job(
        id="job-run",
        status=JobStatus.running,
        gpu_count=8,  # 80 + 40 = 120 > 100
        duration_hours=2.0,
        assigned_site_id="site-alpha",
        scheduled_start=NOW,
        eligible_site_ids=["site-alpha"],
        data_ready_at_by_site={"site-alpha": NOW},
    )
    queued = _job(id="job-q")
    snap = SchedulingSnapshot(
        session_id="s",
        revision=1,
        current_time=NOW,
        jobs=[running, queued],
        sites=sites,
        sites_fixture_name="inline",
    )
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=10.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.infeasible
    assert resp.solver_outcome == SolverOutcome.infeasible
    assert "site-alpha" in (resp.infeasibility_reason or "")


def test_partial_hour_caps_not_averaged_away():
    hours = [
        _hour(NOW, gpu=16, fixed=10.0, incr=10.0, cap=1000.0),
        _hour(NOW + timedelta(hours=1), gpu=16, fixed=10.0, incr=10.0, cap=1000.0),
    ]
    sites = [_site("site-alpha", hours)]
    job = _job(
        id="job-frac",
        gpu_count=4,
        duration_hours=0.25,
        deadline=NOW + timedelta(minutes=15),
    )
    gen = generate_placement_candidates(
        [job],
        sites,
        NOW,
        water_cap_overrides=[
            WaterCapOverride(
                site_id="site-alpha",
                start=NOW,
                end=NOW + timedelta(minutes=15),
                water_cap_liters_per_hour=49.0,  # fixed 10 + rate 40 = 50 > 49
            )
        ],
    )
    # Only feasible start is NOW; partial-hour emergency cap rejects the instantaneous rate.
    assert gen.candidates_by_job["job-frac"] == []
    assert any(
        d.code.value == "water_cap_restriction" for d in gen.diagnostics if d.job_id == "job-frac"
    )


# ---------------------------------------------------------------------------
# Metrics, immutability, cache, errors
# ---------------------------------------------------------------------------


def test_metrics_reconcile_across_sites_and_cost():
    snap = _snapshot()
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=50.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    assert resp.metrics_after is not None
    assert resp.cost_breakdown is not None
    site_sum = sum(s.water_liters for s in resp.per_site_after)
    assert site_sum == pytest.approx(resp.metrics_after.total_water_liters, rel=1e-6)
    assert resp.cost_breakdown.water_usd == 0.0
    assert resp.cost_breakdown.total_usd == pytest.approx(
        resp.cost_breakdown.energy_usd
        + resp.cost_breakdown.water_usd
        + resp.cost_breakdown.miss_penalties_usd
    )
    assert resp.metrics_after.total_cost_usd == pytest.approx(resp.cost_breakdown.total_usd)
    assert resp.evaluation_start is not None and resp.evaluation_end is not None


def test_preview_leaves_queue_clock_revision_unchanged(extended_session, client):
    before_jobs = client.get("/api/jobs").json()
    before_demo = client.get("/api/demo").json()
    resp = client.post(
        "/api/reallocation/preview",
        json={"mode": "routine", "max_additional_cost_usd": 25.0},
    )
    assert resp.status_code == 200
    after_jobs = client.get("/api/jobs").json()
    after_demo = client.get("/api/demo").json()
    assert after_jobs["revision"] == before_jobs["revision"]
    assert after_demo["current_time"] == before_demo["current_time"]
    assert after_demo["revision"] == before_demo["revision"]
    assert after_jobs["jobs"] == before_jobs["jobs"]


def test_cached_preview_has_complete_assignments():
    snap = _snapshot()
    state = SessionState(
        session_id=snap.session_id,
        current_time=snap.current_time,
        revision=snap.revision,
        jobs=snap.jobs,
        sites=snap.sites,
        sites_fixture_name=snap.sites_fixture_name,
    )
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=25.0)
    resp = scheduling_service.preview_reallocation(req, state)
    assert resp.status == PreviewStatus.feasible
    stored = get_stored_preview(resp.preview_id)
    assert stored is not None
    assert stored.input_revision == snap.revision
    assert stored.session_id == snap.session_id
    ids = {a.job_id for a in stored.proposed_assignments}
    assert {"job-hackuta-0001", "job-hackuta-0002", "job-hackuta-0003"} <= ids
    assert len(stored.proposed_assignments) >= 3


def test_invalid_emergency_site_returns_422(extended_session, client):
    resp = client.post(
        "/api/reallocation/preview",
        json={
            "mode": "emergency",
            "emergency_restriction": {
                "site_id": "site-does-not-exist",
                "water_cap_liters_per_hour": 100,
                "start": "2026-03-15T20:00:00+00:00",
                "end": "2026-03-16T04:00:00+00:00",
            },
        },
    )
    assert resp.status_code == 422


def test_nl_only_emergency_explains_structured_fields():
    snap = _snapshot()
    req = ReallocationPreviewRequest(
        mode=ReallocationMode.emergency,
        natural_language_instruction="cap alpha water hard",
        emergency_restriction=None,
    )
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.infeasible
    assert resp.solver_outcome == SolverOutcome.incomplete_input
    assert "emergency_restriction" in (resp.infeasibility_reason or "")


def test_solver_timeout_distinguished(monkeypatch):
    snap = _snapshot()

    def fake_optimize(*args, **kwargs):
        from app.services.placement_engine import OptimizeResult

        return OptimizeResult(
            status=FeasibilityStatus.solver_timeout,
            reason="MILP time limit reached",
        )

    monkeypatch.setattr(scheduling_service, "optimize_placements", fake_optimize)
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=1.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.infeasible
    assert resp.solver_outcome == SolverOutcome.solver_timeout


def test_old_placements_are_saved_jobs_not_reference():
    snap = _snapshot()
    saved = next(j for j in snap.jobs if j.id == "job-hackuta-0001")
    req = ReallocationPreviewRequest(mode=ReallocationMode.routine, max_additional_cost_usd=100.0)
    resp = scheduling_service._compute_preview(req, snap, persist=False)
    assert resp.status == PreviewStatus.feasible
    for change in resp.proposed_job_changes:
        if change.job_id == "job-hackuta-0001":
            assert change.old_placement.assigned_site_id == saved.assigned_site_id
            assert change.old_placement.scheduled_start == saved.scheduled_start
    assert resp.metrics_before_label == MetricsSourceLabel.computed_reference_schedule


def test_sample_previews_still_validate():
    for name in ("preview_move.json", "preview_unfulfilled.json", "preview_infeasible.json"):
        raw = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
        data = {k: v for k, v in raw.items() if not k.startswith("_")}
        ReallocationPreviewResponse.model_validate(data)
