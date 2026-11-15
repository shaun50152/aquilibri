"""Site telemetry boundary — Tiger in live mode; local fixtures in synthetic mode.

``GET /api/sites`` and the reallocation optimizer both read through this module so
they share one profile source.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.config import get_settings
from app.integrations.tigerdata import (
    TigerDataClient,
    TigerDataError,
    fetch_site_profiles,
    tiger_env_configured,
)
from app.schemas.scheduling import EmergencyRestriction
from app.schemas.sites import (
    HourlySiteProfile,
    SiteListResponse,
    SiteProfile,
    TemporaryWaterRestriction,
)
from app.services.site_time_align import align_site_profiles_to_clock
from app.state import get_session_state

logger = logging.getLogger(__name__)


def _align_for_session(profiles: list[SiteProfile]) -> list[SiteProfile]:
    state = get_session_state()
    aligned = align_site_profiles_to_clock(profiles, state.current_time)
    state.sites = list(aligned)
    return list(state.sites)


def apply_temporary_water_caps(
    profiles: list[SiteProfile],
    restriction: EmergencyRestriction | None,
) -> list[SiteProfile]:
    """Overlay a time-bounded emergency water cap onto matching site hours.

    Permanent baseline caps remain for hours outside the window. During the
    temporary window the effective cap is ``min(baseline, temporary)``.
    """
    if restriction is None:
        return profiles

    out: list[SiteProfile] = []
    for site in profiles:
        if site.site_id != restriction.site_id:
            out.append(site)
            continue
        rows: list[HourlySiteProfile] = []
        for row in site.hourly_profiles:
            hour_end = row.hour_start + timedelta(hours=1)
            overlaps = row.hour_start < restriction.end and hour_end > restriction.start
            if not overlaps:
                rows.append(row)
                continue
            base = row.water_cap_liters_per_hour
            # Keep the permanent baseline for UI tank scaling even when the
            # temporary emergency cap is far below facility fixed load.
            baseline = (
                row.baseline_water_cap_liters_per_hour
                if row.baseline_water_cap_liters_per_hour is not None
                else base
            )
            temp = float(restriction.water_cap_liters_per_hour)
            effective = temp if base is None else min(float(base), temp)
            rows.append(
                row.model_copy(
                    update={
                        "water_cap_liters_per_hour": effective,
                        "baseline_water_cap_liters_per_hour": baseline,
                    }
                )
            )
        out.append(SiteProfile(site_id=site.site_id, name=site.name, hourly_profiles=rows))
    return out


def list_site_profiles() -> list[SiteProfile]:
    """Return site profiles for the dashboard and solver.

    ``DATA_MODE=live`` reads Tiger Data only (empty list on miss/error — no fixture
    fallback). ``DATA_MODE=synthetic`` returns the local fixture snapshot.

    Profiles are slid to the demo clock when the clock has moved past the seeded
    absolute horizon so placement keeps working after Advance. Active temporary
    emergency caps are merged into the returned hourly water caps.
    """
    state = get_session_state()
    settings = get_settings()
    _ = TigerDataClient  # public integration surface
    restriction = state.get_active_temporary_restriction()

    if settings.should_use_tiger_site_profiles():
        if not tiger_env_configured():
            logger.warning(
                "DATA_MODE=live but Tiger/Postgres is not configured; returning empty sites."
            )
            state.sites = []
            return []
        try:
            profiles = fetch_site_profiles()
            if not profiles:
                logger.warning(
                    "Tiger Data returned no site profiles; live mode keeps a blank slate."
                )
                state.sites = []
                return []
            aligned = _align_for_session(profiles)
            return apply_temporary_water_caps(aligned, restriction)
        except TigerDataError as exc:
            logger.warning(
                "Tiger Data unavailable (%s); live mode returns empty sites (no fixture fallback).",
                exc,
            )
            state.sites = []
            return []
        except Exception as exc:  # noqa: BLE001 — keep API up on telemetry outages
            logger.warning(
                "Tiger Data query failed (%s); live mode returns empty sites (no fixture fallback).",
                exc,
            )
            state.sites = []
            return []

    aligned = _align_for_session(list(state.sites))
    return apply_temporary_water_caps(aligned, restriction)


def sites_list_response() -> SiteListResponse:
    """Dashboard payload: sites (+ temporary emergency overlay metadata)."""
    state = get_session_state()
    sites = list_site_profiles()
    er = state.get_active_temporary_restriction()
    temp = None
    if er is not None:
        temp = TemporaryWaterRestriction(
            site_id=er.site_id,
            water_cap_liters_per_hour=er.water_cap_liters_per_hour,
            start=er.start,
            end=er.end,
            temporary=True,
            label="temporary emergency water restriction",
        )
    return SiteListResponse(sites=sites, temporary_restriction=temp)
