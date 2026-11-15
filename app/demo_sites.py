"""Enterprise demo data-center catalog shared by fixtures and Tiger seeding."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import DEMO_CLOCK_ANCHOR_ISO, FIXTURES_DIR
from app.schemas.sites import HourlySiteProfile, SiteProfile


@dataclass(frozen=True)
class DemoSiteSpec:
    site_id: str
    name: str
    region: str
    gpu_capacity: int
    fixed_water_liters_per_hour: float
    incremental_water_liters_per_gpu_hour: float
    energy_kwh_per_gpu_hour: float
    electricity_usd_per_kwh: float
    water_cap_liters_per_hour: float
    # Mild diurnal amplitude multipliers (0 = flat).
    water_diurnal: float = 0.04
    price_diurnal: float = 0.08


# Five production-looking regions for the AquaShift X pitch.
DEMO_SITE_SPECS: tuple[DemoSiteSpec, ...] = (
    DemoSiteSpec(
        site_id="us-east-1",
        name="US-East-1 (Ashburn, VA)",
        region="us-east",
        gpu_capacity=48,  # high capacity
        fixed_water_liters_per_hour=105.0,
        incremental_water_liters_per_gpu_hour=0.75,
        energy_kwh_per_gpu_hour=0.42,
        electricity_usd_per_kwh=0.085,  # average cost
        water_cap_liters_per_hour=520.0,
    ),
    DemoSiteSpec(
        site_id="us-south-central",
        name="US-South-Central (Dallas, TX)",
        region="us-south",
        gpu_capacity=32,  # moderate capacity
        fixed_water_liters_per_hour=95.0,
        incremental_water_liters_per_gpu_hour=0.88,
        energy_kwh_per_gpu_hour=0.48,
        electricity_usd_per_kwh=0.055,  # lower cost
        water_cap_liters_per_hour=480.0,
    ),
    DemoSiteSpec(
        site_id="us-west-1",
        name="US-West-1 (Santa Clara, CA)",
        region="us-west",
        gpu_capacity=36,
        fixed_water_liters_per_hour=115.0,
        incremental_water_liters_per_gpu_hour=0.92,
        energy_kwh_per_gpu_hour=0.40,
        electricity_usd_per_kwh=0.145,  # high cost
        water_cap_liters_per_hour=280.0,  # stricter water limits
        water_diurnal=0.06,
    ),
    DemoSiteSpec(
        site_id="eu-west-1",
        name="EU-West-1 (Dublin, Ireland)",
        region="eu-west",
        gpu_capacity=40,
        fixed_water_liters_per_hour=88.0,
        incremental_water_liters_per_gpu_hour=0.70,
        energy_kwh_per_gpu_hour=0.34,  # green / efficient
        electricity_usd_per_kwh=0.155,  # high cost
        water_cap_liters_per_hour=400.0,
    ),
    DemoSiteSpec(
        site_id="ap-northeast",
        name="AP-Northeast (Tokyo, Japan)",
        region="ap-northeast",
        gpu_capacity=40,
        fixed_water_liters_per_hour=92.0,
        incremental_water_liters_per_gpu_hour=0.62,  # high efficiency
        energy_kwh_per_gpu_hour=0.36,
        electricity_usd_per_kwh=0.175,  # highest cost
        water_cap_liters_per_hour=420.0,
    ),
)

DEMO_SITE_IDS: tuple[str, ...] = tuple(s.site_id for s in DEMO_SITE_SPECS)

# Santa Clara — primary emergency / water-restriction demo target.
DEMO_EMERGENCY_SITE_ID = "us-west-1"


def build_demo_site_profiles(
    *,
    hours: int = 48,
    anchor: datetime | None = None,
) -> list[SiteProfile]:
    """Build hourly SiteProfile snapshots for the enterprise demo fleet."""
    if hours < 1:
        raise ValueError("hours must be >= 1")
    if anchor is None:
        anchor = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)

    sites: list[SiteProfile] = []
    for spec in DEMO_SITE_SPECS:
        rows: list[HourlySiteProfile] = []
        for h in range(hours):
            hour_start = anchor + timedelta(hours=h)
            # Local-ish diurnal using UTC hour for stable demos.
            phase = (hour_start.hour / 24.0) * 2.0 * math.pi
            water_factor = 1.0 + spec.water_diurnal * math.sin(phase - 0.4)
            price_factor = 1.0 + spec.price_diurnal * math.sin(phase + 0.8)
            # Slight GPU capacity headroom drop in peak afternoon hours.
            gpu = spec.gpu_capacity
            if 14 <= hour_start.hour <= 19:
                gpu = max(16, spec.gpu_capacity - 4)

            rows.append(
                HourlySiteProfile(
                    hour_start=hour_start,
                    gpu_capacity=gpu,
                    fixed_water_liters_per_hour=round(
                        spec.fixed_water_liters_per_hour * water_factor, 3
                    ),
                    incremental_water_liters_per_gpu_hour=spec.incremental_water_liters_per_gpu_hour,
                    energy_kwh_per_gpu_hour=spec.energy_kwh_per_gpu_hour,
                    electricity_usd_per_kwh=round(
                        spec.electricity_usd_per_kwh * price_factor, 5
                    ),
                    water_cap_liters_per_hour=spec.water_cap_liters_per_hour,
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
                    "Mirrors the five enterprise regions used in live Tiger Data."
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
                    "EXPLICIT SYNTHETIC EXTENSION (48h) for placement/preview demos. "
                    "Same five enterprise sites as scripts/seed_tiger_sites.py."
                ),
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return short_path, extended_path
