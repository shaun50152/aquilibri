"""Create Tiger Data site tables and seed from the extended synthetic fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import SITE_PROFILES_EXTENDED_SYNTHETIC, FIXTURES_DIR, clear_settings_cache
from app.integrations.tigerdata import (
    ensure_site_profile_schema,
    fetch_site_profiles,
    tiger_connection,
    tiger_env_configured,
)
from app.schemas.sites import SiteProfile


def main() -> None:
    clear_settings_cache()
    if not tiger_env_configured():
        raise SystemExit(
            "Postgres/Tiger env not configured. Set PGHOST, PGDATABASE, PGUSER, PGPASSWORD."
        )

    fixture_path = FIXTURES_DIR / SITE_PROFILES_EXTENDED_SYNTHETIC
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    sites = [SiteProfile.model_validate(s) for s in raw["sites"]]

    with tiger_connection() as conn:
        ensure_site_profile_schema(conn)
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

    loaded = fetch_site_profiles()
    hours = sum(len(s.hourly_profiles) for s in loaded)
    print(f"Seeded {len(loaded)} sites / {hours} hourly rows into Tiger Data.")


if __name__ == "__main__":
    main()
