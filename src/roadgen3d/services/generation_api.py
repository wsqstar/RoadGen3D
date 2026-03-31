"""FastAPI routes for direct scene generation.

This module provides RESTful APIs for the web viewer to trigger
scene generation without going through the LLM workflow.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from .generation_core import (
    GenerationOptions,
    MetaurbanDesignParams,
    OsmDesignParams,
    SceneGenerationResult,
    TemplateDesignParams,
    generate_metaurban_scene,
    generate_template_scene,
)

router = APIRouter()

# In-memory job store (replace with persistent storage in production)
_job_store: Dict[str, SceneGenerationResult] = {}


class JobStatusResponse(BaseModel):
    """Response for job status queries."""

    job_id: str
    status: str  # "queued", "processing", "completed", "failed"
    created_at: str = ""
    finished_at: str = ""
    result: Optional[Dict[str, Any]] = None
    error: str = ""


class MetaurbanDesignRequest(BaseModel):
    """Request body for MetaUrban design generation."""

    reference_plan_id: str = Field(
        default="hkust_gz_gate",
        description="Reference plan ID (e.g., 'hkust_gz_gate')",
    )
    lane_count: int = Field(default=2, ge=1, le=6, description="Number of lanes")
    lane_width_m: float = Field(default=3.5, ge=2.8, le=4.0, description="Lane width in meters")
    sidewalk_width_m: float = Field(default=2.5, ge=1.5, le=5.0, description="Sidewalk width in meters")
    road_width_m: Optional[float] = Field(
        default=None,
        ge=5.0,
        le=30.0,
        description="Total road width (auto-calculated if None)",
    )
    segment_length_m: float = Field(default=12.0, ge=4.0, le=50.0)
    start_heading_deg: float = Field(default=0.0, ge=0.0, le=360.0)
    block_sequence: Optional[str] = Field(
        default=None,
        pattern=r"^[SCXTO]*$",
        description="Block sequence (S=Straight, C=Curve, X=Intersection, T=T-junction, O=Roundabout)",
    )
    block_count: int = Field(default=6, ge=1, le=20)
    seed: int = Field(default=42)


class TemplateDesignRequest(BaseModel):
    """Request body for graph template design generation."""

    template_id: str = Field(default="hkust_gz_gate", description="Graph template ID")
    lane_count: int = Field(default=2, ge=1, le=6)
    lane_width_m: float = Field(default=3.5, ge=2.8, le=4.0)
    sidewalk_width_m: float = Field(default=2.5, ge=1.5, le=5.0)
    road_width_m: float = Field(default=7.0, ge=5.0, le=30.0)
    length_m: float = Field(default=80.0, ge=20.0, le=500.0)
    seed: int = Field(default=42)


class OsmDesignRequest(BaseModel):
    """Request body for OSM-based design generation."""

    city_name_en: str = Field(default="generic_city")
    lane_count: int = Field(default=2, ge=1, le=6)
    lane_width_m: float = Field(default=3.5, ge=2.8, le=4.0)
    sidewalk_width_m: float = Field(default=2.5, ge=1.5, le=5.0)
    road_width_m: float = Field(default=7.0, ge=5.0, le=30.0)
    length_m: float = Field(default=80.0, ge=20.0, le=500.0)
    aoi_bbox: Optional[List[float]] = Field(
        default=None,
        min_length=4,
        max_length=4,
        description="[min_lon, min_lat, max_lon, max_lat]",
    )
    road_selection: str = Field(default="auto")
    seed: int = Field(default=42)


def _run_generation_task(
    job_id: str,
    design_type: str,
    params: MetaurbanDesignParams | TemplateDesignParams | OsmDesignParams,
    options: GenerationOptions,
) -> None:
    """Run scene generation in background."""
    from .generation_core import generate_metaurban_scene, generate_template_scene

    try:
        if design_type == "metaurban":
            result = generate_metaurban_scene(params, options)
        elif design_type == "template":
            result = generate_template_scene(params, options)
        else:
            raise ValueError(f"Unknown design type: {design_type}")

        _job_store[job_id] = result

    except Exception as exc:
        _job_store[job_id] = SceneGenerationResult(
            job_id=job_id,
            status="failed",
            error=str(exc),
            created_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


@router.post("/designs/metaurban", response_model=Dict[str, str])
async def create_metaurban_design(request: MetaurbanDesignRequest) -> Dict[str, str]:
    """Create a new MetaUrban-style street design.

    This endpoint triggers asynchronous scene generation. Use the returned
    job_id to poll for status via GET /api/designs/{job_id}/status.

    Example request:
    ```json
    {
      "reference_plan_id": "hkust_gz_gate",
      "lane_count": 2,
      "lane_width_m": 3.5,
      "sidewalk_width_m": 2.5,
      "block_sequence": "SXSOXS",
      "seed": 42
    }
    ```
    """
    job_id = f"mu_{uuid.uuid4().hex[:8]}"

    # Convert request to params
    params = MetaurbanDesignParams(
        reference_plan_id=request.reference_plan_id,
        lane_count=request.lane_count,
        lane_width_m=request.lane_width_m,
        sidewalk_width_m=request.sidewalk_width_m,
        road_width_m=request.road_width_m,
        segment_length_m=request.segment_length_m,
        start_heading_deg=request.start_heading_deg,
        block_sequence=request.block_sequence,
        block_count=request.block_count,
        seed=request.seed,
    )

    # Initialize job status
    _job_store[job_id] = SceneGenerationResult(
        job_id=job_id,
        status="queued",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    # TODO: Run in background with proper task queue
    # For now, run synchronously (blocking) - will be improved later
    options = GenerationOptions()
    result = generate_metaurban_scene(params, options)
    _job_store[job_id] = result

    return {"job_id": job_id, "status": result.status}


@router.post("/designs/template", response_model=Dict[str, str])
async def create_template_design(request: TemplateDesignRequest) -> Dict[str, str]:
    """Create a new graph template-based street design.

    Similar to MetaUrban but uses predefined graph templates.
    """
    job_id = f"gt_{uuid.uuid4().hex[:8]}"

    params = TemplateDesignParams(
        template_id=request.template_id,
        lane_count=request.lane_count,
        lane_width_m=request.lane_width_m,
        sidewalk_width_m=request.sidewalk_width_m,
        road_width_m=request.road_width_m,
        length_m=request.length_m,
        seed=request.seed,
    )

    _job_store[job_id] = SceneGenerationResult(
        job_id=job_id,
        status="queued",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    options = GenerationOptions()
    result = generate_template_scene(params, options)
    _job_store[job_id] = result

    return {"job_id": job_id, "status": result.status}


@router.post("/designs/osm", response_model=Dict[str, str])
async def create_osm_design(request: OsmDesignRequest) -> Dict[str, str]:
    """Create a new OSM-based street design.

    Note: OSM-based generation is not yet fully implemented.
    """
    job_id = f"osm_{uuid.uuid4().hex[:8]}"

    params = OsmDesignParams(
        city_name_en=request.city_name_en,
        lane_count=request.lane_count,
        lane_width_m=request.lane_width_m,
        sidewalk_width_m=request.sidewalk_width_m,
        road_width_m=request.road_width_m,
        length_m=request.length_m,
        aoi_bbox=tuple(request.aoi_bbox) if request.aoi_bbox else None,
        road_selection=request.road_selection,
        seed=request.seed,
    )

    _job_store[job_id] = SceneGenerationResult(
        job_id=job_id,
        status="queued",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    # Placeholder - not yet implemented
    _job_store[job_id] = SceneGenerationResult(
        job_id=job_id,
        status="failed",
        error="OSM-based generation is not yet implemented",
        created_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
    )

    return {"job_id": job_id, "status": "failed"}


@router.get("/designs/{job_id}/status", response_model=JobStatusResponse)
async def get_design_status(job_id: str) -> JobStatusResponse:
    """Get the status of a design generation job."""
    if job_id not in _job_store:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    result = _job_store[job_id]
    return JobStatusResponse(
        job_id=result.job_id,
        status=result.status,
        created_at=result.created_at,
        finished_at=result.finished_at,
        result=result.to_dict() if result.status == "completed" else None,
        error=result.error,
    )


@router.get("/scenes/{job_id}", response_model=Dict[str, Any])
async def get_scene_result(job_id: str) -> Dict[str, Any]:
    """Get the complete result of a completed scene generation job.

    Returns the full SceneGenerationResult including file paths and viewer URL.
    """
    if job_id not in _job_store:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    result = _job_store[job_id]
    if result.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed yet. Status: {result.status}",
        )

    return result.to_dict()


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "service": "roadgen3d-generation-api"}


__all__ = ["router"]
