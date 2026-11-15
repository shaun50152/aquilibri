from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: parent of app/
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
FIXTURES_DIR = ROOT_DIR / "fixtures"
FRONTEND_DIR = ROOT_DIR / "frontend"

# Documented demo anchor — fixtures use timestamps relative to this instant.
DEMO_CLOCK_ANCHOR_ISO = "2026-03-15T18:00:00+00:00"

SUPPORTED_DATA_MODES = frozenset({"fixtures", "mongodb"})

# Synthetic site telemetry fixtures under fixtures/.
# - sites.json: short 3-hour horizon (missing-coverage tests)
# - sites_extended_synthetic.json: 18-hour synthetic horizon for runnable preview demos
SITE_PROFILES_SHORT = "sites.json"
SITE_PROFILES_EXTENDED_SYNTHETIC = "sites_extended_synthetic.json"
SUPPORTED_SITE_PROFILE_FIXTURES = frozenset(
    {SITE_PROFILES_SHORT, SITE_PROFILES_EXTENDED_SYNTHETIC}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_mode: str = "fixtures"
    demo_session_id: str = "demo-session-hackuta-2026"
    # Filename under fixtures/ — both files are explicitly synthetic demo data.
    site_profiles_fixture: str = SITE_PROFILES_SHORT
    mongodb_uri: str = ""
    mongodb_database: str = "aquilibri"
    tigerdata_database_url: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""

    def require_supported_data_mode(self) -> None:
        if self.data_mode not in SUPPORTED_DATA_MODES:
            raise RuntimeError(
                f"Unsupported DATA_MODE={self.data_mode!r}. "
                f"Supported modes: {sorted(SUPPORTED_DATA_MODES)}. "
                "Cloud integrations are not enabled in this scaffold build."
            )
        if self.data_mode == "mongodb" and not self.mongodb_uri.strip():
            raise RuntimeError(
                "DATA_MODE=mongodb requires MONGODB_URI to be set (e.g. in local .env)."
            )

    def require_supported_site_profiles_fixture(self) -> None:
        name = self.site_profiles_fixture
        if name not in SUPPORTED_SITE_PROFILE_FIXTURES:
            raise RuntimeError(
                f"Unsupported SITE_PROFILES_FIXTURE={name!r}. "
                f"Supported: {sorted(SUPPORTED_SITE_PROFILE_FIXTURES)}."
            )
        path = FIXTURES_DIR / name
        if not path.is_file():
            raise RuntimeError(f"Site profiles fixture not found: {path}")

    def site_profiles_path(self) -> Path:
        self.require_supported_site_profiles_fixture()
        return FIXTURES_DIR / self.site_profiles_fixture


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.require_supported_data_mode()
    settings.require_supported_site_profiles_fixture()
    return settings


def clear_settings_cache() -> None:
    """Test helper — clear cached settings after env changes."""
    get_settings.cache_clear()
