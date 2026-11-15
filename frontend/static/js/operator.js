/* Operator — Operator.dc.html renderVals translated onto live /api data.
   GPU load and hourly water are modeled from /api/sites hourly profiles and the jobs'
   assignments; the solver's numbers come only from /api/reallocation/preview.
   Visual demo mode (?demo=1 or the toggle) reads window.AQ_DEMO and labels every panel. */

const freshDemo = () => (window.AQ_DEMO ? JSON.parse(JSON.stringify(window.AQ_DEMO)) : null);
let DEMO = isVisualDemoMode() ? freshDemo() : null;

const $ = (id) => document.getElementById(id);
const ringHost = $("fleet-flow");
const panel = $("preview-panel");
const cmpEl = $("cmp");

let sites = [];
let jobs = [];
let clockIso = null;
let revision = null;
let mode = "emergency";
let declared = null; // emergency restriction the operator submitted (or the demo scenario)
let previewState = "none"; // none | loading | unavailable | error | ready
let preview = null;
let previewBudget = null;
let previewDetail = "";
let applyUnavailable = null;
let applying = false;
let applied = null; // { preview, revision, message, beforeJobs, key }
let selSite = null;
let focusMove = true;
let instructionDirty = false;
let fieldsInitialised = false;
let advanceUnavailable = null;
let advancing = false;
let demoOutcome = "feasible";
let panelSig = "";
let pollTimer = null;
let knownJobs = null; // job id -> assigned site, from the previous load
const pendingFalls = []; // new jobs to drop into their tank (or the queue well)
const recentPlacements = []; // queued jobs the scheduler just placed: hop from the queue well

/** Water motion for real changes seen between loads: new jobs fall in, placed jobs hop over. */
function noteJobChanges(next) {
  const prev = knownJobs;
  knownJobs = new Map(next.map((j) => [j.id, j.assigned_site_id || null]));
  if (!prev) return; // first load: nothing is "new"
  for (const j of next) {
    if (!prev.has(j.id)) pendingFalls.push({ key: `f:${j.id}`, to: j.assigned_site_id || "queue" });
    else if (!prev.get(j.id) && j.assigned_site_id) {
      recentPlacements.push({ job: j.id, to: j.assigned_site_id, key: `pl:${j.id}:${Date.now()}`, until: Date.now() + 4000 });
      setTimeout(renderAll, 4100);
    }
  }
}

/* =========================== model =========================== */
const ms = (iso) => new Date(iso).getTime();

function hourList() {
  const set = new Set();
  for (const s of sites) for (const p of s.hourly_profiles || []) set.add(ms(p.hour_start));
  return Array.from(set).sort((a, b) => a - b);
}
function currentHour() {
  const t = ms(clockIso);
  return t - (t % HOUR_MS);
}
function profileAt(site, t) {
  const ps = site.hourly_profiles || [];
  if (!ps.length) return null;
  let best = ps[0];
  for (const p of ps) if (ms(p.hour_start) <= t) best = p;
  return best;
}
function gpusAt(siteId, t0, list) {
  const t1 = t0 + HOUR_MS;
  let g = 0;
  for (const j of list) {
    if (j.assigned_site_id !== siteId || !j.scheduled_start) continue;
    if (j.status === "failed" || j.status === "unfulfilled") continue;
    const s = ms(j.scheduled_start);
    const e = j.completed_at ? ms(j.completed_at) : s + j.duration_hours * HOUR_MS;
    if (s < t1 && e > t0) g += j.gpu_count;
  }
  return g;
}
function baselineCapAt(site, t) {
  const p = profileAt(site, t);
  if (!p) return null;
  if (p.baseline_water_cap_liters_per_hour != null) return p.baseline_water_cap_liters_per_hour;
  // Before the temporary overlay lands in /api/sites, the profile still holds the permanent cap.
  if (declared && declared.site_id === site.site_id && t >= ms(declared.start) && t < ms(declared.end)) {
    return p.water_cap_liters_per_hour ?? null;
  }
  return p.water_cap_liters_per_hour ?? null;
}
function capAt(site, t) {
  // Prefer caps already baked into GET /api/sites (includes active temporary emergency).
  const p = profileAt(site, t);
  let c = p?.water_cap_liters_per_hour ?? null;
  // Client-side declared restriction (pre-apply / local form) still overlays until sites refresh.
  if (declared && declared.site_id === site.site_id && t >= ms(declared.start) && t < ms(declared.end)) {
    c = c == null ? declared.water_cap_liters_per_hour : Math.min(c, declared.water_cap_liters_per_hour);
  }
  return c;
}
function isBottleneckHour(h) {
  // Red only when *job* load pushes total water to/over the active cap.
  // Facility fixed water alone (0 GPUs) is not a scheduler bottleneck — after
  // emergency reallocation empties a site, the warning clears even if fixed > temp cap.
  return h.c != null && h.w >= h.c && h.gpus > 0;
}
function hourModel(site, t, list) {
  const p = profileAt(site, t);
  const g = gpusAt(site.site_id, t, list);
  const cap = p?.gpu_capacity || 0;
  const fixed = p ? p.fixed_water_liters_per_hour : 0;
  const inc = p ? p.incremental_water_liters_per_gpu_hour * g : 0;
  return {
    t,
    gpus: g,
    u: cap ? Math.round((g / cap) * 100) : 0,
    fixed,
    inc,
    w: fixed + inc,
    c: capAt(site, t),
    scaleC: baselineCapAt(site, t),
  };
}
function siteModel(site, list) {
  // Looks forward only: this hour plus the profile hours still ahead. Hours that have
  // already passed can't breach anything any more, and must not drive what the cards show.
  const nowT = currentHour();
  const now = hourModel(site, nowT, list);
  const series = [now, ...hourList().filter((t) => t > nowT).map((t) => hourModel(site, t, list))];
  const over = series.filter((h) => isBottleneckHour(h));
  let worst = null;
  for (const h of over) if (!worst || h.w - h.c > worst.w - worst.c) worst = h;
  let peak = now;
  for (const h of series) if (h.w > peak.w) peak = h;
  return { site, series, now, overH: over.length, worst, peak };
}
function patchJobs(list, changes) {
  const byId = new Map((changes || []).map((c) => [c.job_id, c.new_placement]));
  return list.map((j) => {
    const np = byId.get(j.id);
    if (!np) return j;
    return { ...j, assigned_site_id: np.assigned_site_id ?? j.assigned_site_id, scheduled_start: np.scheduled_start ?? j.scheduled_start, status: j.status === "queued" && np.assigned_site_id ? "scheduled" : j.status };
  });
}
const siteOf = (id) => sites.find((s) => s.site_id === id);
const nameOf = (id) => (id ? siteOf(id)?.name || id : "Unassigned");

function derived() {
  const showProposed = previewState === "ready" && preview?.status === "feasible";
  const stale = showProposed && preview.input_revision !== revision;
  const infeasible = previewState === "ready" && preview?.status === "infeasible";
  const changes = showProposed ? preview.proposed_job_changes || [] : [];
  const appliedChanges = applied ? applied.preview.proposed_job_changes || [] : [];
  const models = sites.map((s) => siteModel(s, jobs));
  const propModels = showProposed ? sites.map((s) => siteModel(s, patchJobs(jobs, changes))) : null;
  const breaches = models.filter((m) => m.overH > 0);
  return { showProposed, stale, infeasible, changes, appliedChanges, models, propModels, breaches, loading: previewState === "loading" };
}

