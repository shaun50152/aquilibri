from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ReallocationMode(str, Enum):
    routine = "routine"
    emergency = "emergency"


class EmergencyRestriction(BaseModel):
    site_id: str
    water_cap_liters_per_hour: float = Field(ge=0)
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def end_after_start(self) -> "EmergencyRestriction":
        if self.end <= self.start:
            raise ValueError("Emergency restriction end must be after start.")
        return self


class ReallocationPreviewRequest(BaseModel):
    mode: ReallocationMode
    natural_language_instruction: Optional[str] = None
    max_additional_cost_usd: Optional[float] = Field(default=None, ge=0)
    emergency_restriction: Optional[EmergencyRestriction] = None


class PlacementSnapshot(BaseModel):
    job_id: str
    assigned_site_id: Optional[str] = None
    scheduled_start: Optional[datetime] = None


class ProposedJobChange(BaseModel):
    job_id: str
    old_placement: PlacementSnapshot
    new_placement: PlacementSnapshot


class AggregateMetrics(BaseModel):
    total_water_liters: float
    total_energy_kwh: float
    total_cost_usd: float
    jobs_on_time: int
    jobs_late: int
    jobs_unfulfilled: int


class SiteWaterMetrics(BaseModel):
    site_id: str
    water_liters: float
    water_cap_violations: int


class CostBreakdown(BaseModel):
    energy_usd: float
    water_usd: float
    miss_penalties_usd: float
    total_usd: float


class DeadlineOutcome(BaseModel):
    job_id: str
    met_deadline: bool
    projected_completion: Optional[datetime] = None


class PreviewStatus(str, Enum):
    feasible = "feasible"
    infeasible = "infeasible"


class ReallocationPreviewResponse(BaseModel):
    preview_id: str
    input_revision: int
    status: PreviewStatus
    proposed_job_changes: list[ProposedJobChange]
    metrics_before: AggregateMetrics
    metrics_after: AggregateMetrics
    per_site_water: list[SiteWaterMetrics]
    cost_breakdown: CostBreakdown
    deadline_outcomes: list[DeadlineOutcome]
    explanation: str
    infeasibility_reason: Optional[str] = None
    simulated: bool = True


class ReallocationApplyRequest(BaseModel):
    preview_id: str
    input_revision: int


class ReallocationApplyResponse(BaseModel):
    applied: bool
    revision: int
    message: str