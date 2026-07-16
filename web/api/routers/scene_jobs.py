"""Scene job and recent-scene API routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from roadgen3d.json_safe import make_json_safe
from roadgen3d.services.asset_manifest_registry import (
    AssetManifestConflictError,
    freeze_candidate_manifests,
)
from roadgen3d.template_patch import TemplatePatchError
from web.api.route_utils import prepare_scene_generation_request
from web.api.schemas import SceneJobCreateRequestModel

router = APIRouter(tags=["scene-jobs"])


@router.post("/api/scene/jobs")
def create_scene_job(request_body: SceneJobCreateRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        draft, scene_context, patch_overrides, generation_options = prepare_scene_generation_request(
            request_body,
            scenario_design_service=request.app.state.scenario_design_service,
        )
        candidate_manifests = generation_options.get("candidate_asset_manifests")
        if candidate_manifests is not None:
            if not isinstance(candidate_manifests, list) or not all(isinstance(item, dict) for item in candidate_manifests):
                raise ValueError("candidate_asset_manifests must be an ordered list of registered manifest references.")
            generation_options.update(freeze_candidate_manifests(candidate_manifests))
        result = service.create_scene_job(
            draft=draft,
            scene_context=scene_context,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
        )
    except AssetManifestConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, ValueError, TemplatePatchError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(result.to_dict())


@router.get("/api/scene/jobs")
def list_scene_jobs(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    service = request.app.state.design_service
    jobs = service.list_scene_jobs(limit=int(limit))
    return make_json_safe({"items": [item.to_dict() for item in jobs]})


@router.get("/api/scene/jobs/{job_id}")
def get_scene_job(job_id: str, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    result = service.get_scene_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scene job not found: {job_id}")
    return make_json_safe(result.to_dict())


@router.get("/api/scenes/recent")
def list_recent_scenes(request: Request, limit: int = Query(default=12, ge=1, le=100)) -> Dict[str, Any]:
    service = request.app.state.design_service
    items = service.list_recent_scenes(limit=int(limit))
    return make_json_safe({"items": [item.to_dict() for item in items]})
