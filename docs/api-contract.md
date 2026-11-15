# API contract

All timestamps are **timezone-aware UTC** (ISO-8601 with offset or `Z`). Frontend `datetime-local` fields are interpreted as UTC in the scaffold demo.

## Working endpoints

### `GET /api/health`

```json
{
  "status": "ok",
  "data_mode": "fixtures",
  "demo_session_id": "demo-session-hackuta-2026"
}
```

Never includes API keys or connection strings.

### `GET /api/jobs`

```json
{
  "session_id": "demo-session-hackuta-2026",
  "revision": 1,
  "jobs": [ { "id": "job-hackuta-0001", "status": "scheduled", "...": "..." } ]
}
```

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

Profiles come from the same in-memory session snapshot the optimizer uses. Select the fixture with `SITE_PROFILES_FIXTURE`:

| Value | Role |
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

Restores fixture jobs, resets demo clock to anchor, increments `revision`.

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
    "site_id": "site-alpha",
    "water_cap_liters_per_hour": 115.0,
    "start": "2026-03-15T20:00:00+00:00",
    "end": "2026-03-16T12:00:00+00:00"
  }
}
```

`metrics_before` is the **computed reference schedule** (`metrics_before_label=computed_reference_schedule`), not the operator’s currently saved placements. `proposed_job_changes[].old_placement` still reflects saved jobs. Infeasible previews set `metrics_after` to `null` rather than inventing zeros.

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

## Still 501

| Method | Path | Request highlights |
|--------|------|--------------------|
| POST | `/api/demo/advance` | (no body) advances simulated clock |

`501` body: `{ "detail": "<reason>" }`.

### Emergency restriction validation

`emergency_restriction` requires known `site_id`, `water_cap_liters_per_hour >= 0`, timezone-aware `start`/`end`, and `end > start`. Invalid bodies return `422`.

## Revision and stale previews

- `revision` increases on job create, demo reset, and (future) successful apply.
- Preview responses include `input_revision`; apply must match current revision or be rejected (future).

## Sample preview JSON

Files under `fixtures/preview_*.json` are **sample data** for UI work. They validate against `ReallocationPreviewResponse` but are not returned by the API until scheduling is implemented.