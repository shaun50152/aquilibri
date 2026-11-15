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
    # Omitted allowance is treated as 0.0 additional USD above the reference schedule.
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
    """Backward-compatible per-site water summary (proposed / after schedule)."""

    site_id: str
    water_liters: float
    water_cap_violations: int


class SiteScheduleMetrics(BaseModel):
    """Extended per-site metrics for before/after comparison and peak withdrawal."""

    site_id: str
    water_liters: float
    water_cap_violations: int
    energy_kwh: float = 0.0
    energy_cost_usd: float = 0.0
    peak_withdrawal_lph: float = 0.0
    peak_withdrawal_at: Optional[datetime] = None
    within_cap: bool = True
    active_cap_lph: Optional[float] = None


class CostBreakdown(BaseModel):
    energy_usd: float
    # No water tariff exists in the current schema — always 0.0 unless configured later.
    water_usd: float
    miss_penalties_usd: float
    total_usd: float


class DeadlineOutcome(BaseModel):
    job_id: str
    met_deadline: bool
    projected_completion: Optional[datetime] = None
    unfulfilled: bool = False
    unfulfilled_reason: Optional[str] = None


class PreviewStatus(str, Enum):
    feasible = "feasible"
    infeasible = "infeasible"


class SolverOutcome(str, Enum):
    ok = "ok"
    infeasible = "infeasible"
    incomplete_input = "incomplete_input"
    solver_timeout = "solver_timeout"
    solver_error = "solver_error"


class MetricsSourceLabel(str, Enum):
    """Identifies what metrics_before / comparison schedules represent."""

    computed_reference_schedule = "computed_reference_schedule"
    saved_schedule_under_restriction = "saved_schedule_under_restriction"
    unavailable = "unavailable"


class ProposedAssignment(BaseModel):
    """Complete proposed assignment for Apply handoff (includes unchanged jobs)."""

    job_id: str
    assigned_site_id: Optional[str] = None
    scheduled_start: Optional[datetime] = None
    projected_completion: Optional[datetime] = None
    unfulfilled: bool = False
    unfulfilled_reason: Optional[str] = None


class ReallocationPreviewResponse(BaseModel):
    preview_id: str
    input_revision: int
    status: PreviewStatus
    proposed_job_changes: list[ProposedJobChange]
    # metrics_before is the computed reference schedule for routine comparisons,
    # not the operator's currently saved queue placements.
    metrics_before: Optional[AggregateMetrics] = None
    # None when no proposed schedule exists (infeasible / solver failure).
    metrics_after: Optional[AggregateMetrics] = None
    per_site_water: list[SiteWaterMetrics] = Field(default_factory=list)
    cost_breakdown: Optional[CostBreakdown] = None
    deadline_outcomes: list[DeadlineOutcome] = Field(default_factory=list)
    explanation: str
    infeasibility_reason: Optional[str] = None
    simulated: bool = True

    # --- Extended fields (compatible defaults) ---
    metrics_before_label: MetricsSourceLabel = MetricsSourceLabel.unavailable
    reference_cost_usd: Optional[float] = None
    cost_ceiling_usd: Optional[float] = None
    max_additional_cost_usd: Optional[float] = None
    evaluation_start: Optional[datetime] = None
    evaluation_end: Optional[datetime] = None
    per_site_before: list[SiteScheduleMetrics] = Field(default_factory=list)
    per_site_after: list[SiteScheduleMetrics] = Field(default_factory=list)
    proposed_assignments: list[ProposedAssignment] = Field(default_factory=list)
    solver_outcome: SolverOutcome = SolverOutcome.ok
    session_id: Optional[str] = None
    sites_fixture_name: Optional[str] = None
    cost_assumptions: str = (
        "Modeled cost = electricity (GPU×hours×kWh/GPU-h×USD/kWh) + miss_penalty_usd "
        "for explicitly unfulfilled jobs. water_usd=0 (no water tariff in schema). "
        "No transfer or delay charges are modeled."
    )


class ReallocationApplyRequest(BaseModel):
    preview_id: str
    input_revision: int


class ReallocationApplyResponse(BaseModel):
    applied: bool
    revision: int
    message: str
