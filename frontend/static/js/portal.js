/* Portal — Portal.dc.html renderVals translated onto live /api data.
   Visual demo mode (?demo=1 or the toggle) reads window.AQ_DEMO instead and says so. */

const DEMO = isVisualDemoMode() && window.AQ_DEMO ? JSON.parse(JSON.stringify(window.AQ_DEMO)) : null;
const DEFAULT_WINDOW_HOURS = 12;
const PLACEHOLDER_SITE = "Not yet placed";

const $ = (id) => document.getElementById(id);
const form = $("job-form");
const earliestInput = $("earliest_start");
const deadlineInput = $("deadline");
const submitBtn = $("submit-btn");
const formError = $("form-error");
const formSuccess = $("form-success");
const jobsBody = $("jobs-body");

let sites = [];
let jobs = [];
let clockIso = null;
let ownerId = null;
let selectedJobId = null;
let successJobId = null;
let loadedOnce = false;
let pollTimer = null;
const pendingFalls = []; // a just-submitted job drops into its tank, or the queue well
const placements = []; // queued jobs the scheduler just placed: hop from the queue well
let submitting = false;
const obs = new Map(); // job id -> what this browser has observed

/* ---------- helpers ---------- */
function siteById(id) {
  return sites.find((s) => s.site_id === id);
}
function siteName(id) {
  if (!id) return PLACEHOLDER_SITE;
  return siteById(id)?.name || id;
}
function selectedTemplate() {
  return form.querySelector('input[name="template"]:checked')?.value || "training_small";
}
function jobEnd(job) {
  if (job.completed_at) return new Date(job.completed_at).getTime();
  if (!job.scheduled_start) return null;
  return new Date(job.scheduled_start).getTime() + job.duration_hours * HOUR_MS;
}
function myJobs() {
  const mine = ownerId ? jobs.filter((j) => j.owner_id === ownerId) : jobs;
  return mine.slice().reverse(); // newest submissions first
}

/* ---------- observation log (activity is "recorded in this browser") ---------- */
function observe(job, submittedHere) {
  const at = clockIso;
  let o = obs.get(job.id);
  const site = job.assigned_site_id;
  if (!o) {
    o = { firstSeen: at, placedSeen: site ? at : null, site, status: job.status, start: job.scheduled_start, deadline: job.deadline, events: [], move: null };
    obs.set(job.id, o);
    const spec = `${tplName(job.template)}, ${job.gpu_count} GPUs for ${fmtHours(job.duration_hours)} h, deadline ${fullUtc(job.deadline)}.`;
    o.events.push({ at, text: submittedHere ? `Submitted. ${spec}` : `In the queue when this page opened. ${spec}` });
    if (site) o.events.push({ at, text: `Placed on ${siteName(site)}.${job.scheduled_start ? ` Starts ${hhmm(job.scheduled_start)}.` : ""}` });
    if (job.status === "running") o.events.push({ at, text: `Running on ${siteName(site)}.` });
    if (job.status === "completed") o.events.push({ at: job.completed_at || at, text: `Completed.${job.result_url ? " Sample result is ready." : ""}`, dot: "#0F6E76" });
    if (job.status === "failed") o.events.push({ at, text: `Failed on ${siteName(site)}.`, dot: "#B4410F" });
    if (job.status === "unfulfilled") o.events.push({ at, text: "Marked unfulfilled by the scheduler.", dot: "#B4410F" });
    return { changed: false };
  }

  let completedNow = false;
  if (site !== o.site) {
    if (!o.site && site) {
      o.placedSeen = at;
      o.events.push({ at, text: `Placed on ${siteName(site)}.${job.scheduled_start ? ` Starts ${hhmm(job.scheduled_start)}.` : ""}` });
      placements.push({ job: job.id, to: site, key: `pl:${job.id}:${Date.now()}`, until: Date.now() + 4000 });
      setTimeout(renderMini, 4100);
    } else if (o.site && site) {
      const sameTimes = o.start === job.scheduled_start && o.deadline === job.deadline;
      o.move = { from: o.site, to: site, at, sameTimes, start: job.scheduled_start };
      o.events.push({
        at,
        text: `Reassigned from ${siteName(o.site)} to ${siteName(site)}. ${sameTimes ? "Start time and deadline unchanged." : `Now starts ${hhmm(job.scheduled_start)}.`}`,
        dot: "#0F6E76",
      });
      toast(`${shortJobId(job.id)} moved to ${siteName(site)}.`);
    } else {
      o.events.push({ at, text: `Unassigned from ${siteName(o.site)}.`, dot: "#C9D6D6" });
    }
  }
  if (job.status !== o.status) {
    if (job.status === "running") o.events.push({ at, text: `Started on ${siteName(site)}.` });
    else if (job.status === "completed") {
      o.events.push({ at: job.completed_at || at, text: `Completed.${job.result_url ? " Sample result is ready to download." : ""}`, dot: "#0F6E76" });
      completedNow = true;
    } else if (job.status === "failed") o.events.push({ at, text: `Failed on ${siteName(site)}.`, dot: "#B4410F" });
    else if (job.status === "unfulfilled") o.events.push({ at, text: "Marked unfulfilled by the scheduler.", dot: "#B4410F" });
    else if (job.status === "scheduled" && o.status === "queued" && site === o.site) o.events.push({ at, text: "Scheduled." });
  }
  o.site = site;
  o.status = job.status;
  o.start = job.scheduled_start;
  o.deadline = job.deadline;
  return { completedNow };
}

