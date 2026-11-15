/* Shared DOM helpers and SVG renderers. Everything is built with createElement /
   createElementNS and textContent — never innerHTML — so API text, Gemini text and
   user input always render as plain text. */

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "className") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "dataset" && typeof v === "object") Object.assign(node.dataset, v);
      else if (k === "disabled") node.disabled = Boolean(v);
      else if (k.startsWith("aria") && !k.includes("-")) node.setAttribute(k.replace(/([A-Z])/g, "-$1").toLowerCase(), v);
      else node.setAttribute(k, v);
    }
  }
  appendAll(node, children);
  return node;
}

function svg(tag, attrs, children) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "text") node.textContent = v;
      else node.setAttribute(k, String(v));
    }
  }
  appendAll(node, children);
  return node;
}

function appendAll(node, children) {
  if (!children) return;
  for (const child of children) {
    if (child == null || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
}

function setAttrs(node, attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) node.removeAttribute(k);
    else node.setAttribute(k, String(v));
  }
}

/** Return the child of `parent` keyed by `key`, creating it with `create()` if missing. */
function keyedChild(parent, key, create) {
  for (const c of parent.children) if (c.dataset && c.dataset.key === key) return c;
  for (const c of parent.childNodes) if (c.getAttribute && c.getAttribute("data-key") === key) return c;
  const node = create();
  node.setAttribute("data-key", key);
  parent.appendChild(node);
  return node;
}

