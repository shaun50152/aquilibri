"""Site telemetry boundary — fixture profiles today; Tiger Data later."""

from __future__ import annotations

from app.integrations.tigerdata import TigerDataClient
from app.schemas.sites import SiteProfile
from app.state import get_session_state


def list_site_profiles() -> list[SiteProfile]:
    state = get_session_state()
    _ = TigerDataClient  # integration hook; unused in fixtures mode
    return list(state.sites)