/* =========================== render =========================== */
function renderAll() {
  const D = derived();
  renderHeader();
  renderBand(D);
  renderStats(D);
  renderRing(D);
  renderDrawer(D);
  renderPanel(D);
  renderChart(D);
  renderQueue(D);
  renderCompliance(D);
}

function renderHeader() {
  $("demo-clock").textContent = clockText(clockIso);
}

/* ---------- status band ---------- */
const ICON_OK = "M8 14 L12.5 18.5 L20 10";
const ICON_ALERT = "M14 7 L14 15 M14 19.5 L14 19.6";
const ICON_X = "M9 9 L19 19 M19 9 L9 19";

let bandDismissed = null;
function renderBand(D) {
  const n = sites.length;
  let b;
  const restricted = declared ? siteOf(declared.site_id) : null;
  const rModel = restricted ? D.models.find((m) => m.site === restricted) : null;
  if (applied && previewState !== "ready") {
    const siteOk = !restricted || !rModel || rModel.overH === 0;
    const fleetOk = D.breaches.length === 0;
    const resolved = siteOk && fleetOk;
    b = {
      tone: resolved ? "done" : "alert",
      kick: declared
        ? `Temporary emergency applied · revision ${applied.revision ?? revision}`
        : `Applied · revision ${applied.revision ?? revision}`,
      title: resolved ? "Emergency mitigated." : "Plan applied — residual overage remains.",
      sub: declared
        ? `Temporary ${fmtNum(declared.water_cap_liters_per_hour)} L/h window on ${siteShort(restricted)} (${hhmm(declared.start)}–${hhmm(declared.end)} UTC) is active — not a permanent site limit. ${
            resolved
              ? "Water use is now under the temporary cap."
              : `${siteShort(restricted)} still models ${rModel?.overH || 0} h at/over the temporary cap.`
          }`
        : rModel && rModel.peak
          ? `${siteShort(restricted)} peaks at ${fmtNum(rModel.peak.w)} L/h${rModel.peak.c != null ? ` against ${fmtNum(rModel.peak.c)} L/h` : ""}.`
          : "The portal shows the new assignments.",
      dash: "0",
      icon: resolved ? ICON_OK : ICON_ALERT,
    };
  } else if (D.stale) {
    b = { tone: "alert", kick: "Stale preview", title: "The queue changed after this plan was made.", sub: `Revision ${preview.input_revision} to ${revision}. Apply is locked until you preview again.`, dash: "3 4", icon: ICON_ALERT };
  } else if (D.infeasible) {
    b = { tone: "alert", kick: "Infeasible", title: "No schedule satisfies every constraint.", sub: "No plan fits the cap, GPU capacity and deadlines within budget.", dash: "3 4", icon: ICON_X };
  } else if (D.showProposed) {
    const k = D.changes.length;
    let title = `${numberWord(k)} move${k === 1 ? "" : "s"} proposed.`;
    if (restricted && D.propModels) {
      const after = D.propModels.find((m) => m.site === restricted);
      title = k === 0
        ? (after.overH === 0 ? `No moves needed. ${siteShort(restricted)} stays under its cap.` : `No moves found. ${siteShort(restricted)} stays over its cap for ${after.overH} h.`)
        : after.overH === 0
        ? `${numberWord(k)} move${k === 1 ? "" : "s"} bring${k === 1 ? "s" : ""} ${siteShort(restricted)} back under its cap.`
        : `${numberWord(k)} move${k === 1 ? "" : "s"}; ${siteShort(restricted)} stays over its cap for ${after.overH} h.`;
    }
    b = { tone: "info", kick: "Proposed · not applied", title, sub: k === 0 ? "Nothing to apply." : "Dashed routes show the plan. Nothing changes until you apply it.", dash: "3 4", icon: ICON_OK };
  } else if (declared && restricted) {
    const over = rModel.overH > 0;
    b = {
      tone: over ? "alert" : "info",
      kick: `Temporary water restriction · ${restricted.name}`,
      title: `${fmtNum(declared.water_cap_liters_per_hour)} L/h temporary cap · ${hhmm(declared.start)}–${hhmm(declared.end)} UTC`,
      sub: over
        ? `${siteShort(restricted)} is projected at ${fmtNum(rModel.worst.w)} L/h, over the temporary cap for ${rModel.overH} hour${rModel.overH === 1 ? "" : "s"}. Preview a plan to fix it.`
        : `${siteShort(restricted)} stays under the temporary cap, peaking at ${fmtNum(rModel.peak.w)} L/h.`,
      dash: "0", icon: over ? ICON_ALERT : ICON_OK, cta: over,
    };
  } else if (D.breaches.length) {
    const m = D.breaches[0];
    b = { tone: "alert", kick: "Cap breach", title: `${m.site.name} is over its water cap.`, sub: `${fmtNum(m.worst.w)} L/h against ${fmtNum(m.worst.c)} L/h at ${hhmm(m.worst.t)} UTC, ${m.overH} hour${m.overH === 1 ? "" : "s"} over.`, dash: "0", icon: ICON_ALERT, cta: true };
  } else {
    b = { tone: "calm", kick: "All sites within caps", title: "Fleet is balanced.", sub: `${numberWord(n)} site${n === 1 ? "" : "s"}, no cap breaches.`, dash: "3 4", icon: ICON_OK };
  }
  const band = $("status-band");
  band.className = `band band--${b.tone}`;
  const key = `${b.kick}|${b.title}`;
  band.dataset.key = key;
  band.hidden = (b.tone === "calm" && advanceUnavailable == null) || bandDismissed === key;
  $("band-ring").setAttribute("stroke-dasharray", b.dash);
  $("band-icon").setAttribute("d", b.icon);
  $("band-kick").textContent = b.kick;
  $("band-title").textContent = b.title;
  $("band-sub").textContent = b.sub;
  $("band-cta").hidden = !(b.cta && previewState !== "loading" && previewState !== "ready");
  const extra = $("band-extra");
  if (advanceUnavailable != null) {
    if (!extra.firstChild) extra.replaceChildren(unavailablePanel(`Advance +1 h: ${advanceUnavailable}`));
  } else extra.replaceChildren();
}

function renderStats(D) {
  $("stat-water").textContent = fmtNum(D.models.reduce((a, m) => a + m.now.w, 0));
  $("stat-over").textContent = `${D.breaches.length} of ${sites.length}`;
  $("stat-over-icon").classList.toggle("isAlert", D.breaches.length > 0);
  const running = jobs.filter((j) => j.status === "running").length;
  const queued = jobs.filter((j) => j.status === "queued").length;
  $("stat-jobs").textContent = `${running} · ${queued}`;
}

/* ---------- tank row ---------- */
// Each tank shows the hour that matters for its site: the worst hour over its cap if it
// breaches, otherwise its peak modeled hour. Water = withdrawal, ember line = cap that hour,
// base bar = GPU load that hour, dashed line = proposed level.
function basisHour(m) {
  return m.now;
}

