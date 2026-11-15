async function apiRequest(path, options = {}) {
  const config = {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  };

  if (config.body && typeof config.body === "object" && !(config.body instanceof FormData)) {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }

  let response;
  try {
    response = await fetch(path, config);
  } catch (err) {
    throw new Error("Network error — is the API server running?");
  }

  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }

  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || payload?.message || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

async function apiRequestAllow501(path, options = {}) {
  const config = {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  };
  if (config.body && typeof config.body === "object" && !(config.body instanceof FormData)) {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  let response;
  try {
    response = await fetch(path, config);
  } catch {
    throw new Error("Network error — is the API server running?");
  }
  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }
  if (response.status === 501) {
    const detail = payload?.detail || "Not implemented";
    return { unavailable: true, detail: typeof detail === "string" ? detail : String(detail) };
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function getHealth() {
  return apiRequest("/api/health");
}
function getDemo() {
  return apiRequest("/api/demo");
}
function getJobs() {
  return apiRequest("/api/jobs");
}
function getSites() {
  return apiRequest("/api/sites");
}
function postJob(body) {
  return apiRequest("/api/jobs", { method: "POST", body });
}
function resetDemo() {
  return apiRequest("/api/demo/reset", { method: "POST" });
}
function previewReallocation(body) {
  return apiRequestAllow501("/api/reallocation/preview", { method: "POST", body });
}
function applyReallocation(body) {
  return apiRequest("/api/reallocation/apply", { method: "POST", body });
}
function advanceDemo(body = {}) {
  const payload = { hours: body.hours ?? 1, ...(body.expected_revision != null ? { expected_revision: body.expected_revision } : {}) };
  return apiRequest("/api/demo/advance", { method: "POST", body: payload });
}
function getNotifications() {
  return apiRequest("/api/notifications");
}

function formatUtc(isoString) {
  if (!isoString) return "—";
  const d = new Date(isoString);
  return d.toISOString().replace("T", " ").replace(".000Z", " UTC");
}

function toDatetimeLocalValue(isoString) {
  const d = new Date(isoString);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function addHoursToIso(isoString, hours) {
  const ms = new Date(isoString).getTime() + hours * 60 * 60 * 1000;
  return new Date(ms).toISOString();
}

function datetimeLocalToUtcIso(value) {
  if (!value) return null;
  return `${value}:00.000Z`;
}

function setError(el, message) {
  if (!el) return;
  if (!message) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = message;
}

function formatAssignment(value) {
  if (value == null || value === "") return "Unassigned";
  return value;
}

function shortJobId(id) {
  if (!id) return "—";
  const m = id.match(/(\d+)$/);
  const num = m ? String(parseInt(m[1], 10)).padStart(3, "0") : "000";
  return `JOB-${num}`;
}

function siteShortName(site) {
  if (!site) return "—";
  if (typeof site === "string") {
    return site.replace(/^site-/, "").replace(/-/g, " ").slice(0, 12) || site;
  }
  return site.name || site.site_id;
}

function hourLabel(isoString) {
  if (!isoString) return "—";
  const d = new Date(isoString);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getUTCHours())}:00 UTC`;
}

const VISUAL_DEMO_KEY = "aq_visual_demo";

function isVisualDemoMode() {
  if (typeof window === "undefined") return false;
  if (new URLSearchParams(window.location.search).get("demo") === "1") return true;
  try {
    return window.localStorage.getItem(VISUAL_DEMO_KEY) === "1";
  } catch {
    return false;
  }
}

function setVisualDemoMode(on) {
  try {
    window.localStorage.setItem(VISUAL_DEMO_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
  const url = new URL(window.location.href);
  if (on) url.searchParams.set("demo", "1");
  else url.searchParams.delete("demo");
  window.location.href = url.toString();
}