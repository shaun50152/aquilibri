import os

# Tests always run offline; local .env may set mongodb for manual runs.
os.environ["DATA_MODE"] = "fixtures"
os.environ["MONGODB_URI"] = ""

from app.config import clear_settings_cache

clear_settings_cache()

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client