"""Catalog, reference, and template-preview API routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from roadgen3d.graph_templates import (
    get_graph_template,
    list_graph_templates,
    load_graph_template_annotation_payload,
)
from roadgen3d.json_safe import make_json_safe
from roadgen3d.llm import public_llm_capabilities_from_env
from roadgen3d.metaurban_procedural import (
    get_metaurban_reference_plan,
    list_metaurban_reference_plans,
)
from roadgen3d.presets import SCENE_PRESETS
from roadgen3d.reference_annotation import (
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
)
from roadgen3d.reference_regions import derive_regions_from_annotation
from roadgen3d.services.design_types import sanitize_compose_config_patch
from roadgen3d.services.scene_context_service import build_osm_semantic_preview
from roadgen3d.template_patch import TemplatePatchError, apply_template_patch
from web.api.schemas import (
    OsmSemanticPreviewRequestModel,
    ReferenceAnnotationConvertRequestModel,
    ReferenceAnnotationDeriveRegionsRequestModel,
    TemplatePatchPreviewRequestModel,
)

router = APIRouter(tags=["catalog"])


@router.get("/")
def root() -> Dict[str, Any]:
    return make_json_safe({
        "ok": True,
        "service": "roadgen3d-design-assistant-api",
        "message": "RoadGen3D API is running. Open the Viewer at http://127.0.0.1:4173/.",
        "health_url": "/api/health",
        "docs_url": "/docs",
        "viewer_url": "http://127.0.0.1:4173/",
    })


@router.get("/api/health")
def health(request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    return make_json_safe({
        "ok": True,
        "default_pdf_path": str(service.default_pdf_path),
        "default_artifact_dir": str(service.default_artifact_dir),
        "capabilities": {
            "llm": public_llm_capabilities_from_env(),
        },
    })


@router.get("/api/geo/china-cities")
def list_china_cities(request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    return make_json_safe({"items": service.list_china_cities()})


@router.get("/api/reference-plans")
def list_reference_plans() -> Dict[str, Any]:
    items = []
    for plan in list_metaurban_reference_plans():
        payload = plan.to_dict()
        payload["image_url"] = f"/api/reference-plans/{plan.plan_id}/image"
        items.append(payload)
    return make_json_safe({"items": items})


@router.get("/api/reference-plans/{plan_id}/image")
def get_reference_plan_image(plan_id: str) -> FileResponse:
    try:
        plan = get_metaurban_reference_plan(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not plan.image_path.exists():
        raise HTTPException(status_code=404, detail=f"Reference plan image not found: {plan.image_path}")
    return FileResponse(plan.image_path)


@router.get("/api/graph-templates")
def list_graph_template_items() -> Dict[str, Any]:
    items = []
    for template in list_graph_templates():
        payload = template.to_dict()
        payload["image_url"] = f"/api/graph-templates/{template.template_id}/image"
        items.append(payload)
    return make_json_safe({"items": items})


@router.get("/api/presets")
def list_presets() -> Dict[str, Any]:
    return make_json_safe({"items": SCENE_PRESETS})


@router.get("/api/graph-templates/{template_id}/image")
def get_graph_template_image(template_id: str) -> FileResponse:
    try:
        template = get_graph_template(template_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not template.image_path.exists():
        raise HTTPException(status_code=404, detail=f"Graph template image not found: {template.image_path}")
    return FileResponse(template.image_path)


@router.post("/api/graph-templates/{template_id}/template-patch/preview")
def preview_graph_template_patch(template_id: str, request: TemplatePatchPreviewRequestModel) -> Dict[str, Any]:
    try:
        base_annotation = load_graph_template_annotation_payload(template_id)
        application = apply_template_patch(base_annotation, request.patch)
        payload: Dict[str, Any] = {
            "annotation": application.annotation,
            "summary": application.summary,
        }
        if request.include_graph_payload:
            compose_config = build_reference_annotation_compose_config(request.compose_config)
            payload["graph_payload"] = build_reference_annotation_graph_payload(
                application.annotation,
                config=compose_config,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TemplatePatchError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(payload)


@router.post("/api/reference-annotations/convert")
def convert_reference_annotation(request: ReferenceAnnotationConvertRequestModel) -> Dict[str, Any]:
    try:
        compose_config = build_reference_annotation_compose_config(request.compose_config)
        payload = build_reference_annotation_graph_payload(
            request.annotation,
            config=compose_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(payload)


@router.post("/api/reference-annotations/derive-regions")
def derive_reference_annotation_regions(request: ReferenceAnnotationDeriveRegionsRequestModel) -> Dict[str, Any]:
    try:
        payload = derive_regions_from_annotation(
            request.annotation,
            options=request.options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(payload)


@router.post("/api/osm/semantic-preview")
def osm_semantic_preview(request: OsmSemanticPreviewRequestModel) -> Dict[str, Any]:
    try:
        payload = build_osm_semantic_preview(
            aoi_bbox=tuple(float(item) for item in request.aoi_bbox),
            osm_cache_dir=Path(request.osm_cache_dir) if request.osm_cache_dir else None,
            compose_config_patch=sanitize_compose_config_patch(request.compose_config),
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(payload)

