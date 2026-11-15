"""Gemini API for interpreting operator requests and invoking scheduling tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import get_settings
from app.schemas.scheduling import (
    EmergencyRestriction,
    ReallocationMode,
    ReallocationPreviewRequest,
    ReallocationPreviewResponse,
)
from app.state import get_session_state


class GeminiSchedulingError(RuntimeError):
    """Raised for Gemini configuration or tool-calling failures."""


def _parse_iso_utc(value: str, label: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError(f"{label} is required and must be an ISO-8601 timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware (include offset or Z)")
    return dt.astimezone(timezone.utc)


def _validate_water_cap_lph(water_cap: Any) -> int:
    """Strict L/h integer validation for emergency caps."""
    if isinstance(water_cap, bool) or water_cap is None:
        raise ValueError("water_cap must be an integer liters/hour (L/h)")
    if isinstance(water_cap, float) and not water_cap.is_integer():
        raise ValueError("water_cap must be an integer liters/hour (L/h), not a fraction")
    try:
        value = int(water_cap)
    except (TypeError, ValueError) as exc:
        raise ValueError("water_cap must be an integer liters/hour (L/h)") from exc
    if value < 0:
        raise ValueError("water_cap must be a non-negative integer L/h")
    return value


def _resolve_site_id(site_name: str, sites: list) -> str:
    needle = (site_name or "").strip().lower()
    if not needle:
        raise ValueError("site_name is required for emergency previews")
    for site in sites:
        if site.site_id.lower() == needle or site.name.lower() == needle:
            return site.site_id
    for site in sites:
        if site.site_id.lower().endswith(needle) or needle in site.name.lower():
            return site.site_id
    known = ", ".join(f"{s.site_id} ({s.name})" for s in sites)
    raise ValueError(f"Unknown site_name={site_name!r}. Known sites: {known}")


def ai_preview_reallocation(
    site_name: Optional[str] = None,
    water_cap: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    max_additional_cost_usd: Optional[float] = None,
) -> dict[str, Any]:
    """Run a routine or emergency reallocation preview via the placement engine.

    - Routine: omit water_cap / start_time / end_time (optional max_additional_cost_usd).
    - Emergency: provide site_name, integer water_cap (L/h), and ISO start_time/end_time
      with end_time strictly after start_time.
    """
    from app.repositories import telemetry_repository
    from app.services import scheduling_service

    telemetry_repository.list_site_profiles()
    state = get_session_state()

    emergency_fields = [site_name, water_cap, start_time, end_time]
    provided = [v for v in emergency_fields if v is not None and v != ""]
    if provided and len(provided) < 4:
        raise ValueError(
            "Emergency previews require site_name, water_cap (integer L/h), "
            "start_time, and end_time together."
        )

    if provided:
        site_id = _resolve_site_id(str(site_name), state.sites)
        cap = _validate_water_cap_lph(water_cap)
        start = _parse_iso_utc(str(start_time), "start_time")
        end = _parse_iso_utc(str(end_time), "end_time")
        if end <= start:
            raise ValueError("end_time must be after start_time")
        request = ReallocationPreviewRequest(
            mode=ReallocationMode.emergency,
            emergency_restriction=EmergencyRestriction(
                site_id=site_id,
                water_cap_liters_per_hour=float(cap),
                start=start,
                end=end,
            ),
        )
    else:
        extra = max_additional_cost_usd
        if extra is not None:
            try:
                extra = float(extra)
            except (TypeError, ValueError) as exc:
                raise ValueError("max_additional_cost_usd must be a number") from exc
            if extra < 0:
                raise ValueError("max_additional_cost_usd must be >= 0")
        request = ReallocationPreviewRequest(
            mode=ReallocationMode.routine,
            max_additional_cost_usd=extra,
        )

    preview: ReallocationPreviewResponse = scheduling_service.preview_reallocation(
        request, state
    )
    return preview.model_dump(mode="json")


class GeminiSchedulingAssistant:
    def __init__(self, api_key: str = "", model: str = "") -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.gemini_api_key or "").strip()
        self.model = (model or settings.gemini_model or "gemini-1.5-flash").strip()
        if not self.api_key:
            raise GeminiSchedulingError(
                "GEMINI_API_KEY is not configured. Set it in .env for operator chat."
            )

    def interpret_reallocation_request(self, instruction: str, context: dict) -> dict:
        return self.run_operator_chat(instruction, context=context)

    def call_scheduling_tool(self, tool_name: str, arguments: dict) -> dict:
        if tool_name != "ai_preview_reallocation":
            raise GeminiSchedulingError(f"Unknown scheduling tool: {tool_name}")
        return ai_preview_reallocation(**(arguments or {}))

    def run_operator_chat(
        self,
        prompt: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        text = (prompt or "").strip()
        if not text:
            raise GeminiSchedulingError("prompt must be a non-empty string")

        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise GeminiSchedulingError(
                "google-generativeai is required (pip install google-generativeai)"
            ) from exc

        genai.configure(api_key=self.api_key)

        captured: dict[str, Any] = {"preview": None, "tool_calls": []}

        def ai_preview_reallocation_tool(
            site_name: Optional[str] = None,
            water_cap: Optional[int] = None,
            start_time: Optional[str] = None,
            end_time: Optional[str] = None,
            max_additional_cost_usd: Optional[float] = None,
        ) -> dict[str, Any]:
            """Compute AquaLibri routine or emergency reallocation preview.

            Routine: omit emergency fields; optional max_additional_cost_usd.
            Emergency: site_name, integer water_cap (L/h), ISO start_time/end_time.
            """
            args = {
                "site_name": site_name,
                "water_cap": water_cap,
                "start_time": start_time,
                "end_time": end_time,
                "max_additional_cost_usd": max_additional_cost_usd,
            }
            captured["tool_calls"].append(
                {"name": "ai_preview_reallocation", "arguments": args}
            )
            result = ai_preview_reallocation(
                site_name=site_name,
                water_cap=water_cap,
                start_time=start_time,
                end_time=end_time,
                max_additional_cost_usd=max_additional_cost_usd,
            )
            captured["preview"] = result
            return result

        model = genai.GenerativeModel(
            model_name=self.model,
            tools=[ai_preview_reallocation_tool],
            system_instruction=(
                "You are the AquaLibri operator assistant. "
                "Always use ai_preview_reallocation for scheduling previews. "
                "For emergency water limits, pass integer water_cap in L/h and "
                "timezone-aware ISO start_time/end_time with end after start. "
                "For routine optimization, call the tool without emergency fields. "
                "Do not invent metrics; summarize the tool result."
            ),
        )
        chat = model.start_chat(enable_automatic_function_calling=True)
        payload = {"operator_prompt": text, "context": context or {}}
        response = chat.send_message(json.dumps(payload))
        assistant_message = getattr(response, "text", None) or ""

        return {
            "assistant_message": assistant_message.strip(),
            "tool_calls": captured["tool_calls"],
            "preview": captured["preview"],
        }
