from datetime import datetime

from pydantic import BaseModel, Field


class CompletionNotification(BaseModel):
    id: str = Field(description="Stable notification id for deduplication in the UI.")
    session_id: str
    owner_id: str
    job_id: str
    completed_at: datetime
    message: str
    result_url: str
    dedupe_key: str


class NotificationListResponse(BaseModel):
    session_id: str
    owner_id: str
    revision: int
    notifications: list[CompletionNotification]