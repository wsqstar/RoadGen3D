"""Road-centred study-area selection for shared OSM scene sources."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from roadgen3d.osm_ingest import (
    OVERPASS_QUERY_VERSION,
    OsmFeatures,
    OsmRoad,
    auto_detect_utm_epsg,
    fetch_osm_data,
    parse_osm_features,
)
from roadgen3d.scene_source_geojson import normalize_teaching_geojson, osm_features_to_geojson
from roadgen3d.services.osm_scene_source import validate_osm_aoi_bbox


ProgressCallback = Callable[[Mapping[str, Any]], None]
DEFAULT_CONTEXT_BUFFER_M = 100.0
MIN_CONTEXT_BUFFER_M = 25.0
MAX_CONTEXT_BUFFER_M = 300.0


@dataclass(frozen=True)
class LogicalRoad:
    logical_road_id: str
    label: str
    ref: str
    name: str
    highway_type: str
    way_ids: tuple[int, ...]
    node_ids: tuple[int, ...]
    coordinates: tuple[tuple[tuple[float, float], ...], ...]
    length_m: float
    touches_retrieval_boundary: bool


@dataclass(frozen=True)
class OsmRoadPreviewBundle:
    preview_id: str
    source_id: str
    bbox: tuple[float, float, float, float]
    raw_osm: dict[str, Any]
    features: OsmFeatures
    logical_roads: tuple[LogicalRoad, ...]
    adjacency: dict[str, tuple[str, ...]]
    preview: dict[str, Any]


def _emit(
    callback: ProgressCallback | None,
    stage: str,
    progress: int,
    message: str,
    **detail: Any,
) -> None:
    if callback is not None:
        callback({"stage": stage, "progress": progress, "message": message, "detail": detail})


def _normalized_road_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _road_identity(road: OsmRoad) -> tuple[str, str]:
    tags = dict(road.tags or {})
    ref = _normalized_road_text(tags.get("ref"))
    name = _normalized_road_text(tags.get("name"))
    if ref:
        return "ref", ref
    if name:
        return "name", name
    return "way", str(int(road.osm_id))


def _haversine_m(left: tuple[float, float], right: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, left)
    lon2, lat2 = map(math.radians, right)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_008.8 * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1.0 - value)))


def _road_length_m(road: OsmRoad) -> float:
    return sum(_haversine_m(left, right) for left, right in zip(road.coords, road.coords[1:]))


def _near_bbox_boundary(
    coordinates: Sequence[tuple[float, float]],
    bbox: tuple[float, float, float, float],
    tolerance_m: float = 8.0,
) -> bool:
    west, south, east, north = bbox
    mid_lat = (south + north) * 0.5
    lon_tol = tolerance_m / max(1.0, 111_320.0 * math.cos(math.radians(mid_lat)))
    lat_tol = tolerance_m / 110_540.0
    return any(
        lon <= west + lon_tol or lon >= east - lon_tol or lat <= south + lat_tol or lat >= north - lat_tol
        for lon, lat in coordinates
    )


def build_logical_roads(
    roads: Sequence[OsmRoad],
    bbox: Sequence[float],
) -> tuple[tuple[LogicalRoad, ...], dict[str, tuple[str, ...]]]:
    """Merge connected ways that represent the same named/ref road and build adjacency."""

    validated_bbox = validate_osm_aoi_bbox(bbox)
    grouped: dict[tuple[str, str, str], list[OsmRoad]] = defaultdict(list)
    for road in roads:
        identity_kind, identity_value = _road_identity(road)
        grouped[(identity_kind, identity_value, str(road.highway_type))].append(road)

    logical: list[LogicalRoad] = []
    for group_key, members in sorted(grouped.items(), key=lambda item: item[0]):
        node_to_members: dict[int, set[int]] = defaultdict(set)
        for index, road in enumerate(members):
            for node_id in road.node_ids:
                node_to_members[int(node_id)].add(index)
        unvisited = set(range(len(members)))
        while unvisited:
            seed = min(unvisited)
            queue = deque([seed])
            component: list[OsmRoad] = []
            unvisited.remove(seed)
            while queue:
                index = queue.popleft()
                road = members[index]
                component.append(road)
                neighbours = set()
                for node_id in road.node_ids:
                    neighbours.update(node_to_members.get(int(node_id), set()))
                for neighbour in sorted(neighbours & unvisited):
                    unvisited.remove(neighbour)
                    queue.append(neighbour)
            way_ids = tuple(sorted(int(item.osm_id) for item in component))
            digest = hashlib.sha256(
                f"{group_key[0]}:{group_key[1]}:{group_key[2]}:{','.join(map(str, way_ids))}".encode()
            ).hexdigest()[:12]
            first_tags = dict(component[0].tags or {})
            name = str(first_tags.get("name") or "").strip()
            ref = str(first_tags.get("ref") or "").strip()
            label = name or ref or f"OSM way {way_ids[0]}"
            all_coords = [point for road in component for point in road.coords]
            logical.append(LogicalRoad(
                logical_road_id=f"logical-road-{digest}",
                label=label,
                ref=ref,
                name=name,
                highway_type=str(component[0].highway_type),
                way_ids=way_ids,
                node_ids=tuple(sorted({int(node_id) for road in component for node_id in road.node_ids})),
                coordinates=tuple(tuple(tuple(point) for point in road.coords) for road in component),
                length_m=sum(_road_length_m(road) for road in component),
                touches_retrieval_boundary=_near_bbox_boundary(all_coords, validated_bbox),
            ))

    node_to_logical: dict[int, set[str]] = defaultdict(set)
    for road in logical:
        for node_id in road.node_ids:
            node_to_logical[node_id].add(road.logical_road_id)
    adjacency: dict[str, set[str]] = {road.logical_road_id: set() for road in logical}
    for ids in node_to_logical.values():
        for road_id in ids:
            adjacency[road_id].update(ids - {road_id})
    return tuple(logical), {key: tuple(sorted(value)) for key, value in adjacency.items()}


def _logical_roads_geojson(logical_roads: Sequence[LogicalRoad]) -> dict[str, Any]:
    features = []
    for road in logical_roads:
        features.append({
            "type": "Feature",
            "id": road.logical_road_id,
            "properties": {
                "logical_road_id": road.logical_road_id,
                "label": road.label,
                "name": road.name,
                "ref": road.ref,
                "highway_type": road.highway_type,
                "way_ids": list(road.way_ids),
                "way_count": len(road.way_ids),
                "length_m": round(road.length_m, 2),
                "touches_retrieval_boundary": road.touches_retrieval_boundary,
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[list(point) for point in line] for line in road.coordinates],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _context_geojson(features: OsmFeatures) -> dict[str, Any]:
    payload = osm_features_to_geojson(features)
    return {
        "type": "FeatureCollection",
        "features": [
            item for item in payload["features"]
            if str((item.get("properties") or {}).get("highway") or "") == ""
        ],
    }


def build_osm_road_preview(
    *,
    aoi_bbox: Sequence[float],
    source_id: str,
    cache_dir: str | Path,
    force_refetch: bool = False,
    preview_id: str,
    progress_callback: ProgressCallback | None = None,
) -> OsmRoadPreviewBundle:
    bbox = validate_osm_aoi_bbox(aoi_bbox)
    fetch_detail: dict[str, Any] = {"cache_hit": False}

    def forward_progress(event: Mapping[str, Any]) -> None:
        detail = event.get("detail")
        if isinstance(detail, Mapping) and "cache_hit" in detail:
            fetch_detail["cache_hit"] = bool(detail.get("cache_hit"))
        if progress_callback is not None:
            progress_callback(event)

    raw = fetch_osm_data(
        bbox,
        Path(cache_dir),
        force_refetch=force_refetch,
        progress_callback=forward_progress,
    )
    _emit(progress_callback, "parse_features", 62, "Parsing roads and contextual OSM objects.", element_count=len(raw.get("elements", [])))
    features = parse_osm_features(raw)
    _emit(
        progress_callback,
        "build_road_graph",
        76,
        "Building logical roads and shared-node topology.",
        road_way_count=len(features.roads),
        building_count=len(features.buildings),
    )
    logical_roads, adjacency = build_logical_roads(features.roads, bbox)
    raw_fingerprint = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    preview = {
        "preview_id": preview_id,
        "source_id": source_id,
        "retrieval_bbox": list(bbox),
        "logical_roads": _logical_roads_geojson(logical_roads),
        "context_geojson": _context_geojson(features),
        "feature_counts": {
            "road_ways": len(features.roads),
            "logical_roads": len(logical_roads),
            "buildings": len(features.buildings),
            "land_use": len(features.land_use_polygons),
            "poi": sum(1 for item in features.context_points if item.tags.get("natural") != "tree"),
            "trees": sum(1 for item in features.context_points if item.tags.get("natural") == "tree"),
        },
        "cache_hit": bool(fetch_detail["cache_hit"]),
        "fingerprint": raw_fingerprint,
        "query_version": OVERPASS_QUERY_VERSION,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _emit(
        progress_callback,
        "prepare_selection",
        94,
        "Road selection preview is ready.",
        logical_road_count=len(logical_roads),
        building_count=len(features.buildings),
    )
    return OsmRoadPreviewBundle(
        preview_id=preview_id,
        source_id=source_id,
        bbox=bbox,
        raw_osm=dict(raw),
        features=features,
        logical_roads=logical_roads,
        adjacency=adjacency,
        preview=preview,
    )


def _hop_layers(bundle: OsmRoadPreviewBundle, seed_id: str, hop_count: int) -> dict[str, int]:
    if seed_id not in bundle.adjacency:
        raise ValueError(f"Unknown logical road: {seed_id}")
    layers = {seed_id: 0}
    queue = deque([seed_id])
    while queue:
        road_id = queue.popleft()
        layer = layers[road_id]
        if layer >= hop_count:
            continue
        for neighbour in bundle.adjacency.get(road_id, ()):
            if neighbour not in layers:
                layers[neighbour] = layer + 1
                queue.append(neighbour)
    return layers


def _feature_coordinates(value: Any):
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            yield float(value[0]), float(value[1])
        else:
            for item in value:
                yield from _feature_coordinates(item)


def select_osm_road_study_area(
    bundle: OsmRoadPreviewBundle,
    *,
    seed_logical_road_id: str,
    hop_count: int = 1,
    context_buffer_m: float = DEFAULT_CONTEXT_BUFFER_M,
    source_id: str | None = None,
) -> dict[str, Any]:
    if int(hop_count) not in {1, 2}:
        raise ValueError("hop_count must be 1 or 2.")
    buffer_m = float(context_buffer_m)
    if not MIN_CONTEXT_BUFFER_M <= buffer_m <= MAX_CONTEXT_BUFFER_M:
        raise ValueError(f"context_buffer_m must be between {MIN_CONTEXT_BUFFER_M:g} and {MAX_CONTEXT_BUFFER_M:g}.")

    from pyproj import Transformer
    from shapely.geometry import LineString, mapping, shape
    from shapely.ops import transform, unary_union

    layers = _hop_layers(bundle, seed_logical_road_id, int(hop_count))
    logical_by_id = {road.logical_road_id: road for road in bundle.logical_roads}
    selected_logical = [logical_by_id[road_id] for road_id in layers]
    selected_way_ids = {way_id for road in selected_logical for way_id in road.way_ids}
    logical_road_by_way_id = {
        int(way_id): road.logical_road_id
        for road in selected_logical
        for way_id in road.way_ids
    }

    west, south, east, north = bundle.bbox
    epsg = auto_detect_utm_epsg((west + east) * 0.5, (south + north) * 0.5)
    forward = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    inverse = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True).transform
    road_lines = [
        transform(forward, LineString(line))
        for road in selected_logical
        for line in road.coordinates
        if len(line) >= 2
    ]
    corridor_metric = unary_union(road_lines).buffer(buffer_m, cap_style=1, join_style=1)
    corridor_wgs84 = transform(inverse, corridor_metric)

    full_geojson = osm_features_to_geojson(bundle.features)
    filtered_features: list[dict[str, Any]] = []
    role_counts: dict[str, int] = defaultdict(int)
    for feature in full_geojson["features"]:
        feature_id = str(feature.get("id") or "")
        properties = dict(feature.get("properties") or {})
        geometry = dict(feature.get("geometry") or {})
        is_road = feature_id.startswith("osm-road-")
        if is_road:
            try:
                keep = int(feature_id.rsplit("-", 1)[-1]) in selected_way_ids
            except ValueError:
                keep = False
            role = "roads"
        else:
            try:
                keep = shape(geometry).intersects(corridor_wgs84)
            except Exception:
                keep = False
            role = (
                "buildings" if feature_id.startswith("osm-building-")
                else "land_use" if feature_id.startswith("osm-zone-")
                else "trees" if feature_id.startswith("osm-tree-")
                else "poi"
            )
        if keep:
            if role == "roads":
                try:
                    way_id = int(feature_id.rsplit("-", 1)[-1])
                except ValueError:
                    way_id = 0
                properties["logical_road_id"] = logical_road_by_way_id.get(way_id, "")
            if role == "land_use":
                properties["context_intersection"] = True
            filtered_features.append({**feature, "properties": properties})
            role_counts[role] += 1

    if not any(str(item.get("id") or "").startswith("osm-road-") for item in filtered_features):
        raise ValueError("The selected road neighborhood does not contain a usable road geometry.")

    coordinates = [
        pair
        for feature in filtered_features
        for pair in _feature_coordinates((feature.get("geometry") or {}).get("coordinates"))
    ]
    min_lon = min(item[0] for item in coordinates)
    min_lat = min(item[1] for item in coordinates)
    max_lon = max(item[0] for item in coordinates)
    max_lat = max(item[1] for item in coordinates)
    mid_lat = (min_lat + max_lat) * 0.5
    lon_pad = 10.0 / max(1.0, 111_320.0 * math.cos(math.radians(mid_lat)))
    lat_pad = 10.0 / 110_540.0
    annotation_bbox = [min_lon - lon_pad, min_lat - lat_pad, max_lon + lon_pad, max_lat + lat_pad]
    filtered_geojson = {"type": "FeatureCollection", "features": filtered_features}
    normalized = normalize_teaching_geojson(
        filtered_geojson,
        source_id=str(source_id or bundle.source_id),
        bbox=annotation_bbox,
    )
    warnings = list(normalized["warnings"])
    if any(road.touches_retrieval_boundary for road in selected_logical):
        warnings.append("The selected road neighborhood touches the OSM retrieval boundary; adjacent roads may be truncated.")
    study = {
        "selection": {
            "seed_logical_road_id": seed_logical_road_id,
            "hop_count": int(hop_count),
            "context_buffer_m": buffer_m,
        },
        "selected_way_ids": sorted(selected_way_ids),
        "hop_layers": {key: int(value) for key, value in layers.items()},
        "study_area": {
            "type": "Feature",
            "properties": {"buffer_m": buffer_m},
            "geometry": mapping(corridor_wgs84),
        },
        "included_feature_counts": dict(role_counts),
        "annotation_bbox": annotation_bbox,
        "retrieval_bbox": list(bundle.bbox),
        "preview_id": bundle.preview_id,
        "preview_fingerprint": str(bundle.preview.get("fingerprint") or ""),
        "warnings": warnings,
    }
    return {
        "normalized": normalized,
        "study": study,
        "filtered_geojson": filtered_geojson,
        "osm_annotation_context": {
            "schema_version": "roadgen3d.osm_annotation_context.v1",
            "raw_feature_collection": filtered_geojson,
            "retrieval_bbox": list(bundle.bbox),
            "annotation_bbox": annotation_bbox,
            "selected_way_ids": sorted(selected_way_ids),
            "selection": dict(study["selection"]),
            "projection": {
                "crs": "EPSG:4326",
                "utm_epsg": int(epsg),
                "origin_wgs84": [(west + east) * 0.5, (south + north) * 0.5],
            },
        },
    }


def preview_bundle_from_raw(
    *,
    raw_osm: Mapping[str, Any],
    aoi_bbox: Sequence[float],
    source_id: str,
    preview_id: str,
) -> OsmRoadPreviewBundle:
    bbox = validate_osm_aoi_bbox(aoi_bbox)
    features = parse_osm_features(dict(raw_osm))
    logical_roads, adjacency = build_logical_roads(features.roads, bbox)
    fingerprint = hashlib.sha256(
        json.dumps(raw_osm, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    preview = {
        "preview_id": preview_id,
        "source_id": source_id,
        "retrieval_bbox": list(bbox),
        "logical_roads": _logical_roads_geojson(logical_roads),
        "context_geojson": _context_geojson(features),
        "feature_counts": {
            "road_ways": len(features.roads),
            "logical_roads": len(logical_roads),
            "buildings": len(features.buildings),
            "land_use": len(features.land_use_polygons),
            "poi": sum(1 for item in features.context_points if item.tags.get("natural") != "tree"),
            "trees": sum(1 for item in features.context_points if item.tags.get("natural") == "tree"),
        },
        "cache_hit": True,
        "fingerprint": fingerprint,
        "query_version": OVERPASS_QUERY_VERSION,
    }
    return OsmRoadPreviewBundle(preview_id, source_id, bbox, dict(raw_osm), features, logical_roads, adjacency, preview)


__all__ = [
    "DEFAULT_CONTEXT_BUFFER_M",
    "MAX_CONTEXT_BUFFER_M",
    "MIN_CONTEXT_BUFFER_M",
    "LogicalRoad",
    "OsmRoadPreviewBundle",
    "build_logical_roads",
    "build_osm_road_preview",
    "preview_bundle_from_raw",
    "select_osm_road_study_area",
]
