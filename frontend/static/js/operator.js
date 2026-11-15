const opError = document.getElementById("op-error");
const sitesGrid = document.getElementById("sites-grid");
const jobsBody = document.getElementById("jobs-body");

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

refreshAll();