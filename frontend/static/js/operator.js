/* Operator — Operator.dc.html renderVals translated onto live /api data.
   GPU load and hourly water are modeled from /api/sites hourly profiles and the jobs'
   assignments; the solver's numbers come only from /api/reallocation/preview.
   Visual demo mode (?demo=1 or the toggle) reads window.AQ_DEMO and labels every panel. */

const freshDemo = () => (window.AQ_DEMO ? JSON.parse(JSON.stringify(window.AQ_DEMO)) : null);
let DEMO = isVisualDemoMode() ? freshDemo() : null;

const $ = (id) => document.getElementById(id);
const ringSvg = $("fleet-ring");
const ringOverlay = $("ring-overlay");
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
function capAt(site, t) {
  const p = profileAt(site, t);
  let c = p?.water_cap_liters_per_hour ?? null;
  if (declared && declared.site_id === site.site_id && t >= ms(declared.start) && t < ms(declared.end)) {
    c = c == null ? declared.water_cap_liters_per_hour : Math.min(c, declared.water_cap_liters_per_hour);
  }
  return c;
}
function hourModel(site, t, list) {
  const p = profileAt(site, t);
  const g = gpusAt(site.site_id, t, list);
  const cap = p?.gpu_capacity || 0;
  return {
    t,
    gpus: g,
    u: cap ? Math.round((g / cap) * 100) : 0,
    w: p ? p.fixed_water_liters_per_hour + p.incremental_water_liters_per_gpu_hour * g : 0,
    c: capAt(site, t),
  };
}
function siteModel(site, list) {
  const hours = hourList();
  const series = hours.map((t) => hourModel(site, t, list));
  const now = hourModel(site, currentHour(), list);
  const over = series.filter((h) => h.c != null && h.w > h.c);
  let worst = null;
  for (const h of over) if (!worst || h.w - h.c > worst.w - worst.c) worst = h;
  let peak = series[0] || now;
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
  $("sim-banner").textContent = `SIMULATED · ${sites.length} SITE${sites.length === 1 ? "" : "S"} · NO REAL GPUS`;
}

/* ---------- status band ---------- */
const ICON_OK = "M8 14 L12.5 18.5 L20 10";
const ICON_ALERT = "M14 7 L14 15 M14 19.5 L14 19.6";
const ICON_X = "M9 9 L19 19 M19 9 L9 19";

function renderBand(D) {
  const n = sites.length;
  let b;
  const restricted = declared ? siteOf(declared.site_id) : null;
  const rModel = restricted ? D.models.find((m) => m.site === restricted) : null;
  if (applied && previewState !== "ready") {
    b = {
      tone: "done", kick: `Applied · revision ${applied.revision ?? revision}`,
      title: D.breaches.length ? "Plan applied." : "Equilibrium restored.",
      sub: rModel && rModel.peak ? `${siteShort(restricted)} peaks at ${fmtNum(rModel.peak.w)} L/h${rModel.peak.c != null ? ` against a ${fmtNum(rModel.peak.c)} L/h cap` : ""} (modeled). The portal shows the new assignments.` : "The portal shows the new assignments.",
      dash: "0", icon: ICON_OK,
    };
  } else if (D.stale) {
    b = { tone: "alert", kick: "Stale preview", title: "The queue changed after this plan was made.", sub: `Revision ${preview.input_revision} to ${revision}. Apply is locked until you preview again.`, dash: "3 4", icon: ICON_ALERT };
  } else if (D.infeasible) {
    b = { tone: "alert", kick: "Infeasible", title: "No schedule satisfies every constraint.", sub: "The solver found no plan that respects the cap, GPU capacity and deadlines within budget.", dash: "3 4", icon: ICON_X };
  } else if (D.showProposed) {
    const k = D.changes.length;
    let title = `${numberWord(k)} move${k === 1 ? "" : "s"} proposed.`;
    if (restricted && D.propModels) {
      const after = D.propModels.find((m) => m.site === restricted);
      title = after.overH === 0
        ? `${numberWord(k)} move${k === 1 ? "" : "s"} bring${k === 1 ? "s" : ""} ${siteShort(restricted)} back under its cap.`
        : `${numberWord(k)} move${k === 1 ? "" : "s"}; ${siteShort(restricted)} stays over its cap for ${after.overH} h (modeled).`;
    }
    b = { tone: "info", kick: "Proposed · not applied", title, sub: "Dashed arcs are a preview. Nothing has changed in the portal yet.", dash: "3 4", icon: ICON_OK };
  } else if (declared && restricted) {
    const over = rModel.overH > 0;
    b = {
      tone: over ? "alert" : "info",
      kick: `Water restriction · ${restricted.name}`,
      title: `${fmtNum(declared.water_cap_liters_per_hour)} L/h cap from ${hhmm(declared.start)} to ${hhmm(declared.end)} UTC`,
      sub: over
        ? `${siteShort(restricted)} is projected at ${fmtNum(rModel.worst.w)} L/h (modeled), over the cap for ${rModel.overH} hour${rModel.overH === 1 ? "" : "s"}.`
        : `${siteShort(restricted)} stays within it at a modeled peak of ${fmtNum(rModel.peak.w)} L/h.`,
      dash: "0", icon: over ? ICON_ALERT : ICON_OK, cta: over,
    };
  } else if (D.breaches.length) {
    const m = D.breaches[0];
    b = { tone: "alert", kick: "Cap breach · modeled", title: `${m.site.name} is over its water cap.`, sub: `${fmtNum(m.worst.w)} L/h against ${fmtNum(m.worst.c)} L/h at ${hhmm(m.worst.t)} UTC, ${m.overH} hour${m.overH === 1 ? "" : "s"} over.`, dash: "0", icon: ICON_ALERT, cta: true };
  } else {
    b = { tone: "calm", kick: "All sites within caps", title: "Fleet is balanced.", sub: `${numberWord(n)} simulated site${n === 1 ? "" : "s"}, no cap breaches (modeled).`, dash: "3 4", icon: ICON_OK };
  }
  const band = $("status-band");
  band.className = `band band--${b.tone}`;
  band.hidden = b.tone === "calm" && advanceUnavailable == null;
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
  return m.overH > 0 ? m.worst : m.peak || m.now;
}

