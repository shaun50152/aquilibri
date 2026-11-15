/* Shared DOM helpers and SVG renderers. Everything is built with createElement /
   createElementNS and textContent — never innerHTML — so API text, Gemini text and
   user input always render as plain text. */

const SVG_NS = "http://www.w3.org/2000/svg";

/** SVG and inline colors. These are the CSS tokens themselves, so light and dark themes
    both apply to SVG attributes and inline styles without re-rendering. */
const PAL = {
  accent: "var(--teal)",
  accentInk: "var(--teal-ink)",
  accentSoft: "var(--teal-soft)",
  violet: "var(--violet)",
  ember: "var(--ember)",
  emberInk: "var(--ember-ink)",
  emberSoft: "var(--ember-soft)",
  grid: "var(--line)",
  label: "var(--ink-2)",
  muted: "var(--muted)",
  surface: "var(--surface)",
  ink: "var(--ink)",
  tile: "var(--tile-2)",
};
const FONT = "Inter, system-ui, sans-serif";

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
  return String(name).replace(/\s*\([^)]*\)\s*$/, "").replace(/\s+DC$/i, "");
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
    svg("circle", { cx: 11, cy: 11, r: 10, fill: PAL.accentSoft }),
    svg("path", { d: "M6 11.5 L9.5 15 L16 7.5", fill: "none", stroke: PAL.accentInk, "stroke-width": 2.4, "stroke-linecap": "round", "stroke-linejoin": "round" }),
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

/* ---------- shared brand mark ----------
   The AquaLibri mark: a water drop with a wave curling inside it and a balance scale at
   its edge (water in equilibrium). Drawn as strokes so it stays crisp at 30 px and takes
   its colours from the theme tokens. */
function logoSvg() {
  const line = (d, extra) => svg("path", { d, fill: "none", stroke: "url(#aqLogoGrad)", "stroke-width": 2.1, "stroke-linecap": "round", "stroke-linejoin": "round", ...extra });
  return svg("svg", { class: "brandMark", width: 34, height: 34, viewBox: "0 0 48 48", "aria-hidden": "true" }, [
    svg("defs", null, [
      svg("linearGradient", { id: "aqLogoGrad", x1: 0, y1: 0, x2: 0.9, y2: 1 }, [
        svg("stop", { offset: "0%", "stop-color": "var(--logo-a)" }),
        svg("stop", { offset: "100%", "stop-color": "var(--logo-b)" }),
      ]),
    ]),
    // drop outline, left open where the scale sits
    line("M27 15.5 C24.5 11.5 22.3 8 20 4 C14 13 5 21.5 5 31.5 A15.5 15.5 0 0 0 30.5 43.2"),
    // inner peak
    line("M20 11.5 C16.5 17 11.5 22 11 29"),
    line("M20 11.5 C21.6 14 23 16 24.4 18.2"),
    // wave curling in from the left
    line("M6.5 34 C10 26.5 19.5 25.5 21.5 32 C22.7 36 19 38.6 16.6 36.4 C15 34.8 16.3 32.4 18.3 33"),
    line("M11.5 41 C17 44.5 25.5 43.5 29 37.5"),
    // balance scale
    svg("g", { fill: "none", stroke: "var(--logo-c)", "stroke-width": 2.1, "stroke-linecap": "round", "stroke-linejoin": "round" }, [
      svg("path", { d: "M27 22 H45 M36 18.5 V38 M31 38 H41" }),
      svg("path", { d: "M27 22 L24.5 29 M27 22 L29.5 29 M45 22 L42.5 29 M45 22 L47.5 29" , "stroke-width": 1.4 }),
      svg("path", { d: "M24 29 H30 A3 3 0 0 1 24 29 Z M42 29 H48 A3 3 0 0 1 42 29 Z", fill: "var(--logo-c)", "fill-opacity": 0.18 }),
    ]),
  ]);
}

/* ---------- app chrome: side nav scroll-spy + profile menu ---------- */
const USER = { name: "Shaun", initial: "S", role: "Workspace owner" };

