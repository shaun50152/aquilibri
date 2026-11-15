"""Alias module requested by Hours 6–9 task naming.

Prefer importing from ``app.integrations.tigerdata``.
"""

from app.integrations.tigerdata import (
    TigerDataClient,
    TigerDataError,
    ensure_site_profile_schema,
    fetch_site_profiles,
    tiger_connection,
    tiger_env_configured,
)

__all__ = [
    "TigerDataClient",
    "TigerDataError",
    "ensure_site_profile_schema",
    "fetch_site_profiles",
    "tiger_connection",
    "tiger_env_configured",
]
