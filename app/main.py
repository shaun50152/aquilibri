from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import health, jobs, notifications, operator, scheduling, sites
from app.config import FRONTEND_DIR
from app.state import init_session_state

PAGES_DIR = FRONTEND_DIR / "pages"
STATIC_DIR = FRONTEND_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_session_state()
    yield


app = FastAPI(
    title="AquaLibri",
    description="Water-aware AI batch job scheduling decision support (HackUTA scaffold).",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(notifications.router)
app.include_router(operator.router)
app.include_router(sites.router)
app.include_router(scheduling.router)

@app.middleware("http")
async def revalidate_static(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _page(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing page: {path}")
    return FileResponse(path, media_type="text/html")


@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/portal", status_code=302)


@app.get("/portal", include_in_schema=False)
def portal_page():
    return _page(PAGES_DIR / "portal.html")


@app.get("/operator", include_in_schema=False)
def operator_page():
    return _page(PAGES_DIR / "operator.html")