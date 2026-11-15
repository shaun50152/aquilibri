"""Equilibrium / multi-objective placement scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import DEMO_CLOCK_ANCHOR_ISO
from app.schemas.jobs import Job, JobStatus, JobTemplate
from app.schemas.sites import HourlySiteProfile, SiteProfile
from app.services.placement_engine import (
    FeasibilityStatus,
    OptimizeObjective,
    generate_placement_candidates,
    optimize_placements,
    score_placement_candidate,
)
from app.services.scheduling_service import try_place_submitted_job

UTC = timezone.utc
NOW = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)


def _hour(h: int) -> HourlySiteProfile:
    return HourlySiteProfile(
        hour_start=NOW + timedelta(hours=h),
        gpu_capacity=32,
        fixed_water_liters_per_hour=10.0,
        incremental_water_liters_per_gpu_hour=1.0,
        energy_kwh_per_gpu_hour=1.0,
        electricity_usd_per_kwh=0.1,
        water_cap_liters_per_hour=500.0,
    )


def _sites() -> list[SiteProfile]:
    hours = [_hour(i) for i in range(12)]
    return [
        SiteProfile(site_id="site-alpha", name="Alpha Mesa DC", hourly_profiles=hours),
        SiteProfile(site_id="site-beta", name="Beta River DC", hourly_profiles=list(hours)),
    ]


def _job(job_id: str) -> Job:
    return Job(
        id=job_id,
        session_id="test-session",
        owner_id="demo-user-001",
        template=JobTemplate.inference_batch,
        earliest_start=NOW,
        deadline=NOW + timedelta(hours=12),
        gpu_count=4,
        duration_hours=1.0,
        eligible_site_ids=["site-alpha", "site-beta"],
        data_ready_at_by_site={"site-alpha": NOW, "site-beta": NOW},
        critical=False,
        miss_penalty_usd=10.0,
        status=JobStatus.queued,
        assigned_site_id=None,
        scheduled_start=None,
    )


def test_utilization_penalty_squares_with_load():
    from app.services.placement_engine import PlacementCandidate

    def cand(site: str):
        return PlacementCandidate(
            job_id="j",
            site_id=site,
            start=NOW,
            finish=NOW + timedelta(hours=1),
            gpu_count=4,
            contributions=(),
            estimated_incremental_water_liters=4.0,
            estimated_energy_kwh=4.0,
            estimated_electricity_cost_usd=0.4,
        )

    empty = score_placement_candidate(cand("site-alpha"), jobs_already_at_site=0, earliest_start=NOW)
    busy = score_placement_candidate(cand("site-alpha"), jobs_already_at_site=2, earliest_start=NOW)
    assert busy > empty
    # (3^2 - 1^2) * W1 = 8 * 10 = 80
    assert busy - empty == 80.0


def test_balanced_optimizer_spreads_ten_jobs():
    sites = _sites()
    jobs = [_job(f"job-{i:02d}") for i in range(10)]
    gen = generate_placement_candidates(jobs, sites, NOW)
    opt = optimize_placements(
        gen.candidates_by_job,
        gen.running_reservations,
        sites,
        {j.id: j for j in jobs},
        objective=OptimizeObjective.balanced,
        job_ids=[j.id for j in jobs],
        allow_unfulfilled=set(),
    )
    assert opt.status == FeasibilityStatus.feasible
    assert len(opt.selected) == 10
    counts = {"site-alpha": 0, "site-beta": 0}
    for cand in opt.selected.values():
        counts[cand.site_id] += 1
    assert counts["site-alpha"] == 5
    assert counts["site-beta"] == 5


def test_try_place_alternates_sites_on_sequential_submit():
    sites = _sites()
    existing: list[Job] = []
    assigned_sites: list[str] = []
    for i in range(6):
        job = _job(f"job-seq-{i}")
        placed, rebalanced = try_place_submitted_job(job, existing, sites, NOW)
        assert placed.status == JobStatus.scheduled
        assert placed.assigned_site_id in {"site-alpha", "site-beta"}
        for rb in rebalanced:
            for idx, ej in enumerate(existing):
                if ej.id == rb.id:
                    existing[idx] = rb
        existing.append(placed)
        assigned_sites.append(placed.assigned_site_id)  # type: ignore[arg-type]

    # Final distribution across the queue should be balanced (rebalance on each submit).
    final_counts = {"site-alpha": 0, "site-beta": 0}
    for j in existing:
        assert j.assigned_site_id is not None
        final_counts[j.assigned_site_id] += 1
    assert abs(final_counts["site-alpha"] - final_counts["site-beta"]) <= 1