/* ---------- header / hero ---------- */
function renderHeader() {
  $("demo-clock").textContent = clockText(clockIso);
  const n = sites.length;
  $("sim-banner").textContent = `SIMULATED · ${n} SITE${n === 1 ? "" : "S"} · NO REAL GPUS`;
  $("hero-kick").textContent = `User portal · ${ownerId || "demo user"}`;
  $("hero-sub").textContent = `Send a workload and we place it on whichever of ${numberWord(n).toLowerCase()} simulated data center${n === 1 ? "" : "s"} has the easiest water.`;
  const mine = myJobs();
  $("stat-jobs").textContent = String(mine.length);
  $("stat-running").textContent = String(mine.filter((j) => j.status === "running").length);
  const clockMs = clockIso ? new Date(clockIso).getTime() : 0;
  const next = mine
    .filter((j) => j.status === "scheduled" && j.scheduled_start && new Date(j.scheduled_start).getTime() >= clockMs)
    .map((j) => new Date(j.scheduled_start).getTime())
    .sort((a, b) => a - b)[0];
  $("stat-next").textContent = next != null ? hhmm(next) : "—";
}

function renderMini() {
  const n = sites.length;
  const sel = jobs.find((j) => j.id === selectedJobId);
  const o = sel ? obs.get(sel.id) : null;
  const tMs = clockIso ? new Date(clockIso).getTime() : Date.now();
  let top = 1;
  const tankSites = sites.map((s) => {
    const m = siteHourModel(s, jobs, tMs);
    top = Math.max(top, m.w, m.c ?? 0);
    return { key: s.site_id, short: siteShort(s), w: m.w, c: m.c, u: m.u, over: m.c != null && m.w > m.c, highlight: Boolean(sel && sel.assigned_site_id === s.site_id) };
  });
  const arcs = [];
  const drops = [];
  if (sel && o?.move) {
    const key = `mv:${sel.id}:${o.move.from}>${o.move.to}`;
    arcs.push({ key, from: o.move.from, to: o.move.to, kind: "applied" });
    drops.push({ key, arc: key });
  }
  const now = Date.now();
  for (const p of placements) {
    if (p.until < now) continue;
    const key = `q:${p.job}`;
    arcs.push({ key, from: "queue", to: p.to, kind: "applied" });
    drops.push({ key: p.key, arc: key });
  }
  let caption = `${numberWord(n)} simulated site${n === 1 ? "" : "s"}. Water level is each site's modeled withdrawal right now.`;
  if (sel) {
    const id = shortJobId(sel.id);
    if (o?.move) caption = `${id} moved from ${siteShort(siteName(o.move.from))} to ${siteShort(siteName(o.move.to))}. Simulated.`;
    else if (sel.assigned_site_id) caption = `${id} runs on ${siteName(sel.assigned_site_id)} (outlined). Simulated.`;
    else caption = `${id} is in the queue, waiting for a site.`;
  }
  $("mini-caption").textContent = caption;
  const queued = myJobs().filter((j) => j.status === "queued" && !j.assigned_site_id).length;
  const svgEl = $("topology-mini");
  svgEl.setAttribute("aria-label", caption);
  drawTanks(svgEl, null, {
    mini: true,
    scaleMax: niceCeil(top * 1.15),
    sites: tankSites,
    queue: { count: queued },
    arcs, drops,
    falls: pendingFalls.splice(0),
  });
}

