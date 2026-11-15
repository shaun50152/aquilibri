import os

import pytest

# Keep automated tests on fixture-backed session state (not live Atlas).
os.environ.setdefault("DATA_MODE", "fixtures")
os.environ["DATA_MODE"] = "fixtures"
os.environ.setdefault("SITE_PROFILES_FIXTURE", "sites_extended_synthetic.json")
os.environ.setdefault("USE_TIGER_SITE_PROFILES", "false")

from app.config import clear_settings_cache  # noqa: E402

clear_settings_cache()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
