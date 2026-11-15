"""Apply handoff from stored reallocation previews (fixture mode, no Atlas)."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.config import SITE_PROFILES_EXTENDED_SYNTHETIC, clear_settings_cache
from app.schemas.jobs import Job
from app.services.preview_store import clear_preview_store, get_stored_preview
from app.state import get_session_state


@pytest.fixture(autouse=True)
def _clear_previews():
    clear_preview_store()
    yield
    clear_preview_store()


@pytest.fixture
def extended_session(client):
    clear_settings_cache()
    import os

    os.environ["SITE_PROFILES_FIXTURE"] = SITE_PROFILES_EXTENDED_SYNTHETIC
    clear_settings_cache()
    from app.state import build_session_state, replace_session_state

    state = build_session_state()
    previous = get_session_state()
    replace_session_state(state)
    client.post("/api/demo/reset")
    yield
    replace_session_state(previous)
    os.environ.pop("SITE_PROFILES_FIXTURE", None)
    clear_settings_cache()


def _jobs_snapshot(client) -> list[dict]:
    return client.get("/api/jobs").json()["jobs"]


def test_apply_rejects_stale_revision_without_mutating_jobs(extended_session, client):
    preview = client.post("/api/reallocation/preview", json={"mode": "routine"}).json()
    assert preview["status"] == "feasible"
    jobs_before = _jobs_snapshot(client)

    get_session_state().revision += 1

    response = client.post(
        "/api/reallocation/apply",
        json={
            "preview_id": preview["preview_id"],
            "input_revision": preview["input_revision"],
        },
    )
    assert response.status_code == 409
    assert jobs_before == _jobs_snapshot(client)
    assert get_stored_preview(preview["preview_id"]) is not None


def test_apply_writes_assignments_and_bumps_revision(extended_session, client):
    demo = client.get("/api/demo").json()
    preview = client.post("/api/reallocation/preview", json={"mode": "routine"}).json()
    assert preview["input_revision"] == demo["revision"]

    response = client.post(
        "/api/reallocation/apply",
        json={
            "preview_id": preview["preview_id"],
            "input_revision": preview["input_revision"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is True
    assert body["revision"] == demo["revision"] + 1

    jobs = [Job.model_validate(j) for j in _jobs_snapshot(client)]
    by_id = {j.id: j for j in jobs}
    for assignment in preview["proposed_assignments"]:
        job = by_id.get(assignment["job_id"])
        if job is None or job.status.value in ("running", "completed", "failed"):
            continue
        if assignment["unfulfilled"]:
            assert job.status.value == "unfulfilled"
            assert job.assigned_site_id is None
        else:
            assert job.assigned_site_id == assignment["assigned_site_id"]
            expected_start = assignment["scheduled_start"]
            if isinstance(expected_start, str):
                expected_start = datetime.fromisoformat(expected_start.replace("Z", "+00:00"))
            assert job.scheduled_start == expected_start

    assert get_stored_preview(preview["preview_id"]) is None