function renderRing(D) {
  const showApplied = applied && previewState !== "ready";
  const moved = new Set();
  D.changes.forEach((c) => {
    if (c.old_placement?.assigned_site_id) moved.add(c.old_placement.assigned_site_id);
    if (c.new_placement?.assigned_site_id) moved.add(c.new_placement.assigned_site_id);
  });
  const landSites = new Set(D.appliedChanges.map((c) => c.new_placement?.assigned_site_id).filter(Boolean));

  const flowSites = D.models.map((m, i) => {
    const s = m.site;
    const h = basisHour(m);
    const pm = D.propModels ? D.propModels[i] : null;
    const ghost = pm && moved.has(s.site_id) ? pm.now.w : null;
    const over = m.overH > 0;
    const overNow = isBottleneckHour(h);
    const shifts = ghost != null && Math.abs(ghost - h.w) > 0.5;
    const displayCap = h.scaleC != null ? h.scaleC : h.c;
    const tempTight = h.c != null && h.scaleC != null && h.c < h.scaleC - 0.5;
    let fig = displayCap != null ? `${fmtNum(h.w)} / ${fmtNum(displayCap)} L/h` : `${fmtNum(h.w)} L/h · no cap`;
    let figTone = "";
    if (overNow) {
      fig = `▲ ${fmtNum(h.w)} / ${fmtNum(h.c)} L/h`;
      figTone = "over";
    } else if (tempTight && h.gpus === 0) {
      fig = `${fmtNum(h.w)} L/h · cleared`;
      figTone = "good";
    }
    if (shifts) {
      fig = `${fmtNum(h.w)} → ${fmtNum(ghost)} L/h`;
      figTone = ghost < h.w ? "good" : "";
    }
    const mine = jobs.filter((j) => j.assigned_site_id === s.site_id);
    const runningHere = mine.filter((j) => j.status === "running").length;
    const nextHere = mine.filter((j) => j.status === "scheduled").length;
    const jobsTxt = runningHere || nextHere
      ? [runningHere ? `${runningHere} running` : "", nextHere ? `${nextHere} scheduled` : ""].filter(Boolean).join(" · ")
      : "no jobs";
    const warn = over
      ? (overNow ? ` · over cap ${m.overH} h` : ` · ▲ over cap at ${hhmm(m.worst.t)}`)
      : tempTight && h.gpus === 0 ? " · emergency cleared" : "";
    const sub = `${h.u}% GPU · ${jobsTxt}${warn}`;
    const aria =
      `${s.name}: ${fmtNum(h.w)} liters per hour of water right now` +
      (h.c != null ? ` against a cap of ${fmtNum(h.c)}` : ", no cap this hour") +
      `, ${h.u} percent GPU load, ${jobsTxt}` +
      (over ? `, over cap for ${m.overH} hour${m.overH === 1 ? "" : "s"} starting ${hhmm(m.worst.t)} UTC` : ", within cap") +
      (ghost != null ? `, proposed ${fmtNum(ghost)} liters per hour` : "") +
      ". Opens site details.";
    return {
      key: s.site_id, name: s.name.replace(/ DC$/, ""), fig, figTone, sub,
      w: h.w, c: h.c, scaleC: h.scaleC, ghost, weight: h.u / 100, active: h.u > 0, over, aria,
      selected: selSite === s.site_id,
      land: showApplied && landSites.has(s.site_id) ? applied.key : "",
      onClick: () => {
        selSite = selSite === s.site_id ? null : s.site_id;
        renderAll();
      },
    };
  });

  // Routes: one chip per destination ("○ JOB-014, JOB-009 →") and one per origin ("2 leaving").
  const routes = [];
  const drops = [];
  const real = (list) => list.filter((c) => c.new_placement?.assigned_site_id && c.old_placement?.assigned_site_id !== c.new_placement.assigned_site_id);
  const group = (list, pick) => {
    const m = new Map();
    for (const c of list) {
      const k = pick(c);
      if (!k) continue;
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(shortJobId(c.job_id));
    }
    return m;
  };
  const names = (ids) => (ids.length > 2 ? `${ids[0]} +${ids.length - 1}` : ids.join(", "));
  const addMoves = (list, kind) => {
    const mv = real(list);
    for (const [to, ids] of group(mv, (c) => c.new_placement.assigned_site_id)) {
      const text = kind === "proposed" ? `○ ${names(ids)} →${D.stale ? " · stale" : ""}` : `● ${names(ids)} ✓`;
      routes.push({ key: `${kind[0]}:${to}`, to, kind, text, stale: kind === "proposed" && D.stale });
    }
    for (const [from, ids] of group(mv, (c) => c.old_placement?.assigned_site_id)) {
      routes.push({ key: `l:${from}`, to: from, kind: "leave", text: kind === "proposed" ? `${ids.length} leaving` : `${ids.length} moved off`, stale: kind === "proposed" && D.stale });
    }
    return mv;
  };
  addMoves(D.changes, "proposed");
  if (D.infeasible) {
    const miss = (preview.deadline_outcomes || []).find((o) => !o.met_deadline);
    const job = miss ? jobs.find((j) => j.id === miss.job_id) : null;
    if (miss && job?.assigned_site_id) routes.push({ key: `x:${miss.job_id}`, to: job.assigned_site_id, kind: "infeasible", text: `✕ no site fits ${shortJobId(miss.job_id)}` });
  }
  if (showApplied) {
    for (const c of addMoves(D.appliedChanges, "applied")) drops.push({ key: `${applied.key}:${c.job_id}`, to: c.new_placement.assigned_site_id });
  }
  // A queued job the scheduler just placed runs down its route.
  const now = Date.now();
  for (const p of recentPlacements) {
    if (p.until < now) continue;
    routes.push({ key: `q:${p.job}`, to: p.to, kind: "place", text: `${shortJobId(p.job)} →` });
    drops.push({ key: p.key, to: p.to });
  }

  const queued = jobs.filter((j) => j.status === "queued" && !j.assigned_site_id).length;
  const running = jobs.filter((j) => j.status === "running").length;
  drawFlow(ringHost, {
    meter: true,
    loading: D.loading,
    hub: {
      title: "Queue",
      sub: D.loading ? "Planning…" : `${queued ? `${queued} waiting` : "Nothing waiting"} · ${running} running`,
      count: queued,
      aria: `Queue: ${queued} job${queued === 1 ? "" : "s"} waiting for a site, ${running} running across the fleet.`,
    },
    sites: flowSites,
    routes,
    drops,
    falls: pendingFalls.splice(0),
  });

  const fleet = D.models.reduce((a, m) => a + m.now.w, 0);
  $("orb-num").textContent = fmtNum(fleet);
  let sub = "Fleet water right now";
  if (D.propModels) sub = `Proposed peak ${fmtNum(D.propModels.reduce((a, m) => a + m.peak.w, 0))} L/h`;
  if (showApplied && applied.beforeJobs) {
    const before = sites.reduce((a, s) => a + siteModel(s, applied.beforeJobs).peak.w, 0);
    const after = D.models.reduce((a, m) => a + m.peak.w, 0);
    const d = after - before;
    sub = `Peak ${d <= 0 ? "−" : "+"}${fmtNum(Math.abs(d))} L/h · ${before ? `${d <= 0 ? "−" : "+"}${Math.abs((d / before) * 100).toFixed(1)}%` : "—"} after apply`;
  }
  $("orb-sub").textContent = sub;
}

