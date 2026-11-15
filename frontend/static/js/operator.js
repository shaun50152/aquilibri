const opError = document.getElementById("op-error");
const sitesGrid = document.getElementById("sites-grid");
const jobsBody = document.getElementById("jobs-body");
const previewError = document.getElementById("preview-error");
const previewEmpty = document.getElementById("preview-empty");
const previewBody = document.getElementById("preview-body");
const changesBody = document.getElementById("changes-body");
const changesEmpty = document.getElementById("changes-empty");
const changesTable = document.getElementById("changes-table");

function textCell(text) {
  const td = document.createElement("td");
  td.textContent = text ?? "—";
  return td;
}

function renderSites(sites) {
  sitesGrid.replaceChildren();
  for (const site of sites) {
    const card = document.createElement("article");
    card.className = "card";
    const title = document.createElement("h3");
    title.textContent = site.name;
    card.appendChild(title);
    const meta = document.createElement("p");
    meta.className = "site-meta muted";
    meta.textContent = site.site_id;
    card.appendChild(meta);
    const profile = site.hourly_profiles[0];
    if (profile) {
      const stats = document.createElement("p");
      stats.textContent = `Next hour: ${profile.gpu_capacity} GPU cap · water fixed ${profile.fixed_water_liters_per_hour} L/h`;
      card.appendChild(stats);
    }
    sitesGrid.appendChild(card);
  }
}

function renderJobs(jobs) {
  jobsBody.replaceChildren();
  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.appendChild(textCell(job.id));
    tr.appendChild(textCell(job.status));
    tr.appendChild(textCell(String(job.gpu_count)));
    tr.appendChild(textCell(job.assigned_site_id));
    tr.appendChild(textCell(formatUtc(job.scheduled_start)));
    tr.appendChild(textCell(formatUtc(job.deadline)));
    jobsBody.appendChild(tr);
  }
}

function metricRows(container, metrics) {
  container.replaceChildren();
  if (!metrics) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Unavailable for this preview.";
    container.appendChild(p);
    return;
  }
  const entries = [
    ["Water (L)", metrics.total_water_liters],
    ["Energy (kWh)", metrics.total_energy_kwh],
    ["Cost (USD)", metrics.total_cost_usd],
    ["On time", metrics.jobs_on_time],
    ["Late", metrics.jobs_late],
    ["Unfulfilled", metrics.jobs_unfulfilled],
  ];
  for (const [label, value] of entries) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = typeof value === "number" ? String(value) : "—";
    container.appendChild(dt);
    container.appendChild(dd);
  }
}

function costRows(container, breakdown) {
  container.replaceChildren();
  if (!breakdown) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Unavailable for this preview.";
    container.appendChild(p);
    return;
  }
  const entries = [
    ["Energy USD", breakdown.energy_usd],
    ["Water USD", breakdown.water_usd],
    ["Miss penalties USD", breakdown.miss_penalties_usd],
    ["Total USD", breakdown.total_usd],
  ];
  for (const [label, value] of entries) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = typeof value === "number" ? String(value) : "—";
    container.appendChild(dt);
    container.appendChild(dd);
  }
}

function renderPreview(preview) {
  setError(previewError, null);
  previewEmpty.hidden = true;
  previewBody.hidden = false;

  document.getElementById("preview-status").textContent = preview.status || "—";
  document.getElementById("preview-id").textContent = preview.preview_id || "—";
  document.getElementById("preview-revision").textContent = String(
    preview.input_revision ?? "—"
  );
  document.getElementById("preview-explanation").textContent =
    preview.explanation || "";

  const infeas = document.getElementById("preview-infeasible");
  if (preview.infeasibility_reason) {
    infeas.hidden = false;
    infeas.textContent = preview.infeasibility_reason;
  } else {
    infeas.hidden = true;
    infeas.textContent = "";
  }

  changesBody.replaceChildren();
  const changes = preview.proposed_job_changes || [];
  const hasChanges = changes.length > 0;
  changesTable.hidden = !hasChanges;
  changesEmpty.hidden = hasChanges;
  for (const change of changes) {
    const tr = document.createElement("tr");
    tr.appendChild(textCell(change.job_id));
    tr.appendChild(textCell(change.old_placement?.assigned_site_id));
    tr.appendChild(textCell(formatUtc(change.old_placement?.scheduled_start)));
    tr.appendChild(textCell(change.new_placement?.assigned_site_id));
    tr.appendChild(textCell(formatUtc(change.new_placement?.scheduled_start)));
    changesBody.appendChild(tr);
  }

  metricRows(document.getElementById("metrics-before"), preview.metrics_before);
  metricRows(document.getElementById("metrics-after"), preview.metrics_after);
  costRows(document.getElementById("cost-breakdown"), preview.cost_breakdown);
}

async function loadDemo() {
  const demo = await apiRequest("/api/demo");
  document.getElementById("demo-clock").textContent = formatUtc(demo.current_time);
}

async function refreshAll() {
  setError(opError, null);
  try {
    await loadDemo();
    const sites = await apiRequest("/api/sites");
    renderSites(sites.sites);
    const jobs = await apiRequest("/api/jobs");
    renderJobs(jobs.jobs);
  } catch (err) {
    setError(opError, err.message);
  }
}

document.getElementById("refresh-btn").addEventListener("click", refreshAll);

document.getElementById("reset-btn").addEventListener("click", async () => {
  setError(opError, null);
  try {
    await apiRequest("/api/demo/reset", { method: "POST" });
    await refreshAll();
  } catch (err) {
    setError(opError, err.message);
  }
});

document.getElementById("load-sample-btn").addEventListener("click", async () => {
  const name = document.getElementById("sample-select").value;
  setError(previewError, null);
  try {
    const preview = await apiRequest(`/api/reallocation/samples/${name}`);
    renderPreview(preview);
  } catch (err) {
    setError(previewError, err.message);
  }
});

document.getElementById("run-routine-btn").addEventListener("click", async () => {
  setError(previewError, null);
  try {
    const preview = await apiRequest("/api/reallocation/preview", {
      method: "POST",
      body: { mode: "routine", max_additional_cost_usd: 25.0 },
    });
    renderPreview(preview);
  } catch (err) {
    setError(previewError, err.message);
  }
});

document.getElementById("run-emergency-btn").addEventListener("click", async () => {
  setError(previewError, null);
  try {
    const preview = await apiRequest("/api/reallocation/preview", {
      method: "POST",
      body: {
        mode: "emergency",
        emergency_restriction: {
          site_id: "site-alpha",
          water_cap_liters_per_hour: 115.0,
          start: "2026-03-15T20:00:00+00:00",
          end: "2026-03-16T12:00:00+00:00",
        },
      },
    });
    renderPreview(preview);
  } catch (err) {
    setError(previewError, err.message);
  }
});

refreshAll();
