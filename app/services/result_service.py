"""Deterministic simulated CSV results for completed jobs."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from app.schemas.jobs import Job, JobTemplate


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def finish_time(job: Job) -> datetime | None:
    if job.scheduled_start is None:
        return None
    return _ensure_utc(job.scheduled_start) + timedelta(hours=float(job.duration_hours))


def build_result_snapshot(job: Job, completed_at: datetime) -> list[dict[str, str]]:
    """Row-oriented snapshot stored on the job for restart-safe downloads."""
    site = job.assigned_site_id or ""
    start = job.scheduled_start.isoformat() if job.scheduled_start else ""
    done = _ensure_utc(completed_at).isoformat()
    header = {
        "section": "metadata",
        "job_id": job.id,
        "template": job.template.value,
        "simulated": "true",
        "site_id": site,
        "scheduled_start": start,
        "completed_at": done,
        "sample_output": "",
    }
    if job.template == JobTemplate.inference_batch:
        rows = [
            header,
            {"section": "predictions", "sample_output": "row_id,score,confidence", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
            {"section": "predictions", "sample_output": "1,0.91,0.97", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
            {"section": "predictions", "sample_output": "2,0.44,0.88", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
        ]
    elif job.template == JobTemplate.training_small:
        rows = [
            header,
            {"section": "metrics", "sample_output": "epoch,loss,val_loss", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
            {"section": "metrics", "sample_output": "10,0.42,0.51", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
            {"section": "metrics", "sample_output": "20,0.31,0.39", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
        ]
    else:
        rows = [
            header,
            {"section": "checkpoints", "sample_output": "step,train_loss,val_loss,gpu_hours", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
            {"section": "checkpoints", "sample_output": "1000,1.8,1.9,64", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
            {"section": "checkpoints", "sample_output": "2000,1.2,1.3,128", "job_id": "", "template": "", "simulated": "", "site_id": "", "scheduled_start": "", "completed_at": ""},
        ]
    return rows


def render_result_csv(job: Job) -> str:
    if not job.result_snapshot:
        raise ValueError("Job has no stored result snapshot")
    buf = io.StringIO()
    fieldnames = [
        "section",
        "job_id",
        "template",
        "simulated",
        "site_id",
        "scheduled_start",
        "completed_at",
        "sample_output",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in job.result_snapshot:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return buf.getvalue()


def attach_completed_result(job: Job, completed_at: datetime) -> Job:
    snapshot = build_result_snapshot(job, completed_at)
    return job.model_copy(
        update={
            "result_snapshot": snapshot,
            "result_url": f"/api/jobs/{job.id}/result",
        }
    )