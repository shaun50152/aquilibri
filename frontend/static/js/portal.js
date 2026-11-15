const DEMO_DEADLINE_OFFSET_HOURS = 30;
const TEMPLATE_HOURS = {
  inference_batch: 2,
  training_small: 6,
  training_large: 12,
};

const form = document.getElementById("job-form");
const submitBtn = document.getElementById("submit-btn");
const submitSpinner = document.getElementById("submit-spinner");
const submitLabel = document.getElementById("submit-label");
const formError = document.getElementById("form-error");
const formSuccess = document.getElementById("form-success");
const demoError = document.getElementById("demo-error");
const listError = document.getElementById("list-error");
const jobsTable = document.getElementById("jobs-table");
const jobsBody = document.getElementById("jobs-body");
const jobsEmpty = document.getElementById("jobs-empty");
const jobsSkeleton = document.getElementById("jobs-skeleton");
const earliestInput = document.getElementById("earliest_start");
const deadlineInput = document.getElementById("deadline");
const estimateBox = document.getElementById("estimate-box");
const topologyMini = document.getElementById("topology-mini");
const jobDetail = document.getElementById("job-detail");

let pollTimer = null;
let jobsCache = [];
let demoClock = null;
let selectedJobId = null;
const prevSiteByJob = new Map();
const activityLog = [];

function selectedTemplate() {
  const checked = form.querySelector('input[name="template"]:checked');
  return checked ? checked.value : "inference_batch";
}

function applyDemoDefaults(currentTimeIso) {
  if (!earliestInput.value) earliestInput.value = toDatetimeLocalValue(currentTimeIso);
  if (!deadlineInput.value) {
    deadlineInput.value = toDatetimeLocalValue(addHoursToIso(currentTimeIso, DEMO_DEADLINE_OFFSET_HOURS));
  }
}

function updateEstimate() {
  const est = TEMPLATE_HOURS[selectedTemplate()] || 2;
  const e = earliestInput.value;
  const d = deadlineInput.value;
  if (!e || !d) {
    estimateBox.textContent = `Estimated runtime: ${est} h (from template). Set times to compare window.`;
    return;
  }
  const windowH = (new Date(datetimeLocalToUtcIso(d)) - new Date(datetimeLocalToUtcIso(e))) / 3600000;
  const ok = windowH >= est;
  estimateBox.textContent = ok
    ? `Window ${windowH.toFixed(1)} h · template needs ${est} h — OK.`
    : `Warning: window ${windowH.toFixed(1)} h is shorter than template duration ${est} h.`;
  estimateBox.classList.toggle("estimate-box--warn", !ok);
}

function updateSimBanner(siteCount) {
  document.getElementById("sim-banner").textContent =
    `SIMULATED · ${siteCount} SITES · NO REAL GPUS`;
}

function logActivity(message) {
  activityLog.unshift({ at: new Date().toISOString(), message });
  if (activityLog.length > 30) activityLog.length = 30;
}

function renderActivityLog() {
  const ul = document.getElementById("activity-log");
  ul.replaceChildren();
  for (const row of activityLog) {
    ul.appendChild(el("li", { text: `${formatUtc(row.at)} — ${row.message}` }));
  }
}

function journeySteps(status) {
  const order = ["Submitted", "Placed", "Running", "Complete"];
  const idx =
    status === "queued"
      ? 0
      : status === "scheduled"
        ? 1
        : status === "running"
          ? 2
          : status === "completed"
            ? 3
            : status === "failed" || status === "unfulfilled"
              ? 2
              : 0;
  return order.map((label, i) => ({ label, done: i <= idx, current: i === idx }));
}

