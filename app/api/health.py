from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health_check():
    settings = get_settings()
    return {
        "status": "ok",
        "data_mode": settings.data_mode,
        "demo_session_id": settings.demo_session_id,
    }