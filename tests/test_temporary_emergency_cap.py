"""Temporary emergency water caps persist on apply and overlay GET /api/sites."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import DEMO_CLOCK_ANCHOR_ISO
from app.repositories.telemetry_repository import apply_temporary_water_caps
from app.schemas.scheduling import EmergencyRestriction
from app.schemas.sites import HourlySiteProfile, SiteProfile
from app.state import get_session_state

NOW = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)


def test_apply_overlays_temporary_cap_on_matching_hours_only():
    hours = [
        HourlySiteProfile(
            hour_start=NOW + timedelta(hours=i),
            gpu_capacity=32,
            fixed_water_liters_per_hour=100.0,
            incremental_water_liters_per_gpu_hour=1.0,
            energy_kwh_per_gpu_hour=0.5,
            electricity_usd_per_kwh=0.1,
            water_cap_liters_per_hour=500.0,
        )
        for i in range(6)
    ]
    sites = [
        SiteProfile(site_id="us-west-1", name="US-West-1 (Santa Clara, CA)", hourly_profiles=hours),
        SiteProfile(
            site_id="us-east-1",
            name="US-East-1 (Ashburn, VA)",
            hourly_profiles=[h.model_copy() for h in hours],
        ),
    ]
    er = EmergencyRestriction(
        site_id="us-west-1",
        water_cap_liters_per_hour=120.0,
        start=NOW + timedelta(hours=2),
        end=NOW + timedelta(hours=4),
    )
    out = apply_temporary_water_caps(sites, er)
    west = next(s for s in out if s.site_id == "us-west-1")
    east = next(s for s in out if s.site_id == "us-east-1")
    assert west.hourly_profiles[0].water_cap_liters_per_hour == 500.0
    assert west.hourly_profiles[2].water_cap_liters_per_hour == 120.0
    assert west.hourly_profiles[2].baseline_water_cap_liters_per_hour == 500.0
    assert west.hourly_profiles[3].water_cap_liters_per_hour == 120.0
    assert west.hourly_profiles[3].baseline_water_cap_liters_per_hour == 500.0
    assert west.hourly_profiles[4].water_cap_liters_per_hour == 500.0
    assert west.hourly_profiles[4].baseline_water_cap_liters_per_hour is None
    assert all(h.water_cap_liters_per_hour == 500.0 for h in east.hourly_profiles)


def test_sites_api_exposes_temporary_restriction_after_apply(client):
    client.post("/api/demo/reset")
    # Seed a restriction into session state the way Apply would.
    state = get_session_state()
    state.active_temporary_restriction = EmergencyRestriction(
        site_id="us-west-1",
        water_cap_liters_per_hour=115.0,
        start=NOW + timedelta(hours=1),
        end=NOW + timedelta(hours=8),
    )
    res = client.get("/api/sites")
    assert res.status_code == 200
    body = res.json()
    assert body["temporary_restriction"]["temporary"] is True
    assert body["temporary_restriction"]["site_id"] == "us-west-1"
    assert "temporary" in body["temporary_restriction"]["label"]
    west = next(s for s in body["sites"] if s["site_id"] == "us-west-1")
    # At least one hour inside the window should show the tighter temporary cap
    # while preserving the permanent baseline for UI tank scaling.
    caps = [h["water_cap_liters_per_hour"] for h in west["hourly_profiles"]]
    assert 115.0 in caps
    tightened = [h for h in west["hourly_profiles"] if h["water_cap_liters_per_hour"] == 115.0]
    assert tightened
    assert all(h.get("baseline_water_cap_liters_per_hour") is not None for h in tightened)
    assert all(h["baseline_water_cap_liters_per_hour"] >= 115.0 for h in tightened)