/* ---------- estimate (dc: est) ---------- */
function windowHours() {
  const e = earliestInput.value;
  const d = deadlineInput.value;
  if (!e || !d) return NaN;
  return Math.round((new Date(datetimeLocalToUtcIso(d)) - new Date(datetimeLocalToUtcIso(e))) / HOUR_MS);
}
function updateEstimate() {
  const t = TEMPLATES[selectedTemplate()];
  const winH = windowHours();
  const line = $("est-line");
  line.replaceChildren(
    el("strong", { className: "mono", text: `${t.gpu} GPUs` }), " for ",
    el("strong", { className: "mono", text: `${t.dur} h` }), " inside a ",
    el("strong", { className: "mono", text: `${isNaN(winH) ? "?" : winH} h` }), " window",
  );
  const note = $("est-note");
  let bad = true;
  if (isNaN(winH) || winH <= 0) note.textContent = "The deadline must be after the earliest start.";
  else if (winH < t.dur) note.textContent = "The window is shorter than the runtime, so this job can never finish.";
  else {
    note.textContent = `${winH - t.dur} h of slack gives the scheduler room to choose cheaper water.`;
    bad = false;
  }
  note.classList.toggle("isBad", bad);
}

/* ---------- jobs list ---------- */
function renderList() {
  const list = myJobs();
  $("jobs-skeleton").hidden = true;
  $("jobs-empty").hidden = list.length > 0;
  $("list-note").textContent = `${list.length} job${list.length === 1 ? "" : "s"} · simulated`;
  const keep = new Set();
  const frag = [];
  for (const job of list) {
    keep.add(job.id);
    const isNew = !jobsBody.querySelector(`[data-key="${CSS.escape(job.id)}"]`) && loadedOnce;
    const btn = el("button", { type: "button", className: `jobBtn${isNew ? " dropIn" : ""}`, "aria-pressed": job.id === selectedJobId ? "true" : "false", dataset: { key: job.id } }, [
      el("span", { className: "jbCol" }, [el("strong", { className: "mono jbId", text: shortJobId(job.id), title: job.id }), el("span", { className: "jbTpl", text: `${tplName(job.template)} · ${job.gpu_count} GPU` })]),
      el("span", { className: "jbStatus" }, [statusDot(job.status), statusWord(job.status)]),
      el("span", { className: "jbCol" }, [el("span", { className: "kick jbKick", text: "Data center" }), el("span", { className: "jbSite", text: siteName(job.assigned_site_id) })]),
      el("span", { className: "jbCol" }, [
        el("span", { className: "kick jbKick", text: "Starts · deadline" }),
        el("span", { className: "mono jbTime", text: `${job.scheduled_start && job.assigned_site_id ? hhmm(job.scheduled_start) : "—"} · ${hhmm(job.deadline)}` }),
      ]),
    ]);
    btn.addEventListener("click", () => {
      selectedJobId = job.id;
      renderList();
      renderDetail();
      renderMini();
    });
    frag.push(btn);
  }
  jobsBody.replaceChildren(...frag);
}

