"""MILP scheduling and reallocation — SciPy integration pending (Person 2)."""

from __future__ import annotations

from app.integrations.gemini import GeminiSchedulingAssistant
from app.schemas.scheduling import (
    ReallocationApplyRequest,
    ReallocationApplyResponse,
    ReallocationPreviewRequest,
    ReallocationPreviewResponse,
)


def compute_proposed_placements(
    jobs_snapshot: list,
    site_profiles: list,
    current_time,
    objective: dict,
) -> ReallocationPreviewResponse:
    raise NotImplementedError(
        "SciPy MILP scheduler is not implemented. See fixtures/preview_*.json for sample output."
    )


def preview_reallocation(
    request: ReallocationPreviewRequest,
    revision: int,
) -> ReallocationPreviewResponse:
    """Request a preview through Gemini + optimizer (not implemented)."""
    _ = GeminiSchedulingAssistant
    raise NotImplementedError("POST /api/reallocation/preview behavior is not implemented.")


def apply_reallocation(request: ReallocationApplyRequest) -> ReallocationApplyResponse:
    raise NotImplementedError("POST /api/reallocation/apply behavior is not implemented.")