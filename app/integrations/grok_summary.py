"""xAI Grok executive summaries for completed jobs (isolated from Gemini/Tiger)."""

from __future__ import annotations

from typing import Any, Optional

from app.config import get_settings


class GrokSummaryError(RuntimeError):
    """Raised when Grok is misconfigured or the completion call fails."""


def _client():
    settings = get_settings()
    key = settings.grok_api_key.strip()
    if not key:
        raise GrokSummaryError("GROK_API_KEY is not configured")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise GrokSummaryError(
            "openai package is required for Grok summaries (pip install openai)"
        ) from exc
    return OpenAI(api_key=key, base_url=settings.grok_base_url.strip() or "https://api.x.ai/v1")


def _format_xai_error(exc: Exception) -> str:
    """Pull the human-readable message out of an OpenAI/xAI APIError."""
    # openai.APIStatusError exposes .status_code and .response / .body
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        msg = body.get("error") or body.get("message") or body
        code = body.get("code")
        if code == "permission-denied" or (
            isinstance(msg, str) and "credits" in msg.lower()
        ):
            return (
                "xAI rejected the key: your team has no API credits yet. "
                "Add credits at https://console.x.ai/ then retry. "
                f"Details: {msg}"
            )
        return f"xAI error ({status}): {msg}"
    text = str(exc)
    if "credits" in text.lower() or "permission-denied" in text.lower():
        return (
            "xAI rejected the key: your team has no API credits yet. "
            "Add credits at https://console.x.ai/ then retry. "
            f"Details: {text}"
        )
    return f"Grok summary request failed: {text}"


def generate_job_summary(job_data: dict[str, Any]) -> str:
    """Ask Grok for a 2-sentence data-center ops executive summary."""
    settings = get_settings()
    model = settings.grok_model.strip() or "grok-3-mini"

    site = job_data.get("site_name") or job_data.get("site_id") or "unknown site"
    duration = job_data.get("duration_hours")
    water = job_data.get("water_liters")
    cost = job_data.get("cost_usd")
    template = job_data.get("template") or "workload"
    job_id = job_data.get("job_id") or "job"

    prompt = (
        "You are writing a brief executive summary for a data-center operations dashboard. "
        "Reply with exactly two professional sentences and nothing else "
        "(no bullets, no markdown, no greeting).\n\n"
        f"Job ID: {job_id}\n"
        f"Workload template: {template}\n"
        f"Assigned site: {site}\n"
        f"Duration (hours): {duration}\n"
        f"Estimated water use (liters): {water}\n"
        f"Estimated electricity cost (USD): {cost}\n"
        f"GPU count: {job_data.get('gpu_count')}\n"
        f"Completed at (UTC): {job_data.get('completed_at')}\n"
        f"Scheduled start (UTC): {job_data.get('scheduled_start')}\n"
    )

    try:
        client = _client()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You summarize completed AI batch jobs for AquaLibri operators. "
                        "Be concise, factual, and ops-focused."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=160,
        )
    except GrokSummaryError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GrokSummaryError(_format_xai_error(exc)) from exc

    try:
        text = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError) as exc:
        raise GrokSummaryError("Grok returned an empty or unexpected response") from exc
    if not text:
        raise GrokSummaryError("Grok returned an empty summary")
    return text


def estimate_job_ops_metrics(job: Any, sites: list[Any]) -> dict[str, Optional[float]]:
    """Best-effort water/cost estimates from site hourly profiles (no Tiger calls)."""
    site_id = getattr(job, "assigned_site_id", None)
    site = next((s for s in sites if getattr(s, "site_id", None) == site_id), None)
    duration = float(getattr(job, "duration_hours", 0) or 0)
    gpus = int(getattr(job, "gpu_count", 0) or 0)
    if site is None or duration <= 0:
        return {"water_liters": None, "cost_usd": None, "site_name": site_id}

    start = getattr(job, "scheduled_start", None)
    profiles = list(getattr(site, "hourly_profiles", []) or [])
    if start is not None and profiles:
        # Average metrics over overlapping hours when available.
        from datetime import timedelta, timezone

        def _aware(dt):
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        start_u = _aware(start)
        end_u = start_u + timedelta(hours=duration)
        window = [
            p
            for p in profiles
            if _aware(p.hour_start) < end_u and _aware(p.hour_start) + timedelta(hours=1) > start_u
        ]
        if window:
            profiles = window

    n = max(len(profiles), 1)
    avg_fixed = sum(float(p.fixed_water_liters_per_hour) for p in profiles) / n
    avg_incr = sum(float(p.incremental_water_liters_per_gpu_hour) for p in profiles) / n
    avg_energy = sum(float(p.energy_kwh_per_gpu_hour) for p in profiles) / n
    avg_price = sum(float(p.electricity_usd_per_kwh) for p in profiles) / n

    water = (avg_fixed + avg_incr * gpus) * duration
    cost = avg_energy * gpus * duration * avg_price
    return {
        "water_liters": round(water, 2),
        "cost_usd": round(cost, 4),
        "site_name": getattr(site, "name", None) or site_id,
    }


def build_job_summary_payload(job: Any, sites: list[Any]) -> dict[str, Any]:
    metrics = estimate_job_ops_metrics(job, sites)
    return {
        "job_id": job.id,
        "template": job.template.value if hasattr(job.template, "value") else str(job.template),
        "site_id": job.assigned_site_id,
        "site_name": metrics.get("site_name"),
        "duration_hours": job.duration_hours,
        "gpu_count": job.gpu_count,
        "water_liters": metrics.get("water_liters"),
        "cost_usd": metrics.get("cost_usd"),
        "scheduled_start": job.scheduled_start.isoformat() if job.scheduled_start else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
