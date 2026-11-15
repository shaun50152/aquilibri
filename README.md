# aquilibri

**Aqualibri** is an AI-powered, water-aware workload orchestration platform that dynamically optimizes AI data center scheduling and redistributes workloads to reduce water consumption, manage resource constraints, and minimize operational costs.

This repository is the **HackUTA scaffold**: a runnable local demo using fixture-backed jobs and site profiles. Cloud integrations (MongoDB Atlas, Tiger Data, Gemini, SciPy MILP) are stubbed with clear boundaries.

## What works now

- User portal at `/portal` — submit jobs (three templates), list queue, demo clock
- Operator dashboard at `/operator` — sites, shared job queue, reset demo
- REST API with OpenAPI at `/docs`
- In-memory session state with `POST /api/demo/reset`
- Sample reallocation preview JSON under `fixtures/preview_*.json` for UI prototyping

## Not implemented yet

- `POST /api/reallocation/apply` (returns **501** — Person 1; previews are cached for handoff)
- `POST /api/demo/advance` (returns **501**)
- MongoDB, Tiger Data, Gemini NL parsing, simulated job worker ticks

`POST /api/reallocation/preview` is implemented (routine + emergency MILP). Site profiles prefer Tiger Data when `PG*` env vars are set (`python scripts/seed_tiger_sites.py` seeds tables); otherwise the selected `SITE_PROFILES_FIXTURE` is used. `POST /api/operator/chat` adds Gemini tool-calling on top of the same preview engine without replacing the manual preview route.

See `docs/team-workflow.md` for ownership split.

## Prerequisites

- Python 3.11+ recommended
- No database or API keys required for `DATA_MODE=fixtures`

## Setup

### Windows (PowerShell)

```powershell
cd c:\Projects\aquilibri
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# For runnable reallocation demos (6h/12h jobs), ensure:
# SITE_PROFILES_FIXTURE=sites_extended_synthetic.json
```

### macOS / Linux

```bash
cd /path/to/aquilibri
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# For runnable reallocation demos (6h/12h jobs), ensure:
# SITE_PROFILES_FIXTURE=sites_extended_synthetic.json
```

## Run (one command — API + both pages)

From the project root with `.venv` activated:

```powershell
python -m uvicorn app.main:app --reload
```

That single process serves the REST API, static CSS/JS, and the HTML pages. Open a browser (no separate frontend build).

Preview demo (with extended synthetic sites selected in `.env`): open `/docs`, call `GET /api/sites`, then `POST /api/reallocation/preview` with the routine/emergency bodies in `docs/api-contract.md`. Preview does not change saved job assignments, the demo clock, or revision until apply is implemented.

| URL | Purpose |
|-----|---------|
| http://127.0.0.1:8000/portal | User job portal |
| http://127.0.0.1:8000/operator | Operator dashboard |
| http://127.0.0.1:8000/docs | OpenAPI docs |

Root `/` redirects to `/portal`.

**Port already in use (`WinError 10013` on Windows):** another `uvicorn` (or other app) is bound to port 8000. Stop it (Ctrl+C in that terminal) or use another port:

```powershell
python -m uvicorn app.main:app --reload --port 8001
```

Then open http://127.0.0.1:8001/portal instead.

## Fixture state & demo clock

- Demo anchor: **2026-03-15T18:00:00 UTC** (form defaults and fixtures align to this instant).
- Jobs and sites load from `fixtures/` at startup.
- **Reset** (`POST /api/demo/reset` or the operator button) restores the three seeded jobs and increments `revision` (previews tied to an old revision should be treated as stale).
- Restarting the server reloads fixtures from disk; unsaved in-memory changes are lost.

## Verify

```bash
pytest
python scripts/seed_demo.py
```

## Configuration

Copy `.env.example` to `.env`. Key variables:

- `DATA_MODE=fixtures` (only supported mode in this scaffold)
- `DEMO_SESSION_ID` — local session label
- Placeholders: `MONGODB_*`, `TIGERDATA_DATABASE_URL`, `GEMINI_*` (unused until integrations land)

## Documentation

- `docs/architecture.md` — layers and integration map
- `docs/api-contract.md` — endpoints, units, errors
- `docs/team-workflow.md` — branches and ownership