function initSideNav() {
  const side = document.querySelector(".side");
  if (!side) return;
  const links = [...side.querySelectorAll('a.sideLink[href^="#"]')];
  const targets = links.map((a) => document.querySelector(a.getAttribute("href"))).filter(Boolean);
  const mark = (id) => links.forEach((a) => a.classList.toggle("isActive", a.getAttribute("href") === `#${id}`));
  if (targets.length) {
    // The active section is the last one whose top has passed a line near the top of the
    // viewport (just under the sticky bar). After a click, that link wins until scrolling settles.
    let raf = 0;
    let lockUntil = 0;
    const spy = () => {
      raf = 0;
      if (Date.now() < lockUntil) return;
      const line = Math.min(window.innerHeight * 0.3, 160);
      const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      let cur = targets[0];
      if (atBottom && window.scrollY > 0) cur = targets[targets.length - 1];
      else for (const t of targets) if (t.getBoundingClientRect().top <= line) cur = t;
      mark(cur.id);
    };
    for (const a of links) {
      a.addEventListener("click", () => {
        mark(a.getAttribute("href").slice(1));
        lockUntil = Date.now() + 900;
      });
    }
    window.addEventListener("scroll", () => {
      if (Date.now() < lockUntil) lockUntil = Date.now() + 150; // still smooth-scrolling
      raf ||= requestAnimationFrame(spy);
    }, { passive: true });
    spy();
  }

  // Rail mode: icons only. Hover or keyboard focus slides the full panel out over the page,
  // and it closes again when the pointer leaves.
  const app = document.querySelector(".app");
  const dock = window.matchMedia("(max-width: 680px)");
  app.classList.add("isRail");
  let enterT = 0;
  let leaveT = 0;
  const peek = (on) => side.classList.toggle("isPeek", on && !dock.matches);
  side.addEventListener("mouseenter", () => {
    clearTimeout(leaveT);
    enterT = setTimeout(() => peek(true), 90);
  });
  side.addEventListener("mouseleave", () => {
    clearTimeout(enterT);
    leaveT = setTimeout(() => peek(false), 160);
  });
  side.addEventListener("focusin", (e) => {
    if (e.target.matches(":focus-visible")) peek(true);
  });
  side.addEventListener("focusout", (e) => {
    if (!side.contains(e.relatedTarget)) peek(false);
  });
  side.addEventListener("click", (e) => {
    if (e.target.closest("a.sideLink")) peek(false);
  });
}

/* ---------- light / dark theme ---------- */
const THEME_KEY = "aq.theme";
function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}
function initThemeToggle() {
  const item = document.getElementById("theme-item");
  if (!item) return;
  const label = document.getElementById("theme-label");
  const paint = () => {
    const dark = currentTheme() === "dark";
    item.setAttribute("aria-checked", dark ? "true" : "false");
    label.textContent = "Dark mode";
  };
  item.addEventListener("click", (e) => {
    e.stopPropagation(); // keep the menu open so the switch visibly flips
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch { /* storage blocked */ }
    paint();
  });
  paint();
}

function initProfileMenu() {
  const btn = document.getElementById("profile-btn");
  const menu = document.getElementById("profile-menu");
  if (!btn || !menu) return;
  const close = () => {
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) menu.querySelector("[role=menuitem]")?.focus();
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !menu.contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) {
      close();
      btn.focus();
    }
  });
  for (const item of menu.querySelectorAll("[data-action]")) {
    item.addEventListener("click", () => {
      close();
      const what = { profile: "Profile", settings: "Settings", switch: "Switching accounts", logout: "Signing out" }[item.dataset.action];
      toast(`${what} isn't wired up in this demo yet. You're signed in as ${USER.name}.`);
    });
  }
}

function initChrome() {
  initSideNav();
  initProfileMenu();
  initThemeToggle();
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
  const fixed = p ? p.fixed_water_liters_per_hour : 0;
  const inc = p ? p.incremental_water_liters_per_gpu_hour * gpus : 0;
  const activeCap = p?.water_cap_liters_per_hour ?? null;
  const baselineCap = p?.baseline_water_cap_liters_per_hour ?? activeCap;
  return {
    gpus,
    u: cap ? Math.round((gpus / cap) * 100) : 0,
    fixed,
    inc,
    w: fixed + inc,
    c: activeCap,
    scaleC: baselineCap,
    // Red only when job load is what breaches the active (possibly temporary) cap.
    over: activeCap != null && fixed + inc >= activeCap && gpus > 0,
  };
}