/* ---------- detail (dc: d) ---------- */
function renderDetail() {
  const job = jobs.find((j) => j.id === selectedJobId);
  const card = $("job-detail");
  if (!job) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const o = obs.get(job.id) || { events: [] };
  const status = job.status;
  const placed = Boolean(job.assigned_site_id);
  const bad = status === "failed" || status === "unfulfilled";
  const dur = job.duration_hours;
  const clockMs = clockIso ? new Date(clockIso).getTime() : null;
  const startMs = job.scheduled_start ? new Date(job.scheduled_start).getTime() : null;

  const title = $("d-title");
  title.textContent = `${shortJobId(job.id)} · ${tplName(job.template)}`;
  title.title = job.id;
  $("d-spec").textContent = `${job.gpu_count} GPUs · ${fmtHours(dur)} h runtime · simulated`;
  const pill = $("d-pill");
  pill.replaceChildren(statusDot(status), statusWord(status));
  pill.classList.toggle("isBad", bad);

  // moved notice
  const moved = $("moved-notice");
  moved.hidden = !o.move;
  if (o.move) {
    $("moved-route").textContent = `${siteName(o.move.from)} → ${siteName(o.move.to)}.`;
    $("moved-text").textContent = o.move.sameTimes
      ? "The operator's new schedule reassigned it. Your start time and deadline did not change."
      : `The operator's new schedule reassigned it. It now starts at ${hhmm(o.move.start)} UTC; your deadline did not change.`;
  }

  const failed = $("d-failed");
  failed.hidden = !bad;
  if (status === "failed") failed.replaceChildren(el("strong", { text: "This job failed." }), ` The simulation reported a failure${placed ? ` on ${siteName(job.assigned_site_id)}` : ""}. No result was produced.`);
  if (status === "unfulfilled") failed.replaceChildren(el("strong", { text: "This job is unfulfilled." }), " The scheduler could not fit it on any eligible site before its deadline. No result was produced.");

  // journey
  let phase = 0;
  if (status === "scheduled") phase = 1;
  else if (status === "running" || status === "failed") phase = 2;
  else if (status === "completed") phase = 3;
  let prog = 0;
  if (status === "running" && clockMs != null && startMs != null) prog = Math.max(0, Math.min((clockMs - startMs) / (dur * HOUR_MS), 1));
  let fill = [0, 33.3, 66.6 + prog * 33.3 * 0.9, 100][phase];
  if (status === "failed") fill = 66.6;
  if (status === "unfulfilled") fill = 0;
  const lastLabel = status === "failed" ? "Failed" : status === "unfulfilled" ? "Unfulfilled" : "Complete";
  const labels = ["Submitted", "Placed", "Running", lastLabel];
  const endMs = jobEnd(job);
  const times = [
    stampUtc(o.firstSeen),
    placed ? (o.placedSeen ? stampUtc(o.placedSeen) : "—") : "waiting",
    startMs != null && placed ? (phase >= 2 ? stampUtc(startMs) : `est. ${stampUtc(startMs)}`) : "—",
    job.completed_at ? stampUtc(job.completed_at) : endMs != null && placed && !bad ? `est. ${stampUtc(endMs)}` : "—",
  ];
  const fillEl = $("j-fill");
  fillEl.className = `jFill${status === "running" ? " fillRun" : ""}`;
  requestAnimationFrame(() => (fillEl.style.width = `${fill.toFixed(1)}%`));
  const track = $("j-track");
  track.querySelectorAll(".jStep").forEach((n) => n.remove());
  const labelsEl = $("j-labels");
  labelsEl.replaceChildren();
  [0, 1, 2, 3].forEach((i) => {
    const done = i < phase || (i === phase && phase === 3);
    const cur = i === phase && phase < 3 && !bad;
    const isBadStep = bad && i === 3;
    const step = el("div", { className: `jStep${cur ? " pulseNode" : ""}` }, [el("span", { "aria-hidden": "true", text: isBadStep ? "✕" : done ? "✓" : "" })]);
    step.style.left = `${(i * 33.333).toFixed(2)}%`;
    step.style.background = isBadStep ? "#B4410F" : done ? "#2A9D9F" : "#FFFFFF";
    step.style.borderColor = isBadStep ? "#B4410F" : done || cur ? "#2A9D9F" : "#C9D6D6";
    track.appendChild(step);
    labelsEl.appendChild(
      el("div", { className: `jLabel${done || cur || isBadStep ? " isOn" : ""}` }, [
        el("strong", { text: labels[i] }),
        el("span", { className: "mono jTime", text: times[i] }),
      ]),
    );
  });

  let progressLine = "Waiting in the queue. The scheduler has not picked a site yet.";
  if (status === "scheduled") {
    if (startMs != null && clockMs != null) {
      const inH = Math.ceil((startMs - clockMs) / HOUR_MS);
      progressLine = inH > 0 ? `Placed. Starts at ${hhmm(startMs)} UTC, in ${inH} simulated hour${inH === 1 ? "" : "s"}.` : `Placed. Starts at ${hhmm(startMs)} UTC.`;
    } else progressLine = "Placed. Start time not set yet.";
  }
  if (status === "running") {
    progressLine =
      startMs != null && clockMs != null
        ? `Simulated runtime: ${fmtHours(Math.max(0, (clockMs - startMs) / HOUR_MS))} h of ${fmtHours(dur)} h · ${Math.round(prog * 100)}%`
        : "Running. Simulated runtime unavailable (no scheduled start).";
  }
  if (status === "completed") progressLine = `Finished. Simulated runtime: ${fmtHours(dur)} h of ${fmtHours(dur)} h · 100%`;
  if (status === "failed") progressLine = "Stopped. No result was produced.";
  if (status === "unfulfilled") progressLine = "Not placed before the deadline.";
  $("runtime-progress").textContent = progressLine;

  // activity
  const log = $("activity-log");
  log.replaceChildren(
    ...o.events.map((e) =>
      el("li", { className: "actItem rise" }, [
        el("span", { className: "mono actStamp", text: stampUtc(e.at) }),
        el("span", { className: "actRail", "aria-hidden": "true" }, [(() => {
          const d = el("span", { className: "actDot" });
          if (e.dot) d.style.background = e.dot;
          return d;
        })(), el("span", { className: "actLine" })]),
        el("span", { className: "actText", text: e.text }),
      ]),
    ),
  );

  $("d-site").textContent = siteName(job.assigned_site_id);
  $("d-start").textContent = placed && startMs != null ? `${fullUtc(startMs)} UTC` : "—";
  $("d-dl").textContent = `${fullUtc(job.deadline)} UTC`;
  $("d-es").textContent = `${fullUtc(job.earliest_start)} UTC`;

  // result
  const result = $("d-result");
  result.hidden = !(status === "completed" || job.result_url);
  if (!result.hidden) {
    let fileName = `${shortJobId(job.id).toLowerCase()}-sample-result.csv`;
    if (job.result_url && !job.result_url.startsWith("data:")) {
      const last = job.result_url.split(/[?#]/)[0].split("/").pop();
      if (last && last.includes(".")) fileName = decodeURIComponent(last);
    }
    $("d-file").textContent = job.result_url ? fileName : "Sample result";
    const slot = $("d-download");
    if (job.result_url) {
      const a = el("a", { className: "btn btnP", href: job.result_url, download: fileName, text: "Download sample result" });
      slot.replaceChildren(a);
    } else {
      slot.replaceChildren(el("button", { type: "button", className: "btn btnP", disabled: true, text: "Sample result not ready." }));
    }
  }
}

/* ---------- success card + completion notice ---------- */
function renderSuccess() {
  const job = jobs.find((j) => j.id === successJobId);
  if (!job) return;
  $("success-title").textContent = `${shortJobId(job.id)} is in.`;
  $("success-text").textContent = job.assigned_site_id
    ? `Placed on ${siteName(job.assigned_site_id)}. It starts at ${hhmm(job.scheduled_start)} UTC and must finish by ${hhmm(job.deadline)} on ${MONTHS[new Date(job.deadline).getUTCMonth()]} ${new Date(job.deadline).getUTCDate()}. We will tell you if it moves.`
    : "Accepted and queued. The scheduler is choosing a site now.";
}

function showCompletion(job) {
  const id = shortJobId(job.id);
  $("notice-title").textContent = `${id} is done.`;
  const fin = job.completed_at ? new Date(job.completed_at).getTime() : null;
  const spare = fin != null ? Math.round((new Date(job.deadline).getTime() - fin) / HOUR_MS) : null;
  const parts = [];
  if (fin != null) parts.push(`Finished at ${hhmm(fin)} UTC on ${siteName(job.assigned_site_id)}${spare != null && spare > 0 ? `, ${numberWord(spare).toLowerCase()} hour${spare === 1 ? "" : "s"} before your deadline` : ""}.`);
  else parts.push(`Finished on ${siteName(job.assigned_site_id)}.`);
  parts.push(job.result_url ? "Your sample result is ready below." : "The sample result is not ready yet.");
  $("notice-text").textContent = parts.join(" ");
  $("notice").hidden = false;
  toast(`${id} completed.`);
}

/* ---------- data ---------- */
async function fetchAll() {
  if (DEMO) return [DEMO, DEMO.sitesResponse, DEMO.jobs];
  return Promise.all([getDemo(), getSites(), getJobs()]);
}

async function loadAll({ first = false } = {}) {
  try {
    const [demo, sitesResp, jobsResp] = await fetchAll();
    setError($("demo-error"), null);
    setError($("list-error"), null);
    clockIso = demo.current_time;
    sites = sitesResp.sites || [];
    jobs = jobsResp.jobs || [];
    ownerId = ownerId || jobs.find((j) => j.owner_id === "demo-user-001")?.owner_id || jobs[0]?.owner_id || null;
    if (first) {
      if (!earliestInput.value) earliestInput.value = toDatetimeLocalValue(clockIso);
      if (!deadlineInput.value) deadlineInput.value = toDatetimeLocalValue(addHoursToIso(clockIso, DEFAULT_WINDOW_HOURS));
    }
    for (const job of jobs) {
      const { completedNow } = observe(job, false);
      if (completedNow && loadedOnce && (!ownerId || job.owner_id === ownerId)) showCompletion(job);
    }
    if (!selectedJobId || !jobs.some((j) => j.id === selectedJobId)) selectedJobId = myJobs()[0]?.id || null;
    renderHeader();
    renderList();
    renderDetail();
    renderMini();
    renderSuccess();
    updateEstimate();
    loadedOnce = true;
  } catch (err) {
    $("jobs-skeleton").hidden = true;
    const msg = err.status ? err.message : "Network error. Is the API server running?";
    setError($("list-error"), `Could not load jobs. ${msg}`);
  }
}

/* ---------- submit ---------- */
function setSubmitting(on) {
  submitting = on;
  submitBtn.disabled = on;
  $("submit-spinner").hidden = !on;
  $("submit-label").textContent = on ? "Sending to the scheduler…" : "Submit job";
}
function showFormError(msg) {
  if (!msg) return setError(formError, null);
  formError.hidden = false;
  formError.replaceChildren(el("strong", { text: "Could not submit." }), ` ${msg}`);
}

form.addEventListener("input", () => {
  showFormError(null);
  updateEstimate();
});
form.addEventListener("change", updateEstimate);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting) return;
  const t = TEMPLATES[selectedTemplate()];
  const winH = windowHours();
  if (isNaN(winH) || winH <= 0) return showFormError("Deadline must be after the earliest start.");
  if (winH < t.dur) return showFormError(`The window is shorter than the ${t.dur} h runtime. Move the deadline later.`);
  if (DEMO) return showFormError("Visual demo mode uses labeled sample data and never sends jobs. Turn off Visual demo to submit to the API.");
  showFormError(null);
  setSubmitting(true);
  try {
    const created = await postJob({
      template: selectedTemplate(),
      earliest_start: datetimeLocalToUtcIso(earliestInput.value),
      deadline: datetimeLocalToUtcIso(deadlineInput.value),
    });
    ownerId = ownerId || created.owner_id;
    if (!jobs.some((j) => j.id === created.id)) jobs.push(created);
    observe(created, true);
    pendingFalls.push({ key: `f:${created.id}`, to: created.assigned_site_id || "queue" });
    selectedJobId = created.id;
    successJobId = created.id;
    form.hidden = true;
    formSuccess.hidden = false;
    renderSuccess();
    await loadAll();
  } catch (err) {
    showFormError(err.status ? err.message : "Network error. Is the API server running?");
  } finally {
    setSubmitting(false);
  }
});

