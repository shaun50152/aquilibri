"""Reset Tiger Data site tables and seed five enterprise data centers (48h).

Water caps are mathematically grounded in published Water Usage Effectiveness
(WUE) from Meta / Microsoft 2024–2025 sustainability reports — not random ints.

Also refreshes local synthetic fixtures so DATA_MODE=synthetic mirrors live.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import clear_settings_cache
from app.demo_sites import (
    DEMO_SITE_SPECS,
    IT_POWER_KW,
    SITE_WUE_BASELINES_L_PER_KWH,
    base_water_usage_lph,
    build_demo_site_profiles,
    daily_heat_multipliers,
    write_site_fixtures,
)
from app.integrations.tigerdata import (
    ensure_site_profile_schema,
    fetch_site_profiles,
    tiger_connection,
    tiger_env_configured,
)

# ---------------------------------------------------------------------------
# Real-world WUE baselines (L/kWh) — Meta 2024 / Microsoft 2025 + cooling class
# ---------------------------------------------------------------------------
# Keys match the pitch regions; values are liters of water per kWh of IT load.
SITE_WUE: dict[str, float] = {
    "EU-West-1 (Dublin)": 0.19,  # Meta 2024 Average
    "US-West-1 (Santa Clara)": 0.27,  # Microsoft 2025 Average
    "AP-Northeast (Tokyo)": 1.00,  # Mixed Cooling
    "US-East-1 (Ashburn)": 1.50,  # Standard Evaporative
    "US-South-Central (Dallas)": 1.90,  # High-Temp Evaporative
}

# Keep the module-level dict identical to the shared catalog (single source of truth).
assert SITE_WUE == SITE_WUE_BASELINES_L_PER_KWH


def _print_wue_plan(hours: int) -> None:
    heat = daily_heat_multipliers(hours)
    print(
        f"IT load assumption: {IT_POWER_KW:,.0f} kW (50 MW) continuous (WUE pitch math)\n"
        f"Daily heat curve: {min(heat):.2f}x night -> {max(heat):.2f}x afternoon "
        f"({hours} h)\n"
        "Persisted water_cap = demo_ceiling (200-500 L/h) * heat * U(0.95, 1.05)\n"
        "WUE and regional $/kWh are unchanged; only the UI/scheduler ceiling is scaled.\n"
    )
    print(
        f"{'Site':<32} {'WUE':>6} {'Source':<28} {'WUE base':>12} {'Demo cap':>10}"
    )
    print("-" * 94)
    label_by_wue = {v: k for k, v in SITE_WUE.items()}
    for spec in DEMO_SITE_SPECS:
        base = base_water_usage_lph(spec.wue_l_per_kwh)
        label = label_by_wue.get(spec.wue_l_per_kwh, spec.name)
        print(
            f"{label:<32} {spec.wue_l_per_kwh:>6.2f} {spec.wue_source:<28} "
            f"{base:>12,.1f} {spec.demo_water_cap_liters_per_hour:>10.0f}"
        )
    print()


def _drop_and_recreate(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS site_hourly_profiles CASCADE")
        cur.execute("DROP TABLE IF EXISTS sites CASCADE")
    conn.commit()
    ensure_site_profile_schema(conn)


def _seed_profiles(conn, sites) -> None:
    with conn.cursor() as cur:
        for site in sites:
            cur.execute(
                """
                INSERT INTO sites (site_id, name)
                VALUES (%s, %s)
                ON CONFLICT (site_id) DO UPDATE SET name = EXCLUDED.name
                """,
                (site.site_id, site.name),
            )
            cur.execute(
                "DELETE FROM site_hourly_profiles WHERE site_id = %s",
                (site.site_id,),
            )
            for row in site.hourly_profiles:
                cur.execute(
                    """
                    INSERT INTO site_hourly_profiles (
                        site_id, hour_start, gpu_capacity,
                        fixed_water_liters_per_hour,
                        incremental_water_liters_per_gpu_hour,
                        energy_kwh_per_gpu_hour,
                        electricity_usd_per_kwh,
                        water_cap_liters_per_hour
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        site.site_id,
                        row.hour_start,
                        row.gpu_capacity,
                        row.fixed_water_liters_per_hour,
                        row.incremental_water_liters_per_gpu_hour,
                        row.energy_kwh_per_gpu_hour,
                        row.electricity_usd_per_kwh,
                        row.water_cap_liters_per_hour,
                    ),
                )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures-only",
        action="store_true",
        help="Only rewrite local JSON fixtures; skip Tiger Data.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Hourly horizon to seed into Tiger (default 48).",
    )
    args = parser.parse_args()

    clear_settings_cache()
    _print_wue_plan(args.hours)

    short_path, extended_path = write_site_fixtures(
        short_hours=3, extended_hours=max(args.hours, 24)
    )
    print(f"Wrote fixtures: {short_path.name}, {extended_path.name}")

    if args.fixtures_only:
        return

    if not tiger_env_configured():
        raise SystemExit(
            "Postgres/Tiger env not configured. Set PGHOST, PGDATABASE, PGUSER, PGPASSWORD."
        )

    sites = build_demo_site_profiles(hours=args.hours)
    with tiger_connection() as conn:
        _drop_and_recreate(conn)
        _seed_profiles(conn, sites)

    loaded = fetch_site_profiles()
    hours = sum(len(s.hourly_profiles) for s in loaded)
    print(f"Seeded {len(loaded)} sites / {hours} hourly rows into Tiger Data:")
    for site in loaded:
        caps = [r.water_cap_liters_per_hour for r in site.hourly_profiles if r.water_cap_liters_per_hour is not None]
        lo = min(caps) if caps else 0.0
        hi = max(caps) if caps else 0.0
        print(
            f"  - {site.site_id}: {site.name} "
            f"({len(site.hourly_profiles)} h, water_cap {lo:,.0f}-{hi:,.0f} L/h)"
        )


if __name__ == "__main__":
    main()
