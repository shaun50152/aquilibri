let sitesCache = [];
let jobsCache = [];
let demoClock = null;
let currentRevision = 1;
let activePreview = null;
let appliedOverrides = {};
let proposedMap = {};
let routineMode = true;
let advanceDisabled = false;

const previewPanel = document.getElementById("preview-panel");
const applyBtn = document.getElementById("apply-btn");
const discardBtn = document.getElementById("discard-btn");
const advanceBtn = document.getElementById("advance-btn");
const jobsBody = document.getElementById("jobs-body");
const opError = document.getElementById("op-error");
const ringEl = document.getElementById("fleet-ring");
const chartEl = document.getElementById("water-chart");
const drawer = document.getElementById("drawer");
const statusBand = document.getElementById("status-band");
const demoPreviewChoices = document.getElementById("demo-preview-choices");

function hourProfileFor(site, clockIso) {
  if (!site?.hourly_profiles?.length) return null;
  const t = new Date(clockIso).getTime();
  let best = site.hourly_profiles[0];
  for (const p of site.hourly_profiles) {
    if (new Date(p.hour_start).getTime() <= t) best = p;
  }
  return best;
}

function assignedGpusAtSite(siteId, jobs, clockIso) {
  const t = new Date(clockIso).getTime();
  let gpus = 0;
  for (const job of jobs) {
    if (job.assigned_site_id !== siteId || !job.scheduled_start) continue;
    const start = new Date(job.scheduled_start).getTime();
    const end = start + job.duration_hours * 3600000;
    if (t >= start && t < end) gpus += job.gpu_count;
  }
  return gpus;
}

function modeledWater(site, assignedGpus) {
  const profile = hourProfileFor(site, demoClock);
  if (!profile) return 0;
  return profile.fixed_water_liters_per_hour + profile.incremental_water_liters_per_gpu_hour * assignedGpus;
}

function computeSiteMetrics(sites, jobs) {
  return sites.map((site, i) => {
    const profile = hourProfileFor(site, demoClock);
    const cap = profile?.gpu_capacity || 1;
    const gpus = assignedGpusAtSite(site.site_id, jobs, demoClock);
    const loadRatio = gpus / cap;
    const water = modeledWater(site, gpus);
    const capLiters = profile?.water_cap_liters_per_hour;
    const overCap = capLiters != null && water > capLiters;
    return {
      ...site,
      loadRatio,
      overCap,
      assignedGpus: gpus,
      water,
      angleDeg: (360 / sites.length) * i,
    };
  });
}

function totalFleetWater(metrics) {
  return metrics.reduce((sum, s) => sum + s.water, 0);
}

function renderRing(sites, jobs) {
  const metrics = computeSiteMetrics(sites, jobs);
  const proposedArcs = [];
  const appliedArcs = [];
  if (activePreview?.proposed_job_changes) {
    for (const ch of activePreview.proposed_job_changes) {
      const from = ch.old_placement?.assigned_site_id;
      const to = ch.new_placement?.assigned_site_id;
      if (from && to && from !== to) proposedArcs.push({ from, to });
      proposedMap[ch.job_id] = ch.new_placement?.assigned_site_id;
    }
  }
  drawRing(ringEl, metrics, {
    fleetWater: `${Math.round(totalFleetWater(metrics))} L/h modeled`,
    proposed: proposedArcs,
    applied: appliedArcs,
    onNodeClick: (site) => openDrawer(site, jobs),
  });
}

function openDrawer(site, jobs) {
  drawer.hidden = false;
  document.getElementById("drawer-title").textContent = site.name || site.site_id;
  const placed = jobs.filter((j) => j.assigned_site_id === site.site_id);
  const profile = hourProfileFor(site, demoClock);
  const cap = profile?.water_cap_liters_per_hour;
  document.getElementById("drawer-body").textContent =
    `GPU load from assignments: ${Math.round((site.loadRatio || 0) * 100)}% (${site.assignedGpus}/${profile?.gpu_capacity ?? "—"} GPUs). ` +
    `Modeled water: ${Math.round(site.water)} L/h.` +
    (cap != null ? ` Cap: ${cap} L/h${site.overCap ? " · over cap" : ""}.` : " No hourly cap.") +
    ` Jobs placed: ${placed.map((j) => shortJobId(j.id)).join(", ") || "none"}.`;
}

