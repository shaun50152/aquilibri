#!/usr/bin/env python3
"""Standalone baseline vs AquaLibri balancer comparison (no FastAPI).

Prints Excel-ready JSON for:
  - artificial infeasible emergency-cap edge case
  - naive cheapest-cost greedy baseline
  - aqualibri_balancer (balanced multi-objective placement engine)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DEMO_CLOCK_ANCHOR_ISO
from app.demo_sites import DEMO_SITE_IDS, DEMO_SITE_SPECS, build_demo_site_profiles
from app.schemas.jobs import DEMO_OWNER_ID, Job, JobStatus, JobTemplate
from app.services.placement_engine import (
    OptimizeObjective,
    evaluate_schedule,
    generate_placement_candidates,
    optimize_placements,
)

UTC = timezone.utc
NOW = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)
SESSION_ID = "metrics-demo-session"

# ---------------------------------------------------------------------------
# Site config used by the naive baseline (cost_per_unit from demo specs)
# ---------------------------------------------------------------------------
SITES_CONFIG: list[dict[str, Any]] = [
    {
        "site_id": s.site_id,
        "name": s.name,
        "gpu_capacity": s.gpu_capacity,
        "wue_l_per_kwh": s.wue_l_per_kwh,
        "fixed_water_liters_per_hour": s.fixed_water_liters_per_hour,
        "incremental_water_liters_per_gpu_hour": s.incremental_water_liters_per_gpu_hour,
        "energy_kwh_per_gpu_hour": s.energy_kwh_per_gpu_hour,
        "electricity_usd_per_kwh": s.electricity_usd_per_kwh,
        # $/GPU-hour — naive solver sorts strictly by this.
        "cost_per_unit": round(s.electricity_usd_per_kwh * s.energy_kwh_per_gpu_hour, 6),
    }
    for s in DEMO_SITE_SPECS
]


@dataclass(frozen=True)
class MockJob:
    """Hardcoded demo workload used by both solvers."""

    id: str
    load: int  # abstract running load units (50–150)
    deadline_hours: int  # 1–48 relative to NOW
    duration_hours: float
    gpu_count: int


def generate_mock_jobs() -> list[MockJob]:
    """Return 50 deterministic jobs (loads 50–150, deadlines 1–48h)."""
    # Explicit table so Excel runs never drift with RNG.
    # Columns: load, deadline_h, duration_h, gpu_count
    table: list[tuple[int, int, float, int]] = [
        (52, 8, 2.0, 4),
        (61, 12, 2.0, 4),
        (73, 6, 2.0, 4),
        (88, 18, 6.0, 8),
        (95, 24, 6.0, 8),
        (110, 36, 12.0, 16),
        (64, 10, 2.0, 4),
        (77, 14, 2.0, 4),
        (84, 20, 6.0, 8),
        (99, 28, 6.0, 8),
        (120, 40, 12.0, 16),
        (55, 5, 2.0, 4),
        (68, 9, 2.0, 4),
        (81, 15, 2.0, 4),
        (92, 22, 6.0, 8),
        (105, 30, 6.0, 8),
        (130, 42, 12.0, 16),
        (58, 7, 2.0, 4),
        (70, 11, 2.0, 4),
        (86, 16, 6.0, 8),
        (97, 26, 6.0, 8),
        (115, 34, 12.0, 16),
        (50, 4, 2.0, 4),
        (66, 13, 2.0, 4),
        (79, 17, 2.0, 4),
        (90, 21, 6.0, 8),
        (102, 29, 6.0, 8),
        (125, 38, 12.0, 16),
        (54, 3, 2.0, 4),
        (72, 19, 2.0, 4),
        (83, 23, 6.0, 8),
        (94, 27, 6.0, 8),
        (108, 32, 6.0, 8),
        (140, 44, 12.0, 16),
        (57, 1, 2.0, 4),
        (63, 2, 2.0, 4),
        (75, 25, 2.0, 4),
        (89, 31, 6.0, 8),
        (101, 33, 6.0, 8),
        (118, 35, 12.0, 16),
        (53, 37, 2.0, 4),
        (69, 39, 2.0, 4),
        (82, 41, 6.0, 8),
        (96, 43, 6.0, 8),
        (112, 45, 12.0, 16),
        (60, 46, 2.0, 4),
        (74, 47, 2.0, 4),
        (87, 48, 6.0, 8),
        (103, 12, 6.0, 8),
        (150, 48, 12.0, 16),
    ]
    assert len(table) == 50
    jobs: list[MockJob] = []
    for i, (load, deadline_h, duration_h, gpus) in enumerate(table, start=1):
        jobs.append(
            MockJob(
                id=f"metric-job-{i:02d}",
                load=load,
                deadline_hours=deadline_h,
                duration_hours=duration_h,
                gpu_count=gpus,
            )
        )
    return jobs


def _mock_to_engine_jobs(mock_jobs: list[MockJob]) -> list[Job]:
    """Map mock jobs into the placement-engine Job schema."""
    eligible = list(DEMO_SITE_IDS)
    ready = {sid: NOW for sid in eligible}
    out: list[Job] = []
    for m in mock_jobs:
        if m.gpu_count >= 16:
            template = JobTemplate.training_large
        elif m.gpu_count >= 8:
            template = JobTemplate.training_small
        else:
            template = JobTemplate.inference_batch
        out.append(
            Job(
                id=m.id,
                session_id=SESSION_ID,
                owner_id=DEMO_OWNER_ID,
                template=template,
                earliest_start=NOW,
                deadline=NOW + timedelta(hours=m.deadline_hours),
                gpu_count=m.gpu_count,
                duration_hours=m.duration_hours,
                eligible_site_ids=list(eligible),
                data_ready_at_by_site=dict(ready),
                critical=template == JobTemplate.training_large,
                miss_penalty_usd=1000.0 if template == JobTemplate.training_large else 200.0,
                status=JobStatus.queued,
            )
        )
    return out


def check_feasibility(
    jobs: list[MockJob],
    sites_config: list[dict[str, Any]],
    emergency_cap: float,
) -> dict[str, Any]:
    """Global capacity sanity check for an artificially tight emergency cap.

    Infeasible when total_running_load exceeds the effective global capacity
    (sum of site GPU capacities, hard-capped by ``emergency_cap``).
    """
    total_running_load = float(sum(j.load for j in jobs))
    raw_capacity = float(sum(int(s["gpu_capacity"]) for s in sites_config))
    # Demo edge case: the artificial emergency_cap IS the global capacity ceiling.
    total_global_capacity = float(emergency_cap)
    infeasible = total_running_load > total_global_capacity
    return {
        "status": "infeasible" if infeasible else "feasible",
        "total_running_load": total_running_load,
        "raw_site_gpu_capacity": raw_capacity,
        "emergency_cap": float(emergency_cap),
        "total_global_capacity": total_global_capacity,
        "slack": total_global_capacity - total_running_load,
        "job_count": len(jobs),
        "message": (
            "INFEASIBLE: aggregate job load exceeds emergency-capped global capacity"
            if infeasible
            else "Feasible under emergency-capped global capacity"
        ),
    }


def naive_cost_baseline_solver(
    jobs: list[MockJob],
    sites_config: list[dict[str, Any]],
) -> dict[str, Any]:
    """Greedy: assign each job to the cheapest cost_per_unit site (ignore water equilibrium)."""
    sites_by_cost = sorted(sites_config, key=lambda s: (s["cost_per_unit"], s["site_id"]))
    # Track remaining GPU headroom so we don't dump everything on one site blindly —
    # still prefer cheapest; spill to next cheapest when full. No water balancing.
    remaining = {s["site_id"]: int(s["gpu_capacity"]) for s in sites_config}
    site_lookup = {s["site_id"]: s for s in sites_config}

    assignments: list[dict[str, Any]] = []
    unassigned: list[str] = []
    per_site_load: dict[str, float] = {s["site_id"]: 0.0 for s in sites_config}
    per_site_cost: dict[str, float] = {s["site_id"]: 0.0 for s in sites_config}
    per_site_withdrawal: dict[str, float] = {
        s["site_id"]: float(s["fixed_water_liters_per_hour"]) for s in sites_config
    }
    on_time = 0

    for job in jobs:
        placed = False
        for site in sites_by_cost:
            sid = site["site_id"]
            if remaining[sid] < job.gpu_count:
                continue
            remaining[sid] -= job.gpu_count
            job_cost = site["cost_per_unit"] * job.gpu_count * job.duration_hours
            job_water = (
                site["incremental_water_liters_per_gpu_hour"]
                * job.gpu_count
                * job.duration_hours
            )
            per_site_load[sid] += job.load
            per_site_cost[sid] += job_cost
            per_site_withdrawal[sid] += (
                site["incremental_water_liters_per_gpu_hour"] * job.gpu_count
            )
            # Naive: start ASAP; on-time if duration fits under deadline.
            met = job.duration_hours <= job.deadline_hours
            if met:
                on_time += 1
            assignments.append(
                {
                    "job_id": job.id,
                    "site_id": sid,
                    "cost_usd": round(job_cost, 4),
                    "incremental_water_liters": round(job_water, 4),
                    "on_time": met,
                }
            )
            placed = True
            break
        if not placed:
            unassigned.append(job.id)

    peak_site_withdrawal = max(per_site_withdrawal.values()) if per_site_withdrawal else 0.0
    combined_withdrawal = sum(per_site_withdrawal.values())
    total_cost = sum(per_site_cost.values())
    # Unassigned jobs: count miss penalty (same order as engine critical miss).
    total_cost += 1000.0 * len(unassigned)

    return {
        "solver": "naive_cost_baseline",
        "total_cost": round(total_cost, 4),
        "on_time_jobs": on_time,
        "unassigned_jobs": len(unassigned),
        "peak_site_withdrawal": round(peak_site_withdrawal, 4),
        "combined_withdrawal": round(combined_withdrawal, 4),
        "per_site_withdrawal_lph": {k: round(v, 4) for k, v in per_site_withdrawal.items()},
        "assignments": assignments,
        "cheapest_site_first": [s["site_id"] for s in sites_by_cost],
        "notes": (
            "Greedy cheapest $/GPU-h only; no water equilibrium. "
            "Withdrawal ≈ fixed + incremental×GPUs at assigned sites (instantaneous)."
        ),
    }


def aqualibri_balancer(
    jobs: list[MockJob],
    sites_config: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the real balanced placement engine on the same mock jobs.

    ``sites_config`` is accepted for API symmetry with the naive solver; the
    engine uses full 48h ``build_demo_site_profiles()`` telemetry.
    """
    _ = sites_config  # API parity with naive_cost_baseline_solver
    sites = build_demo_site_profiles(hours=48, anchor=NOW)
    engine_jobs = _mock_to_engine_jobs(jobs)
    jobs_by_id = {j.id: j for j in engine_jobs}

    gen = generate_placement_candidates(engine_jobs, sites, NOW)
    opt = optimize_placements(
        gen.candidates_by_job,
        gen.running_reservations,
        sites,
        jobs_by_id,
        objective=OptimizeObjective.balanced,
        job_ids=[j.id for j in engine_jobs],
        allow_unfulfilled=set(jobs_by_id),
    )

    evaluation = evaluate_schedule(
        engine_jobs,
        sites,
        NOW,
        selected=opt.selected,
        running_reservations=gen.running_reservations,
        unfulfilled_job_ids=set(opt.unfulfilled_job_ids),
        unfulfilled_reasons={jid: "left_unfulfilled_by_optimizer" for jid in opt.unfulfilled_job_ids},
    )

    peak_site_withdrawal = max((s.peak_withdrawal_lph for s in evaluation.per_site), default=0.0)
    # Instantaneous combined withdrawal ≈ sum of per-site peaks is misleading;
    # report sum of site peak rates for Excel parity with the naive "withdrawal" column,
    # plus total liters from the evaluator.
    combined_peak_rates = sum(s.peak_withdrawal_lph for s in evaluation.per_site)

    return {
        "solver": "aqualibri_balancer",
        "optimize_status": opt.status.value,
        "total_cost": round(evaluation.total_cost_usd, 4),
        "on_time_jobs": evaluation.jobs_on_time,
        "jobs_late": evaluation.jobs_late,
        "jobs_unfulfilled": evaluation.jobs_unfulfilled,
        "peak_site_withdrawal": round(peak_site_withdrawal, 4),
        "combined_withdrawal": round(combined_peak_rates, 4),
        "total_water_liters": round(evaluation.total_water_liters, 4),
        "energy_cost_usd": round(evaluation.energy_cost_usd, 4),
        "miss_penalties_usd": round(evaluation.miss_penalties_usd, 4),
        "per_site": [
            {
                "site_id": s.site_id,
                "peak_withdrawal_lph": round(s.peak_withdrawal_lph, 4),
                "water_liters": round(s.water_liters, 4),
                "energy_cost_usd": round(s.energy_cost_usd, 4),
                "within_cap": s.within_cap,
            }
            for s in evaluation.per_site
        ],
        "placed_jobs": len(opt.selected),
        "notes": (
            "Balanced hard-cap → equilibrium (n+1)^2 → cost → earliness "
            "via optimize_placements(OptimizeObjective.balanced)."
        ),
    }


