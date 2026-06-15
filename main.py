"""
main.py — ABIP entry point
Creates the FastAPI app, wires all routers, initialises the database.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.session import Base, engine
from app.routers import auth, files, investigations, pages, reports
from app.routers.settings_router import router as settings_router
from app.routers.insights_api import router as insights_router          # ← ADDED

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("server starting", app=settings.APP_NAME, version=settings.APP_VERSION)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("database tables ready")
    yield
    await engine.dispose()
    logger.info("server stopped")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,           prefix="/auth",          tags=["auth"])
app.include_router(files.router,          prefix="/files",         tags=["files"])
app.include_router(investigations.router, prefix="/investigations", tags=["investigations"])
app.include_router(reports.router,        prefix="/reports",       tags=["reports"])
app.include_router(settings_router,       prefix="/settings",      tags=["settings"])
app.include_router(insights_router,       prefix="/api/insights",      tags=["insights"])  # ← ADDED
app.include_router(pages.router,          tags=["pages"])


@app.get("/health")
async def health():
    return {
        "status":  "ok",
        "app":     settings.APP_NAME,
        "version": settings.APP_VERSION,
    }