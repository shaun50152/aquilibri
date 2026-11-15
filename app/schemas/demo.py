from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.schemas.scheduling import ReallocationPreviewResponse


class DemoState(BaseModel):
    session_id: str
    current_time: datetime = Field(description="Simulated UTC clock for the demo session.")
    revision: int = Field(
        description="Monotonic counter; increments on job changes, reset, and (future) apply."
    )


class DemoResetResponse(BaseModel):
    session_id: str
    revision: int
    jobs_restored: int
    current_time: datetime


class DemoAdvanceRequest(BaseModel):
    hours: float = Field(default=1.0, gt=0, description="Simulated hours to advance (fractional ok).")
    expected_revision: int | None = Field(
        default=None,
        description="When set, advance is rejected with 409 if it does not match the live revision.",
    )


class DemoAdvanceResponse(BaseModel):
    session_id: str
    revision: int
    previous_time: datetime
    current_time: datetime
    hours_advanced: float
    jobs_started: list[str]
    jobs_completed: list[str]
    jobs_failed: list[str]


class OperatorChatRequest(BaseModel):
    prompt: str = Field(min_length=1, description="Raw operator natural-language instruction.")


class OperatorChatResponse(BaseModel):
    assistant_message: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    preview: Optional[ReallocationPreviewResponse] = None
    detail: Optional[str] = None