document.getElementById("drawer-close").addEventListener("click", () => {
  drawer.hidden = true;
});

function renderWaterChart(sites) {
  if (!sites.length) return;
  const site = sites[0];
  const data = site.hourly_profiles.map((p) => ({
    water: p.fixed_water_liters_per_hour + p.incremental_water_liters_per_gpu_hour * 4,
    hour: p.hour_start,
  }));
  buildHourlyWaterChart(
    chartEl,
    data,
    site.hourly_profiles[0]?.water_cap_liters_per_hour,
  );
}

function renderQueue(jobs) {
  jobsBody.replaceChildren();
  for (const job of jobs) {
    const tr = el("tr");
    const idCell = el("td");
    const idSpan = el("span", { className: "num", text: shortJobId(job.id) });
    idSpan.title = job.id;
    idCell.appendChild(idSpan);
    tr.appendChild(idCell);
    tr.appendChild(el("td", { text: job.template }));
    const st = el("td");
    st.appendChild(statusPill(job.status));
    tr.appendChild(st);
    tr.appendChild(el("td", { text: formatAssignment(job.assigned_site_id) }));
    tr.appendChild(el("td", { text: formatAssignment(proposedMap[job.id]) }));
    tr.appendChild(el("td", { text: formatAssignment(appliedOverrides[job.id]) }));
    tr.appendChild(el("td", { text: formatUtc(job.deadline) }));
    jobsBody.appendChild(tr);
  }
}

function renderPreviewUnavailable(detail) {
  previewPanel.replaceChildren(unavailablePanel(detail));
  applyBtn.disabled = true;
  discardBtn.disabled = true;
}

