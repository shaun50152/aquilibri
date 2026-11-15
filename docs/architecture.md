# Architecture

## Folder responsibilities

| Path | Role |
|------|------|
| `app/main.py` | FastAPI app, static/page routes, router registration, lifespan state init |
| `app/config.py` | Environment settings, paths relative to repo root, supported `DATA_MODE` |
| `app/state.py` | Single in-memory session (jobs, sites snapshot, revision, demo clock) |
| `app/schemas/` | Shared Pydantic contracts (jobs, sites, scheduling, demo) |
| `app/api/` | HTTP routers — thin validation + service calls |
| `app/services/` | Business logic: job lifecycle, scheduling, demo worker |
| `app/repositories/` | Persistence boundaries (jobs, telemetry) |
| `app/integrations/` | MongoDB, Tiger Data, Gemini clients (stubs raise `NotImplementedError`) |
| `frontend/` | Plain HTML/CSS/JS served by FastAPI |
| `fixtures/` | Local JSON data + sample preview payloads |

## Request flow

```
Browser (portal/operator)
  → fetch /api/* on same origin
  → FastAPI router (app/api)
  → service (app/services)
  → repository (app/repositories) → session state or future external store
```

Scheduling previews and apply will follow:

```
Operator UI
  → POST /api/reallocation/preview
  → scheduling_service.preview_reallocation
      → gemini.py (interpret instruction, optional)
      → scheduling_service.compute_proposed_placements (SciPy MILP)
      → job snapshot + site profiles + demo clock from state
  → POST /api/reallocation/apply
  → scheduling_service.apply_reallocation
      → job_repository (update placements, bump revision)
```

Job submission and reset stay on the job lifecycle side (`job_service`, `job_repository`, MongoDB later).

## Cloud integration boundaries

| Integration | Module | Consumed by |
|-------------|--------|-------------|
| MongoDB Atlas | `integrations/mongodb.py` | `repositories/job_repository.py` |
| Tiger Data | `integrations/tigerdata.py` | `repositories/telemetry_repository.py` |
| Gemini API | `integrations/gemini.py` | `services/scheduling_service.py` |

Clients must not connect at import time. Credentials stay in backend `.env` only.

## Demo state

- **Anchor time:** `2026-03-15T18:00:00+00:00` (see `DEMO_CLOCK_ANCHOR_ISO` in config).
- **DATA_MODE:** `synthetic` loads local fixtures; `live` uses MongoDB + Tiger and boots empty when those stores are empty (no mock seed).
- **Process lifetime:** Synthetic in-memory state is lost on restart (fixtures reload). Live jobs survive in Atlas across restarts.
- **Reset:** `POST /api/demo/reset` restores fixtures in synthetic mode, or wipes the Mongo session to a blank slate in live mode; always increments `revision` (stale previews).