import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException, status

from app.config import FIXTURES_DIR
from app.schemas.demo import DemoAdvanceRequest, DemoAdvanceResponse, DemoResetResponse, DemoState
from app.schemas.scheduling import ReallocationPreviewResponse
from app.services import demo_service
from app.session_lock import SessionConflictError
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
    count, revision = demo_service.reset_demo_session()
    return DemoResetResponse(
        session_id=state.session_id,
        revision=revision,
        jobs_restored=count,
        current_time=get_session_state().current_time,
    )


@router.post(
    "/api/demo/advance",
    response_model=DemoAdvanceResponse,
    responses={409: {"description": "Stale session revision"}, 422: {"description": "Invalid hours"}},
)
def demo_advance(
    body: DemoAdvanceRequest | None = Body(default=None),
):
    req = body or DemoAdvanceRequest()
    try:
        return demo_service.advance_session(
            hours=req.hours,
            expected_revision=req.expected_revision,
        )
    except SessionConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


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