function renderPreview(preview) {
  previewPanel.replaceChildren();
  if (isVisualDemoMode()) previewPanel.appendChild(visualDemoTag());

  if (preview.status === "infeasible") {
    previewPanel.appendChild(chip("infeasible", "INFEASIBLE"));
    previewPanel.appendChild(el("p", { className: "body-serif", text: preview.infeasibility_reason || preview.explanation }));
    applyBtn.disabled = true;
    discardBtn.disabled = false;
    return;
  }

  const stale = preview.input_revision !== currentRevision;
  if (stale) {
    previewPanel.appendChild(el("div", { className: "stale-band", text: "Stale preview · revision mismatch · Apply locked" }));
    applyBtn.disabled = true;
  } else {
    applyBtn.disabled = false;
  }
  discardBtn.disabled = false;

  previewPanel.appendChild(el("p", { className: "body-serif", text: preview.explanation }));
  for (const ch of preview.proposed_job_changes || []) {
    const row = el("div", { className: "toolbar" });
    row.appendChild(chip("proposed", "PROPOSED"));
    row.appendChild(
      el("span", {
        className: "num",
        text: `${shortJobId(ch.job_id)} → ${formatAssignment(ch.new_placement?.assigned_site_id)}`,
      }),
    );
    previewPanel.appendChild(row);
  }

  const table = el("table", { className: "data-table" });
  const thead = el("thead");
  thead.appendChild(el("tr", null, [
    el("th", { scope: "col", text: "Metric" }),
    el("th", { scope: "col", text: "Before" }),
    el("th", { scope: "col", text: "After" }),
  ]));
  table.appendChild(thead);
  const tbody = el("tbody");
  const mb = preview.metrics_before;
  const ma = preview.metrics_after;
  const rows = [
    ["Water (L)", mb?.total_water_liters, ma?.total_water_liters],
    ["Energy (kWh)", mb?.total_energy_kwh, ma?.total_energy_kwh],
    ["Cost (USD)", mb?.total_cost_usd, ma?.total_cost_usd],
  ];
  for (const [label, b, a] of rows) {
    const tr = el("tr");
    tr.appendChild(el("td", { text: label }));
    tr.appendChild(el("td", { className: "num", text: String(b ?? "—") }));
    tr.appendChild(el("td", { className: "num", text: String(a ?? "—") }));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  previewPanel.appendChild(table);

  if (preview.per_site_water?.length) {
    const ul = el("ul");
    for (const sw of preview.per_site_water) {
      ul.appendChild(
        el("li", {
          text: `${sw.site_id}: ${sw.water_liters} L · cap violations ${sw.water_cap_violations}`,
        }),
      );
    }
    previewPanel.appendChild(ul);
  }
}

async function loadAll() {
  setError(opError, null);
  try {
    const visual = isVisualDemoMode();
    const demo = visual && window.AQ_DEMO ? window.AQ_DEMO : await getDemo();
    demoClock = demo.current_time;
    document.getElementById("demo-clock").textContent = formatUtc(demoClock);

    const sitesResp = visual && window.AQ_DEMO ? window.AQ_DEMO.sitesResponse : await getSites();
    sitesCache = sitesResp.sites;

    const jobsResp = visual && window.AQ_DEMO?.jobs ? window.AQ_DEMO.jobs : await getJobs();
    jobsCache = jobsResp.jobs;
    currentRevision = jobsResp.revision;

    document.getElementById("sim-banner").textContent =
      `SIMULATED · ${sitesCache.length} SITES · NO REAL GPUS`;

    statusBand.textContent = `Revision ${currentRevision} · ${sitesCache.length} sites from API · all execution simulated`;

    populateEmerSites();
    renderRing(sitesCache, jobsCache);
    renderWaterChart(sitesCache);
    renderQueue(jobsCache);

    const slot = document.getElementById("ring-visual-demo-slot");
    slot.replaceChildren();
    if (visual) slot.appendChild(visualDemoTag());

    demoPreviewChoices.hidden = !visual;
    if (visual) setupDemoPreviewButtons();
  } catch (err) {
    setError(opError, err.message);
  }
}

function populateEmerSites() {
  const sel = document.getElementById("emer-site");
  sel.replaceChildren();
  for (const s of sitesCache) {
    sel.appendChild(el("option", { value: s.site_id, text: `${s.name} (${s.site_id})` }));
  }
}

function setupDemoPreviewButtons() {
  demoPreviewChoices.replaceChildren(el("p", { className: "field-label", text: "Load sample preview:" }));
  for (const key of ["feasible", "stale", "infeasible"]) {
    const btn = el("button", { type: "button", className: "btn btn--secondary", text: key });
    btn.addEventListener("click", () => {
      activePreview = window.AQ_DEMO.previews[key];
      proposedMap = {};
      for (const ch of activePreview.proposed_job_changes || []) {
        proposedMap[ch.job_id] = ch.new_placement?.assigned_site_id;
      }
      renderPreview(activePreview);
      renderRing(sitesCache, jobsCache);
      renderQueue(jobsCache);
    });
    demoPreviewChoices.appendChild(btn);
  }
}

document.getElementById("tab-routine").addEventListener("click", () => {
  routineMode = true;
  document.getElementById("panel-emergency").hidden = true;
  document.getElementById("panel-routine").hidden = false;
  document.getElementById("tab-routine").classList.add("is-active");
  document.getElementById("tab-emergency").classList.remove("is-active");
});
document.getElementById("tab-emergency").addEventListener("click", () => {
  routineMode = false;
  document.getElementById("panel-emergency").hidden = false;
  document.getElementById("panel-routine").hidden = true;
  document.getElementById("tab-emergency").classList.add("is-active");
  document.getElementById("tab-routine").classList.remove("is-active");
});

document.getElementById("preview-btn").addEventListener("click", async () => {
  previewPanel.replaceChildren(el("p", { className: "small", text: "Loading preview…" }));
  applyBtn.disabled = true;
  if (isVisualDemoMode()) {
    activePreview = window.AQ_DEMO.previews.feasible;
    renderPreview(activePreview);
    renderRing(sitesCache, jobsCache);
    renderQueue(jobsCache);
    return;
  }
  const body = {
    mode: routineMode ? "routine" : "emergency",
    natural_language_instruction: document.getElementById("instruction").value || null,
  };
  const budget = document.getElementById("budget").value;
  if (budget) body.max_additional_cost_usd = Number(budget);
  if (!routineMode) {
    body.emergency_restriction = {
      site_id: document.getElementById("emer-site").value,
      water_cap_liters_per_hour: Number(document.getElementById("emer-cap").value || 0),
      start: datetimeLocalToUtcIso(document.getElementById("emer-start").value),
      end: datetimeLocalToUtcIso(document.getElementById("emer-end").value),
    };
  }
  const result = await previewReallocation(body);
  if (result.unavailable) {
    renderPreviewUnavailable(result.detail);
    return;
  }
  activePreview = result;
  proposedMap = {};
  for (const ch of result.proposed_job_changes || []) {
    proposedMap[ch.job_id] = ch.new_placement?.assigned_site_id;
  }
  renderPreview(result);
  renderRing(sitesCache, jobsCache);
  renderQueue(jobsCache);
});

document.getElementById("discard-btn").addEventListener("click", () => {
  activePreview = null;
  proposedMap = {};
  previewPanel.replaceChildren(el("p", { className: "muted", text: "Preview discarded." }));
  applyBtn.disabled = true;
  discardBtn.disabled = true;
  renderRing(sitesCache, jobsCache);
  renderQueue(jobsCache);
});

document.getElementById("apply-btn").addEventListener("click", async () => {
  if (!activePreview) return;
  if (activePreview.input_revision !== currentRevision) return;
  if (isVisualDemoMode()) {
    for (const ch of activePreview.proposed_job_changes || []) {
      appliedOverrides[ch.job_id] = ch.new_placement?.assigned_site_id;
    }
    document.querySelectorAll(".chip--proposed").forEach((c) => {
      c.classList.remove("chip--proposed");
      c.classList.add("chip--applied");
      c.textContent = "APPLIED";
    });
    if (!prefersReducedMotion()) {
      const drop = el("div", { className: "droplet", id: "apply-droplet" });
      document.body.appendChild(drop);
      window.setTimeout(() => {
        drop.remove();
        document.body.classList.add("droplet-landed");
      }, 2100);
    } else {
      document.body.classList.add("droplet-landed");
    }
    toast("Sample plan marked APPLIED (visual demo only).");
    renderQueue(jobsCache);
    return;
  }
  const result = await applyReallocation({
    preview_id: activePreview.preview_id,
    input_revision: activePreview.input_revision,
  });
  if (result.unavailable) {
    renderPreviewUnavailable(result.detail);
    return;
  }
  toast("Schedule applied.");
  await loadAll();
});

document.getElementById("advance-btn").addEventListener("click", async () => {
  if (advanceDisabled) return;
  const result = await advanceDemo();
  if (result.unavailable) {
    advanceDisabled = true;
    advanceBtn.disabled = true;
    statusBand.appendChild(unavailablePanel(result.detail));
    return;
  }
  await loadAll();
});

document.getElementById("reset-btn").addEventListener("click", async () => {
  if (isVisualDemoMode()) {
    setError(opError, "Reset calls live API — turn off visual demo first.");
    return;
  }
  await resetDemo();
  activePreview = null;
  proposedMap = {};
  appliedOverrides = {};
  await loadAll();
});

document.getElementById("refresh-btn").addEventListener("click", loadAll);
document.getElementById("visual-demo-toggle").addEventListener("click", () => {
  setVisualDemoMode(!isVisualDemoMode());
});

loadAll();