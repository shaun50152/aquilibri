"""Reset Tiger Data site tables and seed five enterprise data centers (48h).

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
from app.demo_sites import build_demo_site_profiles, write_site_fixtures
from app.integrations.tigerdata import (
    ensure_site_profile_schema,
    fetch_site_profiles,
    tiger_connection,
    tiger_env_configured,
)


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
        print(f"  - {site.site_id}: {site.name} ({len(site.hourly_profiles)} h)")


if __name__ == "__main__":
    main()
