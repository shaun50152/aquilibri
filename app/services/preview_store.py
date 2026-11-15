"""Session-scoped preview cache for Apply handoff (Person 1).

Caching a preview does **not** increment the scheduling revision.

Person 1 Apply should:
1. ``record = get_stored_preview(preview_id)``
2. Reject if missing, ``record.session_id != current session``, or
   ``record.input_revision != current revision`` (stale plan).
3. Apply ``record.proposed_assignments`` / unfulfilled outcomes, then bump revision.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.schemas.scheduling import (
    EmergencyRestriction,
    ProposedAssignment,
    ReallocationPreviewResponse,
)


@dataclass
class StoredPreview:
    preview_id: str
    session_id: str
    input_revision: int
    simulation_time: datetime
    sites_fixture_name: str
    proposed_assignments: list[ProposedAssignment]
    unfulfilled_job_ids: tuple[str, ...]
    response: ReallocationPreviewResponse
    emergency_restriction: EmergencyRestriction | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())


_lock = threading.Lock()
_STORE: dict[str, StoredPreview] = {}


def new_preview_id() -> str:
    return f"preview-{uuid.uuid4().hex[:12]}"


def store_preview(record: StoredPreview) -> StoredPreview:
    with _lock:
        _STORE[record.preview_id] = record
    return record


def get_stored_preview(preview_id: str) -> Optional[StoredPreview]:
    """Lookup interface for Person 1 Apply."""
    with _lock:
        return _STORE.get(preview_id)


def delete_stored_preview(preview_id: str) -> bool:
    with _lock:
        return _STORE.pop(preview_id, None) is not None


def clear_preview_store() -> None:
    """Test helper."""
    with _lock:
        _STORE.clear()


def list_preview_ids_for_session(session_id: str) -> list[str]:
    with _lock:
        return [p.preview_id for p in _STORE.values() if p.session_id == session_id]


def clear_previews_for_session(session_id: str) -> int:
    """Remove stored previews for one session (demo reset)."""
    with _lock:
        doomed = [pid for pid, p in _STORE.items() if p.session_id == session_id]
        for pid in doomed:
            _STORE.pop(pid, None)
        return len(doomed)
