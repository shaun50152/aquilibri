"""Demo-clock alignment for site hourly profiles."""

from datetime import datetime, timedelta, timezone

from app.schemas.sites import HourlySiteProfile, SiteProfile
from app.services.site_time_align import align_site_profiles_to_clock


def _site(start: datetime, hours: int = 3) -> SiteProfile:
    rows = []
    for i in range(hours):
        rows.append(
            HourlySiteProfile(
                hour_start=start + timedelta(hours=i),
                gpu_capacity=8,
                fixed_water_liters_per_hour=10.0,
                incremental_water_liters_per_gpu_hour=1.0,
                energy_kwh_per_gpu_hour=0.5,
                electricity_usd_per_kwh=0.1,
                water_cap_liters_per_hour=100.0,
            )
        )
    return SiteProfile(site_id="us-east-1", name="US-East-1 (Ashburn, VA)", hourly_profiles=rows)


def test_no_shift_while_clock_inside_horizon():
    start = datetime(2026, 3, 15, 18, tzinfo=timezone.utc)
    sites = [_site(start)]
    out = align_site_profiles_to_clock(sites, start + timedelta(hours=1))
    assert out[0].hourly_profiles[0].hour_start == start


def test_shifts_when_clock_past_horizon():
    start = datetime(2026, 3, 15, 18, tzinfo=timezone.utc)
    sites = [_site(start, hours=2)]
    clock = datetime(2026, 3, 17, 1, tzinfo=timezone.utc)
    out = align_site_profiles_to_clock(sites, clock)
    assert out[0].hourly_profiles[0].hour_start == clock.replace(
        minute=0, second=0, microsecond=0
    )
    assert out[0].name == "US-East-1 (Ashburn, VA)"