function renderDetail(job) {
  if (!job) {
    jobDetail.hidden = true;
    return;
  }
  jobDetail.hidden = false;
  document.getElementById("detail-id").textContent = `${shortJobId(job.id)} (${job.id})`;
  document.getElementById("detail-id").title = job.id;

  const prev = prevSiteByJob.get(job.id);
  const movedEl = document.getElementById("moved-notice");
  if (prev != null && prev !== job.assigned_site_id && job.assigned_site_id) {
    movedEl.hidden = false;
    movedEl.textContent = `Moved from ${formatAssignment(prev)} to ${formatAssignment(job.assigned_site_id)}.`;
    logActivity(`Job ${shortJobId(job.id)} moved to ${job.assigned_site_id}`);
  } else {
    movedEl.hidden = true;
  }

  const track = document.getElementById("journey-track");
  track.replaceChildren();
  for (const step of journeySteps(job.status)) {
    const li = el("li", { className: step.done ? "journey-step done" : "journey-step" });
    li.appendChild(statusPill(step.current ? job.status : "queued"));
    li.appendChild(el("span", { text: step.label }));
    track.appendChild(li);
  }

  const prog = document.getElementById("runtime-progress");
  if (job.status === "running" && job.scheduled_start && demoClock) {
    const elapsed = Math.max(0, (new Date(demoClock) - new Date(job.scheduled_start)) / 3600000);
    const total = job.duration_hours;
    const pct = Math.min(100, (elapsed / total) * 100);
    prog.textContent = `Simulated runtime: ${elapsed.toFixed(1)} h of ${total} h. Basis: simulated clock minus scheduled start. Not GPU telemetry. (${pct.toFixed(0)}%)`;
    prog.style.setProperty("--progress", `${pct}%`);
  } else {
    prog.textContent =
      job.status === "running"
        ? "Simulated runtime unavailable (missing scheduled start or clock)."
        : "Simulated runtime: not started.";
    prog.style.removeProperty("--progress");
  }

  const actions = document.getElementById("result-actions");
  actions.replaceChildren();
  if (job.status === "completed") {
    toast(`Job ${shortJobId(job.id)} completed.`);
  }
  if (job.result_url) {
    const a = el("a", { className: "btn btn--primary", href: job.result_url, text: "Download sample result" });
    a.setAttribute("download", "");
    actions.appendChild(a);
  } else {
    actions.appendChild(
      el("button", {
        type: "button",
        className: "btn btn--secondary",
        disabled: "true",
        text: "Sample result not ready.",
      }),
    );
  }
  renderActivityLog();
}

function renderTopology(sites, highlightSiteId) {
  topologyMini.replaceChildren();
  topologyMini.appendChild(el("p", { className: "small", text: "Simulated topology (API sites):" }));
  const row = el("div", { className: "topology-row" });
  for (const s of sites || []) {
    const pill = el("span", {
      className: `topology-pill${s.site_id === highlightSiteId ? " topology-pill--active" : ""}`,
      text: siteShortName(s),
    });
    row.appendChild(pill);
  }
  topologyMini.appendChild(row);
}

function renderJobs(jobs, sites) {
  jobsCache = jobs || [];
  jobsBody.replaceChildren();
  const hasJobs = jobsCache.length > 0;
  jobsTable.parentElement.parentElement; // table-scroll
  jobsEmpty.hidden = hasJobs;
  const scroll = jobsTable.closest(".table-scroll");
  if (scroll) scroll.hidden = !hasJobs;

  const highlight = jobsCache.find((j) => j.id === selectedJobId)?.assigned_site_id;
  renderTopology(sites, highlight);

  for (const job of jobsCache) {
    const tr = el("tr", {
      className: job.id === selectedJobId ? "row-selected" : "",
      tabIndex: "0",
      role: "button",
      "aria-label": `Select job ${shortJobId(job.id)}`,
    });
    tr.dataset.jobId = job.id;

    const idTd = el("td");
    const idSpan = el("span", { className: "num", text: shortJobId(job.id) });
    idSpan.title = job.id;
    idTd.appendChild(idSpan);
    tr.appendChild(idTd);
    tr.appendChild(el("td", { text: job.template }));
    const st = el("td");
    st.appendChild(statusPill(job.status));
    tr.appendChild(st);
    tr.appendChild(el("td", { text: formatAssignment(job.assigned_site_id) }));
    tr.appendChild(el("td", { text: job.scheduled_start ? formatUtc(job.scheduled_start) : "Unassigned" }));
    tr.appendChild(el("td", { text: formatUtc(job.deadline) }));

    const pick = () => {
      selectedJobId = job.id;
      renderJobs(jobsCache, sites);
      renderDetail(job);
    };
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        pick();
      }
    });
    jobsBody.appendChild(tr);

    const prev = prevSiteByJob.get(job.id);
    if (prev === undefined) prevSiteByJob.set(job.id, job.assigned_site_id);
    else if (prev !== job.assigned_site_id) {
      prevSiteByJob.set(job.id, job.assigned_site_id);
    }
  }

  if (selectedJobId) {
    const job = jobsCache.find((j) => j.id === selectedJobId);
    renderDetail(job);
  }
}

