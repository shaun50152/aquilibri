"""Completion notifications for the demo portal."""

from __future__ import annotations

from app.config import get_settings
from app.integrations.mongodb import get_mongo_job_store
from app.schemas.jobs import DEMO_OWNER_ID, Job
from app.schemas.notifications import CompletionNotification
from app.state import get_session_state


def _persist_notification(record: CompletionNotification) -> None:
    if get_settings().is_live:
        store = get_mongo_job_store()
        store.upsert_notification(record.model_dump(mode="json"))


def dedupe_key(session_id: str, job_id: str, execution_epoch: int) -> str:
    return f"{session_id}:{job_id}:e{execution_epoch}"


def list_for_session_owner(owner_id: str = DEMO_OWNER_ID) -> list[CompletionNotification]:
    state = get_session_state()
    return [n for n in state.notifications if n.owner_id == owner_id]


def find_by_dedupe_key(key: str) -> CompletionNotification | None:
    state = get_session_state()
    for n in state.notifications:
        if n.dedupe_key == key:
            return n
    return None


def ensure_completion_notification(job: Job) -> CompletionNotification | None:
    """Create exactly one notification per completed job execution epoch."""
    if job.status.value != "completed" or not job.result_url:
        return None
    state = get_session_state()
    key = dedupe_key(state.session_id, job.id, job.execution_epoch)
    existing = find_by_dedupe_key(key)
    if existing:
        return existing
    if job.completed_at is None:
        return None
    notif_id = f"notif-{job.id}-e{job.execution_epoch}"
    record = CompletionNotification(
        id=notif_id,
        session_id=state.session_id,
        owner_id=job.owner_id,
        job_id=job.id,
        completed_at=job.completed_at,
        message=f"Simulated workload {job.id} finished.",
        result_url=job.result_url,
        dedupe_key=key,
    )
    state.notifications.append(record)
    _persist_notification(record)
    return record


def clear_session_notifications(session_id: str) -> None:
    state = get_session_state()
    state.notifications = [n for n in state.notifications if n.session_id != session_id]
    if get_settings().is_live:
        get_mongo_job_store().delete_notifications(session_id)

