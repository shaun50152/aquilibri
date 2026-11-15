"""In-memory demo session state owned by the application (single source of truth)."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import DEMO_CLOCK_ANCHOR_ISO, FIXTURES_DIR, get_settings
from app.schemas.jobs import Job
from app.schemas.sites import SiteProfile


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@dataclass
class SessionState:
    session_id: str
    current_time: datetime
    revision: int
    jobs: list[Job] = field(default_factory=list)
    sites: list[SiteProfile] = field(default_factory=list)
    _jobs_fixture: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def reload_from_fixtures(self) -> int:
        """Deep-copy fixture jobs, restore demo clock, increment revision."""
        self.jobs = [Job.model_validate(copy.deepcopy(j)) for j in self._jobs_fixture]
        self.current_time = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)
        self.revision += 1
        return len(self.jobs)


def build_session_state() -> SessionState:
    settings = get_settings()
    jobs_raw = _load_json(FIXTURES_DIR / "jobs.json")
    sites_raw = _load_json(FIXTURES_DIR / "sites.json")

    jobs_fixture = jobs_raw.get("jobs", jobs_raw if isinstance(jobs_raw, list) else [])
    sites_list = sites_raw.get("sites", sites_raw if isinstance(sites_raw, list) else [])

    state = SessionState(
        session_id=settings.demo_session_id,
        current_time=datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO),
        revision=int(jobs_raw.get("revision", 1) if isinstance(jobs_raw, dict) else 1),
        jobs=[Job.model_validate(copy.deepcopy(j)) for j in jobs_fixture],
        sites=[SiteProfile.model_validate(s) for s in sites_list],
        _jobs_fixture=copy.deepcopy(jobs_fixture),
    )
    return state


# Set during FastAPI lifespan; routers read via get_session_state().
_session_state: SessionState | None = None


def init_session_state() -> SessionState:
    global _session_state
    _session_state = build_session_state()
    return _session_state


def get_session_state() -> SessionState:
    if _session_state is None:
        raise RuntimeError("Session state not initialized — application startup did not run.")
    return _session_state