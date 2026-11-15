"""Demo clock advance, execution, results, notifications, and reset."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from app.config import DEMO_CLOCK_ANCHOR_ISO, SITE_PROFILES_EXTENDED_SYNTHETIC, clear_settings_cache
from app.schemas.jobs import Job, JobStatus, JobTemplate
from app.services import demo_worker
from app.services.preview_store import clear_preview_store
from app.state import get_session_state


UTC = timezone.utc
ANCHOR = datetime.fromisoformat(DEMO_CLOCK_ANCHOR_ISO)


def _job(**kwargs) -> Job:
    base = dict(
        id="job-test-0001",
        session_id="demo-session-hackuta-2026",
        owner_id="demo-user-001",
        template=JobTemplate.inference_batch,
        earliest_start=ANCHOR,
        deadline=ANCHOR + timedelta(hours=12),
        gpu_count=4,
        duration_hours=2.0,
        eligible_site_ids=["site-alpha", "site-beta"],
        data_ready_at_by_site={"site-alpha": ANCHOR, "site-beta": ANCHOR},
        critical=False,
        miss_penalty_usd=50.0,
        status=JobStatus.scheduled,
        assigned_site_id="site-alpha",
        scheduled_start=ANCHOR + timedelta(hours=1),
        completed_at=None,
        result_url=None,
        execution_epoch=1,
    )
    base.update(kwargs)
    return Job(**base)


def test_invalid_hours_rejected(client):
    before = client.get("/api/demo").json()
    for bad in (0, -1):
        r = client.post("/api/demo/advance", json={"hours": bad})
        assert r.status_code == 422
    after = client.get("/api/demo").json()
    assert after["revision"] == before["revision"]
    assert after["current_time"] == before["current_time"]


def test_job_starts_at_scheduled_boundary(client):
    client.post("/api/demo/reset")
    r = client.post("/api/demo/advance", json={"hours": 1})
    assert r.status_code == 200
    body = r.json()
    assert "job-hackuta-0001" in body["jobs_started"]
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "job-hackuta-0001")
    assert job["status"] == "running"


def test_completion_at_scheduled_finish(client):
    client.post("/api/demo/reset")
    client.post("/api/demo/advance", json={"hours": 1})
    r = client.post("/api/demo/advance", json={"hours": 2})
    assert r.status_code == 200
    assert "job-hackuta-0001" in r.json()["jobs_completed"]
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "job-hackuta-0001")
    assert job["status"] == "completed"
    assert datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00")) == datetime(
        2026, 3, 15, 21, 0, tzinfo=UTC
    )
    assert job["result_url"] == "/api/jobs/job-hackuta-0001/result"


def test_single_large_advance_start_and_complete(client):
    client.post("/api/demo/reset")
    r = client.post("/api/demo/advance", json={"hours": 3})
    assert r.status_code == 200
    body = r.json()
    assert "job-hackuta-0001" in body["jobs_started"]
    assert "job-hackuta-0001" in body["jobs_completed"]
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "job-hackuta-0001")
    assert job["status"] == "completed"


def test_running_job_finishes_after_remaining_time(client):
    client.post("/api/demo/reset")
    r = client.post("/api/demo/advance", json={"hours": 6})
    assert r.status_code == 200
    assert "job-hackuta-0002" in r.json()["jobs_completed"]


def test_queued_job_not_executed(client):
    client.post("/api/demo/reset")
    client.post("/api/demo/advance", json={"hours": 2})
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "job-hackuta-0003")
    assert job["status"] == "queued"


def test_result_csv_download(client):
    client.post("/api/demo/reset")
    client.post("/api/demo/advance", json={"hours": 3})
    res = client.get("/api/jobs/job-hackuta-0001/result")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert rows[0]["job_id"] == "job-hackuta-0001"
    assert rows[0]["simulated"] == "true"


def test_result_not_available_before_complete(client):
    client.post("/api/demo/reset")
    res = client.get("/api/jobs/job-hackuta-0001/result")
    assert res.status_code == 404


def test_notification_idempotent(client):
    client.post("/api/demo/reset")
    client.post("/api/demo/advance", json={"hours": 3})
    n1 = client.get("/api/notifications").json()["notifications"]
    n2 = client.get("/api/notifications").json()["notifications"]
    assert len(n1) == len(n2) >= 1
    assert n1[0]["id"] == n2[0]["id"]


def test_second_advance_does_not_duplicate_completion_notification(client):
    client.post("/api/demo/reset")
    client.post("/api/demo/advance", json={"hours": 3})
    before = client.get("/api/notifications").json()["notifications"]
    client.post("/api/demo/advance", json={"hours": 1})
    after = client.get("/api/notifications").json()["notifications"]
    assert len(after) == len(before)


def test_stale_revision_conflict(client):
    client.post("/api/demo/reset")
    rev = client.get("/api/demo").json()["revision"]
    client.post("/api/demo/advance", json={"hours": 1})
    r = client.post("/api/demo/advance", json={"hours": 1, "expected_revision": rev})
    assert r.status_code == 409


def test_reset_clears_notifications_and_old_result(client):
    client.post("/api/demo/reset")
    client.post("/api/demo/advance", json={"hours": 3})
    assert client.get("/api/notifications").json()["notifications"]
    client.post("/api/demo/reset")
    assert client.get("/api/notifications").json()["notifications"] == []
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "job-hackuta-0001")
    assert job["status"] != "completed"
    assert client.get("/api/jobs/job-hackuta-0001/result").status_code == 404


def test_process_jobs_unit_fractional_and_midnight():
    start = datetime(2026, 3, 15, 23, 0, tzinfo=UTC)
    job = _job(
        scheduled_start=start,
        duration_hours=2.5,
        status=JobStatus.scheduled,
    )
    jobs, started, completed, _ = demo_worker.process_jobs_between(
        [job], datetime(2026, 3, 15, 22, 0, tzinfo=UTC), datetime(2026, 3, 16, 2, 0, tzinfo=UTC)
    )
    assert started == ["job-test-0001"]
    assert completed == ["job-test-0001"]
    done = jobs[0]
    assert done.status == JobStatus.completed
    assert done.completed_at == start + timedelta(hours=2.5)


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


def test_preview_stale_after_advance(client, extended_session):
    clear_preview_store()
    preview = client.post("/api/reallocation/preview", json={"mode": "routine"}).json()
    client.post("/api/demo/advance", json={"hours": 1})
    r = client.post(
        "/api/reallocation/apply",
        json={"preview_id": preview["preview_id"], "input_revision": preview["input_revision"]},
    )
    assert r.status_code == 409