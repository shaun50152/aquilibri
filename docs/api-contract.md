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

## Planned endpoints (501 until implemented)

| Method | Path | Request highlights |
|--------|------|--------------------|
| POST | `/api/reallocation/preview` | `mode`, optional NL instruction, `max_additional_cost_usd`, `emergency_restriction` |
| POST | `/api/reallocation/apply` | `preview_id`, `input_revision` |
| POST | `/api/demo/advance` | (no body) advances simulated clock |

`501` body: `{ "detail": "<reason>" }`.

### Emergency restriction validation

`emergency_restriction` requires known `site_id`, `water_cap_liters_per_hour >= 0`, and `end > start`. Invalid bodies return `422` before the 501 handler runs (once implemented, same validation applies).

## Revision and stale previews

- `revision` increases on job create, demo reset, and (future) successful apply.
- Preview responses include `input_revision`; apply must match current revision or be rejected (future).

## Sample preview JSON

Files under `fixtures/preview_*.json` are **sample data** for UI work. They validate against `ReallocationPreviewResponse` but are not returned by the API until scheduling is implemented.