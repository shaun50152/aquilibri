import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import FIXTURES_DIR, ROOT_DIR
from app.schemas.jobs import Job
from app.schemas.scheduling import EmergencyRestriction, ReallocationPreviewResponse
from app.schemas.sites import SiteProfile


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


def _preview_model_data(name: str) -> dict:
    data = _load_fixture(name)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(autouse=True)
def reset_session(client):
    client.post("/api/demo/reset")
    yield


def test_portal_page_loads(client):
    response = client.get("/portal")
    assert response.status_code == 200
    assert "AquaLibri" in response.text


def test_operator_page_loads(client):
    response = client.get("/operator")
    assert response.status_code == 200
    assert "Operator dashboard" in response.text


def test_root_redirects_to_portal(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/portal"


def test_fixture_jobs_match_schema():
    raw = _load_fixture("jobs.json")
    for job in raw["jobs"]:
        Job.model_validate(job)


def test_fixture_sites_match_schema():
    raw = _load_fixture("sites.json")
    for site in raw["sites"]:
        SiteProfile.model_validate(site)


@pytest.mark.parametrize(
    "preview_file",
    ["preview_move.json", "preview_unfulfilled.json", "preview_infeasible.json"],
)
def test_sample_previews_match_schema(preview_file):
    ReallocationPreviewResponse.model_validate(_preview_model_data(preview_file))


def test_health_no_secrets(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data_mode"] == "fixtures"
    assert "gemini" not in json.dumps(body).lower()
    assert "mongodb_uri" not in json.dumps(body).lower()


def test_list_jobs_returns_seeded(client):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    data = response.json()
    assert len(data["jobs"]) == 3


def test_valid_job_submission(client):
    before_jobs = client.get("/api/jobs").json()
    before_count = len(before_jobs["jobs"])
    before_revision = before_jobs["revision"]
    payload = {
        "template": "inference_batch",
        "earliest_start": "2026-03-15T18:00:00+00:00",
        "deadline": "2026-03-16T12:00:00+00:00",
    }
    create = client.post("/api/jobs", json=payload)
    assert create.status_code == 201
    job = create.json()
    assert job["status"] == "queued"
    assert job["gpu_count"] == 4
    assert job["assigned_site_id"] is None
    assert job["scheduled_start"] is None

    listing = client.get("/api/jobs").json()
    assert len(listing["jobs"]) == before_count + 1
    assert any(j["id"] == job["id"] for j in listing["jobs"])
    assert listing["revision"] > before_revision


def test_invalid_job_window(client):
    before_count = len(client.get("/api/jobs").json()["jobs"])
    payload = {
        "template": "training_small",
        "earliest_start": "2026-03-18T00:00:00+00:00",
        "deadline": "2026-03-17T00:00:00+00:00",
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 422
    assert len(client.get("/api/jobs").json()["jobs"]) == before_count


def test_unknown_template_rejected(client):
    response = client.post(
        "/api/jobs",
        json={
            "template": "not_a_real_template",
            "earliest_start": "2026-03-15T18:00:00+00:00",
            "deadline": "2026-03-16T12:00:00+00:00",
        },
    )
    assert response.status_code == 422


def test_reset_restores_jobs_and_increments_revision(client):
    client.post(
        "/api/jobs",
        json={
            "template": "inference_batch",
            "earliest_start": "2026-03-15T18:00:00+00:00",
            "deadline": "2026-03-16T12:00:00+00:00",
        },
    )
    assert len(client.get("/api/jobs").json()["jobs"]) == 4
    revision_before_reset = client.get("/api/demo").json()["revision"]
    reset = client.post("/api/demo/reset")
    assert reset.status_code == 200
    body = reset.json()
    assert body["jobs_restored"] == 3
    assert body["revision"] == revision_before_reset + 1
    assert len(client.get("/api/jobs").json()["jobs"]) == 3


def test_emergency_restriction_validation():
    with pytest.raises(ValidationError):
        EmergencyRestriction(
            site_id="site-alpha",
            water_cap_liters_per_hour=100,
            start="2026-03-15T20:00:00+00:00",
            end="2026-03-15T18:00:00+00:00",
        )


@pytest.mark.parametrize(
    "path",
    [
        "/api/reallocation/apply",
        "/api/demo/advance",
    ],
)
def test_unimplemented_routes_return_501(client, path):
    if path.endswith("apply"):
        response = client.post(path, json={"preview_id": "x", "input_revision": 1})
    else:
        response = client.post(path)
    assert response.status_code == 501
    assert "detail" in response.json()


def test_reallocation_preview_no_longer_returns_501(client):
    """Preview is implemented; may be feasible or infeasible depending on site fixture."""
    response = client.post("/api/reallocation/preview", json={"mode": "routine"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"feasible", "infeasible"}
    assert body["simulated"] is True
    assert "preview_id" in body
    assert "input_revision" in body


def test_sample_reallocation_endpoint_for_operator_ui(client):
    response = client.get("/api/reallocation/samples/preview_move")
    assert response.status_code == 200
    body = response.json()
    assert "proposed_job_changes" in body
    assert "metrics_before" in body
    assert "metrics_after" in body
    assert "cost_breakdown" in body


def test_operator_page_has_reallocation_preview_panel(client):
    response = client.get("/operator")
    assert response.status_code == 200
    assert "preview-panel" in response.text
    assert "Preview schedule" in response.text
    assert "Plan preview" in response.text


def test_openapi_docs_available(client):
    response = client.get("/docs")
    assert response.status_code == 200


def test_env_example_tracked():
    assert (ROOT_DIR / ".env.example").is_file()