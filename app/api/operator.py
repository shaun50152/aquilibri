import json
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.config import FIXTURES_DIR
from app.integrations.gemini import GeminiSchedulingAssistant, GeminiSchedulingError
from app.schemas.demo import (
    DemoResetResponse,
    DemoState,
    OperatorChatRequest,
    OperatorChatResponse,
)
from app.schemas.scheduling import ReallocationPreviewResponse
from app.state import get_session_state

router = APIRouter(tags=["operator", "demo"])

_SAMPLE_PREVIEW_FILES = {
    "preview_move": "preview_move.json",
    "preview_unfulfilled": "preview_unfulfilled.json",
    "preview_infeasible": "preview_infeasible.json",
}


@router.get("/api/demo", response_model=DemoState)
def get_demo_state():
    state = get_session_state()
    return DemoState(
        session_id=state.session_id,
        current_time=state.current_time,
        revision=state.revision,
    )


@router.post("/api/demo/reset", response_model=DemoResetResponse)
def reset_demo():
    state = get_session_state()
    count = state.reload_from_fixtures()
    return DemoResetResponse(
        session_id=state.session_id,
        revision=state.revision,
        jobs_restored=count,
        current_time=state.current_time,
    )


@router.get(
    "/api/reallocation/samples/{sample_name}",
    response_model=ReallocationPreviewResponse,
    tags=["scheduling"],
)
def get_sample_reallocation_preview(sample_name: str) -> Any:
    """Serve fixture sample previews for operator UI prototyping (Hours 0–3)."""
    filename = _SAMPLE_PREVIEW_FILES.get(sample_name)
    if filename is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Unknown sample {sample_name!r}. "
                f"Known: {sorted(_SAMPLE_PREVIEW_FILES)}"
            ),
        )
    path = FIXTURES_DIR / filename
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = {k: v for k, v in raw.items() if not k.startswith("_")}
    return ReallocationPreviewResponse.model_validate(data)


@router.post(
    "/api/operator/chat",
    response_model=OperatorChatResponse,
    responses={
        422: {"description": "Invalid prompt or tool argument validation failure"},
        503: {"description": "Gemini unavailable / misconfigured"},
    },
)
def operator_chat(request: OperatorChatRequest) -> OperatorChatResponse:
    """NL operator assistant → Gemini tool call → same preview engine as manual UI."""
    state = get_session_state()
    try:
        assistant = GeminiSchedulingAssistant()
        result = assistant.run_operator_chat(
            request.prompt,
            context={
                "session_id": state.session_id,
                "revision": state.revision,
                "current_time": state.current_time.isoformat(),
                "site_ids": [s.site_id for s in state.sites],
            },
        )
    except GeminiSchedulingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    preview = None
    if result.get("preview"):
        preview = ReallocationPreviewResponse.model_validate(result["preview"])

    return OperatorChatResponse(
        assistant_message=result.get("assistant_message") or "",
        tool_calls=list(result.get("tool_calls") or []),
        preview=preview,
    )
