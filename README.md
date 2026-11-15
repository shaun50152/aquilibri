# aquilibri

**Aqualibri** is an AI-powered, water-aware workload orchestration platform that dynamically optimizes AI data center scheduling and redistributes workloads to reduce water consumption, manage resource constraints, and minimize operational costs.

This repository is the **HackUTA scaffold**: a runnable demo with a master `DATA_MODE` toggle between synthetic fixtures and live MongoDB/Tiger integrations.

## What works now

- User portal at `/portal` — submit jobs, track running/completed state, completion notice, CSV download
- Operator dashboard at `/operator` — sites, preview/apply reallocation, **advance simulated clock**, reset demo
- REST API with OpenAPI at `/docs`
- `POST /api/demo/advance` — deterministic job start/finish + notifications + CSV results
- `POST /api/reallocation/preview` and `POST /api/reallocation/apply` with revision stale checks
- `DATA_MODE=synthetic` (offline fixtures) or `DATA_MODE=live` (MongoDB jobs + Tiger sites, blank slate when empty)
- Sample reallocation preview JSON under `fixtures/preview_*.json` for visual demo mode
- `POST /api/operator/chat` — Gemini tool-calling on top of the same preview engine

`POST /api/reallocation/preview` is implemented (routine + emergency MILP). In live mode, sites come from Tiger (`python scripts/seed_tiger_sites.py` seeds five enterprise regions × 48h); in synthetic mode, from `SITE_PROFILES_FIXTURE` (same five sites).

See `docs/team-workflow.md` for ownership split.

## Prerequisites

- Python 3.11+ recommended
- No database or API keys required for `DATA_MODE=synthetic`

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
- **synthetic:** jobs/sites load from `fixtures/` at startup. Reset restores the three seeded jobs.
- **live:** boots from MongoDB/Tiger as-is (empty stores → blank slate, no mock seed). Reset wipes the Mongo session collections.
- Both modes increment `revision` on reset (previews tied to an old revision are stale).

## Verify

```bash
pytest
python scripts/seed_demo.py
```

## Configuration

Copy `.env.example` to `.env`. Key variables:

- `DATA_MODE=synthetic` | `live` (aliases: `fixtures`→`synthetic`, `mongodb`→`live`)
- `DEMO_SESSION_ID` — local/Mongo session label
- Live also needs `MONGODB_*` and Tiger `PG*` / `TIGERDATA_DATABASE_URL`; Gemini uses `GEMINI_*`

## Documentation

- `docs/architecture.md` — layers and integration map
- `docs/api-contract.md` — endpoints, units, errors
- `docs/team-workflow.md` — branches and ownership