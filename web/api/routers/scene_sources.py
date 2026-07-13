"""Student-facing scene-source normalization and extraction routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from fastapi import APIRouter, HTTPException

from roadgen3d.json_safe import make_json_safe
from roadgen3d.llm import LLMClient, LLMConfigurationError, LLMResponseError
from roadgen3d.osm_ingest import fetch_osm_data, parse_osm_features
from roadgen3d.scene_sources import normalize_scene_source, validate_image_data_url
from web.api.schemas import (
    OsmBuildingSourceRequestModel,
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


@router.post("/osm-buildings")
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
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("aoi_bbox must be [west,south,east,north].")
    west, south, east, north = (float(item) for item in value)
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError("aoi_bbox is reversed or outside WGS84 bounds.")
    return west, south, east, north
