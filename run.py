"""
run.py

Simple development entry-point.
Run with:  python run.py

For production, use:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import uvicorn
from app.core.config import get_settings

settings = get_settings()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
        log_level="info",
    )

