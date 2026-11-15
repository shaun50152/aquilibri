"""Isolated xAI Grok summary helpers (no live network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import DEMO_CLOCK_ANCHOR_ISO
from app.integrations.grok_summary import (
    GrokSummaryError,
    build_job_summary_payload,
    estimate_job_ops_metrics,
    generate_job_summary,
)
from app.schemas.jobs import Job, JobStatus, JobTemplate
from app.schemas.sites import HourlySiteProfile, SiteProfile

NOW = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)


def _job() -> Job:
    return Job(
        id="job-hackuta-0099",
        session_id="test-session",
        owner_id="demo-user-001",
        template=JobTemplate.inference_batch,
        earliest_start=NOW,
        deadline=NOW + timedelta(hours=12),
        gpu_count=4,
        duration_hours=2.0,
        eligible_site_ids=["us-east-1"],
        data_ready_at_by_site={"us-east-1": NOW},
        critical=False,
        miss_penalty_usd=50.0,
        status=JobStatus.completed,
        assigned_site_id="us-east-1",
        scheduled_start=NOW,
        completed_at=NOW + timedelta(hours=2),
        result_url="/api/jobs/job-hackuta-0099/result",
        result_snapshot=[{"section": "metadata", "job_id": "job-hackuta-0099"}],
    )


def _site() -> SiteProfile:
    hours = [
        HourlySiteProfile(
            hour_start=NOW + timedelta(hours=i),
            gpu_capacity=48,
            fixed_water_liters_per_hour=100.0,
            incremental_water_liters_per_gpu_hour=1.0,
            energy_kwh_per_gpu_hour=0.5,
            electricity_usd_per_kwh=0.1,
            water_cap_liters_per_hour=500.0,
        )
        for i in range(4)
    ]
    return SiteProfile(
        site_id="us-east-1",
        name="US-East-1 (Ashburn, VA)",
        hourly_profiles=hours,
    )


def test_estimate_metrics_from_site_profiles():
    metrics = estimate_job_ops_metrics(_job(), [_site()])
    assert metrics["site_name"] == "US-East-1 (Ashburn, VA)"
    # (100 + 1*4) * 2h = 208 L; 0.5*4*2*0.1 = 0.4 USD
    assert metrics["water_liters"] == pytest.approx(208.0)
    assert metrics["cost_usd"] == pytest.approx(0.4)


def test_build_payload_includes_ops_fields():
    payload = build_job_summary_payload(_job(), [_site()])
    assert payload["job_id"] == "job-hackuta-0099"
    assert payload["site_id"] == "us-east-1"
    assert payload["duration_hours"] == 2.0
    assert payload["water_liters"] is not None


def test_generate_requires_api_key(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "")
    from app.config import clear_settings_cache

    clear_settings_cache()
    with pytest.raises(GrokSummaryError, match="GROK_API_KEY"):
        generate_job_summary({"job_id": "x", "site_name": "Ashburn", "duration_hours": 1})
    clear_settings_cache()


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def _with_key(monkeypatch, fake_post):
    from app.config import clear_settings_cache

    monkeypatch.setenv("GROK_API_KEY", "test-key")
    monkeypatch.setattr("app.integrations.grok_summary.httpx.post", fake_post)
    clear_settings_cache()


def test_generate_posts_chat_completion_over_http(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, auth=headers["Authorization"], model=json["model"])
        return _FakeResponse(200, {"choices": [{"message": {"content": "  Ran clean. Under cap.  "}}]})

    _with_key(monkeypatch, fake_post)
    try:
        assert generate_job_summary({"job_id": "x"}) == "Ran clean. Under cap."
        assert seen["url"].endswith("/chat/completions")
        assert seen["auth"] == "Bearer test-key"
    finally:
        from app.config import clear_settings_cache

        clear_settings_cache()


def test_generate_surfaces_credit_errors(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse(403, {"code": "permission-denied", "error": "no credits"})

    _with_key(monkeypatch, fake_post)
    try:
        with pytest.raises(GrokSummaryError, match="no API credits"):
            generate_job_summary({"job_id": "x"})
    finally:
        from app.config import clear_settings_cache

        clear_settings_cache()


def test_summary_route_requires_completed(client):
    res = client.get("/api/jobs/job-hackuta-0001/summary")
    assert res.status_code == 404
    assert "completed" in res.json()["detail"].lower()
