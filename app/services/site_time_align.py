"""Keep site hourly profiles usable when the demo clock advances past the seeded horizon."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.sites import HourlySiteProfile, SiteProfile


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_to_hour(dt: datetime) -> datetime:
    dt = _ensure_utc(dt)
    return dt.replace(minute=0, second=0, microsecond=0)


def align_site_profiles_to_clock(
    sites: list[SiteProfile],
    current_time: datetime,
) -> list[SiteProfile]:
    """If the demo clock is outside the profile horizon, slide all hours forward/back.

    Seeded Tiger/fixture rows are absolute timestamps around the demo anchor. After the
    operator advances past the last hour, placement has no coverage and jobs stay queued.
    Sliding the window so the first hour matches ``floor(current_time)`` keeps the solver
    and dashboard usable without re-seeding.
    """
    if not sites:
        return sites

    all_hours: list[datetime] = []
    for site in sites:
        for row in site.hourly_profiles:
            all_hours.append(_ensure_utc(row.hour_start))
    if not all_hours:
        return sites

    first = min(all_hours)
    last = max(all_hours)
    clock_hour = floor_to_hour(current_time)

    # Still have a startable hour in-range — leave absolute timestamps alone
    # (emergency previews and docs use fixed ISO windows against the fixture).
    if first <= clock_hour <= last:
        return sites

    shift = clock_hour - first
    aligned: list[SiteProfile] = []
    for site in sites:
        rows = [
            HourlySiteProfile(
                hour_start=_ensure_utc(row.hour_start) + shift,
                gpu_capacity=row.gpu_capacity,
                fixed_water_liters_per_hour=row.fixed_water_liters_per_hour,
                incremental_water_liters_per_gpu_hour=row.incremental_water_liters_per_gpu_hour,
                energy_kwh_per_gpu_hour=row.energy_kwh_per_gpu_hour,
                electricity_usd_per_kwh=row.electricity_usd_per_kwh,
                water_cap_liters_per_hour=row.water_cap_liters_per_hour,
            )
            for row in site.hourly_profiles
        ]
        aligned.append(
            SiteProfile(site_id=site.site_id, name=site.name, hourly_profiles=rows)
        )
    return aligned