def _metrics_slice(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_cost": result["total_cost"],
        "on_time_jobs": result["on_time_jobs"],
        "peak_site_withdrawal": result["peak_site_withdrawal"],
        "combined_withdrawal": result["combined_withdrawal"],
    }


def main() -> None:
    jobs = generate_mock_jobs()
    assert len(jobs) == 50

    print("=" * 72)
    print("AquaLibri metrics demo - 50 hardcoded jobs x 5 WUE sites")
    print(f"Clock anchor: {DEMO_CLOCK_ANCHOR_ISO}")
    print("=" * 72)

    # 1) Artificial infeasible emergency cap
    feasibility = check_feasibility(jobs, SITES_CONFIG, emergency_cap=1000)
    print("\n--- check_feasibility (emergency_cap=1000) ---")
    print(json.dumps(feasibility, indent=2))

    # 2) Naive cheapest-cost baseline
    naive = naive_cost_baseline_solver(jobs, SITES_CONFIG)
    print("\n--- naive_cost_baseline_solver ---")
    print(json.dumps({k: v for k, v in naive.items() if k != "assignments"}, indent=2))

    # 3) Real AquaLibri balancer
    balanced = aqualibri_balancer(jobs, SITES_CONFIG)
    print("\n--- aqualibri_balancer ---")
    print(json.dumps(balanced, indent=2))

    # 4) Excel-ready comparison payload
    payload = {
        "demo": "aqualibri_baseline_vs_balancer",
        "job_count": 50,
        "sites": [s["site_id"] for s in SITES_CONFIG],
        "feasibility_edge_case": feasibility,
        "naive_cost_baseline": _metrics_slice(naive),
        "aqualibri_balancer": _metrics_slice(balanced),
        "delta": {
            "total_cost_usd": round(naive["total_cost"] - balanced["total_cost"], 4),
            "on_time_jobs": balanced["on_time_jobs"] - naive["on_time_jobs"],
            "peak_site_withdrawal_lph": round(
                naive["peak_site_withdrawal"] - balanced["peak_site_withdrawal"], 4
            ),
            "combined_withdrawal_lph": round(
                naive["combined_withdrawal"] - balanced["combined_withdrawal"], 4
            ),
        },
        "excel_columns": [
            "solver",
            "total_cost",
            "on_time_jobs",
            "peak_site_withdrawal",
            "combined_withdrawal",
        ],
        "excel_rows": [
            {"solver": "naive_cost_baseline", **_metrics_slice(naive)},
            {"solver": "aqualibri_balancer", **_metrics_slice(balanced)},
        ],
    }

    print("\n" + "=" * 72)
    print("EXCEL COPY PAYLOAD")
    print("=" * 72)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
