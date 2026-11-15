from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.schemas.scheduling import (
    ReallocationApplyRequest,
    ReallocationApplyResponse,
    ReallocationPreviewRequest,
    ReallocationPreviewResponse,
)
from app.services import demo_worker, scheduling_service
from app.services.scheduling_service import PreviewRequestError
from app.state import get_session_state

router = APIRouter(tags=["scheduling", "demo"])


def _not_implemented(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={"detail": detail},
    )


@router.post(
    "/api/reallocation/preview",
    response_model=ReallocationPreviewResponse,
    responses={
        422: {"description": "Invalid structured preview request"},
        501: {"description": "Not implemented"},
    },
)
def reallocation_preview(request: ReallocationPreviewRequest):
    state = get_session_state()
    try:
        return scheduling_service.preview_reallocation(request, state)
    except PreviewRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except NotImplementedError as exc:
        return _not_implemented(str(exc))


@router.post(
    "/api/reallocation/apply",
    response_model=ReallocationApplyResponse,
    responses={501: {"description": "Not implemented"}},
)
def reallocation_apply(request: ReallocationApplyRequest):
    try:
        scheduling_service.apply_reallocation(request)
    except NotImplementedError as exc:
        return _not_implemented(str(exc))
    raise HTTPException(status_code=500, detail="Unexpected apply success in scaffold.")


@router.post("/api/demo/advance")
def demo_advance():
    try:
        state = get_session_state()
        demo_worker.advance_demo_clock(state.current_time, 1.0)
    except NotImplementedError as exc:
        return _not_implemented(str(exc))
    raise HTTPException(status_code=500, detail="Unexpected advance success in scaffold.")
