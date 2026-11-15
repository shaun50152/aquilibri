from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    scheduled = "scheduled"
    running = "running"
    completed = "completed"
    failed = "failed"
    unfulfilled = "unfulfilled"


class JobTemplate(str, Enum):
    inference_batch = "inference_batch"
    training_small = "training_small"
    training_large = "training_large"


class Job(BaseModel):
    """Workload unit scheduled across data-center sites."""

    id: str
    session_id: str
    owner_id: str
    template: JobTemplate
    earliest_start: datetime = Field(description="UTC; job cannot start before this instant.")
    deadline: datetime = Field(description="UTC; job must complete by this instant.")
    gpu_count: int = Field(ge=1, description="Number of GPUs required for the full run.")
    duration_hours: float = Field(gt=0, description="Estimated wall-clock hours at assigned GPUs.")
    eligible_site_ids: list[str]
    data_ready_at_by_site: dict[str, datetime] = Field(
        description="UTC per site; earliest time input data is available at that site."
    )
    critical: bool = False
    miss_penalty_usd: float = Field(ge=0, description="Penalty if deadline is missed.")
    status: JobStatus
    assigned_site_id: Optional[str] = None
    scheduled_start: Optional[datetime] = Field(
        default=None, description="UTC planned start at assigned_site_id."
    )
    completed_at: Optional[datetime] = None
    result_url: Optional[str] = None
    result_snapshot: Optional[list[dict[str, str]]] = Field(
        default=None,
        description="Immutable simulated CSV rows served by GET /api/jobs/{id}/result.",
    )
    execution_epoch: int = Field(
        default=0,
        description="Increments on session reset so completion ids cannot be confused across runs.",
    )


class JobCreateRequest(BaseModel):
    template: JobTemplate
    earliest_start: datetime
    deadline: datetime


class JobListResponse(BaseModel):
    session_id: str
    revision: int
    jobs: list[Job]


class TemplateDefinition(BaseModel):
    template: JobTemplate
    gpu_count: int
    duration_hours: float
    eligible_site_ids: list[str]
    critical: bool
    miss_penalty_usd: float


# All enterprise demo regions (Ashburn, Dallas, Santa Clara, Dublin, Tokyo).
_DEMO_ELIGIBLE_SITES = [
    "us-east-1",
    "us-south-central",
    "us-west-1",
    "eu-west-1",
    "ap-northeast",
]

TEMPLATE_DEFINITIONS: dict[JobTemplate, TemplateDefinition] = {
    JobTemplate.inference_batch: TemplateDefinition(
        template=JobTemplate.inference_batch,
        gpu_count=4,
        duration_hours=2.0,
        eligible_site_ids=list(_DEMO_ELIGIBLE_SITES),
        critical=False,
        miss_penalty_usd=50.0,
    ),
    JobTemplate.training_small: TemplateDefinition(
        template=JobTemplate.training_small,
        gpu_count=8,
        duration_hours=6.0,
        eligible_site_ids=list(_DEMO_ELIGIBLE_SITES),
        critical=False,
        miss_penalty_usd=200.0,
    ),
    JobTemplate.training_large: TemplateDefinition(
        template=JobTemplate.training_large,
        gpu_count=16,
        duration_hours=12.0,
        eligible_site_ids=list(_DEMO_ELIGIBLE_SITES),
        critical=True,
        miss_penalty_usd=1000.0,
    ),
}

# Fixed local demo identity — replace with real auth in production.
DEMO_OWNER_ID = "demo-user-001"