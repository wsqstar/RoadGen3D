"""Design evaluation and improvement API routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from roadgen3d.json_safe import make_json_safe
from web.api.route_utils import infer_layout_preset_id
from web.api.schemas import EvaluateCompareRequestModel, EvaluateRequestModel, ImproveRequestModel

router = APIRouter(prefix="/api/design", tags=["evaluation"])


@router.post("/evaluate")
def evaluate_scene(request_body: EvaluateRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        result = service.evaluate_scene(
            layout_path=request_body.layout_path,
            image_path=request_body.image_path,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(result)


@router.post("/evaluate/unified")
def evaluate_scene_unified(request_body: EvaluateRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        result = service.evaluate_scene_unified(
            layout_path=request_body.layout_path,
            image_path=request_body.image_path,
            rendered_views=[
                view.model_dump(exclude_none=True) if hasattr(view, "model_dump") else view.dict(exclude_none=True)
                for view in request_body.rendered_views
            ],
            evaluation_profile=request_body.evaluation_profile,
            evaluation_mode=request_body.evaluation_mode,
            evaluation_config=(
                request_body.evaluation_config.model_dump(exclude_none=True)
                if request_body.evaluation_config is not None
                and hasattr(request_body.evaluation_config, "model_dump")
                else (
                    request_body.evaluation_config.dict(exclude_none=True)
                    if request_body.evaluation_config is not None
                    else None
                )
            ),
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request_body.persist_to_benchmark:
        request.app.state.benchmark_store.upsert_evaluation(
            layout_path=request_body.layout_path,
            evaluation=result,
            preset_id=request_body.preset_id or infer_layout_preset_id(request_body.layout_path),
        )
    return make_json_safe(result)


@router.post("/evaluate/compare")
def evaluate_scene_compare(request_body: EvaluateCompareRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        result = service.evaluate_scene_with_history(
            layout_path=request_body.current_layout_path,
            image_path=request_body.current_image_path,
            previous_layout_path=request_body.previous_layout_path,
            previous_image_path=request_body.previous_image_path,
            previous_score=request_body.previous_score or 0.0,
            previous_evaluation=request_body.previous_evaluation or "",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(result)


@router.post("/improve")
def propose_improvement(request_body: ImproveRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        result = service.propose_improvement(
            current_evaluation=request_body.current_evaluation,
            comparison=request_body.comparison or {},
            current_patch=request_body.current_patch or {},
            weakness_queries=request_body.weakness_queries or [],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(result)
