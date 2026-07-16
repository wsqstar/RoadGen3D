"""Student-facing scene-source normalization and extraction routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from fastapi import APIRouter, HTTPException, Request

from roadgen3d.json_safe import make_json_safe
from roadgen3d.llm import LLMClient, LLMConfigurationError, LLMResponseError
from roadgen3d.osm_ingest import fetch_osm_data, parse_osm_features
from roadgen3d.services.osm_scene_source import (
    fetch_normalized_osm_scene_source,
    osm_scene_source_response,
    validate_osm_aoi_bbox,
)
from roadgen3d.scene_sources import normalize_scene_source, validate_image_data_url
from web.api.schemas import (
    OsmBuildingSourceRequestModel,
    OsmSceneSourceRequestModel,
    OsmRoadStudySelectionRequestModel,
    SceneSourceExtractRequestModel,
    SceneSourceNormalizeRequestModel,
)

ROOT = Path(__file__).resolve().parents[3]
router = APIRouter(prefix="/api/scene-sources", tags=["scene-sources"])


@router.post("/normalize")
def normalize_source(request: SceneSourceNormalizeRequestModel) -> Dict[str, Any]:
    try:
        normalized = normalize_scene_source(request.source)
        payload = normalized.to_graph_payload(request.compose_config)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(payload)


@router.post("/extract")
def extract_source_from_image(request: SceneSourceExtractRequestModel) -> Dict[str, Any]:
    try:
        validate_image_data_url(request.image_data_url)
        client = LLMClient()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "vision_not_configured",
            "message": str(exc),
        }) from exc

    prompt = str(request.prompt or "").strip()
    system_text = (
        "Extract a reviewable 2D road and context map from the supplied aerial/map image. "
        "Return JSON only with a top-level `geojson` RFC-7946-style FeatureCollection. "
        "Coordinates MUST be image pixels with origin at top-left, x right, y down. "
        "Use LineString features with properties.role='centerline' for road axes; include "
        "stable feature ids and properties road_width_m, highway_type, "
        "forward_drive_lane_count and reverse_drive_lane_count when inferable. Use Point "
        "features with role='junction' only for visible junction anchors. Use Polygon "
        "features with role='building_footprint' for visible building roofs/footprints. "
        "Do not invent roads or buildings hidden outside the image. Geometry must be finite, "
        "nondegenerate, and contained in the stated image dimensions."
    )
    if prompt:
        system_text += f" Student instruction: {prompt}"
    messages = [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Image width={request.image.get('width_px')} px; "
                        f"height={request.image.get('height_px')} px. Extract GeoJSON now."
                    ),
                },
                {"type": "image_url", "image_url": {"url": request.image_data_url}},
            ],
        },
    ]
    try:
        raw = client.chat_json(messages, temperature=0.0, capability="vision")
        geojson = raw.get("geojson") if isinstance(raw.get("geojson"), Mapping) else raw
        normalized = normalize_scene_source({
            "kind": "geojson",
            "source_id": request.source_id,
            "producer": "ai",
            "coordinate_space": "image_px",
            "geojson": geojson,
            "image": request.image,
        })
        payload = normalized.to_graph_payload(request.compose_config)
        payload["llm"] = client.settings.public_identity("vision")
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail={
            "code": "invalid_vision_response",
            "message": str(exc),
        }) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_extracted_geojson",
            "message": str(exc),
        }) from exc
    finally:
        client.close()
    return make_json_safe(payload)


@router.post("/osm")
def fetch_osm_scene_source(request: OsmSceneSourceRequestModel) -> Dict[str, Any]:
    """Fetch and normalize one complete OSM AOI into ReferenceAnnotation."""

    try:
        bundle = fetch_normalized_osm_scene_source(
            aoi_bbox=request.aoi_bbox,
            source_id=request.source_id,
            cache_dir=ROOT / "artifacts" / "osm_cache",
            force_refetch=bool(request.force_refetch),
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(osm_scene_source_response(bundle))


@router.post("/osm/jobs", status_code=202)
def create_osm_source_job(request_body: OsmSceneSourceRequestModel, request: Request) -> Dict[str, Any]:
    try:
        validate_osm_aoi_bbox(request_body.aoi_bbox)
        return make_json_safe(request.app.state.osm_source_job_service.create_job(request_body.model_dump()))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/osm/jobs/{job_id}")
def get_osm_source_job(job_id: str, request: Request) -> Dict[str, Any]:
    payload = request.app.state.osm_source_job_service.get_job(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"OSM acquisition job not found: {job_id}")
    return make_json_safe(payload)


@router.post("/osm/jobs/{job_id}/cancel")
def cancel_osm_source_job(job_id: str, request: Request) -> Dict[str, Any]:
    payload = request.app.state.osm_source_job_service.cancel_job(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"OSM acquisition job not found: {job_id}")
    return make_json_safe(payload)


@router.post("/osm/jobs/{job_id}/retry", status_code=202)
def retry_osm_source_job(job_id: str, request: Request) -> Dict[str, Any]:
    payload = request.app.state.osm_source_job_service.retry_job(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"OSM acquisition job not found: {job_id}")
    return make_json_safe(payload)


@router.post("/osm/previews/{preview_id}/selection")
def select_osm_road_study_area(
    preview_id: str,
    request_body: OsmRoadStudySelectionRequestModel,
    request: Request,
) -> Dict[str, Any]:
    try:
        payload = request.app.state.osm_source_job_service.select(
            preview_id,
            seed_logical_road_id=request_body.seed_logical_road_id,
            hop_count=request_body.hop_count,
            context_buffer_m=request_body.context_buffer_m,
            source_id=request_body.source_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(payload)


@router.post("/osm-buildings", deprecated=True)
def fetch_osm_building_source(request: OsmBuildingSourceRequestModel) -> Dict[str, Any]:
    try:
        bbox = _validated_bbox(request.aoi_bbox)
        raw = fetch_osm_data(bbox, ROOT / "artifacts" / "osm_cache")
        features = parse_osm_features(raw)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    geojson_features = []
    for building in features.buildings:
        ring = [[float(lon), float(lat)] for lon, lat in building.coords]
        if ring and ring[0] != ring[-1]:
            ring.append(list(ring[0]))
        if len(ring) < 4:
            continue
        geojson_features.append({
            "type": "Feature",
            "id": f"osm-building-{building.osm_id}",
            "properties": {
                "role": "building_footprint",
                "osm_id": int(building.osm_id),
                "tags": dict(building.tags),
                "editable": False,
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    warnings = [
        "OSM building relations, multipolygons, and interior holes are not represented by the current importer.",
        "OSM height and building:levels tags are retained as provenance but are not yet used as authoritative heights.",
    ]
    return make_json_safe({
        "source": {
            "schema_version": "roadgen3d_scene_source_v1",
            "source_id": str(request.source_id),
            "kind": "geojson",
            "producer": "osm",
            "coordinate_space": "EPSG:4326",
        },
        "geojson": {"type": "FeatureCollection", "features": geojson_features},
        "warnings": warnings,
        "summary": {
            "bbox_wgs84": list(bbox),
            "building_way_count": len(geojson_features),
            "unsupported_relation_geometry": True,
        },
        "aligned_buildings": [],
        "source_alignment": {
            "schema_version": "roadgen3d.source_alignment.v1",
            "status": "n/a",
            "reason": "An explicit source-image bbox/alignment is required before OSM buildings can enter a traced scene.",
        },
    })


def _validated_bbox(value: Any) -> tuple[float, float, float, float]:
    return validate_osm_aoi_bbox(value)
