"""Site telemetry boundary — Tiger in live mode; local fixtures in synthetic mode.

``GET /api/sites`` and the reallocation optimizer both read through this module so
they share one profile source.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.integrations.tigerdata import (
    TigerDataClient,
    TigerDataError,
    fetch_site_profiles,
    tiger_env_configured,
)
from app.schemas.sites import SiteProfile
from app.services.site_time_align import align_site_profiles_to_clock
from app.state import get_session_state

logger = logging.getLogger(__name__)


def _align_for_session(profiles: list[SiteProfile]) -> list[SiteProfile]:
    state = get_session_state()
    aligned = align_site_profiles_to_clock(profiles, state.current_time)
    state.sites = list(aligned)
    return list(state.sites)


def list_site_profiles() -> list[SiteProfile]:
    """Return site profiles for the dashboard and solver.

    ``DATA_MODE=live`` reads Tiger Data only (empty list on miss/error — no fixture
    fallback). ``DATA_MODE=synthetic`` returns the local fixture snapshot.

    Profiles are slid to the demo clock when the clock has moved past the seeded
    absolute horizon so placement keeps working after Advance.
    """
    state = get_session_state()
    settings = get_settings()
    _ = TigerDataClient  # public integration surface

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
            return _align_for_session(profiles)
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

    return _align_for_session(list(state.sites))
