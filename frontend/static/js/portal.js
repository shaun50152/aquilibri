const DEMO_ANCHOR = "2026-03-15T18:00:00+00:00";

const form = document.getElementById("job-form");
const submitBtn = document.getElementById("submit-btn");
const formError = document.getElementById("form-error");
const listError = document.getElementById("list-error");
const jobsTable = document.getElementById("jobs-table");
const jobsBody = document.getElementById("jobs-body");
const jobsEmpty = document.getElementById("jobs-empty");

document.getElementById("earliest_start").value = toDatetimeLocalValue(DEMO_ANCHOR);
document.getElementById("deadline").value = toDatetimeLocalValue("2026-03-17T00:00:00+00:00");

function textCell(text) {
  const td = document.createElement("td");
  td.textContent = text ?? "—";
  return td;
}

function renderJobs(jobs) {
  jobsBody.replaceChildren();
  const hasJobs = jobs && jobs.length > 0;
  jobsTable.hidden = !hasJobs;
  jobsEmpty.hidden = hasJobs;

  for (const job of jobs || []) {
    const tr = document.createElement("tr");
    tr.appendChild(textCell(job.id));
    tr.appendChild(textCell(job.template));
    const statusTd = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "status-pill";
    pill.textContent = job.status;
    statusTd.appendChild(pill);
    tr.appendChild(statusTd);
    tr.appendChild(textCell(job.assigned_site_id));
    tr.appendChild(textCell(formatUtc(job.scheduled_start)));
    tr.appendChild(textCell(formatUtc(job.deadline)));
    jobsBody.appendChild(tr);
  }
}

async function loadDemo() {
  const demo = await apiRequest("/api/demo");
  document.getElementById("demo-clock").textContent = formatUtc(demo.current_time);
  document.getElementById("demo-session").textContent = demo.session_id;
  document.getElementById("demo-revision").textContent = String(demo.revision);
}

async function loadJobs() {
  setError(listError, null);
  try {
    const data = await apiRequest("/api/jobs");
    renderJobs(data.jobs);
    document.getElementById("demo-revision").textContent = String(data.revision);
  } catch (err) {
    setError(listError, err.message);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError(formError, null);
  submitBtn.disabled = true;
  try {
    const template = document.getElementById("template").value;
    const earliest = datetimeLocalToUtcIso(document.getElementById("earliest_start").value);
    const deadline = datetimeLocalToUtcIso(document.getElementById("deadline").value);
    await apiRequest("/api/jobs", {
      method: "POST",
      body: { template, earliest_start: earliest, deadline },
    });
    await loadJobs();
    await loadDemo();
  } catch (err) {
    setError(formError, err.message);
  } finally {
    submitBtn.disabled = false;
  }
});

document.getElementById("refresh-btn").addEventListener("click", () => {
  loadJobs();
  loadDemo();
});

loadDemo();
loadJobs();