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

SUPPORTED_DATA_MODES = frozenset({"fixtures"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_mode: str = "fixtures"
    demo_session_id: str = "demo-session-hackuta-2026"
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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.require_supported_data_mode()
    return settings