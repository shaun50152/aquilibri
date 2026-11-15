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
const sheet = $("new-sheet");
const jobsBody = $("jobs-body");

let sites = [];
let jobs = [];
let clockIso = null;
let ownerId = null;
let selectedJobId = null;
let loadedOnce = false;
let pollTimer = null;
const SEEN_NOTICES_KEY = "aq.seenNotifications";
function loadSeenNotices() {
  try {
    return new Set(JSON.parse(localStorage.getItem(SEEN_NOTICES_KEY) || "[]"));
  } catch {
    return new Set();
  }
}
function saveSeenNotices() {
  try {
    localStorage.setItem(SEEN_NOTICES_KEY, JSON.stringify([...seenNotificationIds].slice(-200)));
  } catch {
    /* storage unavailable: dedupe stays per page load */
  }
}
let seenNotificationIds = loadSeenNotices();
let lastRevision = null;
const pendingFalls = []; // a just-submitted job drops into its tank, or the queue well
const placements = []; // queued jobs the scheduler just placed: hop from the queue well
let submitting = false;
const obs = new Map(); // job id -> what this browser has observed
let grokSummary = "";
let isGrokLoading = false;
let grokJobId = null;
let grokError = null;

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
    o = { firstSeen: submittedHere ? at : null, placedSeen: submittedHere && site ? at : null, site, status: job.status, start: job.scheduled_start, deadline: job.deadline, events: [], move: null };
    obs.set(job.id, o);
    const spec = `${tplName(job.template)}, ${job.gpu_count} GPUs for ${fmtHours(job.duration_hours)} h, deadline ${fullUtc(job.deadline)}.`;
    o.events.push({ at, text: submittedHere ? `Submitted. ${spec}` : `In the queue when this page opened. ${spec}` });
    if (site) o.events.push({ at, text: `Placed on ${siteName(site)}.${job.scheduled_start ? ` Starts ${hhmm(job.scheduled_start)}.` : ""}` });
    if (job.status === "running") o.events.push({ at, text: `Running on ${siteName(site)}.` });
    if (job.status === "completed") o.events.push({ at: job.completed_at || at, text: `Completed.${job.result_url ? " Sample result is ready." : ""}`, dot: PAL.accent });
    if (job.status === "failed") o.events.push({ at, text: `Failed on ${siteName(site)}.`, dot: PAL.ember });
    if (job.status === "unfulfilled") o.events.push({ at, text: "Marked unfulfilled by the scheduler.", dot: PAL.ember });
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
        dot: PAL.accent,
      });
      toast(`${shortJobId(job.id)} moved to ${siteName(site)}.`);
    } else {
      o.events.push({ at, text: `Unassigned from ${siteName(o.site)}.`, dot: PAL.muted });
    }
  }
  if (job.status !== o.status) {
    if (job.status === "running") o.events.push({ at, text: `Started on ${siteName(site)}.` });
    else if (job.status === "completed") {
      o.events.push({ at: job.completed_at || at, text: `Completed.${job.result_url ? " Sample result is ready to download." : ""}`, dot: PAL.accent });
      completedNow = true;
    } else if (job.status === "failed") o.events.push({ at, text: `Failed on ${siteName(site)}.`, dot: PAL.ember });
    else if (job.status === "unfulfilled") o.events.push({ at, text: "Marked unfulfilled by the scheduler.", dot: PAL.ember });
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
  $("hero-sub").textContent = `Send a workload and we run it on whichever of your ${numberWord(n).toLowerCase()} data center${n === 1 ? "" : "s"} has the most water headroom.`;
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
  const sel = jobs.find((j) => j.id === selectedJobId);
  const o = sel ? obs.get(sel.id) : null;
  const tMs = clockIso ? new Date(clockIso).getTime() : Date.now();
  const mine = myJobs();
  const live = mine.filter((j) => j.assigned_site_id && j.status !== "completed" && j.status !== "failed" && j.status !== "unfulfilled");
  const now = Date.now();
  const recent = placements.filter((p) => p.until >= now);

  // Show every site when there are few; otherwise the sites that matter to you.
  let shown = sites;
  if (sites.length > 5) {
    const want = new Set(live.map((j) => j.assigned_site_id));
    if (sel?.assigned_site_id) want.add(sel.assigned_site_id);
    if (o?.move) want.add(o.move.from).add(o.move.to);
    recent.forEach((p) => want.add(p.to));
    const byWater = sites.map((s) => ({ s, w: siteHourModel(s, jobs, tMs).w })).sort((x, y) => x.w - y.w);
    for (const { s } of byWater) {
      if (want.size >= 3) break;
      want.add(s.site_id);
    }
    shown = sites.filter((s) => want.has(s.site_id)).slice(0, 5);
  }

  const flowSites = shown.map((s) => {
    const m = siteHourModel(s, jobs, tMs);
    const k = live.filter((j) => j.assigned_site_id === s.site_id).length;
    const over = Boolean(m.over);
    const isSel = Boolean(sel && sel.assigned_site_id === s.site_id);
    const tempTight = m.c != null && m.scaleC != null && m.c < m.scaleC - 0.5;
    let fig = over ? `▲ ${fmtNum(m.w)} L/h` : `${fmtNum(m.w)} L/h`;
    let figTone = over ? "over" : "";
    if (!over && tempTight && m.gpus === 0) {
      fig = `${fmtNum(m.w)} L/h · cleared`;
      figTone = "good";
    }
    return {
      key: s.site_id,
      name: s.name.replace(/ DC$/, ""),
      fig,
      figTone,
      sub: `${k ? `${k} of your job${k === 1 ? "" : "s"}` : "None of yours"} · ${m.gpus} GPUs busy${tempTight && !over ? " · temp emergency" : ""}`,
      w: m.w, c: m.c, scaleC: m.scaleC, weight: m.u / 100, active: m.gpus > 0, over,
      highlight: isSel,
    };
  });

  const routes = [];
  const drops = [];
  if (sel && o?.move) {
    routes.push({ key: `mv:${sel.id}`, to: o.move.to, kind: "applied", text: `● ${shortJobId(sel.id)} moved ✓` });
    routes.push({ key: `mf:${sel.id}`, to: o.move.from, kind: "leave", text: "moved off" });
    drops.push({ key: `mv:${sel.id}:${o.move.from}>${o.move.to}`, to: o.move.to });
  } else if (sel?.assigned_site_id && sel.status !== "completed" && sel.status !== "failed") {
    routes.push({ key: `sel:${sel.id}`, to: sel.assigned_site_id, kind: "sel", text: `${shortJobId(sel.id)} →` });
  }
  for (const p of recent) {
    routes.push({ key: `q:${p.job}`, to: p.to, kind: "place", text: `${shortJobId(p.job)} →` });
    drops.push({ key: p.key, to: p.to });
  }

  const queued = mine.filter((j) => j.status === "queued" && !j.assigned_site_id).length;
  let caption = "";
  if (sel) {
    const id = shortJobId(sel.id);
    if (o?.move) caption = `${id} moved from ${siteName(o.move.from)} to ${siteName(o.move.to)}.`;
    else if (sel.assigned_site_id) caption = `${id} runs on ${siteName(sel.assigned_site_id)}.`;
    else caption = `${id} is in the queue, waiting for a site.`;
  }
  $("mini-caption").textContent = caption;
  $("place-note").textContent = sites.length > shown.length ? `${shown.length} of ${sites.length} sites shown` : `${sites.length} site${sites.length === 1 ? "" : "s"}`;
  drawFlow($("place-flow"), {
    prefer: "one",
    hub: {
      title: "Queue",
      sub: queued ? `${queued} waiting` : "Nothing waiting",
      count: queued,
      aria: `Queue: ${queued} of your jobs waiting for a site.`,
    },
    sites: flowSites,
    routes,
    drops,
    falls: pendingFalls.splice(0),
  });
}