async function fetchSites() {
  if (isVisualDemoMode() && window.AQ_DEMO) return window.AQ_DEMO.sitesResponse.sites;
  const data = await getSites();
  return data.sites;
}

async function loadDemo({ setFormDefaults = false } = {}) {
  setError(demoError, null);
  try {
    const demo = isVisualDemoMode() && window.AQ_DEMO ? window.AQ_DEMO : await getDemo();
    demoClock = demo.current_time;
    document.getElementById("demo-clock").textContent = formatUtc(demo.current_time);
    document.getElementById("demo-session").textContent = demo.session_id;
    document.getElementById("demo-revision").textContent = String(demo.revision ?? "—");
    if (setFormDefaults) applyDemoDefaults(demo.current_time);
    return demo;
  } catch (err) {
    setError(demoError, err.message);
    throw err;
  }
}

async function loadJobs(showSkeleton = false) {
  setError(listError, null);
  if (showSkeleton) jobsSkeleton.hidden = false;
  try {
    const data = isVisualDemoMode() && window.AQ_DEMO?.jobs ? window.AQ_DEMO.jobs : await getJobs();
    const sites = await fetchSites();
    updateSimBanner(sites.length);
    if (isVisualDemoMode()) {
      const tagSlot = document.querySelector(".hero");
      if (tagSlot && !tagSlot.querySelector(".visual-demo-tag")) tagSlot.prepend(visualDemoTag());
    }
    renderJobs(data.jobs, sites);
    document.getElementById("demo-revision").textContent = String(data.revision);
    return data;
  } catch (err) {
    setError(listError, err.message);
    throw err;
  } finally {
    jobsSkeleton.hidden = true;
  }
}

form.addEventListener("change", updateEstimate);
form.addEventListener("input", updateEstimate);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (isVisualDemoMode()) {
    setError(formError, "Live submit is disabled in visual demo mode. Turn off visual demo to POST /api/jobs.");
    return;
  }
  setError(formError, null);
  formSuccess.hidden = true;
  submitBtn.disabled = true;
  submitSpinner.hidden = false;
  submitLabel.textContent = "Submitting…";
  try {
    const template = selectedTemplate();
    const created = await postJob({
      template,
      earliest_start: datetimeLocalToUtcIso(earliestInput.value),
      deadline: datetimeLocalToUtcIso(deadlineInput.value),
    });
    formSuccess.hidden = false;
    formSuccess.textContent = `Submitted ${shortJobId(created.id)} — placed at ${formatAssignment(created.assigned_site_id)}. Full id: ${created.id}`;
    selectedJobId = created.id;
    logActivity(`Submitted ${created.id}`);
    await loadJobs();
    await loadDemo();
    toast(`Job ${shortJobId(created.id)} submitted.`);
  } catch (err) {
    setError(formError, err.message);
  } finally {
    submitBtn.disabled = false;
    submitSpinner.hidden = true;
    submitLabel.textContent = "Submit job";
  }
});

document.getElementById("refresh-btn").addEventListener("click", () => {
  loadJobs(true);
  loadDemo();
});

document.getElementById("visual-demo-toggle").addEventListener("click", () => {
  setVisualDemoMode(!isVisualDemoMode());
});

function startPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (document.visibilityState !== "visible" || isVisualDemoMode()) return;
    loadJobs();
    loadDemo();
  }, 4000);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !isVisualDemoMode()) {
    loadJobs();
    loadDemo();
  }
});

(async function init() {
  try {
    await loadDemo({ setFormDefaults: true });
  } catch {
    /* */
  }
  updateEstimate();
  try {
    await loadJobs(true);
  } catch {
    /* */
  }
  startPoll();
})();