/** Remove keyed children whose key is not in `keep`. */
function pruneKeyed(parent, keep) {
  for (const c of Array.from(parent.childNodes)) {
    const k = c.getAttribute && c.getAttribute("data-key");
    if (k != null && !keep.has(k)) c.remove();
  }
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/* ---------- formatting ---------- */
const TEMPLATES = {
  inference_batch: { name: "Inference batch", gpu: 4, dur: 2, blurb: "Short, latency-tolerant scoring runs." },
  training_small: { name: "Training small", gpu: 8, dur: 6, blurb: "Fine-tunes and mid-size experiments." },
  training_large: { name: "Training large", gpu: 16, dur: 12, blurb: "Long runs that can wait for cheap water." },
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad2 = (n) => String(n).padStart(2, "0");
const HOUR_MS = 3600000;

function tplName(t) {
  return TEMPLATES[t]?.name || String(t || "—");
}
function hhmm(isoOrMs) {
  if (isoOrMs == null) return "—";
  const d = new Date(isoOrMs);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
}
function stampUtc(isoOrMs) {
  if (isoOrMs == null) return "—";
  const d = new Date(isoOrMs);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} · ${hhmm(d)}`;
}
function fullUtc(isoOrMs) {
  if (isoOrMs == null) return "—";
  const d = new Date(isoOrMs);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${hhmm(d)}`;
}
function clockText(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${hhmm(d)} UTC`;
}
function fmtNum(x) {
  return Math.round(Number(x) || 0).toLocaleString("en-US");
}
function fmtUsd(x) {
  return `$${(Number(x) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function fmtHours(h) {
  const r = Math.round(h * 10) / 10;
  return String(r).replace(/\.0$/, "");
}
function numberWord(n) {
  const w = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
  return n >= 0 && n <= 10 ? w[n] : String(n);
}
function siteShort(site) {
  if (!site) return "—";
  const name = typeof site === "string" ? site : site.name || site.site_id;
  return String(name).replace(/\s+DC$/i, "");
}

/* ---------- status ---------- */
const STATUS_WORDS = {
  queued: "Queued",
  scheduled: "Scheduled",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  unfulfilled: "Unfulfilled",
};
function statusWord(s) {
  return STATUS_WORDS[s] || String(s || "Queued");
}
function statusDot(status, small) {
  return el("span", { className: `dot dot--${STATUS_WORDS[status] ? status : "queued"}${small ? " dotSm" : ""}`, "aria-hidden": "true" });
}
function statusPill(status, small) {
  return el("span", { className: "statusInline" }, [statusDot(status, small), statusWord(status)]);
}
function chip(kind, text) {
  return el("span", { className: `arcChip chip--${kind}`, text });
}

/* ---------- toasts, tags, honest unavailable ---------- */
let toastRoot;
function toast(message) {
  if (!toastRoot) {
    toastRoot = el("div", { className: "toastStack", role: "status", "aria-live": "polite" });
    document.body.appendChild(toastRoot);
  }
  const icon = svg("svg", { width: 22, height: 22, viewBox: "0 0 22 22", "aria-hidden": "true" }, [
    svg("circle", { cx: 11, cy: 11, r: 10, fill: "#E7F4F3" }),
    svg("path", { d: "M6 11.5 L9.5 15 L16 7.5", fill: "none", stroke: "#0F6E76", "stroke-width": 2.4, "stroke-linecap": "round", "stroke-linejoin": "round" }),
  ]);
  const item = el("div", { className: "toast pop" }, [icon, el("div", { text: message })]);
  toastRoot.appendChild(item);
  window.setTimeout(() => item.remove(), 6000);
}

function visualDemoTag(short) {
  return el("span", { className: "vdTag", text: short ? "VISUAL DEMO · labeled sample data" : "VISUAL DEMO · labeled sample data, not from the backend" });
}

function unavailablePanel(detail) {
  return el("div", { className: "unavail", role: "status" }, [
    el("p", { className: "unavail__title", text: "Not available yet. This feature is waiting on the scheduling service. Nothing is being faked." }),
    detail ? el("p", { className: "unavail__detail", text: `Server said: ${detail}` }) : null,
  ]);
}

/* ---------- shared brand mark ---------- */
function logoSvg() {
  return svg("svg", { width: 36, height: 36, viewBox: "0 0 36 36", "aria-hidden": "true" }, [
    svg("circle", { cx: 18, cy: 18, r: 15, fill: "none", stroke: "#9FD3D2", "stroke-width": 1.5 }),
    svg("circle", { cx: 18, cy: 18, r: 9, fill: "none", stroke: "#2A9D9F", "stroke-width": 1.5, "stroke-dasharray": "2 3" }),
    svg("path", { d: "M18 9 C21 14 23 16 23 19.5 A5 5 0 0 1 13 19.5 C13 16 15 14 18 9Z", fill: "#0F6E76" }),
  ]);
}

/* ---------- site model shared by both pages ---------- */
/** Modeled load for one site in the hour that contains `clockIso`, from its hourly profile and assigned jobs. */
function siteHourModel(site, jobs, tMs) {
  const ps = site.hourly_profiles || [];
  let p = ps[0] || null;
  for (const x of ps) if (new Date(x.hour_start).getTime() <= tMs) p = x;
  const t0 = tMs - (tMs % HOUR_MS);
  const t1 = t0 + HOUR_MS;
  let gpus = 0;
  for (const j of jobs) {
    if (j.assigned_site_id !== site.site_id || !j.scheduled_start || j.status === "failed" || j.status === "unfulfilled") continue;
    const s = new Date(j.scheduled_start).getTime();
    const e = j.completed_at ? new Date(j.completed_at).getTime() : s + j.duration_hours * HOUR_MS;
    if (s < t1 && e > t0) gpus += j.gpu_count;
  }
  const cap = p?.gpu_capacity || 0;
  return {
    gpus,
    u: cap ? Math.round((gpus / cap) * 100) : 0,
    w: p ? p.fixed_water_liters_per_hour + p.incremental_water_liters_per_gpu_hour * gpus : 0,
    c: p?.water_cap_liters_per_hour ?? null,
  };
}

/* ---------- tank row (fleet instrument) ----------
   Glass tanks in one row. Water level = withdrawal, fine ember line = cap, thin bar at the
   base = GPU load. Moves are arcs over the tops: dashed = proposed, solid + droplet = applied.
   An optional dashed "Queue" well sits first; new jobs fall into their tank or the queue. */
const TANK = { SLOT: 90, TW: 50, TOP: 116, TH: 210, PAD: 24 };

function niceCeil(v) {
  if (v <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/**
 * view: {
 *   mini, loading, scaleMax,
 *   sites: [{ key, short, w, c, ghostW, u, over, overH, selected, highlight, sub, aria, onClick, land }],
 *   queue: { count } | null,
 *   arcs:  [{ key, from, to, kind: "proposed"|"applied"|"infeasible", stale, focus }],
 *   chips: [{ key, arc, text, bg, fg, bc, bstyle }],
 *   drops: [{ key, arc }], falls: [{ key, to }]
 * }  `from`/`to` are site keys or "queue".
 */
function drawTanks(svgEl, overlayEl, view) {
  const reduced = prefersReducedMotion();
  const mini = Boolean(view.mini);
  const slots = view.sites.length + (view.queue ? 1 : 0);
  const labels = mini && slots <= 5;
  // Geometry is measured per render: on a wide, short card the slots and tanks grow to use
  // the width instead of bunching in the middle. Labels live in an HTML row below the art.
  const G = { PAD: 24, TOP: mini ? 116 : 104, TH: 210, SLOT: 90, TW: 50 };
  const LABEL_H = 46;
  const fit = mini ? null : svgEl.closest(".tankFit");
  if (fit) {
    const r = fit.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      const artH = G.TOP + G.TH + 22;
      const ar = r.width / Math.max(r.height - LABEL_H, 120);
      G.SLOT = 2 * Math.round(Math.max(88, Math.min(150, (ar * artH - 2 * G.PAD) / Math.max(slots, 1))) / 2);
      G.TW = Math.round(Math.max(46, Math.min(64, G.SLOT * 0.46)));
    }
  }
  const H = mini ? (labels ? 392 : 352) : G.TOP + G.TH + 22;
  const W = G.PAD * 2 + Math.max(slots, 1) * G.SLOT;
  const base = G.TOP + G.TH;
  const xOf = (i) => G.PAD + G.SLOT * i + G.SLOT / 2;
  const off = view.queue ? 1 : 0;
  const indexOf = (k) => (k === "queue" ? 0 : off + view.sites.findIndex((s) => s.key === k));
  const scaleMax = view.scaleMax || 1;
  const inner = G.TH - 6;
  const levelY = (v) => base - 3 - Math.max(0, Math.min(1, v / scaleMax)) * inner;
  const pre = svgEl.id || "tanks";

  // ---- (re)build static structure when the slot layout changes ----
  const sig = `${mini}|${G.SLOT}|${G.TW}|${view.queue ? 1 : 0}|${view.sites.map((s) => s.key).join(",")}`;
  let L = svgEl._tk;
  if (!L || L.sig !== sig) {
    svgEl.replaceChildren();
    svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const box = svgEl.closest(".tankBox") || svgEl.parentElement;
    if (box) box.style.setProperty("--ar", (W / H).toFixed(4));
    const defs = svg("defs", null, [
      svg("linearGradient", { id: `${pre}-water`, x1: 0, y1: 0, x2: 0, y2: 1 }, [svg("stop", { offset: "0%", "stop-color": "#F2FFFD" }), svg("stop", { offset: "100%", "stop-color": "#9EDFD8" })]),
      svg("linearGradient", { id: `${pre}-hot`, x1: 0, y1: 0, x2: 0, y2: 1 }, [svg("stop", { offset: "0%", "stop-color": "#FFF1E6" }), svg("stop", { offset: "100%", "stop-color": "#F4AE80" })]),
      svg("linearGradient", { id: `${pre}-sweep`, x1: 0, y1: 0, x2: 1, y2: 0 }, [svg("stop", { offset: "0%", "stop-color": "#FFFFFF", "stop-opacity": "0" }), svg("stop", { offset: "50%", "stop-color": "#FFFFFF", "stop-opacity": ".22" }), svg("stop", { offset: "100%", "stop-color": "#FFFFFF", "stop-opacity": "0" })]),
    ]);
    svgEl.appendChild(defs);
    const tanksG = svg("g");
    const arcsG = svg("g");
    const fxG = svg("g");
    const sweep = svg("rect", { x: -80, y: G.TOP - 8, width: 80, height: G.TH + 16, fill: `url(#${pre}-sweep)`, class: "tankSweep", style: `--sw:${W + 160}px` });
    svgEl.append(tanksG, sweep, arcsG, fxG);

    const tanks = new Map();
    view.sites.forEach((s, si) => {
      const i = off + si;
      const x0 = xOf(i) - G.TW / 2;
      const clipId = `${pre}-clip-${i}`;
      defs.appendChild(svg("clipPath", { id: clipId }, [svg("rect", { x: x0 + 3, y: G.TOP + 3, width: G.TW - 6, height: G.TH - 6, rx: 8 })]));
      const A = 3;
      let wave = `M ${x0 - G.TW} 0`;
      for (let k = 0; k < 3; k++) wave += ` q ${G.TW / 4} ${-A} ${G.TW / 2} 0 q ${G.TW / 4} ${A} ${G.TW / 2} 0`;
      wave += ` V ${G.TH + 10} H ${x0 - G.TW} Z`;
      const wavePath = svg("path", { d: wave, class: "wave", style: `--wl:${G.TW}` });
      const levelG = svg("g", { class: "levelG", style: `transform:translateY(${base}px)` }, [wavePath]);
      const g = svg("g", null, [
        svg("rect", { x: x0, y: G.TOP, width: G.TW, height: G.TH, rx: 12, fill: "rgba(255,255,255,.07)" }),
        svg("g", { "clip-path": `url(#${clipId})` }, [levelG]),
        svg("rect", { x: x0 + 7, y: G.TOP + 12, width: 3, height: G.TH - 24, rx: 1.5, fill: "#FFFFFF", opacity: ".16" }),
      ]);
      const ghost = svg("line", { x1: x0 + 4, x2: x0 + G.TW - 4, stroke: "#D9FBF8", "stroke-width": 2, "stroke-dasharray": "4 3", class: "lineT" });
      const cap = svg("line", { x1: x0 - 6, x2: x0 + G.TW + 6, stroke: "#FFB37A", "stroke-width": 1.6, class: "lineT" });
      const outline = svg("rect", { x: x0, y: G.TOP, width: G.TW, height: G.TH, rx: 12, fill: "none" });
      const pulse = svg("rect", { x: x0 - 3, y: G.TOP - 3, width: G.TW + 6, height: G.TH + 6, rx: 14, fill: "none", stroke: "#FFB37A", "stroke-width": 2, class: "pulseT" });
      const mark = svg("text", { x: x0 + G.TW / 2, y: G.TOP + 18, "text-anchor": "middle", "font-size": 12, "font-weight": 700, fill: "#FFB37A", text: "▲" });
      const gpuTrack = svg("rect", { x: x0, y: base + 10, width: G.TW, height: 5, rx: 2.5, fill: "rgba(255,255,255,.2)" });
      const gpuFill = svg("rect", { x: x0, y: base + 10, width: 0, height: 5, rx: 2.5, fill: "#FFFFFF", class: "gpuFill" });
      g.append(ghost, cap, outline, pulse, mark, gpuTrack, gpuFill);
      let label = null;
      if (labels) {
        label = svg("text", { x: xOf(i), y: base + 46, "text-anchor": "middle", "font-size": 21, "font-weight": 600, fill: "#FFFFFF" });
        g.appendChild(label);
      }
      tanksG.appendChild(g);
      tanks.set(s.key, { g, x0, levelG, wavePath, ghost, cap, outline, pulse, mark, gpuFill, label, landKey: "" });
    });

    let queue = null;
    if (view.queue) {
      const x0 = xOf(0) - G.TW / 2;
      const qTop = G.TOP + G.TH * 0.5;
      const dots = svg("g");
      const g = svg("g", null, [
        svg("rect", { x: x0, y: qTop, width: G.TW, height: base - qTop, rx: 12, fill: "rgba(255,255,255,.05)", stroke: "rgba(255,255,255,.6)", "stroke-width": 1.5, "stroke-dasharray": "5 4" }),
        dots,
      ]);
      const more = svg("text", { x: xOf(0), y: qTop - 8, "text-anchor": "middle", "font-size": 13, "font-weight": 600, fill: "#FFFFFF" });
      g.appendChild(more);
      if (labels) g.appendChild(svg("text", { x: xOf(0), y: base + 46, "text-anchor": "middle", "font-size": 21, "font-weight": 600, fill: "#FFFFFF", opacity: ".85", text: "Queue" }));
      tanksG.appendChild(g);
      queue = { dots, more, x0 };
    }

    let chipsEl = null;
    let btnsEl = null;
    if (overlayEl) {
      overlayEl.replaceChildren();
      chipsEl = el("div");
      btnsEl = el("div");
      overlayEl.append(btnsEl, chipsEl);
    }
    L = svgEl._tk = { sig, tanks, queue, arcsG, fxG, sweep, chipsEl, btnsEl, falls: new Set(), drops: new Set() };
  }

  L.sweep.style.display = view.loading && !reduced ? "" : "none";

  // ---- tanks ----
  view.sites.forEach((s, si) => {
    const T = L.tanks.get(s.key);
    const i = off + si;
    const hot = s.over;
    T.wavePath.setAttribute("fill", `url(#${pre}-${hot ? "hot" : "water"})`);
    const ly = levelY(s.w);
    requestAnimationFrame(() => (T.levelG.style.transform = `translateY(${ly.toFixed(1)}px)`));
    T.wavePath.classList.toggle("wave", !reduced);
    if (s.c != null) {
      const cy = levelY(s.c).toFixed(1);
      setAttrs(T.cap, { y1: cy, y2: cy });
      T.cap.style.opacity = "1";
    } else T.cap.style.opacity = "0";
    if (s.ghostW != null && Math.abs(s.ghostW - s.w) > 0.5) {
      const gy = levelY(s.ghostW).toFixed(1);
      // dark dashes when the proposed level sits inside the water, light ones above it
      setAttrs(T.ghost, { y1: gy, y2: gy, stroke: s.ghostW < s.w ? "#0B4F58" : "#D9FBF8" });
      T.ghost.style.opacity = "1";
    } else T.ghost.style.opacity = "0";
    const stroke = s.selected || s.highlight ? "#FFFFFF" : hot ? "#FFB37A" : "rgba(255,255,255,.5)";
    setAttrs(T.outline, { stroke, "stroke-width": s.selected || s.highlight ? 2.6 : 1.5 });
    T.pulse.style.display = (hot || s.highlight) && !view.loading ? "" : "none";
    T.pulse.setAttribute("stroke", hot ? "#FFB37A" : "#FFFFFF");
    T.mark.style.display = hot ? "" : "none";
    T.gpuFill.setAttribute("width", ((Math.min(s.u, 100) / 100) * G.TW).toFixed(1));
    T.gpuFill.setAttribute("fill", hot ? "#FFB37A" : "#FFFFFF");
    if (T.label) {
      T.label.textContent = s.short;
      T.label.setAttribute("opacity", s.highlight ? "1" : ".75");
    }
    // landing ripple at the water surface, once per apply
    if (s.land && s.land !== T.landKey && !reduced) {
      const rip = svg("ellipse", { cx: xOf(i), cy: ly.toFixed(1), rx: G.TW / 2 - 4, ry: 4, fill: "none", stroke: "#FFFFFF", "stroke-width": 2, class: "land" });
      T.g.appendChild(rip);
      setTimeout(() => rip.remove(), 3400);
    }
    T.landKey = s.land || "";
  });

  // ---- queue well: one droplet per queued job ----
  if (L.queue) {
    const q = L.queue;
    const n = Math.max(0, view.queue.count || 0);
    const shown = Math.min(n, 9);
    q.dots.replaceChildren();
    for (let k = 0; k < shown; k++) {
      const col = k % 3;
      const row = Math.floor(k / 3);
      q.dots.appendChild(svg("path", { d: dropPath(q.x0 + 12 + col * 13, base - 12 - row * 16, 0.62), fill: "#FFFFFF", opacity: ".9" }));
    }
    q.more.textContent = n ? String(n) : "";
  }

  // ---- arcs over the tops ----
  const arcGeom = (a) => {
    const ia = indexOf(a.from);
    let ib = a.to != null ? indexOf(a.to) : -1;
    const xa = xOf(ia < 0 ? 0 : ia);
    let xb;
    if (ib < 0) xb = xa + (ia >= slots - 2 ? -2 : 2) * G.SLOT; // infeasible: aim at nowhere
    else xb = xOf(ib);
    const ya = G.TOP - 6;
    const peak = Math.min(G.TOP - 30, 24 + Math.abs(xb - xa) * 0.2); // apex stays clear of the card header
    const cx = (xa + xb) / 2;
    return { d: `M ${xa.toFixed(1)} ${ya} Q ${cx.toFixed(1)} ${(ya - 2 * peak).toFixed(1)} ${xb.toFixed(1)} ${ya}`, apex: [cx, ya - peak] };
  };
  const geo = new Map();
  const keep = new Set();
  for (const a of view.arcs) {
    const g = arcGeom(a);
    geo.set(a.key, g);
    keep.add(a.key);
    const p = keyedChild(L.arcsG, a.key, () => svg("path", { fill: "none", "stroke-linecap": "round", pathLength: 1 }));
    const style = {
      proposed: { stroke: "#D9FBF8", w: a.focus ? 3.5 : 2.2, dash: "0.03 0.025", cls: a.stale || reduced ? "" : "dashflow", op: a.stale ? 0.4 : 1 },
      applied: { stroke: "#FFFFFF", w: 2.4, dash: "1 0", cls: "", op: 0.95 },
      infeasible: { stroke: "#FFB37A", w: 2.6, dash: "0.45 1", cls: "", op: 1 },
    }[a.kind];
    setAttrs(p, { d: g.d, stroke: style.stroke, "stroke-width": style.w, class: `arcPath ${style.cls}`.trim() });
    p.style.strokeDasharray = style.dash;
    p.style.opacity = String(style.op);
  }
  pruneKeyed(L.arcsG, keep);

  // ---- droplet travelling an applied arc (once per key) ----
  if (!reduced) {
    for (const d of view.drops || []) {
      const g = geo.get(d.arc);
      if (!g || L.drops.has(d.key)) continue;
      L.drops.add(d.key);
      const drop = svg("circle", { r: 7, fill: "#FFFFFF", class: "drop", style: `offset-path:path('${g.d}');offset-rotate:0deg;filter:drop-shadow(0 0 6px rgba(255,255,255,.9))` });
      L.fxG.appendChild(drop);
      setTimeout(() => drop.remove(), 2300);
    }
    // ---- a new job falls into its tank, or into the queue ----
    for (const f of view.falls || []) {
      if (L.falls.has(f.key)) continue;
      const i = indexOf(f.to);
      if (i < 0) continue;
      L.falls.add(f.key);
      const site = view.sites.find((s) => s.key === f.to);
      const sy = f.to === "queue" ? base - 14 - Math.floor(Math.min(view.queue?.count || 1, 9) / 3) * 16 : levelY(site ? site.w : 0);
      const grp = svg("g", { transform: `translate(${xOf(i).toFixed(1)} ${sy.toFixed(1)})` }, [
        svg("path", { d: dropPath(0, -6, 1.15), fill: "#FFFFFF", class: "fallDrop", style: `--fy:${(sy + 10).toFixed(0)};filter:drop-shadow(0 0 6px rgba(255,255,255,.9))` }),
        svg("ellipse", { cx: 0, cy: 0, rx: G.TW / 2 - 4, ry: 4, fill: "none", stroke: "#FFFFFF", "stroke-width": 2, class: "splash" }),
      ]);
      L.fxG.appendChild(grp);
      setTimeout(() => grp.remove(), 2600);
    }
  }

  // ---- HTML overlay: chips at arc apexes, accessible tank buttons with labels ----
  if (!L.chipsEl) return;
  const chipKeep = new Set();
  for (const c of view.chips || []) {
    const g = geo.get(c.arc);
    if (!g) continue;
    const key = `${c.key}|${c.text}`;
    chipKeep.add(key);
    const pos = keyedChild(L.chipsEl, key, () => el("div", { className: "arcChipPos" }, [el("div", { className: "arcChip pop", text: c.text })]));
    pos.style.left = `${((g.apex[0] / W) * 100).toFixed(2)}%`;
    pos.style.top = `${(((g.apex[1] - 2) / H) * 100).toFixed(2)}%`;
    const inner = pos.firstChild;
    inner.style.background = c.bg;
    inner.style.color = c.fg;
    inner.style.border = `1.5px ${c.bstyle} ${c.bc}`;
  }
  pruneKeyed(L.chipsEl, chipKeep);

  const btnKeep = new Set();
  const place = (node, i) => {
    node.style.left = `${(((xOf(i) - G.SLOT / 2) / W) * 100).toFixed(3)}%`;
    node.style.width = `${((G.SLOT / W) * 100).toFixed(3)}%`;
    node.style.top = `${(((G.TOP - 10) / H) * 100).toFixed(3)}%`;
    node.style.height = `${(((H - G.TOP + 10) / H) * 100).toFixed(3)}%`;
  };
  view.sites.forEach((s, si) => {
    btnKeep.add(s.key);
    const b = keyedChild(L.btnsEl, s.key, () => {
      const node = el("button", { type: "button", className: "tankBtn" });
      node.addEventListener("click", () => node._onClick && node._onClick());
      return node;
    });
    place(b, off + si);
    b._onClick = s.onClick;
    b.setAttribute("aria-label", s.aria);
    b.setAttribute("aria-pressed", s.selected ? "true" : "false");
  });
  pruneKeyed(L.btnsEl, btnKeep);

  // ---- label row: one cell per slot, aligned under each tank, never on the glass ----
  const row = svgEl.closest(".tankBox")?.querySelector(".tankLabels");
  if (row) {
    row.style.gridTemplateColumns = `repeat(${Math.max(slots, 1)}, minmax(0, 1fr))`;
    row.style.paddingInline = `${((G.PAD / W) * 100).toFixed(3)}%`;
    const cell = (name, figure, cls, onClick) => {
      const c = el("div", { className: `tankCell ${cls}`.trim(), "aria-hidden": "true" }, [
        el("span", { className: "tankName", text: name }),
        el("span", { className: "tankFig", text: figure }),
      ]);
      if (onClick) c.addEventListener("click", onClick);
      return c;
    };
    const cells = [];
    if (view.queue) {
      const n = view.queue.count || 0;
      cells.push(cell("Queue", n ? `${n} waiting` : "empty", "isQueue", null));
    }
    for (const s of view.sites) cells.push(cell(s.short, s.fig || s.sub, `${s.over ? "isOver" : ""} ${s.selected ? "isSel" : ""}`, s.onClick));
    row.replaceChildren(...cells);
  }
}

/** Teardrop centered on (x, y), tip up. */
function dropPath(x, y, s) {
  const k = (n) => (n * s).toFixed(2);
  return `M ${x} ${(y - 9 * s).toFixed(2)} C ${(x + 4 * s).toFixed(2)} ${(y - 3 * s).toFixed(2)} ${(x + 6 * s).toFixed(2)} ${y} ${(x + 6 * s).toFixed(2)} ${(y + 3 * s).toFixed(2)} A ${k(6)} ${k(6)} 0 0 1 ${(x - 6 * s).toFixed(2)} ${(y + 3 * s).toFixed(2)} C ${(x - 6 * s).toFixed(2)} ${y} ${(x - 4 * s).toFixed(2)} ${(y - 3 * s).toFixed(2)} ${x} ${(y - 9 * s).toFixed(2)} Z`;
}

/* ---------- hourly water chart (Operator.dc, 800 × 270) ---------- */
function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

/**
 * view: { labels:[hour labels], cur:[L/h], prop:[L/h]|null, caps:[L/h|null], win:[i0,i1]|null,
 *         nowIdx:number|null, applied:bool, capLabel:string|null }
 */
function drawWaterChart(svgEl, view) {
  let L = svgEl._aq;
  if (!L) {
    svgEl.replaceChildren();
    const hatch = svg("pattern", { id: "hatch", width: 8, height: 8, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, [
      svg("rect", { width: 8, height: 8, fill: "rgba(224,122,63,.05)" }),
      svg("line", { x1: 0, y1: 0, x2: 0, y2: 8, stroke: "rgba(224,122,63,.28)", "stroke-width": 2 }),
    ]);
    const wipeRect = svg("rect", { x: 0, y: 0, height: 270, class: "wipeRect", style: "width:0px" });
    svgEl.appendChild(svg("defs", null, [hatch, svg("clipPath", { id: "wipe" }, [wipeRect]), svg("linearGradient", { id: "curFill", x1: 0, y1: 0, x2: 0, y2: 1 }, [svg("stop", { offset: "0%", "stop-color": "#2A9D9F", "stop-opacity": ".28" }), svg("stop", { offset: "100%", "stop-color": "#2A9D9F", "stop-opacity": "0" })])]));
    const grid = svg("g", { stroke: "#E6ECEC", "stroke-width": 1 });
    const yLabels = svg("g", { "font-family": "Hanken Grotesk, sans-serif", "font-size": 12, fill: "#5A6B72", "text-anchor": "end" });
    const xLabels = svg("g", { "font-family": "Hanken Grotesk, sans-serif", "font-size": 12, fill: "#5A6B72", "text-anchor": "middle" });
    const win = svg("rect", { y: 28, height: 206, fill: "url(#hatch)", class: "lineT" });
    const viol = svg("path", { fill: "rgba(224,122,63,.30)", class: "lineT" });
    const cap = svg("path", { fill: "none", stroke: "#E07A3F", "stroke-width": 2, "stroke-dasharray": "6 4", class: "lineT" });
    const cur = svg("path", { fill: "none", stroke: "#2A9D9F", "stroke-width": 2.5, "stroke-linejoin": "round", class: "lineT" });
    const area = svg("path", { fill: "url(#curFill)", class: "lineT" });
    const prop = svg("path", { fill: "none", stroke: "#0B4F58", "stroke-width": 2.5, "stroke-linejoin": "round", class: "lineT" });
    const dots = svg("g");
    const now = svg("g", { class: "nowG" }, [
      svg("line", { x1: 0, x2: 0, y1: 24, y2: 236, stroke: "#9AAAB0", "stroke-width": 1.2, "stroke-dasharray": "3 4" }),
      svg("text", { x: 6, y: 22, "font-family": "Hanken Grotesk, sans-serif", "font-size": 11, fill: "#5A6B72", "font-weight": 600, text: "Sim now" }),
    ]);
    const capLbl = svg("text", { "font-family": "Hanken Grotesk, sans-serif", "font-size": 11, fill: "#B4410F", "font-weight": 600, "text-anchor": "middle", class: "lineT" });
    [grid, yLabels, xLabels, win, area, viol, cap, cur, svg("g", { "clip-path": "url(#wipe)" }, [prop, dots]), now, capLbl].forEach((n) => svgEl.appendChild(n));
    L = svgEl._aq = { wipeRect, grid, yLabels, xLabels, win, area, viol, cap, cur, prop, dots, now, capLbl };
  }

  const n = view.labels.length;
  const X = (i) => 64 + (n > 1 ? i * (712 / (n - 1)) : 356);
  const all = [...view.cur, ...(view.prop || []), ...view.caps.filter((c) => c != null)];
  const step = niceStep(Math.max(...all, 1) / 3);
  const top = step * 3;
  const Y = (v) => 232 - (v / top) * 200;
  const P = (x, y) => `${x.toFixed(1)} ${y.toFixed(1)}`;

  L.grid.replaceChildren();
  L.yLabels.replaceChildren();
  for (let k = 0; k <= 3; k++) {
    const y = Y(step * k);
    L.grid.appendChild(svg("line", { x1: 64, x2: 776, y1: y.toFixed(1), y2: y.toFixed(1) }));
    L.yLabels.appendChild(svg("text", { x: 56, y: (y + 4).toFixed(1), text: fmtNum(step * k) }));
  }
  L.xLabels.replaceChildren();
  view.labels.forEach((t, i) => L.xLabels.appendChild(svg("text", { x: X(i).toFixed(1), y: 254, text: t })));

  const line = (arr) => arr.map((v, i) => `${i ? "L" : "M"}${P(X(i), Y(v))}`).join(" ");
  L.cur.setAttribute("d", n ? line(view.cur) : "");
  L.cur.style.opacity = view.applied ? "0.35" : "1";
  L.area.setAttribute("d", n ? `${line(view.cur)} L${X(n - 1).toFixed(1)} 232 L${X(0).toFixed(1)} 232 Z` : "");
  L.area.style.opacity = view.applied ? "0.35" : "1";

  // cap: stepped line, broken where an hour has no cap
  let capD = "";
  let prev = null;
  view.caps.forEach((c, i) => {
    if (c == null) {
      prev = null;
      return;
    }
    if (prev == null) capD += ` M${P(X(i), Y(c))}`;
    else capD += ` H${X(i).toFixed(1)} V${Y(c).toFixed(1)}`;
    prev = c;
  });
  L.cap.setAttribute("d", capD.trim());
  L.cap.style.opacity = capD ? "1" : "0";

  // violation area where current exceeds the cap
  let violD = "";
  let run = [];
  const flush = () => {
    if (run.length) {
      const fwd = run.map((i) => `L${P(X(i), Y(view.cur[i]))}`).join(" ");
      const back = run.slice().reverse().map((i) => `L${P(X(i), Y(view.caps[i]))}`).join(" ");
      violD += ` M${P(X(run[0]), Y(view.caps[run[0]]))} ${fwd} ${back} Z`;
    }
    run = [];
  };
  view.cur.forEach((v, i) => (view.caps[i] != null && v > view.caps[i] ? run.push(i) : flush()));
  flush();
  L.viol.setAttribute("d", violD.trim());
  L.viol.style.opacity = violD && !view.applied ? "1" : "0";

  // emergency window hatch + label
  if (view.win) {
    const x0 = X(view.win[0]);
    const x1 = X(Math.min(view.win[1], n - 1));
    setAttrs(L.win, { x: x0.toFixed(1), width: Math.max(0, x1 - x0).toFixed(1) });
    L.win.style.opacity = "1";
    const capV = view.caps[view.win[0]];
    setAttrs(L.capLbl, { x: ((x0 + x1) / 2).toFixed(1), y: (Y(capV ?? 0) + 16).toFixed(1) });
    L.capLbl.textContent = view.capLabel || "";
    L.capLbl.style.opacity = view.capLabel ? "1" : "0";
  } else {
    L.win.style.opacity = "0";
    L.capLbl.style.opacity = "0";
  }

  // proposed / applied series with the left-to-right wipe
  L.dots.replaceChildren();
  if (view.prop) {
    L.prop.setAttribute("d", line(view.prop));
    L.prop.style.strokeDasharray = view.applied ? "0" : "9 7";
    L.prop.style.opacity = "1";
    view.prop.forEach((v, i) => {
      if (Math.abs(v - view.cur[i]) < 0.5) return;
      L.dots.appendChild(svg("circle", { cx: X(i).toFixed(1), cy: Y(v).toFixed(1), r: 5, stroke: "#0B4F58", "stroke-width": 2, style: `fill:${view.applied ? "#0B4F58" : "#FFFFFF"}` }));
    });
    requestAnimationFrame(() => (L.wipeRect.style.width = "820px"));
  } else {
    L.prop.style.opacity = "0";
    L.wipeRect.style.width = "0px";
  }

  if (view.nowIdx == null) L.now.style.display = "none";
  else {
    L.now.style.display = "";
    L.now.style.transform = `translateX(${X(Math.max(0, Math.min(view.nowIdx, n - 1))).toFixed(1)}px)`;
  }
}

/* ---------- layout v2: scroll reveal + one-screen scroll panels ---------- */
/**
 * Sections matching `blocks` rise in, and titles matching `texts` wipe up, as they enter the
 * viewport (including inside scrolling panels). Elements already on screen at load reveal with
 * a short stagger. Classes are removed when each animation ends, so nothing lingers that could
 * fight later animations. Does nothing under prefers-reduced-motion.
 */
function revealOnScroll(blocks, texts) {
  if (prefersReducedMotion() || !("IntersectionObserver" in window)) return;
  const done = (node) => {
    const end = (ev) => {
      if (ev.target !== node) return;
      node.classList.remove("reveal", "revealText", "inView");
      node.style.removeProperty("--rd");
      node.removeEventListener("animationend", end);
    };
    node.addEventListener("animationend", end);
  };
  let order = 0;
  let resetTimer = null;
  const io = new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const node = en.target;
        node.style.setProperty("--rd", `${Math.min(order++, 6) * 90}ms`);
        done(node);
        node.classList.add("inView");
        io.unobserve(node);
      }
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => (order = 0), 400); // restart the stagger for the next batch
    },
    { threshold: 0, rootMargin: "0px 0px -8% 0px" },
  );
  const watch = (sel, cls) => {
    for (const node of document.querySelectorAll(sel)) {
      node.classList.add(cls);
      io.observe(node);
    }
  };
  watch(blocks, "reveal");
  if (texts) watch(texts, "revealText");
}

function initLayout({ blocks, texts, scrollers }) {
  for (const node of document.querySelectorAll(scrollers)) node.classList.add("fitScroll");
  revealOnScroll(blocks, texts);
}
