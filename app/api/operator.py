from fastapi import APIRouter

from app.schemas.demo import DemoResetResponse, DemoState
from app.state import get_session_state

router = APIRouter(tags=["operator", "demo"])


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