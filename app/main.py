"""
app/main.py

Application entry-point.

This is where FastAPI is configured and all routes are registered.
Think of it as the "wiring" layer — it connects all the independent
modules into a runnable application.
"""

import logging
import sys

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes.hospitals import router as hospitals_router
from app.core.config import get_settings
from app.services.hospital_api_client import get_http_client

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
settings = get_settings()


# ── Application lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown lifecycle hook.

    On startup  → warm up the shared HTTP client (creates the connection pool)
    On shutdown → gracefully close all open TCP connections
    """
    logger.info("🚀  %s v%s starting up...", settings.APP_NAME, settings.APP_VERSION)
    logger.info("   External API: %s", settings.HOSPITAL_API_BASE_URL)
    logger.info("   Max concurrent requests: %d", settings.MAX_CONCURRENT_REQUESTS)
    logger.info("   Max CSV rows: %d", settings.MAX_CSV_ROWS)

    # Pre-warm the HTTP connection pool
    get_http_client()

    yield  # ← app runs here

    logger.info("🛑  Shutting down — closing HTTP client...")
    client = get_http_client()
    await client.aclose()
    logger.info("   HTTP client closed. Goodbye!")


# ── FastAPI application ────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=settings.APP_DESCRIPTION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────────
# Allows any origin so the hosted API can be tested from browser-based tools.
# In production you would restrict this to known domains.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(hospitals_router, prefix="/api/v1")


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"], summary="Health check")
async def health_check():
    """
    Simple liveness probe used by Render / Docker health checks.
    Returns 200 OK if the app is running.
    """
    return JSONResponse(
        content={
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
        }
    )


DASHBOARD_FILE = Path(__file__).parent / "templates" / "dashboard.html"


@app.get("/dashboard", response_class=FileResponse, tags=["Dashboard"], summary="Interactive Dashboard")
@app.get("/ui", include_in_schema=False)
async def dashboard():
    """Serves the interactive web dashboard for testing CSV uploads."""
    if not DASHBOARD_FILE.exists():
        return JSONResponse(status_code=404, content={"detail": "Dashboard template not found."})
    return FileResponse(DASHBOARD_FILE, media_type="text/html")


@app.get("/", tags=["Health"], summary="Root — redirect hint")
async def root():
    """Friendly landing page pointing to the docs and dashboard."""
    return JSONResponse(
        content={
            "message": f"Welcome to {settings.APP_NAME}!",
            "dashboard": "/dashboard",
            "docs": "/docs",
            "health": "/health",
            "bulk_create": "POST /api/v1/hospitals/bulk",
            "validate_csv": "POST /api/v1/hospitals/bulk/validate",
        }
    )

