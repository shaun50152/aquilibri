const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "className") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("aria")) node.setAttribute(k.replace(/([A-Z])/g, "-$1").toLowerCase(), v);
      else if (k === "dataset" && v && typeof v === "object") {
        Object.assign(node.dataset, v);
      } else if (k === "disabled") node.disabled = Boolean(v);
      else if (v != null) node.setAttribute(k, v);
    }
  }
  if (children) {
    for (const child of children) {
      if (child == null) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
  }
  return node;
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const STATUS_LABELS = {
  queued: "queued",
  scheduled: "scheduled",
  running: "running",
  completed: "completed",
  failed: "failed",
  unfulfilled: "unfulfilled",
  proposed: "proposed",
  applied: "applied",
};

function statusPill(status) {
  const s = status || "queued";
  const wrap = el("span", { className: `pill pill--${s}`, role: "status" });
  const shape = el("span", { className: `pill-shape pill-shape--${s}`, text: "" });
  const word = el("span", { className: "pill-word", text: STATUS_LABELS[s] || s });
  wrap.appendChild(shape);
  wrap.appendChild(word);
  wrap.setAttribute("aria-label", `Status: ${STATUS_LABELS[s] || s}`);
  return wrap;
}

function chip(kind, text) {
  return el("span", { className: `chip chip--${kind}`, text });
}

let toastRoot;
function toast(message) {
  if (!toastRoot) {
    toastRoot = el("div", {
      className: "toast-stack",
      role: "status",
      "aria-live": "polite",
    });
    document.body.appendChild(toastRoot);
  }
  const item = el("div", { className: "toast", text: message });
  toastRoot.appendChild(item);
  window.setTimeout(() => item.remove(), 5000);
}

function visualDemoTag() {
  return el("span", { className: "visual-demo-tag", text: "VISUAL DEMO · labeled sample data" });
}

function unavailablePanel(message) {
  const panel = el("div", { className: "unavailable-panel", role: "status" });
  panel.appendChild(
    el("p", {
      className: "unavailable-panel__title",
      text: "Not available yet. This feature is waiting on the scheduling service. Nothing is being faked.",
    }),
  );
  if (message) panel.appendChild(el("p", { className: "small muted", text: message }));
  return panel;
}

function polar(cx, cy, r, angleDeg) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function describeArc(cx, cy, r, startDeg, endDeg) {
  const start = polar(cx, cy, r, endDeg);
  const end = polar(cx, cy, r, startDeg);
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${large} 0 ${end.x} ${end.y}`;
}

/**
 * @param {SVGElement} svgEl
 * @param {Array<{site_id:string, name?:string, loadRatio:number, overCap?:boolean, angleDeg:number}>} sites
 * @param {{proposed?:Array, applied?:Array, onNodeClick?:Function, fleetWater?:string}} opts
 */
function drawRing(svgEl, sites, opts = {}) {
  svgEl.replaceChildren();
  const cx = 360;
  const cy = 360;
  const nodeR = 240;
  const arcR = 52;

  const defs = document.createElementNS(SVG_NS, "defs");
  svgEl.appendChild(defs);

  const orb = document.createElementNS(SVG_NS, "circle");
  orb.setAttribute("cx", String(cx));
  orb.setAttribute("cy", String(cy));
  orb.setAttribute("r", "72");
  orb.setAttribute("class", "ring-orb");
  svgEl.appendChild(orb);

  if (opts.fleetWater) {
    const fo = document.createElementNS(SVG_NS, "foreignObject");
    fo.setAttribute("x", String(cx - 80));
    fo.setAttribute("y", String(cy - 24));
    fo.setAttribute("width", "160");
    fo.setAttribute("height", "48");
    const div = el("div", { className: "ring-orb-readout num", text: opts.fleetWater });
    fo.appendChild(div);
    svgEl.appendChild(fo);
  }

  sites.forEach((site) => {
    const angle = site.angleDeg;
    const nx = polar(cx, cy, nodeR, angle).x;
    const ny = polar(cx, cy, nodeR, angle).y;
    const load = Math.min(1, Math.max(0, site.loadRatio || 0));
    const sweep = Math.max(6, load * 300);

    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("class", "ring-node-group");

    const track = document.createElementNS(SVG_NS, "path");
    track.setAttribute("d", describeArc(nx, ny, arcR, 0, 359.9));
    track.setAttribute("class", "ring-track");
    g.appendChild(track);

    const fill = document.createElementNS(SVG_NS, "path");
    fill.setAttribute("d", describeArc(nx, ny, arcR, 0, sweep));
    fill.setAttribute("class", site.overCap ? "ring-load ring-load--alert" : "ring-load");
    g.appendChild(fill);

    if (site.overCap) {
      const capRing = document.createElementNS(SVG_NS, "circle");
      capRing.setAttribute("cx", String(nx));
      capRing.setAttribute("cy", String(ny));
      capRing.setAttribute("r", String(arcR + 6));
      capRing.setAttribute("class", "ring-cap-alert");
      g.appendChild(capRing);
    }

    const btn = document.createElementNS(SVG_NS, "foreignObject");
    btn.setAttribute("x", String(nx - 44));
    btn.setAttribute("y", String(ny + arcR + 4));
    btn.setAttribute("width", "88");
    btn.setAttribute("height", "48");
    const b = el("button", {
      type: "button",
      className: "ring-node-btn",
      "aria-label": `${site.name || site.site_id}, load ${Math.round(load * 100)} percent${site.overCap ? ", over water cap" : ""}`,
    });
    b.appendChild(el("span", { className: "ring-node-name", text: siteShortName(site) }));
    if (opts.onNodeClick) {
      b.addEventListener("click", () => opts.onNodeClick(site));
    }
    btn.appendChild(b);
    g.appendChild(btn);
    svgEl.appendChild(g);
  });

  (opts.proposed || []).forEach((move) => {
    const from = sites.find((s) => s.site_id === move.from);
    const to = sites.find((s) => s.site_id === move.to);
    if (!from || !to) return;
    const line = document.createElementNS(SVG_NS, "path");
    const p1 = polar(cx, cy, nodeR - 20, from.angleDeg);
    const p2 = polar(cx, cy, nodeR - 20, to.angleDeg);
    line.setAttribute("d", `M ${p1.x} ${p1.y} Q ${cx} ${cy} ${p2.x} ${p2.y}`);
    line.setAttribute("class", "ring-ghost-arc");
    svgEl.appendChild(line);
  });

  (opts.applied || []).forEach((move) => {
    const from = sites.find((s) => s.site_id === move.from);
    const to = sites.find((s) => s.site_id === move.to);
    if (!from || !to) return;
    const line = document.createElementNS(SVG_NS, "path");
    const p1 = polar(cx, cy, nodeR - 20, from.angleDeg);
    const p2 = polar(cx, cy, nodeR - 20, to.angleDeg);
    line.setAttribute("d", `M ${p1.x} ${p1.y} Q ${cx} ${cy} ${p2.x} ${p2.y}`);
    line.setAttribute("class", "ring-applied-arc");
    line.setAttribute("id", move.dropletPathId || "droplet-path");
    svgEl.appendChild(line);
  });
}

function buildHourlyWaterChart(svgEl, profile, cap) {
  svgEl.replaceChildren();
  if (!profile || !profile.length) return;
  const w = 400;
  const h = 120;
  svgEl.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const maxY = Math.max(...profile.map((p) => p.water), cap || 0, 1);
  profile.forEach((p, i) => {
    const barW = w / profile.length - 4;
    const x = i * (w / profile.length) + 2;
    const barH = (p.water / maxY) * (h - 24);
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(h - barH - 8));
    rect.setAttribute("width", String(barW));
    rect.setAttribute("height", String(barH));
    rect.setAttribute("class", "chart-bar");
    svgEl.appendChild(rect);
  });
  if (cap != null) {
    const y = h - (cap / maxY) * (h - 24) - 8;
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", "0");
    line.setAttribute("x2", String(w));
    line.setAttribute("y1", String(y));
    line.setAttribute("y2", String(y));
    line.setAttribute("class", "chart-cap-line");
    svgEl.appendChild(line);
  }
}