/* ---------- drawer ---------- */
function renderDrawer(D) {
  const drawer = $("drawer");
  const m = D.models.find((x) => x.site.site_id === selSite);
  drawer.hidden = !m;
  if (!m) return;
  $("drawer-title").textContent = m.site.name;
  $("dr-util").textContent = `${m.now.u}%`;
  $("dr-water").textContent = `${fmtNum(m.now.w)} L/h`;
  $("dr-cap").textContent = m.now.c != null ? `${fmtNum(m.now.c)} L/h` : "No cap";
  const st = $("dr-status");
  if (m.overH > 0) st.textContent = `▲ Job load over cap by ${fmtNum(m.worst.w - m.worst.c)} L/h at ${hhmm(m.worst.t)} UTC · ${m.overH} h over`;
  else if (m.now.c != null && m.now.gpus === 0 && m.now.w > m.now.c) {
    st.textContent = `✓ Temporary emergency cleared — no scheduled GPUs; facility baseline ${fmtNum(m.now.fixed)} L/h remains`;
  } else if (m.now.c != null) st.textContent = `✓ Within cap with ${fmtNum(Math.max(0, m.now.c - m.now.w))} L/h spare`;
  else st.textContent = "✓ No hourly cap this hour";
  st.classList.toggle("isOver", m.overH > 0);
  const here = jobs.filter((j) => j.assigned_site_id === m.site.site_id && j.status !== "completed" && j.status !== "failed").map((j) => shortJobId(j.id));
  $("dr-jobs").textContent = here.join(", ") || "none";
}

/* ---------- Gemini + comparison panel ---------- */
function renderPanel(D) {
  const showApplied = applied && previewState !== "ready";
  const p = showApplied ? applied.preview : preview;
  const sig = JSON.stringify([previewState, p?.preview_id, D.stale, showApplied, applyUnavailable, applying, previewDetail, focusMove, declared && D.breaches.length > 0, sites.length]);
  if (sig === panelSig) return;
  panelSig = sig;

  const tag = $("cmp-tag");
  const setTag = (text, tone) => {
    tag.textContent = text;
    tag.className = `cmpTag${tone ? ` is${tone}` : ""}`;
    for (const t of ["Busy", "Warn", "Prop", "Done"]) cmpEl.classList.toggle(`is${t}`, t === tone);
  };
  const out = [];
  let bar = null;
  if (DEMO) out.push(visualDemoTag(true));

  if (previewState === "loading") {
    setTag("Planning", "Busy");
    out.push(el("div", { className: "stack10", "aria-busy": "true" }, [
      el("div", { className: "loadTxt", text: "Building a plan…" }),
      el("div", { className: "shim" }), el("div", { className: "shim shim88" }), el("div", { className: "shim shim64" }), el("div", { className: "shim shim120" }),
    ]));
  } else if (previewState === "unavailable") {
    setTag("Unavailable", "Warn");
    out.push(unavailablePanel(previewDetail));
    bar = planBar([editBtn("Edit request"), discardBtn("Close")]);
  } else if (previewState === "error") {
    setTag("No plan", "");
    out.push(el("div", { className: "errBox", role: "alert" }, [el("strong", { text: "Could not build a plan." }), ` ${previewDetail}`]));
    bar = planBar([editBtn("Edit request")]);
  } else if (p && (previewState === "ready" || showApplied)) {
    const feasible = p.status === "feasible";
    const stale = !showApplied && D.stale;
    if (showApplied) setTag("Applied ✓", "Done");
    else if (!feasible) setTag("✕ Infeasible", "Warn");
    else if (stale) setTag("Out of date", "Warn");
    else setTag("○ Proposed", "Prop");
    const res = previewBody(p, { feasible, stale, showApplied });
    out.push(res.body);
    bar = res.bar;
  } else {
    const restricted = declared ? siteOf(declared.site_id) : null;
    setTag(declared ? "Awaiting plan" : "No plan", "");
    const openBtn = el("button", { type: "button", className: "btn btnP", text: "New schedule" });
    openBtn.addEventListener("click", () => $("schedule-open").click());
    out.push(el("div", { className: "cmpEmpty" }, [
      el("div", { className: "cmpEmptyIn" }, [
        el("p", {
          text: restricted && D.breaches.some((m) => m.site === restricted)
            ? `${siteShort(restricted)} is over its temporary cap. Preview a plan to see the smallest set of moves that fixes it.`
            : "No plan yet. Start a new schedule with a water restriction or a cost budget, and review it here before applying.",
        }),
        openBtn,
      ]),
    ]));
  }
  const scroll = el("div", { className: "cmpScroll" }, out);
  panel.replaceChildren(scroll, ...(bar ? [bar] : []));
}

