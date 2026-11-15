"""Site telemetry boundary — Tiger Data when configured; fixture session otherwise.

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
from app.state import get_session_state

logger = logging.getLogger(__name__)


def list_site_profiles() -> list[SiteProfile]:
    """Return site profiles for the dashboard and solver.

    Prefer Tiger Data when Postgres env is configured and ``use_tiger_site_profiles``
    is enabled. On empty/error results, fall back to the session fixture snapshot.
    """
    state = get_session_state()
    settings = get_settings()
    _ = TigerDataClient  # public integration surface

    if settings.use_tiger_site_profiles and tiger_env_configured():
        try:
            profiles = fetch_site_profiles()
            if profiles:
                state.sites = profiles
                return list(state.sites)
            logger.warning(
                "Tiger Data returned no site profiles; using session fixture sites."
            )
        except TigerDataError as exc:
            logger.warning("Tiger Data unavailable (%s); using session fixture sites.", exc)
        except Exception as exc:  # noqa: BLE001 — keep API up on telemetry outages
            logger.warning(
                "Tiger Data query failed (%s); using session fixture sites.", exc
            )

    return list(state.sites)
