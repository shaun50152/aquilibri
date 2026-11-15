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

function formatUtc(isoString) {
  if (!isoString) return "—";
  const d = new Date(isoString);
  return d.toISOString().replace("T", " ").replace(".000Z", " UTC");
}

/** datetime-local value from UTC ISO (for demo anchor alignment). */
function toDatetimeLocalValue(isoString) {
  const d = new Date(isoString);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/** Interpret datetime-local control as UTC (demo convention). */
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