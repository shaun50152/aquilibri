from datetime import datetime

from pydantic import BaseModel, Field


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