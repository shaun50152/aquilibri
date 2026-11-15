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
from app.schemas.scheduling import EmergencyRestriction
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
    # Time-bounded emergency water override (cleared on reset / after window ends).
    active_temporary_restriction: EmergencyRestriction | None = None
    _jobs_fixture: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def clear_temporary_restriction(self) -> None:
        self.active_temporary_restriction = None

    def get_active_temporary_restriction(self) -> EmergencyRestriction | None:
        """Return the active temporary cap, expiring it once the demo clock passes end."""
        er = self.active_temporary_restriction
        if er is None:
            return None
        if self.current_time >= er.end:
            self.active_temporary_restriction = None
            return None
        return er

    def reload_from_fixtures(self) -> int:
        """Deep-copy fixture jobs, restore demo clock, increment revision (synthetic mode)."""
        self.jobs = [Job.model_validate(copy.deepcopy(j)) for j in self._jobs_fixture]
        self.current_time = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)
        self.revision += 1
        self.clear_temporary_restriction()
        settings = get_settings()
        if settings.is_live:
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

    def clear_to_blank_slate(self) -> int:
        """Wipe jobs/notifications clock for live demos; return jobs remaining (0)."""
        self.jobs = []
        self.notifications = []
        self.current_time = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)
        self.revision += 1
        self.clear_temporary_restriction()
        settings = get_settings()
        if settings.is_live:
            from app.integrations.mongodb import get_mongo_job_store

            store = get_mongo_job_store()
            store.clear_session(self.session_id)
            store.set_session_meta(
                self.session_id,
                revision=self.revision,
                current_time=self.current_time.isoformat(),
                execution_epoch=self.execution_epoch,
            )
        return 0


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

    if settings.is_live:
        from app.integrations.mongodb import get_mongo_job_store

        store = get_mongo_job_store()
        session_id = settings.demo_session_id
        stored_jobs = store.list_jobs(session_id)
        # Live mode: blank slate when Atlas is empty — never seed mocks.
        jobs = [Job.model_validate(j) for j in stored_jobs]
        revision, current_time_str, execution_epoch = store.get_session_meta(session_id)
        if revision <= 0:
            revision = 1
        if execution_epoch <= 0:
            execution_epoch = 1
        current_time = (
            datetime.fromisoformat(current_time_str)
            if current_time_str
            else anchor_time
        )
        if not stored_jobs and not current_time_str:
            # First boot with empty DB: persist anchor meta so reset/revision are coherent.
            store.set_session_meta(
                session_id,
                revision=revision,
                current_time=current_time.isoformat(),
                execution_epoch=execution_epoch,
            )
        notifications = [
            CompletionNotification.model_validate(d) for d in store.list_notifications(session_id)
        ]
        # Sites come from Tiger at request time; keep fixture snapshot only as unused cache.
        sites = []
    else:
        jobs = [Job.model_validate(copy.deepcopy(j)) for j in jobs_fixture]
        revision = fixture_revision
        current_time = anchor_time
        sites = [SiteProfile.model_validate(s) for s in sites_list]

    state = SessionState(
        session_id=settings.demo_session_id,
        current_time=current_time,
        revision=revision,
        jobs=jobs,
        sites=sites,
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
