from fastapi import APIRouter

from app.repositories import telemetry_repository
from app.schemas.sites import SiteListResponse

router = APIRouter(tags=["sites"])


@router.get("/api/sites", response_model=SiteListResponse)
def list_sites():
    return telemetry_repository.sites_list_response()
