"""FastAPI application for RoadGen3D scene generation API.

This is the main entry point for the web viewer backend.
It provides RESTful APIs for direct scene generation without LLM dependencies.

The LLM/RAG workflow is available as an optional upstream service
(see src/roadgen3d/llm/ for details).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path so we can import roadgen3d modules
ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from roadgen3d.services.generation_api import router as generation_router
from roadgen3d.api.junction_templates import router as junction_templates_router

# Create FastAPI app
app = FastAPI(
    title="RoadGen3D API",
    description="Street scene generation API for web viewer",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS for web viewer development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4173",  # Vite dev server
        "http://127.0.0.1:4173",
        "http://localhost:5173",  # Alternative Vite port
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(generation_router, prefix="/api", tags=["generation"])
app.include_router(junction_templates_router, tags=["junction-templates"])

# Optional: Include LLM workflow routes if needed
# Uncomment to enable LLM-based design assistant
# try:
#     from roadgen3d.llm.api import router as llm_router
#     app.include_router(llm_router, prefix="/api/llm", tags=["llm"])
# except ImportError:
#     pass  # LLM module not available or disabled


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint - API information."""
    return {
        "service": "RoadGen3D API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "generation": {
                "create_metaurban_design": "POST /api/designs/metaurban",
                "create_template_design": "POST /api/designs/template",
                "create_osm_design": "POST /api/designs/osm",
                "get_design_status": "GET /api/designs/{job_id}/status",
                "get_scene_result": "GET /api/scenes/{job_id}",
            },
        },
    }


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


# For uvicorn/gunicorn deployment
# Usage: uvicorn ui.api:app --host 0.0.0.0 --port 8000
__all__ = ["app"]
