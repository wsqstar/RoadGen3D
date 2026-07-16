"""Shared OSM acquisition and SceneSource normalization.

The expert workbench and teaching platform intentionally share this service so
the same AOI cannot drift into two different ReferenceAnnotation payloads.
Persistence remains the responsibility of the calling application boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TypedDict

from roadgen3d.osm_ingest import fetch_osm_data
from roadgen3d.scene_source_geojson import normalize_teaching_geojson, raw_osm_to_geojson


class OsmSceneSourceBundle(TypedDict):
    bbox: tuple[float, float, float, float]
    raw_osm: dict[str, Any]
    geojson: dict[str, Any]
    normalized: dict[str, Any]
    provenance: dict[str, Any]


def validate_osm_aoi_bbox(value: Sequence[float]) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("aoi_bbox must be [west,south,east,north].")
    west, south, east, north = (float(item) for item in value)
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError("aoi_bbox is reversed or outside WGS84 bounds.")
    return west, south, east, north


def fetch_normalized_osm_scene_source(
    *,
    aoi_bbox: Sequence[float],
    source_id: str,
    cache_dir: str | Path,
    force_refetch: bool = False,
) -> OsmSceneSourceBundle:
    """Fetch one OSM AOI and normalize it into the canonical scene-source graph."""

    bbox = validate_osm_aoi_bbox(aoi_bbox)
    raw = fetch_osm_data(bbox, Path(cache_dir), force_refetch=bool(force_refetch))
    geojson = raw_osm_to_geojson(raw)
    normalized = normalize_teaching_geojson(geojson, source_id=str(source_id), bbox=bbox)
    provenance = {
        "provider": "OpenStreetMap/Overpass",
        "attribution": "© OpenStreetMap contributors",
        "bbox": list(bbox),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw_element_count": len(raw.get("elements", [])),
    }
    return {
        "bbox": bbox,
        "raw_osm": dict(raw),
        "geojson": dict(geojson),
        "normalized": dict(normalized),
        "provenance": provenance,
    }


def osm_scene_source_response(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Build the stateless workbench response from a shared OSM bundle."""

    normalized = dict(bundle["normalized"])
    payload = dict(normalized["graph_payload"])
    payload["source"] = {
        **dict(payload.get("source") or {}),
        "kind": "geojson",
        "producer": "osm",
    }
    payload["geojson"] = normalized["geojson"]
    payload["warnings"] = normalized["warnings"]
    payload["quality_report"] = normalized["quality_report"]
    payload["role_counts"] = normalized["role_counts"]
    provenance = dict(bundle["provenance"])
    payload["osm"] = {
        "bbox_wgs84": list(bundle["bbox"]),
        "raw_element_count": int(provenance.get("raw_element_count") or 0),
        "attribution": str(provenance.get("attribution") or "© OpenStreetMap contributors"),
    }
    return payload


__all__ = [
    "OsmSceneSourceBundle",
    "fetch_normalized_osm_scene_source",
    "osm_scene_source_response",
    "validate_osm_aoi_bbox",
]
