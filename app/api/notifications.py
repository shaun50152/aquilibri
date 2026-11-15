from fastapi import APIRouter

from app.repositories import notification_repository
from app.schemas.jobs import DEMO_OWNER_ID
from app.schemas.notifications import NotificationListResponse
from app.state import get_session_state

router = APIRouter(tags=["notifications"])


@router.get("/api/notifications", response_model=NotificationListResponse)
def list_notifications():
    state = get_session_state()
    items = notification_repository.list_for_session_owner(DEMO_OWNER_ID)
    return NotificationListResponse(
        session_id=state.session_id,
        owner_id=DEMO_OWNER_ID,
        revision=state.revision,
        notifications=items,
    )