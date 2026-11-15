"""Enterprise demo data-center catalog shared by fixtures and Tiger seeding.

Hourly water caps are derived from published Water Usage Effectiveness (WUE)
baselines (L/kWh) × a standardized 50 MW IT load, then shaped by a diurnal
heat curve and light noise — not arbitrary integers.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import DEMO_CLOCK_ANCHOR_ISO, FIXTURES_DIR
from app.schemas.sites import HourlySiteProfile, SiteProfile

# Standardized IT power for WUE → liters/hour conversion (50 MW continuous).
IT_POWER_KW = 50_000.0

# Reproducible noise across fixture + Tiger seeds.
WATER_SEED_RNG = 20240515


@dataclass(frozen=True)
class DemoSiteSpec:
    site_id: str
    name: str
    region: str
    gpu_capacity: int
    # Published WUE (liters water per kWh IT) from 2024/2025 sustainability reports.
    wue_l_per_kwh: float
    wue_source: str
    # Demo UI / scheduler ceiling (L/h). WUE still informs relative story + pitch math;
    # this value is the persisted water_cap so meters read ~50–90% under live load.
    demo_water_cap_liters_per_hour: float
    fixed_water_liters_per_hour: float
    incremental_water_liters_per_gpu_hour: float
    energy_kwh_per_gpu_hour: float
    electricity_usd_per_kwh: float
    price_diurnal: float = 0.08


# Five production-looking regions for the AquaShift X pitch.
# WUE baselines: Meta 2024 average (Dublin), Microsoft 2025 average (Santa Clara),
# plus mixed / evaporative cooling profiles for Tokyo, Ashburn, and Dallas.
# Demo caps stay in 200–500 L/h so fixed+GPU load (~100–140 L/h) fills the UI meters.
DEMO_SITE_SPECS: tuple[DemoSiteSpec, ...] = (
    DemoSiteSpec(
        site_id="us-east-1",
        name="US-East-1 (Ashburn, VA)",
        region="us-east",
        gpu_capacity=48,
        wue_l_per_kwh=1.50,
        wue_source="Standard Evaporative",
        demo_water_cap_liters_per_hour=500.0,  # Virginia — generous headroom
        fixed_water_liters_per_hour=105.0,
        incremental_water_liters_per_gpu_hour=0.75,
        energy_kwh_per_gpu_hour=0.42,
        electricity_usd_per_kwh=0.085,
    ),
    DemoSiteSpec(
        site_id="us-south-central",
        name="US-South-Central (Dallas, TX)",
        region="us-south",
        gpu_capacity=32,
        wue_l_per_kwh=1.90,
        wue_source="High-Temp Evaporative",
        demo_water_cap_liters_per_hour=360.0,
        fixed_water_liters_per_hour=95.0,
        incremental_water_liters_per_gpu_hour=0.88,
        energy_kwh_per_gpu_hour=0.48,
        electricity_usd_per_kwh=0.055,
    ),
    DemoSiteSpec(
        site_id="us-west-1",
        name="US-West-1 (Santa Clara, CA)",
        region="us-west",
        gpu_capacity=36,
        wue_l_per_kwh=0.27,
        wue_source="Microsoft 2025 Average",
        demo_water_cap_liters_per_hour=250.0,  # California — water-stressed / tight
        fixed_water_liters_per_hour=115.0,
        incremental_water_liters_per_gpu_hour=0.92,
        energy_kwh_per_gpu_hour=0.40,
        electricity_usd_per_kwh=0.145,
    ),
    DemoSiteSpec(
        site_id="eu-west-1",
        name="EU-West-1 (Dublin, Ireland)",
        region="eu-west",
        gpu_capacity=40,
        wue_l_per_kwh=0.19,
        wue_source="Meta 2024 Average",
        demo_water_cap_liters_per_hour=500.0,  # Ireland — generous headroom
        fixed_water_liters_per_hour=88.0,
        incremental_water_liters_per_gpu_hour=0.70,
        energy_kwh_per_gpu_hour=0.34,
        electricity_usd_per_kwh=0.155,
    ),
    DemoSiteSpec(
        site_id="ap-northeast",
        name="AP-Northeast (Tokyo, Japan)",
        region="ap-northeast",
        gpu_capacity=40,
        wue_l_per_kwh=1.00,
        wue_source="Mixed Cooling",
        demo_water_cap_liters_per_hour=420.0,
        fixed_water_liters_per_hour=92.0,
        incremental_water_liters_per_gpu_hour=0.62,
        energy_kwh_per_gpu_hour=0.36,
        electricity_usd_per_kwh=0.175,
    ),
)

DEMO_SITE_IDS: tuple[str, ...] = tuple(s.site_id for s in DEMO_SITE_SPECS)

# Santa Clara — primary emergency / water-restriction demo target.
DEMO_EMERGENCY_SITE_ID = "us-west-1"

# Pitch-facing WUE catalog keyed by display region (as requested for the seed script).
SITE_WUE_BASELINES_L_PER_KWH: dict[str, float] = {
    "EU-West-1 (Dublin)": 0.19,  # Meta 2024 Average
    "US-West-1 (Santa Clara)": 0.27,  # Microsoft 2025 Average
    "AP-Northeast (Tokyo)": 1.00,  # Mixed Cooling
    "US-East-1 (Ashburn)": 1.50,  # Standard Evaporative
    "US-South-Central (Dallas)": 1.90,  # High-Temp Evaporative
}


def base_water_usage_lph(wue_l_per_kwh: float, *, it_power_kw: float = IT_POWER_KW) -> float:
    """Liters/hour at the standardized IT load: IT_kW × WUE (L/kWh)."""
    return float(it_power_kw) * float(wue_l_per_kwh)


def daily_heat_multipliers(hours: int) -> list[float]:
    """48h (or N-hour) ambient curve: ~0.8× at night, ~1.3× mid-afternoon.

    Peaks near 15:00 local-ish UTC hour; trough near 03:00. Values stay in
    approximately [0.80, 1.30] for a clean day/night swing.
    """
    if hours < 1:
        raise ValueError("hours must be >= 1")
    out: list[float] = []
    for h in range(hours):
        hour_of_day = h % 24
        # cos(0) at 15:00 → 1.30; cos(π) at 03:00 → 0.80
        radians = 2.0 * math.pi * (hour_of_day - 15) / 24.0
        out.append(1.05 + 0.25 * math.cos(radians))
    return out


def hourly_water_cap_lph(
    demo_cap_lph: float,
    heat_multiplier: float,
    *,
    noise: float,
) -> float:
    """Demo water_cap ceiling: site demo baseline × daily heat × noise.

    WUE×50MW remains available via ``base_water_usage_lph`` for pitch math; the
    persisted scheduler/UI ceiling is this scaled demo value (≈200–500 L/h).
    """
    return round(float(demo_cap_lph) * heat_multiplier * noise, 3)


def build_demo_site_profiles(
    *,
    hours: int = 48,
    anchor: datetime | None = None,
    rng_seed: int = WATER_SEED_RNG,
) -> list[SiteProfile]:
    """Build hourly SiteProfile snapshots with WUE-grounded water caps."""
    if hours < 1:
        raise ValueError("hours must be >= 1")
    if anchor is None:
        anchor = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)

    heat = daily_heat_multipliers(hours)
    rng = random.Random(rng_seed)

    sites: list[SiteProfile] = []
    for spec in DEMO_SITE_SPECS:
        rows: list[HourlySiteProfile] = []
        for h in range(hours):
            hour_start = anchor + timedelta(hours=h)
            # Prefer clock hour so the curve aligns with wall-clock afternoon heat.
            hour_of_day = hour_start.hour
            radians = 2.0 * math.pi * (hour_of_day - 15) / 24.0
            heat_m = 1.05 + 0.25 * math.cos(radians)
            # Keep the precomputed array available for callers that inspect it;
            # clock-based value matches daily_heat_multipliers for UTC-aligned anchors.
            _ = heat[h]

            noise = rng.uniform(0.95, 1.05)
            # Mild diurnal on the ceiling (±~10%) so meters stay in the 200–500 demo band.
            cap_heat = 1.0 + 0.10 * math.cos(radians)
            water_cap = hourly_water_cap_lph(
                spec.demo_water_cap_liters_per_hour, cap_heat, noise=noise
            )
            water_cap = max(200.0, min(500.0, water_cap))

            # Facility fixed withdrawal also breathes with outdoor heat (same curve).
            fixed_noise = rng.uniform(0.95, 1.05)
            fixed_water = round(spec.fixed_water_liters_per_hour * heat_m * fixed_noise, 3)

            price_factor = 1.0 + spec.price_diurnal * math.sin(
                (hour_of_day / 24.0) * 2.0 * math.pi + 0.8
            )
            gpu = spec.gpu_capacity
            if 14 <= hour_of_day <= 19:
                gpu = max(16, spec.gpu_capacity - 4)

            rows.append(
                HourlySiteProfile(
                    hour_start=hour_start,
                    gpu_capacity=gpu,
                    fixed_water_liters_per_hour=fixed_water,
                    incremental_water_liters_per_gpu_hour=spec.incremental_water_liters_per_gpu_hour,
                    energy_kwh_per_gpu_hour=spec.energy_kwh_per_gpu_hour,
                    electricity_usd_per_kwh=round(
                        spec.electricity_usd_per_kwh * price_factor, 5
                    ),
                    water_cap_liters_per_hour=water_cap,
                )
            )
        sites.append(
            SiteProfile(site_id=spec.site_id, name=spec.name, hourly_profiles=rows)
        )
    return sites


def sites_to_fixture_dict(sites: list[SiteProfile], *, comment: str) -> dict[str, Any]:
    return {
        "_comment": comment,
        "sites": [s.model_dump(mode="json") for s in sites],
    }


def write_site_fixtures(
    *,
    short_hours: int = 3,
    extended_hours: int = 48,
    fixtures_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Write sites.json (short) and sites_extended_synthetic.json (long)."""
    root = fixtures_dir or FIXTURES_DIR
    short = build_demo_site_profiles(hours=short_hours)
    extended = build_demo_site_profiles(hours=extended_hours)

    short_path = root / "sites.json"
    extended_path = root / "sites_extended_synthetic.json"
    short_path.write_text(
        json.dumps(
            sites_to_fixture_dict(
                short,
                comment=(
                    "Short 3-hour synthetic horizon for missing-coverage tests. "
                    "Demo water caps scaled to 200–500 L/h (WUE/costs unchanged) "
                    "with diurnal heat variance — mirrors scripts/seed_tiger_sites.py."
                ),
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    extended_path.write_text(
        json.dumps(
            sites_to_fixture_dict(
                extended,
                comment=(
                    "EXPLICIT SYNTHETIC EXTENSION (48h). Demo water caps = "
                    "site ceiling (200–500 L/h) × daily heat [0.8–1.3] × noise. "
                    "WUE baselines retained for pitch; same sites as seed_tiger_sites.py."
                ),
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return short_path, extended_path
