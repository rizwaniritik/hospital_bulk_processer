"""
app/core/config.py

Application configuration using Singleton pattern.
All environment-based settings live here — no magic strings scattered across the codebase.

Singleton Pattern: Only one Config instance is ever created, ensuring
consistent settings throughout the application lifecycle.
"""

import os
from functools import lru_cache
from pathlib import Path

# Load local .env file automatically if present
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
except ImportError:
    pass


class Settings:
    """
    Central configuration class.

    Uses environment variables with sensible defaults so the app works
    out-of-the-box locally and can be customized on Render/Docker.
    """

    # ── App Identity ──────────────────────────────────────────────────────────
    APP_NAME: str = "Hospital Bulk Processor"
    APP_VERSION: str = "1.0.0"
    APP_DESCRIPTION: str = (
        "A bulk processing system that integrates with the Hospital Directory API "
        "to handle CSV uploads and process hospital records at scale."
    )

    # ── External API ──────────────────────────────────────────────────────────
    HOSPITAL_API_BASE_URL: str = os.getenv(
        "HOSPITAL_API_BASE_URL", "https://hospital-directory.onrender.com"
    )
    API_TIMEOUT_SECONDS: int = int(os.getenv("API_TIMEOUT_SECONDS", "30"))

    # ── Business Rules ────────────────────────────────────────────────────────
    MAX_CSV_ROWS: int = int(os.getenv("MAX_CSV_ROWS", "20"))
    REQUIRED_CSV_COLUMNS: tuple = ("name", "address")     # phone is optional
    OPTIONAL_CSV_COLUMNS: tuple = ("phone",)

    # ── Concurrency ───────────────────────────────────────────────────────────
    # How many hospital POSTs to fire in parallel (keeps rate limits sane)
    MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))

    # ── Server ────────────────────────────────────────────────────────────────
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns the single Settings instance (Singleton via lru_cache).

    Usage:
        from app.core.config import get_settings
        settings = get_settings()
    """
    return Settings()

