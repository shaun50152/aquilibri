"""Process-wide lock for demo session mutations (advance, reset, apply, create)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from app.state import SessionState, get_session_state

_lock = threading.RLock()


class SessionConflictError(Exception):
    """Raised when expected_revision does not match the live session."""


@contextmanager
def session_mutation(
    *,
    expected_revision: Optional[int] = None,
) -> Iterator[SessionState]:
    with _lock:
        state = get_session_state()
        if expected_revision is not None and expected_revision != state.revision:
            raise SessionConflictError(
                f"Session revision {state.revision} does not match expected {expected_revision}"
            )
        yield state