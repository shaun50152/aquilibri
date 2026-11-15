/**
 * VISUAL DEMO sample data. Used only when ?demo=1 or the Visual demo toggle is on.
 * Every object is shaped like a live API response (/api/demo, /api/sites, /api/jobs,
 * /api/reallocation/preview) so the pages render it with the same code paths.
 * Live mode never reads this file.
 */
(function () {
  const H = 3600000;
  const ANCHOR = Date.UTC(2026, 2, 15, 18);
  const iso = (offsetHours) => new Date(ANCHOR + offsetHours * H).toISOString().replace(".000Z", "+00:00");

  // Enterprise regions — mirrors app/demo_sites.py / Tiger seed.
  // [site_id, name, fixed L/h, incremental L/GPU-h, normal cap L/h, gpu, price]
  const SITE_ROWS = [
    ["us-east-1", "US-East-1 (Ashburn, VA)", 105, 0.75, 520, 48, 0.085],
    ["us-south-central", "US-South-Central (Dallas, TX)", 95, 0.88, 480, 32, 0.055],
    ["us-west-1", "US-West-1 (Santa Clara, CA)", 115, 0.92, 280, 36, 0.145],
    ["eu-west-1", "EU-West-1 (Dublin, Ireland)", 88, 0.7, 400, 40, 0.155],
    ["ap-northeast", "AP-Northeast (Tokyo, Japan)", 92, 0.62, 420, 40, 0.175],
  ];
  const SITE_IDS = SITE_ROWS.map((r) => r[0]);

  const sites = SITE_ROWS.map(([site_id, name, fixed, inc, cap, gpu, price]) => ({
    site_id,
    name,
    hourly_profiles: Array.from({ length: 24 }, (_, h) => ({
      hour_start: iso(h),
      gpu_capacity: gpu,
      fixed_water_liters_per_hour: fixed,
      incremental_water_liters_per_gpu_hour: inc,
      energy_kwh_per_gpu_hour: 0.42,
      electricity_usd_per_kwh: price,
      water_cap_liters_per_hour: cap,
    })),
  }));

  const TPL = {
    inference_batch: { gpu: 4, dur: 2, penalty: 50 },
    training_small: { gpu: 8, dur: 6, penalty: 200 },
    training_large: { gpu: 16, dur: 12, penalty: 1000 },
  };

  function job(n, template, opts) {
    const t = TPL[template];
    const id = `job-hackuta-${String(n).padStart(4, "0")}`;
    const ready = {};
    SITE_IDS.forEach((s) => (ready[s] = iso(opts.es ?? -2)));
    return {
      id,
      session_id: "visual-demo-session",
      owner_id: opts.owner || "demo-user-001",
      template,
      earliest_start: iso(opts.es ?? -2),
      deadline: iso(opts.dl),
      gpu_count: t.gpu,
      duration_hours: t.dur,
      eligible_site_ids: SITE_IDS.slice(),
      data_ready_at_by_site: ready,
      critical: template === "training_large",
      miss_penalty_usd: t.penalty,
      status: opts.status,
      assigned_site_id: opts.site ?? null,
      scheduled_start: opts.st == null ? null : iso(opts.st),
      completed_at: opts.done == null ? null : iso(opts.done),
      result_url: opts.result || null,
    };
  }

  const SAMPLE_CSV =
    "data:text/csv;charset=utf-8," +
    encodeURIComponent("job_id,status,note\njob-hackuta-0006,completed,VISUAL DEMO sample output - not a real GPU run\n");

  const jobs = [
    job(14, "training_small", { status: "scheduled", site: "us-west-1", st: 2, es: 0, dl: 12 }),
    job(11, "inference_batch", { status: "scheduled", site: "us-west-1", st: 2, dl: 9 }),
    job(9, "training_small", { status: "scheduled", site: "us-west-1", st: 4, dl: 14 }),
    job(12, "inference_batch", { status: "running", site: "eu-west-1", st: 0, dl: 5 }),
    job(16, "training_small", { status: "scheduled", site: "us-east-1", st: 1, dl: 13 }),
    job(17, "training_large", { status: "scheduled", site: "ap-northeast", st: 4, dl: 24 }),
    job(19, "inference_batch", { status: "queued", dl: 20, es: 0 }),
    job(6, "inference_batch", { status: "completed", site: "us-south-central", st: -22, done: -20, es: -24, dl: -14, result: SAMPLE_CSV }),
    job(3, "training_small", { status: "failed", site: "eu-west-1", st: -8, es: -12, dl: 2 }),
    job(21, "training_large", { status: "running", site: "us-west-1", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(22, "training_large", { status: "running", site: "us-east-1", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(23, "training_large", { status: "running", site: "us-south-central", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(24, "training_large", { status: "running", site: "eu-west-1", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(25, "training_large", { status: "running", site: "ap-northeast", st: -2, dl: 16, owner: "ops-batch-sim" }),
  ];

  const REVISION = 3;
  const place = (job_id, site, st) => ({ job_id, assigned_site_id: site, scheduled_start: iso(st) });
  const moves = [
    { job_id: "job-hackuta-0014", old_placement: place("job-hackuta-0014", "us-west-1", 2), new_placement: place("job-hackuta-0014", "us-south-central", 2) },
    { job_id: "job-hackuta-0011", old_placement: place("job-hackuta-0011", "us-west-1", 2), new_placement: place("job-hackuta-0011", "us-east-1", 2) },
    { job_id: "job-hackuta-0009", old_placement: place("job-hackuta-0009", "us-west-1", 4), new_placement: place("job-hackuta-0009", "us-south-central", 5) },
  ];
  const before = { total_water_liters: 35952, total_energy_kwh: 2482.0, total_cost_usd: 2412.6, jobs_on_time: 14, jobs_late: 0, jobs_unfulfilled: 0 };

  const feasible = {
    preview_id: "preview-demo-feasible",
    input_revision: REVISION,
    status: "feasible",
    proposed_job_changes: moves,
    metrics_before: before,
    metrics_after: { total_water_liters: 34992, total_energy_kwh: 2490.5, total_cost_usd: 2431.0, jobs_on_time: 14, jobs_late: 0, jobs_unfulfilled: 0 },
    per_site_water: SITE_IDS.map((site_id, i) => ({
      site_id,
      water_liters: 3200 + i * 120,
      water_cap_violations: site_id === "us-west-1" ? 0 : 0,
    })),
    cost_breakdown: { energy_usd: 2263.0, water_usd: 168.0, miss_penalties_usd: 0, total_usd: 2431.0 },
    deadline_outcomes: [
      { job_id: "job-hackuta-0014", met_deadline: true, projected_completion: iso(8) },
      { job_id: "job-hackuta-0011", met_deadline: true, projected_completion: iso(4) },
      { job_id: "job-hackuta-0009", met_deadline: true, projected_completion: iso(11) },
    ],
    explanation:
      "Santa Clara water restriction: move JOB-014 to Dallas and JOB-011 to Ashburn. " +
      "JOB-009 follows to Dallas one hour later. Equilibrium scoring keeps fleet load balanced " +
      "while Santa Clara stays under its temporary cap. Cost rises $18.40, inside your $40.00 budget.",
    infeasibility_reason: null,
    simulated: true,
  };

  const stale = Object.assign({}, feasible, { preview_id: "preview-demo-stale", input_revision: REVISION - 1 });

  const infeasible = {
    preview_id: "preview-demo-infeasible",
    input_revision: REVISION,
    status: "infeasible",
    proposed_job_changes: [],
    metrics_before: before,
    metrics_after: before,
    per_site_water: [{ site_id: "us-west-1", water_liters: 5124, water_cap_violations: 6 }],
    cost_breakdown: { energy_usd: 2245.0, water_usd: 167.6, miss_penalties_usd: 0, total_usd: 2412.6 },
    deadline_outcomes: [{ job_id: "job-hackuta-0009", met_deadline: false, projected_completion: iso(15.5) }],
    explanation:
      "I could not find a plan that fits. JOB-009 needs 8 GPUs for six hours; Dallas and Ashburn are full in the restriction window, and starting later misses the deadline.",
    infeasibility_reason:
      "With a $20 budget and Santa Clara capped at 300 L/h, JOB-009 cannot finish before 08:00 on any eligible site.",
    simulated: true,
  };

  window.AQ_DEMO = {
    session_id: "visual-demo-session",
    current_time: iso(0),
    revision: REVISION,
    sitesResponse: { sites },
    jobs: { session_id: "visual-demo-session", revision: REVISION, jobs },
    emergency: {
      site_id: "us-west-1",
      water_cap_liters_per_hour: 300,
      start: iso(2),
      end: iso(8),
      instruction:
        "US-West-1 (Santa Clara) has a water restriction tonight. Keep it under 300 L/h from 8 p.m. to 2 a.m. and do not miss any deadlines.",
    },
    budgets: { feasible: 40, stale: 40, infeasible: 20 },
    previews: { feasible, stale, infeasible },
  };
})();
