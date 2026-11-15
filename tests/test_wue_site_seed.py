"""WUE-grounded site specs with demo-scaled water caps (200–500 L/h)."""

from __future__ import annotations

from app.demo_sites import (
    DEMO_SITE_SPECS,
    IT_POWER_KW,
    SITE_WUE_BASELINES_L_PER_KWH,
    base_water_usage_lph,
    build_demo_site_profiles,
    daily_heat_multipliers,
    hourly_water_cap_lph,
)


def test_site_wue_baselines_match_pitch_catalog():
    by_wue = {s.wue_l_per_kwh for s in DEMO_SITE_SPECS}
    assert by_wue == set(SITE_WUE_BASELINES_L_PER_KWH.values())
    assert SITE_WUE_BASELINES_L_PER_KWH["EU-West-1 (Dublin)"] == 0.19
    assert SITE_WUE_BASELINES_L_PER_KWH["US-West-1 (Santa Clara)"] == 0.27
    assert SITE_WUE_BASELINES_L_PER_KWH["AP-Northeast (Tokyo)"] == 1.00
    assert SITE_WUE_BASELINES_L_PER_KWH["US-East-1 (Ashburn)"] == 1.50
    assert SITE_WUE_BASELINES_L_PER_KWH["US-South-Central (Dallas)"] == 1.90


def test_base_water_usage_is_it_power_times_wue():
    assert base_water_usage_lph(0.19) == IT_POWER_KW * 0.19
    assert base_water_usage_lph(1.90) == 95_000.0


def test_demo_caps_sit_in_200_to_500_band():
    caps = [s.demo_water_cap_liters_per_hour for s in DEMO_SITE_SPECS]
    assert min(caps) >= 200.0
    assert max(caps) <= 500.0
    by_id = {s.site_id: s.demo_water_cap_liters_per_hour for s in DEMO_SITE_SPECS}
    # Virginia / Ireland generous; California water-stressed / tight.
    assert by_id["us-east-1"] == 500.0
    assert by_id["eu-west-1"] == 500.0
    assert by_id["us-west-1"] == 250.0


def test_daily_heat_curve_swings_from_night_to_afternoon():
    heat = daily_heat_multipliers(48)
    assert len(heat) == 48
    assert min(heat) == 0.8
    assert max(heat) == 1.3
    assert heat[15] == 1.3
    assert heat[3] == 0.8
    assert heat[24:48] == heat[0:24]


def test_hourly_water_cap_applies_heat_and_noise_to_demo_ceiling():
    cap = hourly_water_cap_lph(250.0, 1.3, noise=1.0)
    assert cap == round(250.0 * 1.3, 3)
    noisy = hourly_water_cap_lph(500.0, 1.0, noise=1.05)
    assert noisy == round(500.0 * 1.05, 3)


def test_build_demo_profiles_persist_scaled_demo_caps():
    sites = build_demo_site_profiles(hours=48)
    assert len(sites) == 5
    by_id = {s.site_id: s for s in sites}
    all_caps = [
        h.water_cap_liters_per_hour
        for s in sites
        for h in s.hourly_profiles
    ]
    assert min(all_caps) >= 200.0
    assert max(all_caps) <= 500.0
    california = [h.water_cap_liters_per_hour for h in by_id["us-west-1"].hourly_profiles]
    virginia = [h.water_cap_liters_per_hour for h in by_id["us-east-1"].hourly_profiles]
    # California stays tighter than Virginia/Ireland; not 10k–100k WUE raw.
    assert max(california) < min(virginia)
    assert len(set(round(c, 1) for c in california)) > 5