function planBar(buttons) {
  return el("div", { className: "cmpBar" }, buttons);
}
function editBtn(text) {
  const b = el("button", { type: "button", className: "btn btnGhost", id: "edit-plan-btn" }, [
    svg("svg", { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, [svg("path", { d: "M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" })]),
    el("span", { text }),
  ]);
  b.addEventListener("click", () => $("schedule-open").click());
  return b;
}
function discardBtn(text) {
  const b = el("button", { type: "button", className: "btn btnGhost", id: "discard-btn", text });
  b.addEventListener("click", onDiscard);
  return b;
}

function previewBody(p, { feasible, stale, showApplied }) {
  const body = el("div", { className: "cmpBody" });
  if (stale) {
    body.appendChild(el("div", { className: "stripeBand staleBand", role: "alert" }, [
      el("strong", { text: "The queue changed since this plan was made." }),
      " Re-run it to get a plan you can apply.",
    ]));
  }

  const mb = p.metrics_before || {};
  const ma = p.metrics_after || {};
  const addCost = (ma.total_cost_usd ?? 0) - (mb.total_cost_usd ?? 0);
  const totalJobs = (m) => (m.jobs_on_time ?? 0) + (m.jobs_late ?? 0) + (m.jobs_unfulfilled ?? 0);
  const sign = (d, f) => `${d < 0 ? "−" : d > 0 ? "+" : "±"}${f(Math.abs(d))}`;
  const pct = (a, b) => (b ? `${a - b <= 0 ? "−" : "+"}${Math.abs(((a - b) / b) * 100).toFixed(1)}%` : "");

  if (feasible) {
    // 1. the answer first: three headline numbers
    const dW = (ma.total_water_liters ?? 0) - (mb.total_water_liters ?? 0);
    const overBudget = previewBudget != null && addCost > previewBudget;
    const kpis = [
      { k: "Water", v: sign(dW, (x) => `${fmtNum(x)} L`), s: pct(ma.total_water_liters, mb.total_water_liters) || "no change", tone: dW < 0 ? "isGood" : dW > 0 ? "isBadTxt" : "" },
      { k: "Extra cost", v: sign(addCost, fmtUsd), s: previewBudget != null ? (overBudget ? `over ${fmtUsd(previewBudget)} budget` : `of ${fmtUsd(previewBudget)} budget`) : "no budget set", tone: overBudget ? "isBadTxt" : "" },
      { k: "On time", v: `${ma.jobs_on_time ?? "—"}/${totalJobs(ma)}`, s: ma.jobs_on_time === mb.jobs_on_time ? "no change" : `was ${mb.jobs_on_time ?? "—"}`, tone: ma.jobs_on_time < mb.jobs_on_time ? "isBadTxt" : ma.jobs_on_time > mb.jobs_on_time ? "isGood" : "" },
    ];
    body.appendChild(el("div", { className: `kpiRow${stale ? " dim" : ""}` }, kpis.map((x) =>
      el("div", { className: "kpi" }, [
        el("span", { className: "kpiK", text: x.k }),
        el("span", { className: `kpiV mono ${x.tone}`, text: x.v }),
        el("span", { className: "kpiS", text: x.s }),
      ]))));

    // 2. what moves, with the map highlight on the same row
    const moves = (p.proposed_job_changes || []).filter((c) => c.new_placement?.assigned_site_id && c.old_placement?.assigned_site_id !== c.new_placement.assigned_site_id);
    if (moves.length) {
      const head = el("div", { className: "secHead" }, [el("span", { className: "kick", text: `${moves.length} move${moves.length === 1 ? "" : "s"}` })]);
      if (!showApplied) {
        const t = el("button", { type: "button", className: "linkBtn", "aria-pressed": focusMove ? "true" : "false", text: focusMove ? "Hide on map" : "Show on map" });
        t.addEventListener("click", () => {
          focusMove = !focusMove;
          renderAll();
        });
        head.appendChild(t);
      }
      const list = el("ul", { className: `moveList${stale ? " dim" : ""}` });
      for (const c of moves) {
        list.appendChild(el("li", { className: `moveRow${showApplied ? " isApplied" : ""}` }, [
          el("span", { className: "mono moveId", text: shortJobId(c.job_id), title: c.job_id }),
          el("span", { className: "moveFrom", text: siteShort(nameOf(c.old_placement?.assigned_site_id)) }),
          el("span", { className: "moveArrow", "aria-label": "to", text: "→" }),
          el("span", { className: "moveTo", text: siteShort(nameOf(c.new_placement?.assigned_site_id)) }),
        ]));
      }
      body.append(head, list);
    }
  } else {
    body.appendChild(el("div", { className: "errBox" }, [el("strong", { text: "No plan fits." }), ` ${p.infeasibility_reason || "No reason was given."}`]));
  }

  // 3. the plain-language summary
  const all = String(p.explanation || "").split(/(?<=[.!?])\s+/).filter(Boolean);
  if (all.length) {
    const lines = el("div", { className: `gLines${stale ? " dim" : ""}` }, [el("span", { className: "kick", text: "Why this plan" })]);
    const per = Math.max(1, Math.ceil(all.length / 3));
    for (let i = 0, n = 0; i < all.length; i += per, n++) {
      const para = el("p", { className: "gLine rise", text: all.slice(i, i + per).join(" ") });
      para.style.animationDelay = `${(n * 0.18).toFixed(2)}s`;
      lines.appendChild(para);
    }
    body.appendChild(lines);
  }

  // 4. everything else folds away
  if (feasible || (p.deadline_outcomes || []).length) {
    const more = el("details", { className: "moreBox" }, [el("summary", null, [el("span", { text: "Full comparison" })])]);
    if (feasible) {
      const metrics = [
        { label: "Water", before: `${fmtNum(mb.total_water_liters)} L`, after: `${fmtNum(ma.total_water_liters)} L` },
        { label: "Cost", before: fmtUsd(mb.total_cost_usd), after: fmtUsd(ma.total_cost_usd) },
        { label: "Energy", before: `${fmtNum(mb.total_energy_kwh)} kWh`, after: `${fmtNum(ma.total_energy_kwh)} kWh` },
        { label: "On time", before: `${mb.jobs_on_time ?? "—"} of ${totalJobs(mb)}`, after: `${ma.jobs_on_time ?? "—"} of ${totalJobs(ma)}` },
      ];
      const table = el("div", { className: "mTable", role: "table", "aria-label": "Before and after" });
      table.appendChild(el("div", { role: "row", className: "mRow mHead" }, [el("span", { role: "columnheader", text: "" }), el("span", { role: "columnheader", text: "Before" }), el("span", { "aria-hidden": "true" }), el("span", { role: "columnheader", text: "After" })]));
      for (const m of metrics) {
        table.appendChild(el("div", { role: "row", className: "mRow" }, [
          el("div", { role: "rowheader", className: "mLabel", text: m.label }),
          el("div", { role: "cell", className: "mono mBefore", text: m.before }),
          el("div", { "aria-hidden": "true", className: "mArrow", text: "→" }),
          el("div", { role: "cell", className: "mono mAfter", text: m.after }),
        ]));
      }
      more.appendChild(table);
      if (previewBudget != null) {
        const fill = el("div", { className: "budgetFill" });
        more.appendChild(el("div", { className: "budget" }, [
          el("div", { className: "budgetHead" }, [el("span", { text: "Cost budget used" }), el("span", { className: "mono", text: `${fmtUsd(Math.max(addCost, 0))} of ${fmtUsd(previewBudget)}` })]),
          el("div", { className: "budgetRail" }, [fill]),
        ]));
        requestAnimationFrame(() => (fill.style.width = `${Math.max(0, Math.min(100, previewBudget ? (addCost / previewBudget) * 100 : 0)).toFixed(0)}%`));
      }
      const cb = p.cost_breakdown;
      if (cb) more.appendChild(el("div", { className: "mono costLine", text: `Energy ${fmtUsd(cb.energy_usd)} · water ${fmtUsd(cb.water_usd)} · late penalties ${fmtUsd(cb.miss_penalties_usd)}` }));
    }
    const outs = p.deadline_outcomes || [];
    if (outs.length) {
      const list = el("div", { className: "outList" }, [el("div", { className: "kick", text: "Deadlines" })]);
      for (const o of outs) {
        list.appendChild(el("div", { className: "outRow" }, [
          el("span", { className: "mono", text: shortJobId(o.job_id), title: o.job_id }),
          el("span", { className: o.met_deadline ? "isGood" : "isBadTxt", text: o.met_deadline ? "✓ On time" : "✕ Late" }),
          el("span", { className: "mono", text: o.projected_completion ? `done ${fullUtc(o.projected_completion)}` : "not finished" }),
        ]));
      }
      more.appendChild(list);
    }
    body.appendChild(more);
  }

  // pinned actions: always visible, no scrolling to reach them
  let bar;
  if (showApplied) {
    const first = (p.proposed_job_changes || [])[0];
    const msg = applied.message || (first ? `${shortJobId(first.job_id)} now runs on ${nameOf(first.new_placement?.assigned_site_id)}.` : "");
    body.prepend(el("div", { className: "applyToast pop", role: "status" }, [
      svg("svg", { width: 22, height: 22, viewBox: "0 0 22 22", "aria-hidden": "true" }, [svg("circle", { cx: 11, cy: 11, r: 10, fill: "var(--on-violet)" }), svg("path", { d: "M6 11.5 L9.5 15 L16 7.5", fill: "none", stroke: "var(--violet)", "stroke-width": 2.4, "stroke-linecap": "round", "stroke-linejoin": "round" })]),
      el("div", null, [el("strong", { text: "Plan applied." }), ` ${msg}`]),
    ]));
    bar = planBar([editBtn("New request")]);
  } else if (feasible) {
    const btns = [];
    if (stale) {
      const rerun = el("button", { type: "button", className: "btn btnP", text: "Re-run plan" });
      rerun.addEventListener("click", onPreview);
      btns.push(rerun);
    } else {
      const applyBtn = el("button", { type: "button", className: "btn btnP", id: "apply-btn", text: applying ? "Applying…" : "Apply schedule", disabled: applying || applyUnavailable != null });
      applyBtn.addEventListener("click", onApply);
      btns.push(applyBtn);
    }
    btns.push(editBtn("Edit"), discardBtn("Discard"));
    bar = planBar(btns);
    if (applyUnavailable != null) body.appendChild(unavailablePanel(`Apply: ${applyUnavailable}`));
  } else {
    bar = planBar([editBtn("Edit request"), discardBtn(declared ? "Back" : "Discard")]);
  }
  return { body, bar };
}

/* ---------- chart ---------- */
function renderChart(D) {
  if (!sites.length) return;
  const restricted = declared?.site_id;
  const id = selSite || restricted || D.breaches[0]?.site.site_id || sites.find((s) => (s.hourly_profiles || []).some((p) => p.water_cap_liters_per_hour != null))?.site_id || sites[0].site_id;
  const site = siteOf(id);
  const hours = hourList();
  const showApplied = applied && previewState !== "ready";
  // Solid line = live allocations (post-apply when applied). Dashed = proposed, or pre-apply ghost.
  const cur = hours.map((t) => hourModel(site, t, jobs).w);
  let prop = null;
  if (D.showProposed) prop = hours.map((t) => hourModel(site, t, patchJobs(jobs, D.changes)).w);
  else if (showApplied && applied.beforeJobs) prop = hours.map((t) => hourModel(site, t, applied.beforeJobs).w);
  const caps = hours.map((t) => capAt(site, t));
  let win = null;
  let capLabel = null;
  if (declared && declared.site_id === id) {
    const s = ms(declared.start);
    const e = ms(declared.end);
    let i0 = hours.findIndex((t) => t >= s);
    let i1 = hours.findIndex((t) => t >= e);
    if (i0 >= 0) {
      if (i1 < 0) i1 = hours.length - 1;
      win = [i0, i1];
      capLabel = `TEMP CAP ${fmtNum(declared.water_cap_liters_per_hour)} L/H`;
    }
  }
  const nowIdx = hours.length ? (ms(clockIso) - hours[0]) / HOUR_MS : null;
  $("chart-title").textContent = `${siteShort(site)} · water by hour`;
  const peak = Math.max(...cur, 0);
  const series = hours.map((t) => hourModel(site, t, jobs));
  const overN = series.filter((h) => isBottleneckHour(h)).length;
  const scaleCaps = hours.map((t) => baselineCapAt(site, t));
  const aria = `${site.name} hourly water over ${hours.length} hours, peaking at ${fmtNum(peak)} liters per hour. ` +
    (overN ? `${overN} hour${overN === 1 ? " is" : "s are"} at/over the active cap with scheduled GPU load.` : "No hour has scheduled load at/over its active cap.") +
    (prop ? ` ${showApplied ? "Pre-apply" : "Proposed"} schedule peaks at ${fmtNum(Math.max(...prop))}.` : "") +
    (declared && declared.site_id === id ? " Temporary emergency cap is highlighted." : "");
  $("water-chart").setAttribute("aria-label", aria);
  drawWaterChart($("water-chart"), {
    labels: hours.map((t) => pad2(new Date(t).getUTCHours())),
    cur,
    prop,
    caps,
    scaleCaps,
    breach: series.map((h) => isBottleneckHour(h)),
    win,
    nowIdx,
    applied: showApplied,
    capLabel,
  });
}

/* ---------- queue ---------- */
function renderQueue(D) {
  const showApplied = applied && previewState !== "ready";
  const propBy = new Map(D.changes.map((c) => [c.job_id, c]));
  const appBy = new Map(showApplied ? D.appliedChanges.map((c) => [c.job_id, c]) : []);
  const firstId = (D.changes[0] || D.appliedChanges[0])?.job_id;
  const rows = jobs.map((j) => {
    const pc = propBy.get(j.id);
    const ac = appBy.get(j.id);
    const ch = pc || ac;
    const startIso = pc?.new_placement?.scheduled_start || j.scheduled_start;
    const startMs = startIso ? ms(startIso) : null;
    const endMs = j.completed_at ? ms(j.completed_at) : startMs != null ? startMs + j.duration_hours * HOUR_MS : null;
    const slackH = endMs != null ? Math.floor((ms(j.deadline) - endMs) / HOUR_MS) : null;
    let siteCell;
    if (ch) {
      const text = `${siteShort(nameOf(ch.old_placement?.assigned_site_id)).split(" ")[0]} → ${siteShort(nameOf(ch.new_placement?.assigned_site_id)).split(" ")[0]}`;
      siteCell = pc ? el("span", { className: "mono siteChipP", text: `○ ${text}` }) : el("span", { className: "mono siteChipA pop", text: `● ${text} ✓` });
    } else siteCell = el("span", { text: j.assigned_site_id ? siteShort(nameOf(j.assigned_site_id)) : "Unassigned" });
    const tr = el("tr", null, [
      el("td", { className: "mono qId", text: shortJobId(j.id), title: j.id }),
      el("td", null, [statusPill(j.status, true)]),
      el("td", null, [siteCell]),
      el("td", { className: "mono", title: `Deadline ${fullUtc(j.deadline)} UTC` }, [
        el("span", { className: "tdFinish", text: endMs != null ? hhmm(endMs) : "—" }), " ",
        el("span", { className: "tdDl", text: `/ ${hhmm(j.deadline)}` }), " ",
        slackH != null && j.status !== "completed" ? el("span", { className: `tdSlack${slackH < 0 ? " isLate" : ""}`, text: slackH < 0 ? `${-slackH} h late` : `${slackH} h spare` }) : null,
      ]),
    ]);
    if (focusMove && j.id === firstId && (D.showProposed || showApplied)) tr.style.background = PAL.accentSoft;
    return tr;
  });
  $("jobs-body").replaceChildren(...rows);
  $("queue-note").textContent = D.showProposed ? "Dashed = proposed move" : showApplied ? "Solid = applied move" : "Current assignments";
}

/* ---------- compliance ---------- */
function renderCompliance(D) {
  const box = $("compliance");
  const rows = [];
  const showApplied = applied && previewState !== "ready";
  // After apply: always score live jobs vs active (temporary) caps — never freeze old breach flags.
  if (showApplied || !D.showProposed) {
    $("comp-kick").textContent = declared
      ? "Cap compliance · temporary emergency (live)"
      : "Cap compliance";
    for (const m of D.models) {
      if (!m.series.some((h) => h.c != null)) continue;
      const ref = m.worst || m.peak;
      const over = m.overH > 0;
      const label = over
        ? "Bottlenecked"
        : showApplied
          ? declared
            ? "Resolved under temporary cap"
            : "Compliant"
          : "Within cap";
      rows.push([
        m.site.name,
        `${fmtNum(ref.w)} / ${ref.c != null ? fmtNum(ref.c) : "—"} L/h · ${m.overH} h at/over`,
        over ? "▲" : "✓",
        label,
        over,
      ]);
    }
  } else if (preview?.per_site_water?.length) {
    $("comp-kick").textContent = "Cap compliance · with plan";
    for (const sw of preview.per_site_water) {
      const over = sw.water_cap_violations > 0;
      rows.push([nameOf(sw.site_id), `${fmtNum(sw.water_liters)} L · ${sw.water_cap_violations} h over`, over ? "▲" : "✓", over ? "Over cap" : "Within cap", over]);
    }
  } else {
    $("comp-kick").textContent = "Cap compliance";
    for (const m of D.models) {
      if (!m.series.some((h) => h.c != null)) continue;
      const ref = m.worst || m.peak;
      const over = m.overH > 0;
      const fixed = over && D.propModels && D.propModels.find((x) => x.site === m.site).overH === 0;
      rows.push([m.site.name, `${fmtNum(ref.w)} / ${ref.c != null ? fmtNum(ref.c) : "—"} L/h · ${m.overH} h over`, over && !fixed ? "▲" : "✓", fixed ? "Fixed if applied" : over ? "Over cap" : "Within cap", over && !fixed]);
    }
  }
  if (!rows.length) rows.push(["No capped sites", "No site reports a water cap in its hourly profile.", "✓", "Nothing to check", false]);
  box.replaceChildren(
    ...rows.map(([site, detail, icon, text, bad]) =>
      el("div", { className: "compRow" }, [
        el("span", { className: "compSite", text: site }),
        el("span", { className: "mono compDetail", text: detail }),
        el("span", { className: `compText${bad ? " isOver" : ""}`, text: `${icon} ${text}` }),
      ]),
    ),
  );
}

/* =========================== command panel =========================== */
function setMode(m) {
  mode = m;
  $("mode-routine").setAttribute("aria-pressed", m === "routine" ? "true" : "false");
  $("mode-emergency").setAttribute("aria-pressed", m === "emergency" ? "true" : "false");
  $("panel-routine").hidden = m !== "routine";
  $("panel-emergency").hidden = m !== "emergency";
  if (!instructionDirty) $("instruction").value = defaultInstruction();
}
function defaultInstruction() {
  if (mode === "routine") return "Find the cheapest schedule that keeps every site under its normal water cap and meets all deadlines.";
  const s = siteOf($("emer-site").value);
  const cap = $("emer-cap").value || "?";
  const st = $("emer-start").value;
  const en = $("emer-end").value;
  return `${siteShort(s) || "The site"} has a water restriction. Keep it under ${cap} L/h from ${st ? st.slice(11) : "?"} to ${en ? en.slice(11) : "?"} UTC and do not miss any deadlines.`;
}
function initFields() {
  const sel = $("emer-site");
  const keep = sel.value;
  sel.replaceChildren(...sites.map((s) => el("option", { value: s.site_id, text: s.name })));
  if (keep && sites.some((s) => s.site_id === keep)) sel.value = keep;
  if (!fieldsInitialised && clockIso) {
    $("emer-start").value = toDatetimeLocalValue(addHoursToIso(clockIso, 2));
    $("emer-end").value = toDatetimeLocalValue(addHoursToIso(clockIso, 8));
    fieldsInitialised = true;
    setMode(mode);
  }
}
function fillFieldsFrom(r) {
  $("emer-site").value = r.site_id;
  $("emer-cap").value = String(r.water_cap_liters_per_hour);
  $("emer-start").value = toDatetimeLocalValue(r.start);
  $("emer-end").value = toDatetimeLocalValue(r.end);
  if (r.instruction) {
    $("instruction").value = r.instruction;
    instructionDirty = true;
  }
  setMode("emergency");
}

function buildRequest() {
  const body = { mode, natural_language_instruction: $("instruction").value.trim() || null };
  if (mode === "routine") {
    const b = $("budget").value;
    if (b !== "") body.max_additional_cost_usd = Number(b);
    return { body, restriction: null, budget: b !== "" ? Number(b) : null };
  }
  const start = datetimeLocalToUtcIso($("emer-start").value);
  const end = datetimeLocalToUtcIso($("emer-end").value);
  const cap = Number($("emer-cap").value);
  if (!start || !end) throw new Error("Set both a start and an end for the restriction.");
  if (new Date(end) <= new Date(start)) throw new Error("The restriction end must be after its start.");
  if (!Number.isFinite(cap) || cap < 0) throw new Error("The cap must be zero or more liters per hour.");
  const restriction = { site_id: $("emer-site").value, water_cap_liters_per_hour: cap, start, end };
  body.emergency_restriction = restriction;
  return { body, restriction, budget: null };
}

async function onPreview() {
  if (previewState === "loading") return;
  setError($("cmd-error"), null);
  let req;
  try {
    req = buildRequest();
  } catch (err) {
    setError($("cmd-error"), err.message);
    openSheet();
    return;
  }
  if (sheet.open) sheet.close();
  if (req.restriction) declared = req.restriction;
  previewState = "loading";
  preview = null;
  applyUnavailable = null;
  updatePreviewButton();
  renderAll();
  if (DEMO) {
    // Sample data only: the outcome is whichever the demo director selected.
    await new Promise((r) => setTimeout(r, prefersReducedMotion() ? 0 : 1200));
    preview = DEMO.previews[demoOutcome];
    previewBudget = req.budget ?? DEMO.budgets[demoOutcome];
    previewState = "ready";
    applied = null;
  } else {
    try {
      const res = await previewReallocation(req.body);
      if (res.unavailable) {
        previewState = "unavailable";
        previewDetail = res.detail;
      } else {
        preview = res;
        previewBudget = req.budget;
        previewState = "ready";
        applied = null;
      }
    } catch (err) {
      previewState = "error";
      previewDetail = err.status ? err.message : "Network error. Is the API server running?";
    }
  }
  focusMove = true;
  updatePreviewButton();
  renderAll();
}

function onDiscard() {
  preview = null;
  previewState = "none";
  applyUnavailable = null;
  updatePreviewButton();
  renderAll();
}

async function onApply() {
  if (!preview || applying || preview.input_revision !== revision) return;
  applying = true;
  renderAll();
  const before = JSON.parse(JSON.stringify(jobs));
  let res;
  if (DEMO) {
    // Sample data only: move the demo jobs the way the sample plan says.
    DEMO.jobs.jobs = patchJobs(DEMO.jobs.jobs, preview.proposed_job_changes);
    DEMO.jobs.revision += 1;
    DEMO.revision = DEMO.jobs.revision;
    res = {
      applied: true,
      revision: DEMO.jobs.revision,
      message: declared
        ? `Applied temporary emergency cap on ${declared.site_id} (visual demo).`
        : "Applied (visual demo).",
      temporary_restriction: declared
        ? {
            site_id: declared.site_id,
            water_cap_liters_per_hour: declared.water_cap_liters_per_hour,
            start: declared.start,
            end: declared.end,
            temporary: true,
          }
        : null,
    };
  } else {
    try {
      res = await applyReallocation({ preview_id: preview.preview_id, input_revision: preview.input_revision });
    } catch (err) {
      res = { error: err.status ? err.message : "Network error. Is the API server running?" };
    }
  }
  applying = false;
  if (res.unavailable || res.error) {
    applyUnavailable = res.unavailable ? res.detail : null;
    if (res.error) toast(`Could not apply. ${res.error}`);
    renderAll();
    return;
  }
  applied = { preview, revision: res.revision, message: res.message || "", beforeJobs: before, key: String(Date.now()) };
  preview = null;
  previewState = "none";
  // Keep / refresh the temporary emergency window so caps stay active after apply.
  if (res.temporary_restriction) {
    declared = {
      site_id: res.temporary_restriction.site_id,
      water_cap_liters_per_hour: res.temporary_restriction.water_cap_liters_per_hour,
      start: res.temporary_restriction.start,
      end: res.temporary_restriction.end,
    };
  }
  updatePreviewButton();
  toast(res.message || `Plan applied · revision ${res.revision}.`);
  // Re-fetch jobs + sites (sites now include the temporary emergency water caps).
  await loadAll();
}

function updatePreviewButton() {
  const b = $("preview-btn");
  b.disabled = previewState === "loading";
  b.textContent = previewState === "loading" ? "Solving…" : previewState === "ready" ? "Preview again" : "Preview schedule";
}

/* =========================== data =========================== */
async function fetchAll() {
  if (DEMO) return [DEMO, DEMO.sitesResponse, DEMO.jobs];
  return Promise.all([getDemo(), getSites(), getJobs()]);
}

async function loadAll() {
  try {
    const [demo, sitesResp, jobsResp] = await fetchAll();
    setError($("op-error"), null);
    clockIso = demo.current_time;
    sites = sitesResp.sites || [];
    // Server-active temporary emergency (after apply) drives caps + band copy.
    if (sitesResp.temporary_restriction) {
      const tr = sitesResp.temporary_restriction;
      declared = {
        site_id: tr.site_id,
        water_cap_liters_per_hour: tr.water_cap_liters_per_hour,
        start: tr.start,
        end: tr.end,
      };
      if ($("emer-site") && [...$("emer-site").options].some((o) => o.value === tr.site_id)) {
        fillFieldsFrom(declared);
      }
    }
    const next = jobsResp.jobs || [];
    noteJobChanges(next);
    jobs = next;
    revision = jobsResp.revision;
    initFields();
    renderAll();
  } catch (err) {
    setError($("op-error"), `Could not load the fleet. ${err.status ? err.message : "Network error. Is the API server running?"}`);
  }
}

/* =========================== wiring =========================== */
$("brand").prepend(logoSvg());
initChrome();
$("mode-routine").addEventListener("click", () => setMode("routine"));
$("mode-emergency").addEventListener("click", () => setMode("emergency"));
for (const id of ["emer-site", "emer-cap", "emer-start", "emer-end"]) {
  $(id).addEventListener("input", () => {
    if (!instructionDirty) $("instruction").value = defaultInstruction();
  });
}
$("instruction").addEventListener("input", () => (instructionDirty = true));
$("preview-btn").addEventListener("click", onPreview);
const sheet = $("schedule-sheet");
function openSheet() {
  if (!sheet.open) sheet.showModal();
}
$("schedule-open").addEventListener("click", openSheet);
$("schedule-close").addEventListener("click", () => sheet.close());
sheet.addEventListener("click", (e) => {
  if (e.target === sheet) sheet.close(); // backdrop
});
$("band-cta").addEventListener("click", onPreview);
$("band-close").addEventListener("click", () => {
  const band = $("status-band");
  bandDismissed = band.dataset.key; // stays hidden until the message changes
  band.classList.add("isLeaving");
  setTimeout(() => (band.hidden = true), prefersReducedMotion() ? 0 : 260);
});
$("drawer-close").addEventListener("click", () => {
  selSite = null;
  renderAll();
});

async function onAdvance() {
  if (advancing) return;
  const hours = Number($("advance-hours")?.value || 1);
  if (DEMO) {
    DEMO.current_time = addHoursToIso(DEMO.current_time, hours);
    return loadAll();
  }
  advancing = true;
  advanceUnavailable = null;
  $("advance-btn").disabled = true;
  if ($("advance-hours")) $("advance-hours").disabled = true;
  renderAll();
  try {
    await advanceDemo({ hours, expected_revision: revision });
    if (preview && preview.input_revision !== revision) {
      preview = null;
      previewState = "none";
      applied = null;
      applyUnavailable = null;
    }
    await loadAll();
    toast(`Clock advanced ${hours} h · revision ${revision}.`);
  } catch (err) {
    toast(`Could not advance. ${err.message}`);
  } finally {
    advancing = false;
    $("advance-btn").disabled = false;
    if ($("advance-hours")) $("advance-hours").disabled = false;
    renderAll();
  }
}
function updateAdvanceButtonLabel() {
  const h = Number($("advance-hours")?.value || 1);
  const label = Number.isFinite(h) && h > 0 ? `Advance +${h} h` : "Advance";
  $("advance-btn").textContent = label;
}
if ($("advance-hours")) {
  $("advance-hours").addEventListener("change", updateAdvanceButtonLabel);
}
updateAdvanceButtonLabel();
$("advance-btn").addEventListener("click", onAdvance);

$("reset-btn").addEventListener("click", async () => {
  try {
    if (DEMO) DEMO = freshDemo();
    else await resetDemo();
    advanceUnavailable = null;
    declared = null;
    preview = null;
    previewState = "none";
    applied = null;
    applyUnavailable = null;
    selSite = null;
    setDirectorPressed(null, "d-routine");
    updatePreviewButton();
    toast(DEMO ? "Sample data reset." : "Demo reset.");
    await loadAll();
  } catch (err) {
    toast(`Could not reset. ${err.message}`);
  }
});

const toggle = $("visual-demo-toggle");
toggle.setAttribute("aria-pressed", DEMO ? "true" : "false");
toggle.addEventListener("click", () => setVisualDemoMode(!DEMO));

function setDirectorPressed(group, id) {
  const groups = { scenario: ["d-routine", "d-emergency"], outcome: ["d-feasible", "d-stale", "d-infeasible"] };
  for (const [g, ids] of Object.entries(groups)) {
    if (group && g !== group) continue;
    for (const x of ids) $(x).setAttribute("aria-pressed", x === id ? "true" : "false");
  }
}

if (DEMO) {
  $("director").hidden = false;
  $("director-tag").replaceChildren(visualDemoTag());
  $("ring-tag").replaceChildren(visualDemoTag(true));
  for (const id of ["nav-portal", "nav-operator"]) $(id).href += "?demo=1";
  $("d-routine").addEventListener("click", () => {
    declared = null;
    preview = null;
    previewState = "none";
    applied = null;
    setDirectorPressed(null, "d-routine");
    setMode("routine");
    updatePreviewButton();
    renderAll();
  });
  $("d-emergency").addEventListener("click", () => {
    declared = { ...DEMO.emergency };
    fillFieldsFrom(DEMO.emergency);
    preview = null;
    previewState = "none";
    applied = null;
    setDirectorPressed("scenario", "d-emergency");
    setDirectorPressed("outcome", null);
    updatePreviewButton();
    renderAll();
  });
  for (const k of ["feasible", "stale", "infeasible"]) {
    $(`d-${k}`).addEventListener("click", () => {
      demoOutcome = k;
      if (!declared) {
        declared = { ...DEMO.emergency };
        fillFieldsFrom(DEMO.emergency);
      }
      setDirectorPressed("scenario", "d-emergency");
      setDirectorPressed("outcome", `d-${k}`);
      preview = DEMO.previews[k];
      previewBudget = DEMO.budgets[k];
      previewState = "ready";
      applied = null;
      applyUnavailable = null;
      focusMove = true;
      updatePreviewButton();
      renderAll();
    });
  }
}

/* Poll /api/jobs and /api/demo so a job submitted in the portal makes an open preview stale. */
function startPoll() {
  if (DEMO) return;
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (document.visibilityState === "visible" && previewState !== "loading" && !applying) loadAll();
  }, 4000);
}

loadAll().then(startPoll);

initLayout({
  blocks: ".fleetCard, .cmp, .chartSec, .queueSec, .stat",
  texts: ".pageTitle, .cardTitle",
  scrollers: ".qScroll",
});
