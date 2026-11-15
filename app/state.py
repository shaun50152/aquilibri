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
from app.schemas.notifications import CompletionNotification
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
    sites_fixture_name: str = "sites.json"
    execution_epoch: int = 1
    notifications: list[CompletionNotification] = field(default_factory=list)
    _jobs_fixture: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def reload_from_fixtures(self) -> int:
        """Deep-copy fixture jobs, restore demo clock, increment revision."""
        self.jobs = [Job.model_validate(copy.deepcopy(j)) for j in self._jobs_fixture]
        self.current_time = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)
        self.revision += 1
        settings = get_settings()
        if settings.data_mode == "mongodb":
            from app.integrations.mongodb import get_mongo_job_store

            store = get_mongo_job_store()
            store.replace_session_jobs(
                self.session_id,
                [j.model_dump(mode="json") for j in self.jobs],
            )
            store.set_session_meta(
                self.session_id,
                revision=self.revision,
                current_time=self.current_time.isoformat(),
                execution_epoch=self.execution_epoch,
            )
        return len(self.jobs)


def build_session_state() -> SessionState:
    settings = get_settings()
    jobs_raw = _load_json(FIXTURES_DIR / "jobs.json")
    sites_path = settings.site_profiles_path()
    sites_raw = _load_json(sites_path)

    jobs_fixture = jobs_raw.get("jobs", jobs_raw if isinstance(jobs_raw, list) else [])
    sites_list = sites_raw.get("sites", sites_raw if isinstance(sites_raw, list) else [])

    fixture_revision = int(jobs_raw.get("revision", 1) if isinstance(jobs_raw, dict) else 1)
    anchor_time = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)

    jobs: list[Job]
    revision: int
    current_time: datetime
    execution_epoch: int = 1
    notifications: list[CompletionNotification] = []

    if settings.data_mode == "mongodb":
        from app.integrations.mongodb import get_mongo_job_store

        store = get_mongo_job_store()
        session_id = settings.demo_session_id
        stored_jobs = store.list_jobs(session_id)
        if not stored_jobs:
            jobs = [Job.model_validate(copy.deepcopy(j)) for j in jobs_fixture]
            revision = fixture_revision
            current_time = anchor_time
            store.replace_session_jobs(session_id, [j.model_dump(mode="json") for j in jobs])
            store.set_session_meta(
                session_id,
                revision=revision,
                current_time=current_time.isoformat(),
                execution_epoch=execution_epoch,
            )
        else:
            jobs = [Job.model_validate(j) for j in stored_jobs]
            revision, current_time_str, execution_epoch = store.get_session_meta(session_id)
            if revision <= 0:
                revision = fixture_revision
            current_time = (
                datetime.fromisoformat(current_time_str)
                if current_time_str
                else anchor_time
            )
        notifications = [
            CompletionNotification.model_validate(d) for d in store.list_notifications(session_id)
        ]
    else:
        jobs = [Job.model_validate(copy.deepcopy(j)) for j in jobs_fixture]
        revision = fixture_revision
        current_time = anchor_time

    state = SessionState(
        session_id=settings.demo_session_id,
        current_time=current_time,
        revision=revision,
        jobs=jobs,
        sites=[SiteProfile.model_validate(s) for s in sites_list],
        sites_fixture_name=settings.site_profiles_fixture,
        execution_epoch=execution_epoch,
        notifications=notifications,
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


def replace_session_state(state: SessionState) -> SessionState:
    """Test/helper hook to swap the in-memory session."""
    global _session_state
    _session_state = state
    return _session_state
