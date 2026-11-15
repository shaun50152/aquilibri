# API contract

All timestamps are **timezone-aware UTC** (ISO-8601 with offset or `Z`). Frontend `datetime-local` fields are interpreted as UTC in the scaffold demo.

## Working endpoints

### `GET /api/health`

```json
{
  "status": "ok",
  "data_mode": "synthetic",
  "sites_source": "fixtures",
  "demo_session_id": "demo-session-hackuta-2026"
}
```

`data_mode` is `synthetic` (local fixtures) or `live` (MongoDB + Tiger). `sites_source` is `fixtures` or `tiger`. Never includes API keys or connection strings.

### `GET /api/jobs`

```json
{
  "session_id": "demo-session-hackuta-2026",
  "revision": 1,
  "jobs": [ { "id": "job-hackuta-0001", "status": "scheduled", "...": "..." } ]
}
```

### `GET /api/jobs/{job_id}/summary`

xAI Grok executive summary for a **completed** job. Returns two sentences of ops copy for the portal panel above the sample-result download.

```json
{
  "job_id": "job-hackuta-0001",
  "summary": "…",
  "model": "grok-3-mini",
  "provider": "xai",
  "metrics": { "site_name": "US-East-1 (Ashburn, VA)", "water_liters": 208.0, "cost_usd": 0.4 }
}
```

Errors: `404` if the job is missing or not completed; `503` if `GROK_API_KEY` is missing or the xAI call fails. Does not modify Gemini or Tiger paths.

### `POST /api/jobs` → `201`

Request:

```json
{
  "template": "inference_batch",
  "earliest_start": "2026-03-15T18:00:00+00:00",
  "deadline": "2026-03-16T06:00:00+00:00"
}
```

Server fills `gpu_count`, `duration_hours`, `owner_id` (`demo-user-001`), and `eligible_site_ids` from template definitions.

Errors: `422` for unknown template, `deadline <= earliest_start`, or invalid body.

### `GET /api/sites`

Returns `SiteProfile` list with hourly water/GPU/energy fields (liters, kWh, USD/kWh).

Source follows `DATA_MODE`:

| Mode | Source |
|------|--------|
| `synthetic` | Local fixture selected by `SITE_PROFILES_FIXTURE` |
| `live` | Tiger Data only (empty list if Tiger is empty/unavailable — no fixture fallback) |

| `SITE_PROFILES_FIXTURE` | Role (synthetic mode) |
|-------|------|
| `sites.json` | Short 3-hour synthetic horizon (missing-coverage tests) |
| `sites_extended_synthetic.json` | 18-hour explicitly synthetic horizon for runnable previews |

### `GET /api/demo`

```json
{
  "session_id": "demo-session-hackuta-2026",
  "current_time": "2026-03-15T18:00:00+00:00",
  "revision": 1
}
```

### `POST /api/demo/reset`

- `DATA_MODE=synthetic` — restores fixture jobs, resets demo clock to anchor, increments `revision`.
- `DATA_MODE=live` — deletes session jobs/notifications in MongoDB (blank slate), resets clock, increments `revision`.

## Reallocation preview (implemented)

### `POST /api/reallocation/preview`

Structured request (Gemini NL-only interpretation is not required):

```json
{
  "mode": "routine",
  "max_additional_cost_usd": 25.0
}
```

Omitted `max_additional_cost_usd` is treated as **0.0**.

Emergency:

```json
{
  "mode": "emergency",
  "emergency_restriction": {
    "site_id": "us-west-1",
    "water_cap_liters_per_hour": 115.0,
    "start": "2026-03-15T20:00:00+00:00",
    "end": "2026-03-16T12:00:00+00:00"
  }
}
```

In routine mode, `metrics_before` is the **computed reference schedule** (`metrics_before_label=computed_reference_schedule`), not the operator’s currently saved placements. In emergency mode, `metrics_before` is the **saved schedule left as-is under the temporary restriction** (`metrics_before_label=saved_schedule_under_restriction`); jobs with no saved placement use the proposed placement on both sides, so the delta reflects only the emergency moves. `proposed_job_changes[].old_placement` still reflects saved jobs. Infeasible previews set `metrics_after` to `null` rather than inventing zeros.

### Cost assumptions

- Electricity: GPU × overlap hours × `energy_kwh_per_gpu_hour` × `electricity_usd_per_kwh`
- Unfulfilled: `miss_penalty_usd`
- `water_usd`: always 0 (no water tariff in schema)
- No transfer or delay charges

### `POST /api/operator/chat`

Gemini tool-calling assistant. Same placement engine as manual preview.

```json
{ "prompt": "Emergency: cap Alpha at 115 L/h from 2026-03-15T20:00:00Z to 2026-03-16T12:00:00Z" }
```

Returns `assistant_message`, `tool_calls`, and optional `preview` (`ReallocationPreviewResponse`). Manual `POST /api/reallocation/preview` remains fully functional.

### `POST /api/reallocation/apply`

```json
{ "preview_id": "preview-…", "input_revision": 2 }
```

Success: `{ "applied": true, "revision": 3, "message": "…" }`. Stale previews → **409**.

### `POST /api/demo/advance`

Body (optional — defaults to **1 hour**):

```json
{ "hours": 1, "expected_revision": 2 }
```

Success:

```json
{
  "session_id": "demo-session-hackuta-2026",
  "revision": 3,
  "previous_time": "2026-03-15T18:00:00+00:00",
  "current_time": "2026-03-15T19:00:00+00:00",
  "hours_advanced": 1,
  "jobs_started": ["job-hackuta-0001"],
  "jobs_completed": [],
  "jobs_failed": []
}
```

Invalid `hours` (≤ 0, non-finite) → **422**. Stale `expected_revision` → **409**.

### `GET /api/notifications`

Scoped to the demo session and owner `demo-user-001`:

```json
{
  "session_id": "demo-session-hackuta-2026",
  "owner_id": "demo-user-001",
  "revision": 4,
  "notifications": [
    {
      "id": "notif-job-hackuta-0001-e1",
      "job_id": "job-hackuta-0001",
      "completed_at": "2026-03-15T21:00:00+00:00",
      "message": "Simulated workload job-hackuta-0001 finished.",
      "result_url": "/api/jobs/job-hackuta-0001/result",
      "dedupe_key": "demo-session-hackuta-2026:job-hackuta-0001:e1"
    }
  ]
}
```

### `GET /api/jobs/{job_id}/result`

Completed jobs only. Returns **`text/csv`** attachment (`{job_id}-simulated-result.csv`) built from the immutable `result_snapshot` on the job document.

### Emergency restriction validation

`emergency_restriction` requires known `site_id`, `water_cap_liters_per_hour >= 0`, timezone-aware `start`/`end`, and `end > start`. Invalid bodies return `422`.

## Revision and stale previews

- `revision` increases on job create, demo reset, successful apply, and each demo advance.
- Preview responses include `input_revision`; apply must match current revision or be rejected (future).

## Sample preview JSON

Files under `fixtures/preview_*.json` are **sample data** for UI work. They validate against `ReallocationPreviewResponse` but are not returned by the API until scheduling is implemented.