/** Nothing can start before "now" (the simulation clock), so keep both pickers at or after it. */
function syncDateFloor() {
  if (!clockIso) return;
  const now = toDatetimeLocalValue(clockIso);
  earliestInput.min = now;
  if (earliestInput.value && earliestInput.value < now) {
    const winMs = deadlineInput.value ? new Date(datetimeLocalToUtcIso(deadlineInput.value)) - new Date(datetimeLocalToUtcIso(earliestInput.value)) : 0;
    earliestInput.value = now;
    if (winMs > 0) deadlineInput.value = toDatetimeLocalValue(new Date(new Date(clockIso).getTime() + winMs).toISOString());
  }
  deadlineInput.min = earliestInput.value || now;
}
function startsInPast() {
  return Boolean(clockIso && earliestInput.value && earliestInput.value < toDatetimeLocalValue(clockIso));
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
  if (startsInPast()) note.textContent = "The earliest start can't be before now.";
  else if (isNaN(winH) || winH <= 0) note.textContent = "The deadline must be after the earliest start.";
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
  $("list-note").textContent = `${list.length} job${list.length === 1 ? "" : "s"}`;
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
  $("d-spec").textContent = `${job.gpu_count} GPUs · ${fmtHours(dur)} h runtime`;
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
  if (status === "failed") failed.replaceChildren(el("strong", { text: "This job failed." }), ` It stopped${placed ? ` on ${siteName(job.assigned_site_id)}` : ""} before finishing. No result was produced.`);
  if (status === "unfulfilled") failed.replaceChildren(el("strong", { text: "This job is unfulfilled." }), " The scheduler could not fit it on any eligible site before its deadline. No result was produced.");

  // journey
  let phase = 0;
  if (status === "scheduled") phase = 1;
  else if (status === "running" || status === "failed") phase = 2;
  else if (status === "completed") phase = 3;
  let prog = 0;
  if (status === "running" && clockMs != null && startMs != null) prog = Math.max(0, Math.min((clockMs - startMs) / (dur * HOUR_MS), 1));
  const lastLabel = status === "failed" ? "Failed" : status === "unfulfilled" ? "Unfulfilled" : "Complete";
  const labels = ["Submitted", "Placed", "Running", lastLabel];
  const endMs = jobEnd(job);
  const times = [
    o.firstSeen ? stampUtc(o.firstSeen) : "",
    placed ? (o.placedSeen ? stampUtc(o.placedSeen) : "") : "waiting",
    startMs != null && placed ? (phase >= 2 ? stampUtc(startMs) : `est. ${stampUtc(startMs)}`) : "—",
    job.completed_at ? stampUtc(job.completed_at) : endMs != null && placed && !bad ? `est. ${stampUtc(endMs)}` : "—",
  ];
  let progressLine = "Waiting in the queue. The scheduler has not picked a site yet.";
  if (status === "scheduled") {
    if (startMs != null && clockMs != null) {
      const inH = Math.ceil((startMs - clockMs) / HOUR_MS);
      progressLine = inH > 0 ? `Starts at ${hhmm(startMs)} UTC, in ${inH} hour${inH === 1 ? "" : "s"}.` : `Starts at ${hhmm(startMs)} UTC.`;
    } else progressLine = "Placed. Start time not set yet.";
  }
  if (status === "running") {
    progressLine =
      startMs != null && clockMs != null
        ? `${fmtHours(Math.max(0, (clockMs - startMs) / HOUR_MS))} h of ${fmtHours(dur)} h · ${Math.round(prog * 100)}%`
        : "Running.";
  }
  if (status === "completed") progressLine = `Finished · ${fmtHours(dur)} h runtime`;
  if (status === "failed") progressLine = "Stopped. No result was produced.";
  if (status === "unfulfilled") progressLine = "Not placed before the deadline.";

  const fillEl = $("j-fill");
  fillEl.className = `vjFill${status === "running" ? " fillRunV" : ""}${bad ? " isBad" : ""}`;
  const stepsEl = $("j-steps");
  stepsEl.replaceChildren(
    ...[0, 1, 2, 3].map((i) => {
      const done = i < phase || (i === phase && phase === 3);
      const cur = i === phase && phase < 3 && !bad;
      const isBadStep = bad && i === 3;
      const state = isBadStep ? "isBad" : done ? "isDone" : cur ? "isCur" : "isTodo";
      const body = [
        el("span", { className: "vjHead" }, [el("strong", { className: "vjLabel", text: labels[i] }), el("span", { className: "mono vjTime", text: times[i] })]),
      ];
      if (cur || (isBadStep && i === 3) || (phase === 3 && i === 3)) {
        const box = [el("span", { className: "vjNow", id: "runtime-progress", text: progressLine })];
        if (status === "running") {
          const bar = el("span", { className: "vjBar", "aria-hidden": "true" }, [el("span", { className: "vjBarFill fillRun" })]);
          requestAnimationFrame(() => (bar.firstChild.style.width = `${Math.round(prog * 100)}%`));
          box.push(bar);
        }
        body.push(el("span", { className: "vjNowBox" }, box));
      }
      return el("li", { className: `vjStep ${state}`, "aria-current": cur ? "step" : null }, [
        el("span", { className: `vjDot${cur ? " pulseNode" : ""}`, "aria-hidden": "true", text: isBadStep ? "✕" : done ? "✓" : "" }),
        el("span", { className: "vjBody" }, body),
      ]);
    }),
  );
  // The rail runs dot to dot; the fill stops at the current dot (plus runtime progress).
  requestAnimationFrame(() => {
    const dots = Array.from(stepsEl.querySelectorAll(".vjDot"));
    if (dots.length < 4) return;
    const top = stepsEl.parentElement.getBoundingClientRect().top;
    const cy = (d) => d.getBoundingClientRect().top + d.offsetHeight / 2 - top;
    const y = dots.map(cy);
    const rail = fillEl.parentElement;
    rail.style.top = `${y[0]}px`;
    rail.style.height = `${y[3] - y[0]}px`;
    let to = y[Math.min(phase, 3)];
    if (status === "running" && phase < 3) to += prog * (y[phase + 1] - y[phase]);
    if (status === "unfulfilled") to = y[0];
    fillEl.style.height = `${Math.max(0, to - y[0]).toFixed(1)}px`;
  });

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
  $("d-start").textContent = placed && startMs != null ? stampUtc(startMs) : "—";
  $("d-dl").textContent = stampUtc(job.deadline);
  $("act-count").textContent = `${o.events.length} event${o.events.length === 1 ? "" : "s"}`;
  $("d-site").classList.toggle("isMuted", !placed);
  const icon = $("d-icon");
  icon.className = `dIcon is${status[0].toUpperCase()}${status.slice(1)}`;

  // Grok executive summary (above download) — same .result card chrome as the CSV panel.
  const grokPanel = $("d-grok");
  const showResult = status === "completed" || !!job.result_url;
  if (grokPanel) {
    // If the summary service is not set up, leave the card out rather than show a config error.
    const showGrok = status === "completed";
    grokPanel.hidden = !showGrok;
    if (showGrok) {
      if (isGrokLoading && grokJobId === job.id) {
        $("d-grok-title").textContent = "Generating summary…";
        $("d-grok-text").textContent = "Writing a short brief of this run.";
      } else if (grokJobId === job.id && grokSummary) {
        $("d-grok-title").textContent = "Run summary";
        $("d-grok-text").textContent = grokSummary;
      } else if (grokJobId === job.id && grokError) {
        $("d-grok-title").textContent = "Summary unavailable";
        $("d-grok-text").textContent = grokError;
      } else {
        $("d-grok-title").textContent = "Run summary";
        $("d-grok-text").textContent = "Preparing summary…";
        loadGrokSummary(job.id);
      }
    }
  }

  // result / download
  const result = $("d-result");
  result.hidden = !showResult;
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

async function loadGrokSummary(jobId) {
  if (DEMO) {
    grokJobId = jobId;
    isGrokLoading = false;
    grokError = null;
    grokSummary =
      "Simulated workload finished on schedule within site water limits. " +
      "Open the sample result below for the CSV artifact from this visual demo.";
    if (selectedJobId === jobId) renderDetail();
    return;
  }
  if (isGrokLoading && grokJobId === jobId) return;
  grokJobId = jobId;
  isGrokLoading = true;
  grokSummary = "";
  grokError = null;
  if (selectedJobId === jobId) renderDetail();
  try {
    const body = await getJobSummary(jobId);
    if (grokJobId !== jobId) return;
    grokSummary = body.summary || "";
    if (!grokSummary) grokError = "Grok returned an empty summary.";
  } catch (err) {
    if (grokJobId !== jobId) return;
    grokError = err.message || "Could not load Grok summary.";
  } finally {
    if (grokJobId === jobId) isGrokLoading = false;
    if (selectedJobId === jobId) renderDetail();
  }
}

/* ---------- submit result + completion notice ---------- */
function successText(job) {
  return job.assigned_site_id
    ? `${shortJobId(job.id)} is in. Placed on ${siteName(job.assigned_site_id)}, starting ${hhmm(job.scheduled_start)} UTC.`
    : `${shortJobId(job.id)} is in. Queued while the scheduler picks a site.`;
}

function showCompletion(job, { toastIt = true } = {}) {
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
  if (toastIt) toast(`${id} completed.`);
}

function syncNotifications(notesResp) {
  if (!notesResp || DEMO) return;
  let changed = false;
  for (const n of notesResp.notifications || []) {
    if (seenNotificationIds.has(n.id)) continue;
    seenNotificationIds.add(n.id);
    changed = true;
    const job = jobs.find((j) => j.id === n.job_id);
    if (job && (!ownerId || job.owner_id === ownerId)) showCompletion(job);
  }
  if (changed) saveSeenNotices();
}

/* ---------- data ---------- */
async function fetchAll() {
  if (DEMO) return [DEMO, DEMO.sitesResponse, DEMO.jobs, null];
  const [demo, sitesResp, jobsResp, notesResp] = await Promise.all([
    getDemo(),
    getSites(),
    getJobs(),
    getNotifications(),
  ]);
  return [demo, sitesResp, jobsResp, notesResp];
}

let loadAllInFlight = false;
async function loadAll({ first = false } = {}) {
  if (loadAllInFlight && !first) return;
  loadAllInFlight = true;
  try {
    const [demo, sitesResp, jobsResp, notesResp] = await fetchAll();
    setError($("demo-error"), null);
    setError($("list-error"), null);
    clockIso = demo.current_time;
    sites = sitesResp.sites || [];
    jobs = jobsResp.jobs || [];
    if (lastRevision != null && jobsResp.revision < lastRevision) obs.clear();
    lastRevision = jobsResp.revision;
    ownerId = ownerId || jobs.find((j) => j.owner_id === "demo-user-001")?.owner_id || jobs[0]?.owner_id || null;
    if (first) {
      if (!earliestInput.value) earliestInput.value = toDatetimeLocalValue(clockIso);
      if (!deadlineInput.value) deadlineInput.value = toDatetimeLocalValue(addHoursToIso(clockIso, DEFAULT_WINDOW_HOURS));
    }
    syncDateFloor();
    for (const job of jobs) observe(job, false);
    syncNotifications(notesResp);
    if (!selectedJobId || !jobs.some((j) => j.id === selectedJobId)) selectedJobId = myJobs()[0]?.id || null;
    renderHeader();
    renderList();
    renderDetail();
    renderMini();
    updateEstimate();
    loadedOnce = true;
  } catch (err) {
    $("jobs-skeleton").hidden = true;
    const msg = err.status ? err.message : "Network error. Is the API server running?";
    setError($("list-error"), `Could not load jobs. ${msg}`);
  } finally {
    loadAllInFlight = false;
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
  deadlineInput.min = earliestInput.value || earliestInput.min;
  updateEstimate();
});
form.addEventListener("change", updateEstimate);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting) return;
  const t = TEMPLATES[selectedTemplate()];
  const winH = windowHours();
  if (startsInPast()) return showFormError(`The earliest start can't be before now (${clockText(clockIso)}).`);
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
    if (sheet.open) sheet.close();
    toast(successText(created));
    await loadAll();
  } catch (err) {
    showFormError(err.status ? err.message : "Network error. Is the API server running?");
  } finally {
    setSubmitting(false);
  }
});

function openSheet() {
  if (sheet.open) return;
  showFormError(null);
  syncDateFloor();
  updateEstimate();
  sheet.showModal();
  form.querySelector('input[name="template"]:checked')?.focus();
}
$("new-open").addEventListener("click", openSheet);
$("empty-new").addEventListener("click", openSheet);
$("new-close").addEventListener("click", () => sheet.close());
sheet.addEventListener("click", (e) => {
  if (e.target === sheet) sheet.close(); // backdrop
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
    // Sample-only: mirror Santa Clara → Dallas move from the operator demo plan.
    const j = DEMO.jobs.jobs.find((x) => x.id === "job-hackuta-0014");
    if (j && j.assigned_site_id === "us-west-1") {
      j.assigned_site_id = "us-south-central";
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
initChrome();
updateEstimate();
loadAll({ first: true }).then(startPoll);

initLayout({
  blocks: ".jobsCard, .placeCard, .detail, .stat",
  texts: ".pageTitle, .cardTitle",
  scrollers: "#jobs-body",
});
