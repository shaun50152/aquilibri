from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class HourlySiteProfile(BaseModel):
    hour_start: datetime = Field(description="UTC start of this hourly bucket.")
    gpu_capacity: int = Field(ge=0, description="GPUs available this hour.")
    fixed_water_liters_per_hour: float = Field(
        ge=0, description="Facility water not tied to GPU usage (L/h)."
    )
    incremental_water_liters_per_gpu_hour: float = Field(
        ge=0, description="Additional liters per GPU-hour of compute."
    )
    energy_kwh_per_gpu_hour: float = Field(ge=0, description="kWh per GPU-hour.")
    electricity_usd_per_kwh: float = Field(ge=0, description="USD per kWh.")
    water_cap_liters_per_hour: Optional[float] = Field(
        default=None,
        ge=0,
        description="Optional hourly water withdrawal cap (L/h); null means no explicit cap.",
    )


class SiteProfile(BaseModel):
    site_id: str
    name: str
    hourly_profiles: list[HourlySiteProfile]


class SiteListResponse(BaseModel):
    sites: list[SiteProfile]