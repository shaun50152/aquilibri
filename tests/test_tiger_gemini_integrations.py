"""Unit tests for Tiger/Gemini validation helpers (no live Gemini required)."""

from __future__ import annotations

import pytest

from app.integrations.gemini import _parse_iso_utc, _validate_water_cap_lph


def test_water_cap_requires_nonnegative_integer():
    assert _validate_water_cap_lph(115) == 115
    assert _validate_water_cap_lph(115.0) == 115
    with pytest.raises(ValueError, match="integer"):
        _validate_water_cap_lph(115.5)
    with pytest.raises(ValueError, match="integer"):
        _validate_water_cap_lph("abc")
    with pytest.raises(ValueError, match="non-negative"):
        _validate_water_cap_lph(-1)


def test_iso_timestamps_must_be_timezone_aware():
    start = _parse_iso_utc("2026-03-15T20:00:00+00:00", "start_time")
    end = _parse_iso_utc("2026-03-16T12:00:00Z", "end_time")
    assert start.tzinfo is not None
    assert end > start
    with pytest.raises(ValueError, match="timezone-aware"):
        _parse_iso_utc("2026-03-15T20:00:00", "start_time")


def test_manual_preview_and_chat_routes_registered():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/reallocation/preview" in paths
    assert "/api/operator/chat" in paths
    assert "/api/sites" in paths


def test_operator_chat_requires_prompt():
    from pydantic import ValidationError

    from app.schemas.demo import OperatorChatRequest

    with pytest.raises(ValidationError):
        OperatorChatRequest(prompt="")
