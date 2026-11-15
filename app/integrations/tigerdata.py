"""Tiger Data (Postgres / Timescale) hourly site telemetry."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from app.config import get_settings
from app.schemas.sites import HourlySiteProfile, SiteProfile


class TigerDataError(RuntimeError):
    """Raised when Tiger Data is misconfigured or a query fails."""


def tiger_env_configured() -> bool:
    """True when standard Postgres env vars (or DATABASE_URL) are present."""
    settings = get_settings()
    if settings.pghost.strip() and settings.pgdatabase.strip() and settings.pguser.strip():
        return True
    if settings.tigerdata_database_url.strip():
        return True
    return False


def _connect_kwargs() -> dict[str, Any]:
    settings = get_settings()
    if settings.pghost.strip():
        return {
            "host": settings.pghost,
            "dbname": settings.pgdatabase,
            "user": settings.pguser,
            "password": settings.pgpassword,
            "port": settings.pgport or "5432",
            "sslmode": settings.pgsslmode or "require",
        }
    url = settings.tigerdata_database_url.strip()
    if not url:
        raise TigerDataError(
            "Tiger Data is not configured. Set PGHOST/PGDATABASE/PGUSER/PGPASSWORD "
            "(and optional PGPORT/PGSSLMODE) or TIGERDATA_DATABASE_URL."
        )
    return {"dsn": url}


@contextmanager
def tiger_connection() -> Iterator[Any]:
    try:
        import psycopg2
    except ImportError as exc:
        raise TigerDataError("psycopg2 is required for Tiger Data (pip install psycopg2-binary)") from exc

    conn = psycopg2.connect(**_connect_kwargs())
    try:
        yield conn
    finally:
        conn.close()


class TigerDataClient:
    """Reads hourly GPU, water, and energy telemetry from Tiger Data."""

    def __init__(self, database_url: str = "") -> None:
        self.database_url = database_url

    def fetch_hourly_profiles(
        self,
        site_ids: Optional[list[str]] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[SiteProfile]:
        return fetch_site_profiles(site_ids=site_ids, start=start, end=end)


def ensure_site_profile_schema(conn: Any) -> None:
    """Create public site profile tables if missing (idempotent)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sites (
                site_id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS site_hourly_profiles (
                site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
                hour_start TIMESTAMPTZ NOT NULL,
                gpu_capacity INTEGER NOT NULL CHECK (gpu_capacity >= 0),
                fixed_water_liters_per_hour DOUBLE PRECISION NOT NULL
                    CHECK (fixed_water_liters_per_hour >= 0),
                incremental_water_liters_per_gpu_hour DOUBLE PRECISION NOT NULL
                    CHECK (incremental_water_liters_per_gpu_hour >= 0),
                energy_kwh_per_gpu_hour DOUBLE PRECISION NOT NULL
                    CHECK (energy_kwh_per_gpu_hour >= 0),
                electricity_usd_per_kwh DOUBLE PRECISION NOT NULL
                    CHECK (electricity_usd_per_kwh >= 0),
                water_cap_liters_per_hour DOUBLE PRECISION
                    CHECK (water_cap_liters_per_hour IS NULL OR water_cap_liters_per_hour >= 0),
                PRIMARY KEY (site_id, hour_start)
            );

            CREATE INDEX IF NOT EXISTS idx_site_hourly_hour
                ON site_hourly_profiles (hour_start);
            """
        )
        # Best-effort hypertable; ignore if extension/policy already applied.
        try:
            cur.execute(
                """
                SELECT create_hypertable(
                    'site_hourly_profiles',
                    'hour_start',
                    if_not_exists => TRUE,
                    migrate_data => TRUE
                );
                """
            )
        except Exception:
            conn.rollback()
            # Recreate plain tables after rollback of failed hypertable attempt.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sites (
                    site_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS site_hourly_profiles (
                    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
                    hour_start TIMESTAMPTZ NOT NULL,
                    gpu_capacity INTEGER NOT NULL CHECK (gpu_capacity >= 0),
                    fixed_water_liters_per_hour DOUBLE PRECISION NOT NULL
                        CHECK (fixed_water_liters_per_hour >= 0),
                    incremental_water_liters_per_gpu_hour DOUBLE PRECISION NOT NULL
                        CHECK (incremental_water_liters_per_gpu_hour >= 0),
                    energy_kwh_per_gpu_hour DOUBLE PRECISION NOT NULL
                        CHECK (energy_kwh_per_gpu_hour >= 0),
                    electricity_usd_per_kwh DOUBLE PRECISION NOT NULL
                        CHECK (electricity_usd_per_kwh >= 0),
                    water_cap_liters_per_hour DOUBLE PRECISION
                        CHECK (water_cap_liters_per_hour IS NULL OR water_cap_liters_per_hour >= 0),
                    PRIMARY KEY (site_id, hour_start)
                );
                """
            )
    conn.commit()


def fetch_site_profiles(
    *,
    site_ids: Optional[list[str]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[SiteProfile]:
    """Query data-center hourly profiles (hardware limits, water caps, energy)."""
    with tiger_connection() as conn:
        ensure_site_profile_schema(conn)
        params: list[Any] = []
        where = ["1=1"]
        if site_ids:
            where.append("s.site_id = ANY(%s)")
            params.append(list(site_ids))
        if start is not None:
            where.append("h.hour_start >= %s")
            params.append(start)
        if end is not None:
            where.append("h.hour_start < %s")
            params.append(end)

        sql = f"""
            SELECT
                s.site_id,
                s.name,
                h.hour_start,
                h.gpu_capacity,
                h.fixed_water_liters_per_hour,
                h.incremental_water_liters_per_gpu_hour,
                h.energy_kwh_per_gpu_hour,
                h.electricity_usd_per_kwh,
                h.water_cap_liters_per_hour
            FROM sites s
            JOIN site_hourly_profiles h ON h.site_id = s.site_id
            WHERE {" AND ".join(where)}
            ORDER BY s.site_id, h.hour_start
        """
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    by_site: dict[str, SiteProfile] = {}
    for (
        site_id,
        name,
        hour_start,
        gpu_capacity,
        fixed_water,
        incremental,
        energy,
        price,
        water_cap,
    ) in rows:
        if site_id not in by_site:
            by_site[site_id] = SiteProfile(site_id=site_id, name=name, hourly_profiles=[])
        by_site[site_id].hourly_profiles.append(
            HourlySiteProfile(
                hour_start=hour_start,
                gpu_capacity=int(gpu_capacity),
                fixed_water_liters_per_hour=float(fixed_water),
                incremental_water_liters_per_gpu_hour=float(incremental),
                energy_kwh_per_gpu_hour=float(energy),
                electricity_usd_per_kwh=float(price),
                water_cap_liters_per_hour=(
                    None if water_cap is None else float(water_cap)
                ),
            )
        )

    return [by_site[k] for k in sorted(by_site)]