/* ---------- flow map (queue → sites) ----------
   A Queue node feeds curved routes into site cards. Each card carries a small glass tank
   (water level = modeled withdrawal, ember line = cap). Routes with work on them carry a
   stream of droplets; thickness follows GPU load. Moves are drawn as routes with a chip:
   dashed = proposed, solid = applied, ember = leaving / infeasible.
   Layout adapts to the width: "one" (queue left, sites right), "two" (queue in the middle,
   sites on both sides) for many sites, "stack" (queue on top, a rail down the list) on phones. */

const ICON_LAYERS = "M12 3 3 7.5l9 4.5 9-4.5L12 3Z M3 12l9 4.5 9-4.5 M3 16.5 12 21l9-4.5";

function niceCeil(v) {
  if (v <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function iconSvg(d, size) {
  return svg("svg", { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, [svg("path", { d })]);
}

function flowMode(width, n, prefer) {
  if (width < 560) return "stack";
  if (prefer !== "one" && n > 5 && width >= 860) return "two";
  return "one";
}

function cubicAt(p0, p1, p2, p3, t) {
  const u = 1 - t;
  const a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
  return [a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]];
}

/**
 * view: {
 *   prefer: "auto" | "one", meter: bool, loading: bool,
 *   hub:   { title, sub, count, aria },
 *   sites: [{ key, name, fig, figTone: ""|"over"|"good", sub, w, c, ghost, weight 0..1, active,
 *             over, selected, highlight, aria, onClick, land }],
 *   routes: [{ key, to, kind: "proposed"|"applied"|"place"|"sel"|"leave"|"infeasible", text, stale }],
 *   drops: [{ key, to }], falls: [{ key, to }]
 * }
 */
function drawFlow(root, view) {
  const reduced = prefersReducedMotion();
  const n = view.sites.length;
  const mode = flowMode(root.clientWidth || 800, n, view.prefer);
  const sig = `${mode}|${view.meter ? 1 : 0}|${view.sites.map((s) => `${s.key}${s.onClick ? "*" : ""}`).join(",")}`;
  let F = root._fl;
  if (!F || F.sig !== sig) {
    if (F && F.ro) F.ro.disconnect();
    root.replaceChildren();
    root.classList.remove("flow--one", "flow--two", "flow--stack");
    root.classList.add("flow", `flow--${mode}`);
    const svgEl = svg("svg", { class: "flowSvg", "aria-hidden": "true" });
    const edgesG = svg("g");
    const portsG = svg("g");
    const fxG = svg("g");
    svgEl.append(edgesG, portsG, fxG);

    const hubTitle = el("span", { className: "fHubTitle" });
    const hubSub = el("span", { className: "fHubSub" });
    const hubIcon = el("span", { className: "fHubIcon" }, [iconSvg(ICON_LAYERS, 22)]);
    const hubCount = el("span", { className: "fHubCount mono" });
    const hub = el("div", { className: "fHub", role: "group" }, [hubIcon, el("span", { className: "fHubText" }, [hubTitle, hubSub]), hubCount]);

    const colA = el("div", { className: "fCol fColA", role: "list" });
    const colB = mode === "two" ? el("div", { className: "fCol fColB", role: "list" }) : null;
    const chipsEl = el("div", { className: "fChips", "aria-hidden": "true" });
    root.append(svgEl, hub, colA);
    if (colB) root.append(colB);
    root.append(chipsEl);

    const split = mode === "two" ? Math.ceil(n / 2) : n;
    const nodes = new Map();
    view.sites.forEach((s, i) => {
      const side = i < split ? "A" : "B";
      const water = el("span", { className: "fWater" });
      const capLine = el("span", { className: "fTankCap" });
      const tank = el("span", { className: "fTank", "aria-hidden": "true" }, [water, capLine]);
      const name = el("span", { className: "fName" });
      const fig = el("span", { className: "fFig mono" });
      const sub = el("span", { className: "fSub" });
      const body = [el("span", { className: "fTop" }, [name, fig])];
      let meter = null;
      if (view.meter) {
        meter = {
          fill: el("span", { className: "fMeterFill" }),
          cap: el("span", { className: "fMeterCap" }),
          ghost: el("span", { className: "fMeterGhost" }),
        };
        body.push(el("span", { className: "fMeter", "aria-hidden": "true" }, [meter.fill, meter.ghost, meter.cap]));
      }
      body.push(sub);
      const node = s.onClick
        ? el("button", { type: "button", className: "fNode" }, [tank, el("span", { className: "fBody" }, body)])
        : el("div", { className: "fNode", role: "listitem" }, [tank, el("span", { className: "fBody" }, body)]);
      if (s.onClick) node.addEventListener("click", () => node._onClick && node._onClick());
      const wrap = s.onClick ? el("div", { role: "listitem", className: "fItem" }, [node]) : node;
      (side === "A" ? colA : colB).appendChild(wrap);

      const base = svg("path", { class: "eBase" });
      const flow = svg("path", { class: "eFlow", pathLength: 100 });
      const g = svg("g", { class: "fEdge" }, [base, flow]);
      edgesG.appendChild(g);
      const end = svg("circle", { r: 3.5, class: "ePort" });
      portsG.appendChild(end);
      nodes.set(s.key, { node, side, water, capLine, name, fig, sub, meter, g, base, flow, end, landKey: "" });
    });
    const hubPorts = [svg("circle", { r: 5, class: "eHubPort" }), svg("circle", { r: 5, class: "eHubPort" })];
    portsG.append(...hubPorts);

    F = root._fl = { sig, mode, svg: svgEl, fxG, hub, hubIcon, hubTitle, hubSub, hubCount, chipsEl, nodes, hubPorts, paths: new Map(), drops: new Set(), falls: new Set(), lastCount: null };
    if ("ResizeObserver" in window) {
      let w0 = root.clientWidth;
      F.ro = new ResizeObserver(() => {
        if (root.clientWidth === w0 && F.paths.size) return flowGeometry(root);
        w0 = root.clientWidth;
        if (flowMode(w0, n, F.view.prefer) !== F.mode) drawFlow(root, F.view);
        else flowGeometry(root);
      });
      F.ro.observe(root);
    }
  }
  F.view = view;

  // ---- hub ----
  const h = view.hub || {};
  F.hubTitle.textContent = h.title || "Queue";
  F.hubSub.textContent = h.sub || "";
  const count = h.count ?? 0;
  F.hubCount.textContent = String(count);
  F.hubCount.hidden = !count;
  if (F.lastCount != null && count !== F.lastCount && !reduced) {
    F.hubCount.classList.remove("bump");
    void F.hubCount.offsetWidth;
    F.hubCount.classList.add("bump");
  }
  F.lastCount = count;
  F.hub.setAttribute("aria-label", h.aria || `${h.title || "Queue"}: ${h.sub || ""}`);
  F.hub.classList.toggle("isBusy", Boolean(view.loading));

  // ---- site cards ----
  const routeTo = new Map();
  for (const r of view.routes || []) if (!routeTo.has(r.to)) routeTo.set(r.to, r);
  for (const s of view.sites) {
    const N = F.nodes.get(s.key);
    // Scale against the permanent baseline cap when a temporary emergency cap is active,
    // so evacuating jobs visibly lowers the bar instead of re-normalizing to a tiny temp cap.
    const scale = Math.max(s.w || 0, s.scaleC || 0, s.c || 0, s.ghost || 0, 1) / 0.82;
    const frac = (v) => `${(Math.max(0, Math.min(1, v / scale)) * 100).toFixed(1)}%`;
    N.water.style.height = frac(s.w || 0);
    N.capLine.style.bottom = s.c != null ? frac(s.c) : "";
    N.capLine.hidden = s.c == null;
    if (N.meter) {
      N.meter.fill.style.width = frac(s.w || 0);
      N.meter.cap.hidden = s.c == null;
      if (s.c != null) N.meter.cap.style.left = frac(s.c);
      const showGhost = s.ghost != null && Math.abs(s.ghost - s.w) > 0.5;
      N.meter.ghost.hidden = !showGhost;
      if (showGhost) N.meter.ghost.style.left = frac(s.ghost);
    }
    N.name.textContent = s.name;
    N.name.title = s.name;
    N.fig.textContent = s.fig || "";
    N.fig.className = `fFig mono${s.figTone ? ` is${s.figTone[0].toUpperCase()}${s.figTone.slice(1)}` : ""}`;
    N.sub.textContent = s.sub || "";
    N.sub.hidden = !s.sub;
    N.node.classList.toggle("isOver", Boolean(s.over));
    N.node.classList.toggle("isSel", Boolean(s.selected));
    N.node.classList.toggle("isHi", Boolean(s.highlight));
    if (s.onClick) {
      N.node._onClick = s.onClick;
      N.node.setAttribute("aria-pressed", s.selected ? "true" : "false");
    }
    if (s.aria) N.node.setAttribute("aria-label", s.aria);
    // land ripple once per key
    if (s.land && s.land !== N.landKey && !reduced) {
      N.node.classList.remove("isLand");
      void N.node.offsetWidth;
      N.node.classList.add("isLand");
      setTimeout(() => N.node.classList.remove("isLand"), 3600);
    }
    N.landKey = s.land || "";

    // edge styling
    const r = routeTo.get(s.key);
    const cls = ["fEdge"];
    if (s.active) cls.push("isActive");
    if (s.over) cls.push("isOver");
    if (s.selected || s.highlight) cls.push("isHi");
    if (r) cls.push(`is${r.kind[0].toUpperCase()}${r.kind.slice(1)}`);
    if (r && r.stale) cls.push("isStale");
    if (view.loading) cls.push("isScan");
    if (reduced) cls.push("isStill");
    N.g.setAttribute("class", cls.join(" "));
    N.g.style.setProperty("--ew", (1.6 + 3.2 * Math.max(0, Math.min(1, s.weight || 0))).toFixed(2));
    N.end.setAttribute("class", `ePort${r ? ` is${r.kind[0].toUpperCase()}${r.kind.slice(1)}` : s.active ? " isActive" : ""}${s.over ? " isOver" : ""}`);
  }

  // ---- chips ----
  const keep = new Set();
  for (const r of view.routes || []) {
    if (!r.text || !F.nodes.has(r.to)) continue;
    const key = `${r.key}|${r.text}`;
    keep.add(key);
    const chip = keyedChild(F.chipsEl, key, () => el("div", { className: "fChip pop", text: r.text }));
    chip.className = `fChip is${r.kind[0].toUpperCase()}${r.kind.slice(1)}${r.stale ? " isStale" : ""}`;
    chip.dataset.to = r.to;
  }
  pruneKeyed(F.chipsEl, keep);

  flowGeometry(root);

  // ---- motion: a droplet falls into the queue; droplets run along a route ----
  if (reduced) return;
  const runDrop = (to, key) => {
    const P = F.paths.get(to);
    if (!P) return;
    const dot = svg("circle", { r: 6, class: "fDrop", style: `offset-path:path('${P.d}');offset-rotate:0deg` });
    F.fxG.appendChild(dot);
    setTimeout(() => dot.remove(), 2200);
    const N = F.nodes.get(to);
    if (N) {
      setTimeout(() => {
        N.node.classList.remove("isLand");
        void N.node.offsetWidth;
        N.node.classList.add("isLand");
        setTimeout(() => N.node.classList.remove("isLand"), 3600);
      }, 300);
    }
  };
  for (const d of view.drops || []) {
    if (F.drops.has(d.key)) continue;
    F.drops.add(d.key);
    runDrop(d.to, d.key);
  }
  for (const f of view.falls || []) {
    if (F.falls.has(f.key)) continue;
    F.falls.add(f.key);
    const drip = el("span", { className: "fFall", "aria-hidden": "true" }, [
      svg("svg", { width: 16, height: 20, viewBox: "-8 -11 16 20" }, [svg("path", { d: dropPath(0, 0, 1), fill: "currentColor" })]),
    ]);
    F.hubIcon.appendChild(drip);
    F.hubIcon.classList.remove("isSplash");
    void F.hubIcon.offsetWidth;
    F.hubIcon.classList.add("isSplash");
    setTimeout(() => {
      drip.remove();
      F.hubIcon.classList.remove("isSplash");
    }, 1900);
    if (f.to && f.to !== "queue") setTimeout(() => runDrop(f.to, f.key), 950);
  }
}

/** Measure the cards and draw every route; also called on resize. */
function flowGeometry(root) {
  const F = root._fl;
  if (!F) return;
  const R = root.getBoundingClientRect();
  if (!R.width || !R.height) return;
  const rel = (e) => {
    const r = e.getBoundingClientRect();
    return { l: r.left - R.left, r: r.right - R.left, t: r.top - R.top, b: r.bottom - R.top, cy: (r.top + r.bottom) / 2 - R.top };
  };
  F.svg.setAttribute("viewBox", `0 0 ${R.width.toFixed(1)} ${R.height.toFixed(1)}`);
  const H = rel(F.hub);
  const f1 = (v) => v.toFixed(1);
  const used = { A: false, B: false };
  for (const [key, N] of F.nodes) {
    const nd = rel(N.node);
    let d;
    let chip;
    let q;
    if (F.mode === "stack") {
      const x0 = H.l + 26;
      const y0 = H.b;
      q = [nd.l, nd.cy];
      d = `M ${f1(x0)} ${f1(y0)} V ${f1(q[1] - 12)} Q ${f1(x0)} ${f1(q[1])} ${f1(x0 + 12)} ${f1(q[1])} H ${f1(q[0])}`;
      chip = [nd.r - 6, nd.t];
    } else {
      const left = F.mode === "two" && N.side === "A";
      used[left ? "A" : "B"] = true;
      const p = left ? [H.l, H.cy] : [H.r, H.cy];
      q = left ? [nd.r, nd.cy] : [nd.l, nd.cy];
      const dx = (q[0] - p[0]) * 0.55;
      const c1 = [p[0] + dx, p[1]];
      const c2 = [q[0] - dx, q[1]];
      d = `M ${f1(p[0])} ${f1(p[1])} C ${f1(c1[0])} ${f1(c1[1])} ${f1(c2[0])} ${f1(c2[1])} ${f1(q[0])} ${f1(q[1])}`;
      chip = cubicAt(p, c1, c2, q, 0.6);
    }
    N.base.setAttribute("d", d);
    N.flow.setAttribute("d", d);
    setAttrs(N.end, { cx: f1(q[0]), cy: f1(q[1]) });
    F.paths.set(key, { d, chip });
  }
  const [pa, pb] = F.hubPorts;
  if (F.mode === "stack") {
    setAttrs(pa, { cx: f1(H.l + 26), cy: f1(H.b) });
    pb.style.display = "none";
  } else if (F.mode === "two") {
    setAttrs(pa, { cx: f1(H.l), cy: f1(H.cy) });
    setAttrs(pb, { cx: f1(H.r), cy: f1(H.cy) });
    pa.style.display = used.A ? "" : "none";
    pb.style.display = used.B ? "" : "none";
  } else {
    setAttrs(pa, { cx: f1(H.r), cy: f1(H.cy) });
    pb.style.display = "none";
  }
  // chips: place on their route, then nudge apart any that collide
  const chips = Array.from(F.chipsEl.children).map((c) => ({ c, P: F.paths.get(c.dataset.to) })).filter((x) => x.P);
  const boxes = [];
  for (const { c, P } of chips) {
    let [x, y] = P.chip;
    const w = c.offsetWidth || 90;
    const hgt = c.offsetHeight || 22;
    if (F.mode === "stack") x -= w + 4; // right-aligned inside the card corner
    for (let k = 0; k < 6; k++) {
      const hit = boxes.find((b) => Math.abs(b.x - x) < (b.w + w) / 2 + 4 && Math.abs(b.y - y) < (b.h + hgt) / 2 + 2);
      if (!hit) break;
      y = hit.y + (y >= hit.y ? 1 : -1) * ((hit.h + hgt) / 2 + 3);
    }
    boxes.push({ x, y, w, h: hgt });
    c.style.left = `${f1(x)}px`;
    c.style.top = `${f1(y)}px`;
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
 * view: { labels:[hour labels], cur:[L/h], prop:[L/h]|null, caps:[L/h|null],
 *         scaleCaps:[L/h|null]|null, breach:[bool]|null, win:[i0,i1]|null,
 *         nowIdx:number|null, applied:bool, capLabel:string|null }
 */
function drawWaterChart(svgEl, view) {
  let L = svgEl._aq;
  if (!L) {
    svgEl.replaceChildren();
    const hatch = svg("pattern", { id: "hatch", width: 8, height: 8, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, [
      svg("rect", { width: 8, height: 8, fill: "color-mix(in srgb, var(--ember) 6%, transparent)" }),
      svg("line", { x1: 0, y1: 0, x2: 0, y2: 8, stroke: "color-mix(in srgb, var(--ember) 30%, transparent)", "stroke-width": 2 }),
    ]);
    const wipeRect = svg("rect", { x: 0, y: 0, height: 270, class: "wipeRect", style: "width:0px" });
    svgEl.appendChild(svg("defs", null, [hatch, svg("clipPath", { id: "wipe" }, [wipeRect]), svg("linearGradient", { id: "curFill", x1: 0, y1: 0, x2: 0, y2: 1 }, [svg("stop", { offset: "0%", "stop-color": PAL.accent, "stop-opacity": ".32" }), svg("stop", { offset: "100%", "stop-color": PAL.accent, "stop-opacity": "0" })])]));
    const grid = svg("g", { stroke: PAL.grid, "stroke-width": 1 });
    const yLabels = svg("g", { "font-family": FONT, "font-size": 12, fill: PAL.label, "text-anchor": "end" });
    const xLabels = svg("g", { "font-family": FONT, "font-size": 12, fill: PAL.label, "text-anchor": "middle" });
    const win = svg("rect", { y: 28, height: 206, fill: "url(#hatch)", class: "lineT" });
    const viol = svg("path", { fill: "color-mix(in srgb, var(--ember) 28%, transparent)", class: "lineT" });
    const cap = svg("path", { fill: "none", stroke: PAL.ember, "stroke-width": 2, "stroke-dasharray": "6 4", class: "lineT" });
    const cur = svg("path", { fill: "none", stroke: PAL.accent, "stroke-width": 2.5, "stroke-linejoin": "round", class: "lineT" });
    const area = svg("path", { fill: "url(#curFill)", class: "lineT" });
    const prop = svg("path", { fill: "none", stroke: PAL.violet, "stroke-width": 2.5, "stroke-linejoin": "round", class: "lineT" });
    const dots = svg("g");
    const now = svg("g", { class: "nowG" }, [
      svg("line", { x1: 0, x2: 0, y1: 24, y2: 236, stroke: PAL.muted, "stroke-width": 1.2, "stroke-dasharray": "3 4" }),
      svg("text", { x: 6, y: 22, "font-family": FONT, "font-size": 11, fill: PAL.label, "font-weight": 600, text: "Sim now" }),
    ]);
    const capLbl = svg("text", { "font-family": FONT, "font-size": 11, fill: PAL.emberInk, "font-weight": 600, "text-anchor": "middle", class: "lineT" });
    [grid, yLabels, xLabels, win, area, viol, cap, cur, svg("g", { "clip-path": "url(#wipe)" }, [prop, dots]), now, capLbl].forEach((n) => svgEl.appendChild(n));
    L = svgEl._aq = { wipeRect, grid, yLabels, xLabels, win, area, viol, cap, cur, prop, dots, now, capLbl };
  }

  const n = view.labels.length;
  const X = (i) => 64 + (n > 1 ? i * (712 / (n - 1)) : 356);
  const scaleCaps = view.scaleCaps || [];
  const all = [
    ...view.cur,
    ...(view.prop || []),
    ...view.caps.filter((c) => c != null),
    ...scaleCaps.filter((c) => c != null),
  ];
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

  // Violation hatch only where job load breaches the active cap (not fixed-only floor).
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
  view.cur.forEach((v, i) => {
    const jobBreach = view.breach ? view.breach[i] : view.caps[i] != null && v > view.caps[i];
    if (jobBreach) run.push(i);
    else flush();
  });
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
      L.dots.appendChild(svg("circle", { cx: X(i).toFixed(1), cy: Y(v).toFixed(1), r: 5, stroke: PAL.violet, "stroke-width": 2, style: `fill:${view.applied ? PAL.violet : PAL.surface}` }));
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