function renderRing(D) {
  const showApplied = applied && previewState !== "ready";
  const moved = new Set();
  D.changes.forEach((c) => {
    if (c.old_placement?.assigned_site_id) moved.add(c.old_placement.assigned_site_id);
    if (c.new_placement?.assigned_site_id) moved.add(c.new_placement.assigned_site_id);
  });
  const landSites = new Set(D.appliedChanges.map((c) => c.new_placement?.assigned_site_id).filter(Boolean));

  let top = 1;
  const tankSites = D.models.map((m, i) => {
    const s = m.site;
    const h = basisHour(m);
    const pm = D.propModels ? D.propModels[i] : null;
    const ghostW = pm && moved.has(s.site_id) ? (pm.series.find((x) => x.t === h.t) || pm.peak).w : null;
    const over = m.overH > 0;
    top = Math.max(top, h.w, h.c ?? 0, ghostW ?? 0);
    let sub = h.c != null ? `${fmtNum(h.w)} / ${fmtNum(h.c)} L/h` : `${fmtNum(h.w)} L/h · no cap`;
    if (ghostW != null && Math.abs(ghostW - h.w) > 0.5) sub = `${fmtNum(h.w)} → ${fmtNum(ghostW)} L/h`;
    if (over) sub = `▲ ${fmtNum(h.w)} / ${fmtNum(h.c)} · ${m.overH} h over`;
    // compact figure for the label row (units are in the legend)
    let fig = h.c != null ? `${fmtNum(h.w)} / ${fmtNum(h.c)}` : `${fmtNum(h.w)} · no cap`;
    if (ghostW != null && Math.abs(ghostW - h.w) > 0.5) fig = `${fmtNum(h.w)} → ${fmtNum(ghostW)}`;
    if (over) fig = `▲ ${fmtNum(h.w)} / ${fmtNum(h.c)}`;
    const isSel = selSite === s.site_id;
    const aria =
      `${s.name}: peak modeled withdrawal ${fmtNum(h.w)} liters per hour at ${hhmm(h.t)} UTC` +
      (h.c != null ? ` against a cap of ${fmtNum(h.c)}` : ", no cap that hour") +
      `, ${h.u} percent GPU load from assignments` +
      (over ? `, over cap for ${m.overH} modeled hour${m.overH === 1 ? "" : "s"}` : ", within cap") +
      (ghostW != null ? `, proposed ${fmtNum(ghostW)} liters per hour` : "") +
      ". Simulated.";
    return {
      key: s.site_id, short: siteShort(s), w: h.w, c: h.c, ghostW, u: h.u, over, sub, fig, aria,
      selected: isSel,
      land: showApplied && landSites.has(s.site_id) ? applied.key : "",
      onClick: () => {
        selSite = selSite === s.site_id ? null : s.site_id;
        renderAll();
      },
    };
  });

  const arcs = [];
  const chips = [];
  const drops = [];
  const real = (list) => list.filter((c) => c.new_placement?.assigned_site_id && c.old_placement?.assigned_site_id !== c.new_placement.assigned_site_id);
  // One chip per state keeps the row calm: it labels the first move and counts the rest.
  const more = (list) => (list.length > 1 ? ` · +${list.length - 1} more` : "");

  const moving = real(D.changes);
  moving.forEach((c, k) => {
    const key = `p:${c.job_id}`;
    arcs.push({ key, from: c.old_placement?.assigned_site_id || "queue", to: c.new_placement.assigned_site_id, kind: "proposed", stale: D.stale, focus: focusMove && k === 0 });
    if (k === 0) chips.push({ key: `c:${c.job_id}`, arc: key, text: `○ ${shortJobId(c.job_id)} · PROPOSED${D.stale ? " · STALE" : ""}${more(moving)}`, bg: "#FFFFFF", fg: "#0F6E76", bc: "#0F6E76", bstyle: "dashed" });
  });
  if (D.infeasible) {
    const miss = (preview.deadline_outcomes || []).find((o) => !o.met_deadline);
    if (miss) {
      const job = jobs.find((j) => j.id === miss.job_id);
      const key = `x:${miss.job_id}`;
      arcs.push({ key, from: job?.assigned_site_id || "queue", to: null, kind: "infeasible" });
      chips.push({ key, arc: key, text: `✕ no site fits ${shortJobId(miss.job_id)}`, bg: "#FFF3EA", fg: "#B4410F", bc: "#B4410F", bstyle: "solid" });
    }
  }
  if (showApplied) {
    const done = real(D.appliedChanges);
    done.forEach((c, k) => {
      const key = `a:${c.job_id}`;
      arcs.push({ key, from: c.old_placement?.assigned_site_id || "queue", to: c.new_placement.assigned_site_id, kind: "applied" });
      if (k === 0) chips.push({ key: `c:${c.job_id}`, arc: key, text: `● ${shortJobId(c.job_id)} · APPLIED ✓${more(done)}`, bg: "#0B4F58", fg: "#FFFFFF", bc: "#FFFFFF", bstyle: "solid" });
      drops.push({ key: `${applied.key}:${c.job_id}`, arc: key });
    });
  }
  // A queued job that the scheduler just placed hops from the queue well into its tank.
  const now = Date.now();
  for (const p of recentPlacements) {
    if (p.until < now) continue;
    const key = `q:${p.job}`;
    arcs.push({ key, from: "queue", to: p.to, kind: "applied" });
    drops.push({ key: `${p.key}`, arc: key });
  }

  const queued = jobs.filter((j) => j.status === "queued" && !j.assigned_site_id).length;
  drawTanks(ringSvg, ringOverlay, {
    loading: D.loading,
    scaleMax: niceCeil(top * 1.12),
    sites: tankSites,
    queue: { count: queued },
    arcs, chips, drops,
    falls: pendingFalls.splice(0),
  });

  const fleet = D.models.reduce((a, m) => a + m.now.w, 0);
  $("orb-num").textContent = fmtNum(fleet);
  let sub = "modeled now";
  if (D.propModels) sub = `proposed peak ${fmtNum(D.propModels.reduce((a, m) => a + m.peak.w, 0))} L/h`;
  if (showApplied && applied.beforeJobs) {
    const before = sites.reduce((a, s) => a + siteModel(s, applied.beforeJobs).peak.w, 0);
    const after = D.models.reduce((a, m) => a + m.peak.w, 0);
    const d = after - before;
    sub = `peak ${d <= 0 ? "−" : "+"}${fmtNum(Math.abs(d))} L/h · ${before ? `${d <= 0 ? "−" : "+"}${Math.abs((d / before) * 100).toFixed(1)}%` : "—"} after apply`;
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
  if (m.overH > 0) st.textContent = `▲ Over cap by ${fmtNum(m.worst.w - m.worst.c)} L/h at ${hhmm(m.worst.t)} UTC · ${m.overH} h over (modeled)`;
  else if (m.now.c != null) st.textContent = `✓ Within cap with ${fmtNum(m.now.c - m.now.w)} L/h spare`;
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
  const setTag = (text, bg, fg, bstyle, bcolor) => {
    tag.textContent = text;
    tag.style.background = bg;
    tag.style.color = fg;
    tag.style.border = `2px ${bstyle} ${bcolor}`;
    cmpEl.style.border = `2px ${bstyle} ${bcolor}`;
  };
  const out = [];
  if (DEMO) out.push(visualDemoTag(true));

  if (previewState === "loading") {
    setTag("Solving", "#E7F4F3", "#0F6E76", "dashed", "#2A9D9F");
    out.push(el("div", { className: "stack10", "aria-busy": "true" }, [
      el("div", { className: "mono loadTxt", text: "Gemini read your request · SciPy solver running…" }),
      el("div", { className: "shim" }), el("div", { className: "shim shim88" }), el("div", { className: "shim shim64" }), el("div", { className: "shim shim120" }),
    ]));
  } else if (previewState === "unavailable") {
    setTag("Unavailable", "#FFF3EA", "#B4410F", "dashed", "#E4A57F");
    out.push(unavailablePanel(previewDetail));
  } else if (previewState === "error") {
    setTag("No preview", "#EEF3F3", "#5A6B72", "solid", "transparent");
    out.push(el("div", { className: "errBox", role: "alert" }, [el("strong", { text: "Could not preview." }), ` ${previewDetail}`]));
  } else if (p && (previewState === "ready" || showApplied)) {
    const feasible = p.status === "feasible";
    const stale = !showApplied && D.stale;
    if (showApplied) setTag("Applied ✓", "#0F6E76", "#FFFFFF", "solid", "#0F6E76");
    else if (!feasible) setTag("✕ Infeasible", "#FFF3EA", "#B4410F", "dashed", "#E4A57F");
    else if (stale) setTag("Stale preview", "#FFF3EA", "#B4410F", "dashed", "#E4A57F");
    else setTag("○ Proposed", "#FFFFFF", "#0F6E76", "dashed", "#2A9D9F");
    out.push(previewBody(p, { feasible, stale, showApplied }));
  } else {
    const restricted = declared ? siteOf(declared.site_id) : null;
    setTag(declared ? "Awaiting preview" : "No preview", "#EEF3F3", "#5A6B72", "solid", "transparent");
    out.push(el("div", {
      className: "cmpEmpty",
      text: restricted && D.breaches.some((m) => m.site === restricted)
        ? `${siteShort(restricted)} is over its temporary cap (modeled). Preview a plan and Gemini will propose the smallest set of moves, then show the cost and water before and after.`
        : "No preview yet. Ask for a schedule above. Gemini will explain what the solver returns, and the full comparison appears here before anything is applied.",
    }));
  }
  panel.replaceChildren(...out);
}

function previewBody(p, { feasible, stale, showApplied }) {
  const body = el("div", { className: "cmpBody" });
  if (stale) {
    body.appendChild(el("div", { className: "stripeBand staleBand", role: "alert" }, [
      el("strong", { text: "This preview is out of date." }),
      ` The queue changed after you previewed (revision ${p.input_revision} to ${revision}). Apply is locked. Run the preview again to get a plan that matches the queue.`,
    ]));
  }
  // Group Gemini's sentences into at most three lines (the design's 180 ms staggered reveal).
  const all = String(p.explanation || "").split(/(?<=[.!?])\s+/).filter(Boolean);
  const per = Math.max(1, Math.ceil(all.length / 3));
  const sentences = [];
  for (let i = 0; i < all.length; i += per) sentences.push(all.slice(i, i + per).join(" "));
  const lines = el("div", { className: `gLines${stale ? " dim" : ""}` });
  sentences.forEach((s, i) => {
    const para = el("p", { className: "gLine rise", text: s });
    para.style.animationDelay = `${(i * 0.18).toFixed(2)}s`;
    lines.appendChild(para);
  });
  const firstMove = (p.proposed_job_changes || [])[0];
  if (feasible && firstMove && !showApplied) {
    const t = el("button", { type: "button", className: "chip", text: focusMove ? `Hide ${shortJobId(firstMove.job_id)} highlight` : `Highlight ${shortJobId(firstMove.job_id)} move` });
    t.addEventListener("click", () => {
      focusMove = !focusMove;
      renderAll();
    });
    lines.appendChild(el("div", { className: "chipRow" }, [t]));
  }
  lines.appendChild(el("div", { className: "gNote", text: "Gemini wrote this from the solver's returned plan. The numbers below are the solver's." }));
  body.appendChild(lines);

  if (feasible) {
    const mb = p.metrics_before || {};
    const ma = p.metrics_after || {};
    const pct = (a, b) => (b ? ` · ${a - b <= 0 ? "−" : "+"}${Math.abs(((a - b) / b) * 100).toFixed(1)}%` : "");
    const sign = (d, f) => `${d < 0 ? "−" : d > 0 ? "+" : "±"}${f(Math.abs(d))}`;
    const addCost = (ma.total_cost_usd ?? 0) - (mb.total_cost_usd ?? 0);
    const totalJobs = (m) => (m.jobs_on_time ?? 0) + (m.jobs_late ?? 0) + (m.jobs_unfulfilled ?? 0);
    const metrics = [
      { label: "Fleet withdrawal", before: `${fmtNum(mb.total_water_liters)} L`, after: `${fmtNum(ma.total_water_liters)} L`, delta: `${sign(ma.total_water_liters - mb.total_water_liters, (x) => `${fmtNum(x)} L`)}${pct(ma.total_water_liters, mb.total_water_liters)}`, good: ma.total_water_liters < mb.total_water_liters },
      { label: "Operating cost", before: fmtUsd(mb.total_cost_usd), after: fmtUsd(ma.total_cost_usd), delta: `${sign(addCost, fmtUsd)}${previewBudget != null ? (addCost <= previewBudget ? " · within budget" : " · over budget") : ""}`, good: false, bad: previewBudget != null && addCost > previewBudget },
      { label: "Energy", before: `${fmtNum(mb.total_energy_kwh)} kWh`, after: `${fmtNum(ma.total_energy_kwh)} kWh`, delta: sign(ma.total_energy_kwh - mb.total_energy_kwh, (x) => `${fmtNum(x)} kWh`), good: false },
      { label: "Deadlines met", before: `${mb.jobs_on_time ?? "—"} of ${totalJobs(mb)}`, after: `${ma.jobs_on_time ?? "—"} of ${totalJobs(ma)}`, delta: ma.jobs_on_time === mb.jobs_on_time ? "no change" : sign(ma.jobs_on_time - mb.jobs_on_time, String), good: ma.jobs_on_time > mb.jobs_on_time, bad: ma.jobs_on_time < mb.jobs_on_time },
    ];
    const table = el("div", { className: `mTable${stale ? " dim" : ""}`, role: "table", "aria-label": "Before and after" });
    table.appendChild(el("div", { role: "row", className: "sr-only" }, [el("span", { role: "columnheader", text: "Metric" }), el("span", { role: "columnheader", text: "Before" }), el("span", { role: "columnheader", text: "After" })]));
    for (const m of metrics) {
      table.appendChild(el("div", { role: "row", className: "mRow" }, [
        el("div", { role: "rowheader", className: "mLabel", text: m.label }),
        el("div", { role: "cell", className: "mono mBefore", text: m.before }),
        el("div", { "aria-hidden": "true", className: "mArrow", text: "→" }),
        el("div", { role: "cell", className: "mAfterCol" }, [
          el("span", { className: `mono mAfter${m.good ? " isGood" : ""}${m.bad ? " isBadTxt" : ""}`, text: m.after }),
          el("span", { className: `mono mDelta${m.good ? " isGood" : ""}${m.bad ? " isBadTxt" : ""}`, text: m.delta }),
        ]),
      ]));
    }
    const budget = el("div", { className: "budget" });
    if (previewBudget != null) {
      const fill = el("div", { className: "budgetFill" });
      budget.append(
        el("div", { className: "budgetHead" }, [el("span", { text: "Additional cost budget" }), el("span", { className: "mono", text: `${fmtUsd(Math.max(addCost, 0))} of ${fmtUsd(previewBudget)}` })]),
        el("div", { className: "budgetRail" }, [fill]),
      );
      requestAnimationFrame(() => (fill.style.width = `${Math.max(0, Math.min(100, previewBudget ? (addCost / previewBudget) * 100 : 0)).toFixed(0)}%`));
    }
    const cb = p.cost_breakdown;
    if (cb) budget.appendChild(el("div", { className: "mono costLine", text: `Cost after: energy ${fmtUsd(cb.energy_usd)} · water ${fmtUsd(cb.water_usd)} · miss penalties ${fmtUsd(cb.miss_penalties_usd)} · total ${fmtUsd(cb.total_usd)}` }));
    table.appendChild(budget);
    body.appendChild(table);
  } else {
    body.appendChild(el("div", { className: "errBox" }, [el("strong", { text: "No feasible plan." }), ` ${p.infeasibility_reason || "The solver did not return a reason."}`]));
  }

  const outs = p.deadline_outcomes || [];
  if (outs.length) {
    const list = el("div", { className: "outList" }, [el("div", { className: "kick", text: "Deadline outcomes · solver" })]);
    for (const o of outs) {
      list.appendChild(el("div", { className: "outRow" }, [
        el("span", { className: "mono", text: shortJobId(o.job_id), title: o.job_id }),
        el("span", { className: o.met_deadline ? "isGood" : "isBadTxt", text: o.met_deadline ? "✓ Meets deadline" : "✕ Misses deadline" }),
        el("span", { className: "mono", text: o.projected_completion ? `done ${fullUtc(o.projected_completion)} UTC` : "no completion" }),
      ]));
    }
    body.appendChild(list);
  }

  if (showApplied) {
    const first = (p.proposed_job_changes || [])[0];
    const msg = applied.message || (first ? `The portal now shows ${shortJobId(first.job_id)} on ${nameOf(first.new_placement?.assigned_site_id)}.` : "");
    body.appendChild(el("div", { className: "applyToast pop", role: "status" }, [
      svg("svg", { width: 22, height: 22, viewBox: "0 0 22 22", "aria-hidden": "true" }, [svg("circle", { cx: 11, cy: 11, r: 10, fill: "#FFFFFF" }), svg("path", { d: "M6 11.5 L9.5 15 L16 7.5", fill: "none", stroke: "#0F6E76", "stroke-width": 2.4, "stroke-linecap": "round", "stroke-linejoin": "round" })]),
      el("div", null, [el("strong", { text: `Plan applied · revision ${applied.revision ?? revision}.` }), ` ${msg}`]),
    ]));
  } else if (feasible) {
    const applyBtn = el("button", { type: "button", className: "btn btnBlue", id: "apply-btn", text: applying ? "Applying…" : "Apply schedule", disabled: stale || applying || applyUnavailable != null });
    applyBtn.addEventListener("click", onApply);
    const discard = el("button", { type: "button", className: "btn btnD", id: "discard-btn", text: "Discard preview" });
    discard.addEventListener("click", onDiscard);
    body.appendChild(el("div", { className: "actions" }, [applyBtn, discard]));
    if (applyUnavailable != null) body.appendChild(unavailablePanel(`Apply: ${applyUnavailable}`));
    if (stale) {
      const rerun = el("button", { type: "button", className: "btn btnD", text: "Re-run preview" });
      rerun.addEventListener("click", onPreview);
      body.appendChild(rerun);
    }
  } else {
    const back = el("button", { type: "button", className: "chip", id: "discard-btn", text: declared ? "Back to emergency" : "Discard preview" });
    back.addEventListener("click", onDiscard);
    body.appendChild(el("div", { className: "chipRow" }, [back]));
  }
  return body;
}

/* ---------- chart ---------- */
function renderChart(D) {
  if (!sites.length) return;
  const restricted = declared?.site_id;
  const id = selSite || restricted || D.breaches[0]?.site.site_id || sites.find((s) => (s.hourly_profiles || []).some((p) => p.water_cap_liters_per_hour != null))?.site_id || sites[0].site_id;
  const site = siteOf(id);
  const hours = hourList();
  const showApplied = applied && previewState !== "ready";
  const baseJobs = showApplied && applied.beforeJobs ? applied.beforeJobs : jobs;
  const cur = hours.map((t) => hourModel(site, t, baseJobs).w);
  let prop = null;
  if (D.showProposed) prop = hours.map((t) => hourModel(site, t, patchJobs(jobs, D.changes)).w);
  else if (showApplied) prop = hours.map((t) => hourModel(site, t, jobs).w);
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
  const peak = Math.max(...cur);
  const overN = cur.filter((w, i) => caps[i] != null && w > caps[i]).length;
  const aria = `${site.name} modeled hourly water over ${hours.length} hours, peaking at ${fmtNum(peak)} liters per hour. ` +
    (overN ? `${overN} hour${overN === 1 ? " is" : "s are"} over the cap.` : "No hour is over its cap.") +
    (prop ? ` ${showApplied ? "Applied" : "Proposed"} schedule peaks at ${fmtNum(Math.max(...prop))}.` : "");
  $("water-chart").setAttribute("aria-label", aria);
  drawWaterChart($("water-chart"), { labels: hours.map((t) => pad2(new Date(t).getUTCHours())), cur, prop, caps, win, nowIdx, applied: showApplied, capLabel });
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
    if (focusMove && j.id === firstId && (D.showProposed || showApplied)) tr.style.background = "#F3FAF9";
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
  const solver = D.showProposed ? preview : showApplied ? applied.preview : null;
  if (solver?.per_site_water?.length) {
    $("comp-kick").textContent = "Cap compliance · solver";
    for (const sw of solver.per_site_water) {
      const over = sw.water_cap_violations > 0;
      rows.push([nameOf(sw.site_id), `${fmtNum(sw.water_liters)} L · ${sw.water_cap_violations} h over`, over ? "▲" : "✓", over ? "Over cap" : showApplied ? "Compliant" : "Within cap", over]);
    }
  } else {
    $("comp-kick").textContent = "Cap compliance · modeled";
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
    return;
  }
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
    res = { applied: true, revision: DEMO.jobs.revision, message: "" };
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
  updatePreviewButton();
  toast(`Plan applied · revision ${res.revision}.`);
  await loadAll();
}

/* In the one-screen layout the plan panel lives below the form in a scrolling column:
   bring it into view whenever the preview state changes. */
function showPlanPanel() {
  const col = null;
  if (!col || col.scrollHeight <= col.clientHeight) return;
  col.scrollTo({ top: cmpEl.offsetTop - col.offsetTop, behavior: prefersReducedMotion() ? "auto" : "smooth" });
}

function updatePreviewButton() {
  const b = $("preview-btn");
  b.disabled = previewState === "loading";
  b.textContent = previewState === "loading" ? "Solving…" : previewState === "ready" ? "Preview again" : "Preview schedule";
  if (previewState !== "none") requestAnimationFrame(showPlanPanel);
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
$("mode-routine").addEventListener("click", () => setMode("routine"));
$("mode-emergency").addEventListener("click", () => setMode("emergency"));
for (const id of ["emer-site", "emer-cap", "emer-start", "emer-end"]) {
  $(id).addEventListener("input", () => {
    if (!instructionDirty) $("instruction").value = defaultInstruction();
  });
}
$("instruction").addEventListener("input", () => (instructionDirty = true));
$("preview-btn").addEventListener("click", onPreview);
$("band-cta").addEventListener("click", onPreview);
$("drawer-close").addEventListener("click", () => {
  selSite = null;
  renderAll();
});

$("advance-btn").addEventListener("click", async () => {
  if (DEMO) {
    DEMO.current_time = addHoursToIso(DEMO.current_time, 1);
    return loadAll();
  }
  try {
    const res = await advanceDemo();
    if (res.unavailable) {
      advanceUnavailable = res.detail;
      $("advance-btn").disabled = true;
      renderAll();
      return;
    }
    await loadAll();
  } catch (err) {
    toast(`Could not advance. ${err.message}`);
  }
});

$("reset-btn").addEventListener("click", async () => {
  try {
    if (DEMO) DEMO = freshDemo();
    else await resetDemo();
    declared = null;
    preview = null;
    previewState = "none";
    applied = null;
    applyUnavailable = null;
    selSite = null;
    setDirectorPressed(null, "d-routine");
    updatePreviewButton();
    toast(DEMO ? "Sample data reset." : "Demo reset. Fixtures restored.");
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
  $("foot").textContent = "Visual demo · every figure on this page is labeled sample data shaped like the API. Nothing here came from the backend.";
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

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => sites.length && renderAll(), 150);
});

loadAll().then(startPoll);

initLayout({
  blocks: ".ringSec, .cmd, .cmp, .chartSec, .queueSec, .stat",
  texts: ".pageTitle, .cardTitle",
  scrollers: ".qScroll",
});
