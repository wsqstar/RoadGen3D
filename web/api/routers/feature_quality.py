"""Single-feature visual experiment workbench API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from roadgen3d.json_safe import make_json_safe
from web.api.schemas import FeatureQualityRunCreateRequestModel


router = APIRouter(prefix="/api/design/feature-quality-runs", tags=["feature-quality"])


@router.post("")
def create_feature_quality_run(body: FeatureQualityRunCreateRequestModel, request: Request) -> Dict[str, Any]:
    try:
        return make_json_safe(request.app.state.feature_quality_run_service.submit_run(
            target_id=body.target_id,
            brief=body.brief,
            variant_count=body.variant_count,
            base_patch=body.base_patch,
            graph_template_id=body.graph_template_id,
            scene_context=body.scene_context,
            generation_options=body.generation_options,
            visual_review=body.visual_review,
        ))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{run_id}")
def get_feature_quality_run(run_id: str, request: Request) -> Dict[str, Any]:
    result = request.app.state.feature_quality_run_service.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Feature quality run not found: {run_id}")
    return make_json_safe(result)


@router.post("/{run_id}/accept/{variant_id}")
def accept_feature_quality_variant(run_id: str, variant_id: str, request: Request) -> Dict[str, Any]:
    try:
        return make_json_safe(request.app.state.feature_quality_run_service.accept_variant(run_id, variant_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Feature run or variant not found: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}/artifacts/{variant_id}/{view_id}")
def feature_quality_artifact(run_id: str, variant_id: str, view_id: str, request: Request) -> FileResponse:
    path = request.app.state.feature_quality_run_service.artifact_path(run_id, variant_id, view_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Feature view artifact not found")
    return FileResponse(path, media_type="image/png")
