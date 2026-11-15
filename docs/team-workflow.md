# Team workflow (two-person HackUTA)

## Ownership

### Person 1 — portal & job lifecycle

- `frontend/`
- `app/main.py` (router registration)
- `app/api/jobs.py`, `app/api/operator.py`
- `app/services/job_service.py`, `app/services/demo_worker.py`
- `app/repositories/job_repository.py`
- `app/integrations/mongodb.py`

### Person 2 — scheduler & telemetry integrations

- `app/api/sites.py`, `app/api/scheduling.py`
- `app/services/scheduling_service.py`
- `app/repositories/telemetry_repository.py`
- `app/integrations/tigerdata.py`, `app/integrations/gemini.py`

### Shared (coordinate before merging)

- `app/schemas/`
- `app/config.py`
- `requirements.txt`
- `fixtures/`
- `docs/`

Person 1 wires routers in `main.py`. Person 2 implements service functions and API handlers; Person 1 reviews integration points.

## Branches

- `feat/portal-lifecycle` — Person 1
- `feat/scheduler-integrations` — Person 2

Open small PRs against `main`. Rebase or merge frequently to avoid schema drift.

## Local development

Each developer runs their own `uvicorn` process. **In-memory state is per process** — use separate `DEMO_SESSION_ID` values later when MongoDB is enabled. GitHub shares code; running servers do not share memory.

## First shared checkpoint

- [ ] Both pages load (`/portal`, `/operator`)
- [ ] `GET /api/health` shows `fixtures` mode
- [ ] Job submit + list works end-to-end
- [ ] Operator reset restores three seeded jobs and bumps revision
- [ ] `POST /api/reallocation/preview` returns a real typed preview (`feasible` / `infeasible`)
- [ ] `SITE_PROFILES_FIXTURE=sites_extended_synthetic.json` for runnable long-horizon demos
- [ ] Sample preview JSON files validate against schemas
- [ ] `pytest` passes locally