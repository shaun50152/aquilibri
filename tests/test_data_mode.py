"""DATA_MODE live/synthetic helpers and legacy aliases."""

from __future__ import annotations

import os

import pytest

from app.config import Settings, clear_settings_cache


@pytest.fixture(autouse=True)
def _clear_settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.parametrize(
    ("raw", "canonical", "is_live"),
    [
        ("synthetic", "synthetic", False),
        ("live", "live", True),
        ("fixtures", "synthetic", False),
        ("mongodb", "live", True),
        ("FIXTURES", "synthetic", False),
        ("MongoDB", "live", True),
    ],
)
def test_data_mode_aliases_normalize(raw, canonical, is_live, monkeypatch):
    monkeypatch.setenv("DATA_MODE", raw)
    if is_live:
        monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    else:
        monkeypatch.setenv("MONGODB_URI", "")
    clear_settings_cache()
    settings = Settings()
    assert settings.data_mode == canonical
    assert settings.is_live is is_live
    assert settings.is_synthetic is (not is_live)
    assert settings.should_use_tiger_site_profiles() is is_live


def test_live_requires_mongodb_uri(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "live")
    monkeypatch.setenv("MONGODB_URI", "")
    clear_settings_cache()
    settings = Settings()
    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        settings.require_supported_data_mode()
