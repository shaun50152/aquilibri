/**
 * Visual demo sample data — used only when ?demo=1 or demo toggle is on.
 * Shaped like live API responses; does not call the backend.
 */
(function () {
  const anchor = "2026-03-15T18:00:00+00:00";
  const names = [
    "Alpha Mesa",
    "Beta River",
    "Cedar Basin",
    "Delta Flats",
    "Echo Ridge",
    "Foxtrot Vale",
    "Gulf Strand",
    "High Mesa",
    "Inlet Point",
    "Juniper Reach",
  ];
  const sites = names.map((name, i) => {
    const id = `site-demo-${String(i + 1).padStart(2, "0")}`;
    return {
      site_id: id,
      name,
      hourly_profiles: [
        {
          hour_start: anchor,
          gpu_capacity: 24 + (i % 5) * 4,
          fixed_water_liters_per_hour: 80 + i * 5,
          incremental_water_liters_per_gpu_hour: 0.5 + i * 0.03,
          energy_kwh_per_gpu_hour: 0.42,
          electricity_usd_per_kwh: 0.09,
          water_cap_liters_per_hour: i % 3 === 0 ? 400 : null,
        },
      ],
    };
  });

  const previews = {
    feasible: {
      preview_id: "preview-demo-feasible",
      input_revision: 5,
      status: "feasible",
      proposed_job_changes: [
        {
          job_id: "job-hackuta-0003",
          old_placement: { job_id: "job-hackuta-0003", assigned_site_id: null, scheduled_start: null },
          new_placement: {
            job_id: "job-hackuta-0003",
            assigned_site_id: "site-demo-03",
            scheduled_start: "2026-03-16T02:00:00+00:00",
          },
        },
      ],
      metrics_before: {
        total_water_liters: 4200,
        total_energy_kwh: 120,
        total_cost_usd: 890,
        jobs_on_time: 4,
        jobs_late: 0,
        jobs_unfulfilled: 2,
      },
      metrics_after: {
        total_water_liters: 3950,
        total_energy_kwh: 118,
        total_cost_usd: 860,
        jobs_on_time: 6,
        jobs_late: 0,
        jobs_unfulfilled: 0,
      },
      per_site_water: sites.slice(0, 4).map((s, idx) => ({
        site_id: s.site_id,
        water_liters: 900 + idx * 80,
        water_cap_violations: idx === 2 ? 1 : 0,
      })),
      cost_breakdown: { energy_usd: 720, water_usd: 90, miss_penalties_usd: 0, total_usd: 860 },
      deadline_outcomes: [
        { job_id: "job-hackuta-0003", met_deadline: true, projected_completion: "2026-03-16T14:00:00+00:00" },
      ],
      explanation:
        "Sample: shift queued workloads toward lower incremental water sites while holding the cost budget.",
      infeasibility_reason: null,
      simulated: true,
    },
    stale: {
      preview_id: "preview-demo-stale",
      input_revision: 2,
      status: "feasible",
      proposed_job_changes: [],
      metrics_before: {
        total_water_liters: 4000,
        total_energy_kwh: 115,
        total_cost_usd: 900,
        jobs_on_time: 3,
        jobs_late: 1,
        jobs_unfulfilled: 0,
      },
      metrics_after: {
        total_water_liters: 3800,
        total_energy_kwh: 112,
        total_cost_usd: 880,
        jobs_on_time: 4,
        jobs_late: 0,
        jobs_unfulfilled: 0,
      },
      per_site_water: [],
      cost_breakdown: { energy_usd: 700, water_usd: 80, miss_penalties_usd: 0, total_usd: 880 },
      deadline_outcomes: [],
      explanation: "Sample stale preview — input_revision is behind the live queue.",
      infeasibility_reason: null,
      simulated: true,
    },
    infeasible: {
      preview_id: "preview-demo-infeasible",
      input_revision: 5,
      status: "infeasible",
      proposed_job_changes: [],
      metrics_before: {
        total_water_liters: 4100,
        total_energy_kwh: 118,
        total_cost_usd: 920,
        jobs_on_time: 4,
        jobs_late: 0,
        jobs_unfulfilled: 0,
      },
      metrics_after: {
        total_water_liters: 4100,
        total_energy_kwh: 118,
        total_cost_usd: 920,
        jobs_on_time: 4,
        jobs_late: 0,
        jobs_unfulfilled: 0,
      },
      per_site_water: [{ site_id: "site-demo-01", water_liters: 1200, water_cap_violations: 3 }],
      cost_breakdown: { energy_usd: 700, water_usd: 80, miss_penalties_usd: 0, total_usd: 920 },
      deadline_outcomes: [],
      explanation: "Sample: emergency cap cannot be met with current running jobs.",
      infeasibility_reason: "No placement satisfies site-demo-01 cap 200 L/h for the emergency window.",
      simulated: true,
    },
  };

  window.AQ_DEMO = {
    session_id: "visual-demo-session",
    current_time: anchor,
    revision: 8,
    sites,
    sitesResponse: { sites },
    jobs: {
      session_id: "visual-demo-session",
      revision: 8,
      jobs: [],
    },
    previews,
  };
})();