$("submit-another").addEventListener("click", () => {
  formSuccess.hidden = true;
  form.hidden = false;
  successJobId = null;
  form.querySelector('input[name="template"]:checked')?.focus();
});
$("notice-dismiss").addEventListener("click", () => ($("notice").hidden = true));

/* ---------- visual demo ---------- */
const toggle = $("visual-demo-toggle");
toggle.setAttribute("aria-pressed", DEMO ? "true" : "false");
toggle.addEventListener("click", () => setVisualDemoMode(!DEMO));
if (DEMO) {
  $("director").hidden = false;
  $("director-tag").replaceChildren(visualDemoTag());
  $("list-tag").replaceChildren(visualDemoTag(true));
  for (const id of ["nav-portal", "nav-operator"]) $(id).href += "?demo=1";
  $("d-move").addEventListener("click", () => {
    // Sample-only: mirror what the operator's demo plan does to JOB-014.
    const j = DEMO.jobs.jobs.find((x) => x.id === "job-hackuta-0014");
    if (j && j.assigned_site_id === "site-alpha") {
      j.assigned_site_id = "site-beta";
      DEMO.jobs.revision += 1;
      selectedJobId = j.id;
      loadAll();
    }
  });
}

/* ---------- polling (every 4 s while visible) ---------- */
function startPoll() {
  if (DEMO) return;
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (document.visibilityState === "visible") loadAll();
  }, 4000);
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !DEMO) loadAll();
});

$("brand").prepend(logoSvg());
updateEstimate();
loadAll({ first: true }).then(startPoll);

initLayout({
  blocks: ".submitCard, .jobsCard, .placeCard, .stat",
  texts: ".pageTitle, .cardTitle",
  scrollers: "#jobs-body",
});
