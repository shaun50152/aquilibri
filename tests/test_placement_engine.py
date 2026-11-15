"""Focused tests for Person 2 placement engine (candidates + MILP feasibility)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import DEMO_CLOCK_ANCHOR_ISO, FIXTURES_DIR
from app.schemas.jobs import Job, JobStatus, JobTemplate
from app.schemas.sites import HourlySiteProfile, SiteProfile
from app.services.placement_engine import (
    DiagnosticCode,
    FeasibilityStatus,
    check_simultaneous_feasibility,
    demo_shared_fixture_candidates,
    finish_at,
    generate_placement_candidates,
)

UTC = timezone.utc
NOW = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)


def _ts(hour_offset: int) -> datetime:
    return NOW + timedelta(hours=hour_offset)


def _job(
    *,
    job_id: str = "job-a",
    status: JobStatus = JobStatus.queued,
    gpu_count: int = 4,
    duration_hours: float = 2.0,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    eligible_site_ids: list[str] | None = None,
    data_ready_at_by_site: dict[str, datetime] | None = None,
    assigned_site_id: str | None = None,
    scheduled_start: datetime | None = None,
) -> Job:
    sites = eligible_site_ids or ["site-alpha"]
    ready = data_ready_at_by_site or {s: NOW for s in sites}
    return Job(
        id=job_id,
        session_id="test-session",
        owner_id="demo-user-001",
        template=JobTemplate.inference_batch,
        earliest_start=earliest_start or NOW,
        deadline=deadline or _ts(24),
        gpu_count=gpu_count,
        duration_hours=duration_hours,
        eligible_site_ids=sites,
        data_ready_at_by_site=ready,
        critical=False,
        miss_penalty_usd=10.0,
        status=status,
        assigned_site_id=assigned_site_id,
        scheduled_start=scheduled_start,
    )


def _hour(
    hour_start: datetime,
    *,
    gpu_capacity: int = 16,
    fixed_water: float = 10.0,
    incremental: float = 1.0,
    energy: float = 1.0,
    price: float = 0.1,
    water_cap: float | None = 100.0,
) -> HourlySiteProfile:
    return HourlySiteProfile(
        hour_start=hour_start,
        gpu_capacity=gpu_capacity,
        fixed_water_liters_per_hour=fixed_water,
        incremental_water_liters_per_gpu_hour=incremental,
        energy_kwh_per_gpu_hour=energy,
        electricity_usd_per_kwh=price,
        water_cap_liters_per_hour=water_cap,
    )


def _site(
    site_id: str,
    hours: list[HourlySiteProfile],
    name: str | None = None,
) -> SiteProfile:
    return SiteProfile(site_id=site_id, name=name or site_id, hourly_profiles=hours)


def _hours_range(
    n: int,
    *,
    gpu_capacity: int = 16,
    fixed_water: float = 10.0,
    incremental: float = 1.0,
    water_cap: float | None = 100.0,
    start: datetime | None = None,
) -> list[HourlySiteProfile]:
    base = start or NOW
    return [
        _hour(
            base + timedelta(hours=i),
            gpu_capacity=gpu_capacity,
            fixed_water=fixed_water,
            incremental=incremental,
            water_cap=water_cap,
        )
        for i in range(n)
    ]


def _load_shared_jobs() -> list[Job]:
    raw = json.loads((FIXTURES_DIR / "jobs.json").read_text(encoding="utf-8"))
    return [Job.model_validate(j) for j in raw["jobs"]]


def _load_shared_sites() -> list[SiteProfile]:
    raw = json.loads((FIXTURES_DIR / "sites.json").read_text(encoding="utf-8"))
    return [SiteProfile.model_validate(s) for s in raw["sites"]]


def _load_extended_sites() -> list[SiteProfile]:
    raw = json.loads(
        (FIXTURES_DIR / "sites_extended_synthetic.json").read_text(encoding="utf-8")
    )
    return [SiteProfile.model_validate(s) for s in raw["sites"]]


# ---------------------------------------------------------------------------
# Candidate generation basics
# ---------------------------------------------------------------------------


def test_valid_candidate_is_generated():
    sites = [_site("site-alpha", _hours_range(6, gpu_capacity=16, water_cap=100.0))]
    job = _job(duration_hours=2.0, gpu_count=4)
    result = generate_placement_candidates([job], sites, NOW)
    cands = result.candidates_by_job[job.id]
    assert cands
    first = cands[0]
    assert first.site_id == "site-alpha"
    assert first.start == NOW
    assert first.finish == finish_at(NOW, 2.0)
    assert first.gpu_count == 4
    # 4 GPUs * 1.0 L/gpu-h * 2 h = 8 L incremental
    assert first.estimated_incremental_water_liters == pytest.approx(8.0)
    assert first.estimated_energy_kwh == pytest.approx(8.0)
    assert first.estimated_electricity_cost_usd == pytest.approx(0.8)


def test_site_restricted_job_stays_at_eligible_site():
    sites = [
        _site("site-alpha", _hours_range(4)),
        _site("site-beta", _hours_range(4)),
    ]
    job = _job(
        eligible_site_ids=["site-beta"],
        data_ready_at_by_site={"site-beta": NOW},
    )
    result = generate_placement_candidates([job], sites, NOW)
    cands = result.candidates_by_job[job.id]
    assert cands
    assert {c.site_id for c in cands} == {"site-beta"}


def test_data_readiness_removes_starts_before_data_arrives():
    sites = [_site("site-alpha", _hours_range(6))]
    ready = _ts(2)
    job = _job(data_ready_at_by_site={"site-alpha": ready}, duration_hours=1.0)
    result = generate_placement_candidates([job], sites, NOW)
    starts = [c.start for c in result.candidates_by_job[job.id]]
    assert starts
    assert all(s >= ready for s in starts)
    assert NOW not in starts
    assert _ts(1) not in starts


def test_completion_exactly_at_deadline_is_accepted():
    sites = [_site("site-alpha", _hours_range(4))]
    deadline = _ts(2)
    job = _job(duration_hours=2.0, deadline=deadline)
    result = generate_placement_candidates([job], sites, NOW)
    finishes = [c.finish for c in result.candidates_by_job[job.id]]
    assert deadline in finishes
    match = next(c for c in result.candidates_by_job[job.id] if c.finish == deadline)
    assert match.start == NOW


def test_completion_after_deadline_is_rejected():
    sites = [_site("site-alpha", _hours_range(4))]
    # Duration 2h, deadline at +1h → impossible
    job = _job(duration_hours=2.0, deadline=_ts(1))
    result = generate_placement_candidates([job], sites, NOW)
    assert result.candidates_by_job[job.id] == []


def test_gpu_and_water_checked_across_multi_hour_job():
    # Hour 0: plenty; hour 1: only 2 GPUs — 4-GPU 2h job must fail.
    hours = [
        _hour(_ts(0), gpu_capacity=16, water_cap=100.0),
        _hour(_ts(1), gpu_capacity=2, water_cap=100.0),
        _hour(_ts(2), gpu_capacity=16, water_cap=100.0),
        _hour(_ts(3), gpu_capacity=16, water_cap=100.0),
    ]
    sites = [_site("site-alpha", hours)]
    job = _job(duration_hours=2.0, gpu_count=4)
    result = generate_placement_candidates([job], sites, NOW)
    # start at 18:00 overlaps tight hour → rejected; start at 20:00 ok
    starts = {c.start for c in result.candidates_by_job[job.id]}
    assert NOW not in starts
    assert _ts(2) in starts
    assert any(
        d.code == DiagnosticCode.insufficient_gpus for d in result.diagnostics
    )


def test_missing_telemetry_prevents_unsupported_placements():
    # Only two hours of telemetry; 3h job cannot place.
    sites = [_site("site-alpha", _hours_range(2))]
    job = _job(duration_hours=3.0)
    result = generate_placement_candidates([job], sites, NOW)
    assert result.candidates_by_job[job.id] == []
    assert any(d.code == DiagnosticCode.missing_telemetry for d in result.diagnostics)


def test_missing_data_ready_entry_excludes_site():
    sites = [
        _site("site-alpha", _hours_range(4)),
        _site("site-beta", _hours_range(4)),
    ]
    job = _job(
        eligible_site_ids=["site-alpha", "site-beta"],
        data_ready_at_by_site={"site-alpha": NOW},  # beta missing
    )
    result = generate_placement_candidates([job], sites, NOW)
    assert all(c.site_id == "site-alpha" for c in result.candidates_by_job[job.id])
    assert any(
        d.code == DiagnosticCode.missing_data_ready and d.site_id == "site-beta"
        for d in result.diagnostics
    )


# ---------------------------------------------------------------------------
# Running reservations
# ---------------------------------------------------------------------------


def test_running_work_reserves_remaining_gpus_and_water_without_moving():
    sites = [_site("site-alpha", _hours_range(8, gpu_capacity=16, incremental=2.0))]
    running = _job(
        job_id="job-run",
        status=JobStatus.running,
        gpu_count=8,
        duration_hours=4.0,
        assigned_site_id="site-alpha",
        scheduled_start=NOW,
    )
    queued = _job(
        job_id="job-q",
        gpu_count=12,  # 8+12=20 > 16 → blocked while running overlaps
        duration_hours=1.0,
    )
    result = generate_placement_candidates([running, queued], sites, NOW)
    assert len(result.running_reservations) == 1
    res = result.running_reservations[0]
    assert res.job_id == "job-run"
    assert res.site_id == "site-alpha"
    assert res.scheduled_start == NOW
    assert res.reserved_from == NOW
    assert res.finish == finish_at(NOW, 4.0)
    assert res.gpu_count == 8
    # 8 GPUs * 2 L/gpu-h * 4 h = 64 L
    assert res.estimated_incremental_water_liters == pytest.approx(64.0)

    # Queued job cannot start during the first 4 hours on this site.
    starts = {c.start for c in result.candidates_by_job["job-q"]}
    assert NOW not in starts
    assert _ts(1) not in starts
    assert _ts(2) not in starts
    assert _ts(3) not in starts
    assert _ts(4) in starts


def test_fixed_plus_running_water_above_cap_is_reported():
    # fixed 80 + 8*5=40 → 120 > cap 100
    hours = _hours_range(6, gpu_capacity=32, fixed_water=80.0, incremental=5.0, water_cap=100.0)
    sites = [_site("site-alpha", hours)]
    running = _job(
        job_id="job-run",
        status=JobStatus.running,
        gpu_count=8,
        duration_hours=2.0,
        assigned_site_id="site-alpha",
        scheduled_start=NOW,
    )
    result = generate_placement_candidates([running], sites, NOW)
    assert any(
        d.code == DiagnosticCode.running_water_cap_exceeded
        and d.site_id == "site-alpha"
        for d in result.diagnostics
    )


def test_fractional_duration_water_volume_and_instantaneous_rate_limit():
    # Cap 50; fixed 10; incremental 10 L/gpu-h; 4 GPUs → rate 40; total 50 on the nose for full hour.
    # 1.5h job: volume = 4*10*1.5 = 60 L; must still enforce full rate in each overlap.
    hours = [
        _hour(_ts(0), gpu_capacity=16, fixed_water=10.0, incremental=10.0, water_cap=50.0),
        _hour(_ts(1), gpu_capacity=16, fixed_water=10.0, incremental=10.0, water_cap=50.0),
        _hour(_ts(2), gpu_capacity=16, fixed_water=10.0, incremental=10.0, water_cap=50.0),
    ]
    sites = [_site("site-alpha", hours)]
    ok = _job(job_id="job-ok", gpu_count=4, duration_hours=1.5)
    result_ok = generate_placement_candidates([ok], sites, NOW)
    assert result_ok.candidates_by_job["job-ok"]
    cand = result_ok.candidates_by_job["job-ok"][0]
    assert cand.start == NOW
    assert cand.finish == finish_at(NOW, 1.5)
    assert cand.estimated_incremental_water_liters == pytest.approx(60.0)
    # Partial second hour still contributes rate 40 L/h (not averaged to 20).
    assert any(
        abs(part.incremental_water_rate_lph - 40.0) < 1e-9
        and part.interval_start == _ts(1)
        and part.interval_end == finish_at(NOW, 1.5)
        for part in cand.contributions
    )

    # Tightening cap to 49 makes instantaneous rate 50 > 49 even for a short overlap.
    tight = [
        _hour(_ts(0), gpu_capacity=16, fixed_water=10.0, incremental=10.0, water_cap=49.0),
        _hour(_ts(1), gpu_capacity=16, fixed_water=10.0, incremental=10.0, water_cap=49.0),
        _hour(_ts(2), gpu_capacity=16, fixed_water=10.0, incremental=10.0, water_cap=49.0),
    ]
    result_bad = generate_placement_candidates(
        [_job(job_id="job-bad", gpu_count=4, duration_hours=0.25)],
        [_site("site-alpha", tight)],
        NOW,
    )
    assert result_bad.candidates_by_job["job-bad"] == []
    assert any(
        d.code == DiagnosticCode.water_cap_restriction for d in result_bad.diagnostics
    )


def test_inputs_unchanged_after_computation():
    sites = [_site("site-alpha", _hours_range(4))]
    jobs = [_job(job_id="job-a"), _job(job_id="job-b", status=JobStatus.scheduled, scheduled_start=_ts(1), assigned_site_id="site-alpha")]
    jobs_before = [j.model_dump() for j in jobs]
    sites_before = [s.model_dump() for s in sites]
    generate_placement_candidates(jobs, sites, NOW)
    assert [j.model_dump() for j in jobs] == jobs_before
    assert [s.model_dump() for s in sites] == sites_before


# ---------------------------------------------------------------------------
# Simultaneous feasibility competition
# ---------------------------------------------------------------------------


def test_two_jobs_conflict_on_shared_gpu_capacity():
    # Capacity 10 GPUs. Each job needs 8. Independently OK; together not.
    # Single hour so they cannot stagger around the conflict.
    sites = [_site("site-alpha", _hours_range(1, gpu_capacity=10, water_cap=1000.0))]
    job_a = _job(job_id="job-a", gpu_count=8, duration_hours=1.0, deadline=_ts(1))
    job_b = _job(job_id="job-b", gpu_count=8, duration_hours=1.0, deadline=_ts(1))

    alone_a = generate_placement_candidates([job_a], sites, NOW)
    alone_b = generate_placement_candidates([job_b], sites, NOW)
    assert alone_a.candidates_by_job["job-a"]
    assert alone_b.candidates_by_job["job-b"]
    assert alone_a.candidates_by_job["job-a"][0].start == NOW
    assert alone_a.candidates_by_job["job-a"][0].finish == _ts(1)

    both = generate_placement_candidates([job_a, job_b], sites, NOW)
    feasibility = check_simultaneous_feasibility(
        both.candidates_by_job,
        both.running_reservations,
        sites,
    )
    assert feasibility.status == FeasibilityStatus.infeasible


def test_same_two_jobs_feasible_with_later_slot_or_second_site():
    # Site alpha capacity 10 — conflict if both at same hour.
    # Site beta capacity 16 — both can fit, or staggered starts on alpha work.
    alpha = _site(
        "site-alpha",
        _hours_range(4, gpu_capacity=10, water_cap=1000.0),
    )
    beta = _site(
        "site-beta",
        _hours_range(4, gpu_capacity=16, water_cap=1000.0),
    )
    sites = [alpha, beta]
    job_a = _job(
        job_id="job-a",
        gpu_count=8,
        duration_hours=1.0,
        eligible_site_ids=["site-alpha", "site-beta"],
        data_ready_at_by_site={"site-alpha": NOW, "site-beta": NOW},
    )
    job_b = _job(
        job_id="job-b",
        gpu_count=8,
        duration_hours=1.0,
        eligible_site_ids=["site-alpha", "site-beta"],
        data_ready_at_by_site={"site-alpha": NOW, "site-beta": NOW},
    )
    result = generate_placement_candidates([job_a, job_b], sites, NOW)
    feasibility = check_simultaneous_feasibility(
        result.candidates_by_job,
        result.running_reservations,
        sites,
    )
    assert feasibility.status == FeasibilityStatus.feasible
    assert feasibility.validated
    assert set(feasibility.selected) == {"job-a", "job-b"}

    sel_a = feasibility.selected["job-a"]
    sel_b = feasibility.selected["job-b"]
    # Either different sites, or non-overlapping intervals on the tight site.
    if sel_a.site_id == sel_b.site_id == "site-alpha":
        assert sel_a.finish <= sel_b.start or sel_b.finish <= sel_a.start
    else:
        assert {sel_a.site_id, sel_b.site_id} <= {"site-alpha", "site-beta"}

    # Resource totals: each 8 GPU-h * 1.0 L = 8 L incremental
    assert sel_a.estimated_incremental_water_liters == pytest.approx(8.0)
    assert sel_b.estimated_incremental_water_liters == pytest.approx(8.0)
    assert sel_a.gpu_count == 8 and sel_b.gpu_count == 8


def test_water_limited_competition_fails_with_sufficient_gpus():
    # GPUs: capacity 32, each job 4 → plenty.
    # Water: fixed 10, incremental 20 L/gpu-h, cap 50.
    # One job rate = 4*20=80; fixed+one = 90 > 50 — wait, that blocks even one job.
    # Recalibrate: incremental 5, 4 GPUs → 20; fixed 10 → 30 <= 50 alone.
    # Two jobs → 10+20+20=50 exactly OK. Use cap 49 so two fail, one succeeds.
    sites = [
        _site(
            "site-alpha",
            _hours_range(
                3,
                gpu_capacity=32,
                fixed_water=10.0,
                incremental=5.0,
                water_cap=49.0,
            ),
        )
    ]
    job_a = _job(job_id="job-a", gpu_count=4, duration_hours=1.0)
    job_b = _job(job_id="job-b", gpu_count=4, duration_hours=1.0)

    alone = generate_placement_candidates([job_a], sites, NOW)
    assert alone.candidates_by_job["job-a"]
    # Alone: fixed 10 + 20 = 30 <= 49

    both = generate_placement_candidates([job_a, job_b], sites, NOW)
    # Each still has individual candidates (generation only reserves running).
    assert both.candidates_by_job["job-a"]
    assert both.candidates_by_job["job-b"]

    feasibility = check_simultaneous_feasibility(
        # Restrict to the same hour so staggering cannot dodge the test.
        {
            "job-a": [c for c in both.candidates_by_job["job-a"] if c.start == NOW],
            "job-b": [c for c in both.candidates_by_job["job-b"] if c.start == NOW],
        },
        both.running_reservations,
        sites,
    )
    assert feasibility.status == FeasibilityStatus.infeasible


def test_scheduled_job_not_double_counted_when_reconsidered():
    sites = [_site("site-alpha", _hours_range(4, gpu_capacity=10, water_cap=1000.0))]
    scheduled = _job(
        job_id="job-sched",
        status=JobStatus.scheduled,
        gpu_count=8,
        duration_hours=1.0,
        assigned_site_id="site-alpha",
        scheduled_start=NOW,
    )
    queued = _job(job_id="job-q", gpu_count=8, duration_hours=1.0)
    result = generate_placement_candidates([scheduled, queued], sites, NOW)
    # Scheduled is reconsidered — not in running reservations.
    assert result.running_reservations == []
    feasibility = check_simultaneous_feasibility(
        result.candidates_by_job,
        result.running_reservations,
        sites,
    )
    # Both can be placed by staggering on the 10-GPU site.
    assert feasibility.status == FeasibilityStatus.feasible
    sel = feasibility.selected
    assert (
        sel["job-sched"].finish <= sel["job-q"].start
        or sel["job-q"].finish <= sel["job-sched"].start
    )


# ---------------------------------------------------------------------------
# Shared fixture / extended synthetic coverage
# ---------------------------------------------------------------------------


def test_shared_fixture_surfaces_limited_telemetry():
    result = demo_shared_fixture_candidates()
    assert any(d.code == DiagnosticCode.limited_telemetry_horizon for d in result.diagnostics)

    # 12h queued job cannot fit in 3 hourly records.
    assert result.candidates_by_job.get("job-hackuta-0003") == []
    assert any(
        d.job_id == "job-hackuta-0003"
        and d.code
        in {
            DiagnosticCode.missing_telemetry,
            DiagnosticCode.no_feasible_start,
            DiagnosticCode.data_unavailable_before_deadline,
        }
        for d in result.diagnostics
    )

    # Running 6h job lacks telemetry past hour 20:00.
    assert any(
        d.code == DiagnosticCode.running_missing_telemetry
        and d.job_id == "job-hackuta-0002"
        for d in result.diagnostics
    )

    # Shared sites keep only three hours — do not invent coverage.
    shared = _load_shared_sites()
    for site in shared:
        assert len(site.hourly_profiles) == 3


def test_extended_synthetic_covers_six_and_twelve_hour_jobs():
    jobs = _load_shared_jobs()
    sites = _load_extended_sites()
    # Relabel: extended file is explicitly synthetic.
    assert "synthetic" in (FIXTURES_DIR / "sites_extended_synthetic.json").name

    result = generate_placement_candidates(jobs, sites, NOW)
    # Running 6h reservation should succeed with extended coverage.
    assert any(r.job_id == "job-hackuta-0002" for r in result.running_reservations)
    running = next(r for r in result.running_reservations if r.job_id == "job-hackuta-0002")
    assert running.site_id == "site-beta"
    assert running.finish == finish_at(NOW, 6.0)

    # 2h scheduled job should have candidates.
    assert result.candidates_by_job["job-hackuta-0001"]

    # 12h queued job should find at least one candidate with 18h of telemetry.
    cands_12 = result.candidates_by_job["job-hackuta-0003"]
    assert cands_12
    assert all(hours_between_ok(c) for c in cands_12)


def hours_between_ok(c) -> bool:
    return abs((c.finish - c.start).total_seconds() / 3600.0 - 12.0) < 1e-9


def test_running_inconsistent_timestamps_diagnosed():
    sites = [_site("site-alpha", _hours_range(4))]
    bad = _job(
        job_id="job-bad-run",
        status=JobStatus.running,
        assigned_site_id="site-alpha",
        scheduled_start=_ts(2),  # in the future
        duration_hours=1.0,
    )
    result = generate_placement_candidates([bad], sites, NOW)
    assert result.running_reservations == []
    assert any(
        d.code == DiagnosticCode.running_inconsistent_timestamps for d in result.diagnostics
    )


def test_naive_current_time_rejected():
    sites = [_site("site-alpha", _hours_range(2))]
    job = _job()
    with pytest.raises(ValueError, match="timezone-aware"):
        generate_placement_candidates([job], sites, datetime(2026, 3, 15, 18, 0, 0))
