/**
 * VISUAL DEMO sample data. Used only when ?demo=1 or the Visual demo toggle is on.
 * Every object is shaped like a live API response (/api/demo, /api/sites, /api/jobs,
 * /api/reallocation/preview) so the pages render it with the same code paths.
 * Live mode never reads this file. Numbers follow docs/design/Operator.dc.html.
 */
(function () {
  const H = 3600000;
  const ANCHOR = Date.UTC(2026, 2, 15, 18);
  const iso = (offsetHours) => new Date(ANCHOR + offsetHours * H).toISOString().replace(".000Z", "+00:00");

  // [site_id, name, fixed L/h, incremental L/GPU-h, normal cap L/h]
  const SITE_ROWS = [
    ["site-alpha", "Alpha Mesa DC", 120, 11, 500],
    ["site-gamma", "Gamma Ridge DC", 120, 9, 420],
    ["site-zeta", "Zeta Prairie DC", 130, 9, 480],
    ["site-beta", "Beta River DC", 100, 8, 400],
    ["site-theta", "Theta Harbor DC", 200, 10, 460],
    ["site-iota", "Iota Canyon DC", 60, 9, 300],
    ["site-epsilon", "Epsilon Coast DC", 100, 9, 380],
    ["site-delta", "Delta Basin DC", 150, 7.5, 450],
    ["site-eta", "Eta Summit DC", 130, 9, 330],
    ["site-kappa", "Kappa Vale DC", 140, 9, 410],
  ];
  const SITE_IDS = SITE_ROWS.map((r) => r[0]);

  const sites = SITE_ROWS.map(([site_id, name, fixed, inc, cap]) => ({
    site_id,
    name,
    hourly_profiles: Array.from({ length: 12 }, (_, h) => ({
      hour_start: iso(h),
      gpu_capacity: 32,
      fixed_water_liters_per_hour: fixed,
      incremental_water_liters_per_gpu_hour: inc,
      energy_kwh_per_gpu_hour: 0.45,
      electricity_usd_per_kwh: 0.09,
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
    // The user's jobs (shown in the portal).
    job(14, "training_small", { status: "scheduled", site: "site-alpha", st: 2, es: 0, dl: 12 }),
    job(11, "inference_batch", { status: "scheduled", site: "site-alpha", st: 2, dl: 9 }),
    job(9, "training_small", { status: "scheduled", site: "site-alpha", st: 4, dl: 14 }),
    job(12, "inference_batch", { status: "running", site: "site-theta", st: 0, dl: 5 }),
    job(16, "training_small", { status: "scheduled", site: "site-zeta", st: 1, dl: 13 }),
    job(17, "training_large", { status: "scheduled", site: "site-delta", st: 4, dl: 24 }),
    job(19, "inference_batch", { status: "queued", dl: 20, es: 0 }),
    job(6, "inference_batch", { status: "completed", site: "site-eta", st: -22, done: -20, es: -24, dl: -14, result: SAMPLE_CSV }),
    job(3, "training_small", { status: "failed", site: "site-zeta", st: -8, es: -12, dl: 2 }),
    // Background fleet load (other owners, operator view only).
    job(21, "training_large", { status: "running", site: "site-alpha", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(22, "training_large", { status: "running", site: "site-gamma", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(23, "training_large", { status: "running", site: "site-zeta", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(24, "training_large", { status: "running", site: "site-beta", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(25, "training_large", { status: "running", site: "site-theta", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(26, "training_small", { status: "running", site: "site-iota", st: -2, dl: 10, owner: "ops-batch-sim" }),
    job(27, "training_small", { status: "running", site: "site-epsilon", st: -2, dl: 10, owner: "ops-batch-sim" }),
    job(28, "training_large", { status: "running", site: "site-delta", st: -2, dl: 16, owner: "ops-batch-sim" }),
    job(29, "training_small", { status: "running", site: "site-eta", st: -2, dl: 10, owner: "ops-batch-sim" }),
    job(30, "training_large", { status: "running", site: "site-kappa", st: -2, dl: 16, owner: "ops-batch-sim" }),
  ];

  const REVISION = 3;
  const place = (job_id, site, st) => ({ job_id, assigned_site_id: site, scheduled_start: iso(st) });
  const moves = [
    { job_id: "job-hackuta-0014", old_placement: place("job-hackuta-0014", "site-alpha", 2), new_placement: place("job-hackuta-0014", "site-beta", 2) },
    { job_id: "job-hackuta-0011", old_placement: place("job-hackuta-0011", "site-alpha", 2), new_placement: place("job-hackuta-0011", "site-gamma", 2) },
    { job_id: "job-hackuta-0009", old_placement: place("job-hackuta-0009", "site-alpha", 4), new_placement: place("job-hackuta-0009", "site-beta", 5) },
  ];
  const before = { total_water_liters: 35952, total_energy_kwh: 2482.0, total_cost_usd: 2412.6, jobs_on_time: 14, jobs_late: 0, jobs_unfulfilled: 0 };

  const feasible = {
    preview_id: "preview-demo-feasible",
    input_revision: REVISION,
    status: "feasible",
    proposed_job_changes: moves,
    metrics_before: before,
    metrics_after: { total_water_liters: 34992, total_energy_kwh: 2490.5, total_cost_usd: 2431.0, jobs_on_time: 14, jobs_late: 0, jobs_unfulfilled: 0 },
    per_site_water: [
      { site_id: "site-alpha", water_liters: 3552, water_cap_violations: 0 },
      { site_id: "site-beta", water_liters: 3796, water_cap_violations: 0 },
      { site_id: "site-gamma", water_liters: 3468, water_cap_violations: 0 },
    ],
    cost_breakdown: { energy_usd: 2263.0, water_usd: 168.0, miss_penalties_usd: 0, total_usd: 2431.0 },
    deadline_outcomes: [
      { job_id: "job-hackuta-0014", met_deadline: true, projected_completion: iso(8) },
      { job_id: "job-hackuta-0011", met_deadline: true, projected_completion: iso(4) },
      { job_id: "job-hackuta-0009", met_deadline: true, projected_completion: iso(11) },
    ],
    explanation:
      "Move JOB-014 to Beta River. It finishes at 02:00, four hours before its 06:00 deadline, and keeps Alpha within its temporary cap. " +
      "JOB-011 goes to Gamma Ridge and JOB-009 follows JOB-014 to Beta one hour later. Together they take Alpha from 472 to 296 L/h at the peak. " +
      "Cost rises $18.40, inside your $40.00 budget. No deadline slips.",
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
    per_site_water: [{ site_id: "site-alpha", water_liters: 5124, water_cap_violations: 6 }],
    cost_breakdown: { energy_usd: 2245.0, water_usd: 167.6, miss_penalties_usd: 0, total_usd: 2412.6 },
    deadline_outcomes: [{ job_id: "job-hackuta-0009", met_deadline: false, projected_completion: iso(15.5) }],
    explanation:
      "I could not find a plan that fits. The tightest constraint is JOB-009: it needs 8 GPUs for six hours and only Beta River has room, but starting after 02:00 pushes it past its deadline.",
    infeasibility_reason:
      "With a $20 budget and Alpha capped at 300 L/h, JOB-009 cannot finish before 08:00 on any eligible site. Closest option misses it by 1 h 30 min.",
    simulated: true,
  };

  window.AQ_DEMO = {
    session_id: "visual-demo-session",
    current_time: iso(0),
    revision: REVISION,
    sitesResponse: { sites },
    jobs: { session_id: "visual-demo-session", revision: REVISION, jobs },
    emergency: {
      site_id: "site-alpha",
      water_cap_liters_per_hour: 300,
      start: iso(2),
      end: iso(8),
      instruction:
        "Alpha Mesa has a water restriction tonight. Keep it under 300 L/h from 8 p.m. to 2 a.m. and do not miss any deadlines.",
    },
    budgets: { feasible: 40, stale: 40, infeasible: 20 },
    previews: { feasible, stale, infeasible },
  };
})();
