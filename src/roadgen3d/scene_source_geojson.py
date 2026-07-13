"""GeoJSON normalization, OSM conversion, and round-trip quality checks."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from roadgen3d.osm_ingest import OsmFeatures, parse_osm_features
from roadgen3d.scene_sources import normalize_scene_source


SCHEMA_VERSION = "roadgen3d.teaching_geojson.v1"
DEFAULT_ANNOTATION_PIXELS_PER_METER = 2.0
MAX_ANNOTATION_SIDE_PX = 4096


def _source_crs(payload: Mapping[str, Any]) -> str:
    roadgen = payload.get("roadgen3d") if isinstance(payload.get("roadgen3d"), Mapping) else {}
    candidate = str(roadgen.get("crs") or "").strip()
    legacy = payload.get("crs") if isinstance(payload.get("crs"), Mapping) else {}
    properties = legacy.get("properties") if isinstance(legacy.get("properties"), Mapping) else {}
    candidate = candidate or str(properties.get("name") or "").strip()
    if not candidate:
        return "EPSG:4326"
    upper = candidate.upper()
    if "EPSG" in upper:
        code = upper.rsplit(":", 1)[-1]
        if code.isdigit():
            return f"EPSG:{code}"
    return candidate


def _transform_coordinates(value: Any, transformer: Any) -> Any:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            x, y = transformer.transform(float(value[0]), float(value[1]))
            return [x, y, *value[2:]]
        return [_transform_coordinates(item, transformer) for item in value]
    return value


def _stable_id(feature: Mapping[str, Any], index: int) -> str:
    explicit = feature.get("id") or (feature.get("properties") or {}).get("id")
    if explicit:
        return str(explicit)
    digest = hashlib.sha256(
        json.dumps(feature, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return f"feature-{index + 1:04d}-{digest}"


def _role_for(geometry_type: str, properties: Mapping[str, Any]) -> tuple[str, float, str]:
    explicit = str(properties.get("role") or "").strip().lower()
    if explicit:
        try:
            confidence = float(properties.get("annotation_confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        source = str(properties.get("annotation_source") or "explicit_property").strip()
        return explicit, max(0.0, min(1.0, confidence)), source
    tags = properties.get("tags") if isinstance(properties.get("tags"), Mapping) else properties
    if geometry_type == "LineString" and tags.get("highway"):
        return "centerline", 0.98, "osm.highway"
    if geometry_type in {"Polygon", "MultiPolygon"} and tags.get("building"):
        return "building_footprint", 0.99, "osm.building"
    if geometry_type in {"Polygon", "MultiPolygon"} and any(tags.get(key) for key in ("landuse", "leisure", "amenity")):
        return "functional_zone", 0.9, "osm.semantic_polygon"
    if geometry_type == "Point" and (tags.get("natural") == "tree" or tags.get("tree")):
        return "tree_candidate", 0.98, "osm.natural_tree"
    if geometry_type == "Point" and any(tags.get(key) for key in ("amenity", "shop", "tourism", "highway")):
        return "street_furniture_anchor", 0.78, "osm.poi"
    if geometry_type == "Point":
        return "control_point", 0.4, "geometry_fallback"
    if geometry_type == "LineString":
        return "centerline", 0.35, "geometry_fallback"
    return "scene_region", 0.35, "geometry_fallback"


def _iter_pairs(value: Any) -> Iterable[tuple[float, float]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            yield float(value[0]), float(value[1])
            return
        for item in value:
            yield from _iter_pairs(item)


def _close_polygon(geometry: dict[str, Any]) -> None:
    if geometry.get("type") != "Polygon":
        return
    for ring in geometry.get("coordinates") or []:
        if isinstance(ring, list) and ring and ring[0] != ring[-1]:
            ring.append(copy.deepcopy(ring[0]))


def canonicalize_geojson(payload: Mapping[str, Any], *, bbox: Sequence[float] | None = None) -> dict[str, Any]:
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError("GeoJSON must be a FeatureCollection.")
    source_crs = _source_crs(payload)
    transformer = None
    if source_crs.upper() not in {"EPSG:4326", "CRS84", "OGC:CRS84"}:
        from pyproj import Transformer

        transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload["features"]):
        if not isinstance(raw, Mapping) or raw.get("type") != "Feature":
            raise ValueError(f"features[{index}] must be a GeoJSON Feature.")
        geometry = copy.deepcopy(raw.get("geometry"))
        if not isinstance(geometry, dict) or geometry.get("type") not in {"Point", "LineString", "Polygon"}:
            raise ValueError(f"features[{index}] uses an unsupported geometry type.")
        if transformer is not None:
            geometry["coordinates"] = _transform_coordinates(geometry.get("coordinates"), transformer)
        coordinates = list(_iter_pairs(geometry.get("coordinates")))
        if not coordinates or not all(math.isfinite(x) and math.isfinite(y) for x, y in coordinates):
            raise ValueError(f"features[{index}] has empty or non-finite coordinates.")
        if not all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in coordinates):
            raise ValueError(f"features[{index}] is not valid EPSG:4326 geometry.")
        if geometry["type"] == "LineString" and len(coordinates) < 2:
            raise ValueError(f"features[{index}] LineString must contain at least two coordinates.")
        feature_id = _stable_id(raw, index)
        if feature_id in seen:
            raise ValueError(f"Duplicate feature id: {feature_id}")
        seen.add(feature_id)
        properties = copy.deepcopy(dict(raw.get("properties") or {}))
        role, confidence, source = _role_for(str(geometry["type"]), properties)
        properties.update({
            "role": role,
            "annotation_confidence": confidence,
            "annotation_source": source,
            "annotation_status": properties.get("annotation_status") or "auto",
        })
        _close_polygon(geometry)
        if geometry["type"] == "Polygon" and any(len(ring) < 4 for ring in geometry.get("coordinates") or []):
            raise ValueError(f"features[{index}] Polygon rings must contain at least four coordinates.")
        features.append({"type": "Feature", "id": feature_id, "properties": properties, "geometry": geometry})
    _append_road_intersections(features)
    return {
        "type": "FeatureCollection",
        "features": features,
        "roadgen3d": {
            "schema_version": SCHEMA_VERSION,
            "crs": "EPSG:4326",
            "source_crs": source_crs,
            "bbox": list(bbox) if bbox is not None else _bounds(features),
            "normalized_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _append_road_intersections(features: list[dict[str, Any]]) -> None:
    from shapely.geometry import LineString, Point

    roads = [item for item in features if item["geometry"]["type"] == "LineString" and item["properties"].get("role") == "centerline"]
    seen: set[tuple[float, float]] = {
        (round(float(item["geometry"]["coordinates"][0]), 9), round(float(item["geometry"]["coordinates"][1]), 9))
        for item in features
        if item["geometry"]["type"] == "Point" and item["properties"].get("role") == "road_intersection"
    }
    additions: list[dict[str, Any]] = []
    for left_index, left in enumerate(roads):
        left_line = LineString(left["geometry"]["coordinates"])
        for right in roads[left_index + 1:]:
            intersection = left_line.intersection(LineString(right["geometry"]["coordinates"]))
            points = [intersection] if isinstance(intersection, Point) else list(getattr(intersection, "geoms", []))
            for point in points:
                if not isinstance(point, Point):
                    continue
                key = (round(float(point.x), 9), round(float(point.y), 9))
                if key in seen:
                    continue
                seen.add(key)
                digest = hashlib.sha256(f"{key[0]:.9f},{key[1]:.9f}".encode()).hexdigest()[:16]
                additions.append({
                    "type": "Feature",
                    "id": f"intersection-{digest}",
                    "properties": {
                        "role": "road_intersection",
                        "annotation_confidence": 0.99,
                        "annotation_source": "derived.centerline_intersection",
                        "annotation_status": "auto",
                    },
                    "geometry": {"type": "Point", "coordinates": [float(point.x), float(point.y)]},
                })
    features.extend(additions)


def _bounds(features: Sequence[Mapping[str, Any]]) -> list[float]:
    pairs = [pair for feature in features for pair in _iter_pairs((feature.get("geometry") or {}).get("coordinates"))]
    if not pairs:
        return []
    return [min(x for x, _ in pairs), min(y for _, y in pairs), max(x for x, _ in pairs), max(y for _, y in pairs)]


def _annotation_compatible(canonical: Mapping[str, Any]) -> dict[str, Any]:
    compatible: list[dict[str, Any]] = []
    for feature in canonical.get("features", []):
        item = copy.deepcopy(feature)
        role = str((item.get("properties") or {}).get("role") or "")
        geometry_type = str((item.get("geometry") or {}).get("type") or "")
        if role == "road_intersection" and geometry_type == "Point":
            item["properties"]["kind"] = "intersection"
            item["properties"]["role"] = "junction"
        elif role in {"tree_candidate", "street_furniture_anchor"} and geometry_type == "Point":
            item["properties"]["kind"] = role
            item["properties"]["role"] = "control_point"
        elif geometry_type == "Polygon" and role not in {"building_footprint", "building", "functional_zone", "scene_region", "building_region"}:
            item["properties"]["role"] = "scene_region"
        compatible.append(item)
    return {"type": "FeatureCollection", "features": compatible}


def annotation_image_for_bbox(
    bbox: Sequence[float],
    *,
    pixels_per_meter: float = DEFAULT_ANNOTATION_PIXELS_PER_METER,
    max_side_px: int = MAX_ANNOTATION_SIDE_PX,
) -> dict[str, Any]:
    """Build a deterministic annotation canvas from a WGS84 AOI.

    The previous course path always used a 1024px square at 1px/m, which made
    the annotation frame unrelated to the selected AOI.  The shared path keeps
    physical scale explicit and only reduces resolution for unusually large
    areas.
    """

    if len(bbox) != 4:
        raise ValueError("bbox must be [west,south,east,north].")
    west, south, east, north = (float(item) for item in bbox)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox is reversed or outside WGS84 bounds.")
    from pyproj import Transformer
    from roadgen3d.osm_ingest import auto_detect_utm_epsg

    centre_lon = (west + east) * 0.5
    centre_lat = (south + north) * 0.5
    epsg = auto_detect_utm_epsg(centre_lon, centre_lat)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    left, bottom = transformer.transform(west, south)
    right, top = transformer.transform(east, north)
    width_m = max(1.0, abs(float(right) - float(left)))
    height_m = max(1.0, abs(float(top) - float(bottom)))
    requested_ppm = max(0.1, float(pixels_per_meter))
    effective_ppm = min(requested_ppm, float(max_side_px) / max(width_m, height_m))
    return {
        "width_px": max(64, int(math.ceil(width_m * effective_ppm))),
        "height_px": max(64, int(math.ceil(height_m * effective_ppm))),
        "pixels_per_meter": effective_ppm,
        "bbox_wgs84": [west, south, east, north],
        "utm_epsg": epsg,
    }


def normalize_teaching_geojson(payload: Mapping[str, Any], *, source_id: str, bbox: Sequence[float] | None = None) -> dict[str, Any]:
    canonical = canonicalize_geojson(payload, bbox=bbox)
    bounds = canonical["roadgen3d"]["bbox"]
    image = annotation_image_for_bbox(bounds)
    normalized = normalize_scene_source({
        "kind": "geojson",
        "source_id": source_id,
        "producer": "import",
        "coordinate_space": "EPSG:4326",
        "geojson": _annotation_compatible(canonical),
        "image": image,
    })
    role_counts: dict[str, int] = {}
    for feature in canonical["features"]:
        role = str(feature["properties"]["role"])
        role_counts[role] = role_counts.get(role, 0) + 1
    exported = json.loads(json.dumps(canonical))
    quality = round_trip_report(canonical, exported)
    return {
        "schema_version": SCHEMA_VERSION,
        "geojson": canonical,
        "annotation": normalized.annotation,
        "graph_payload": normalized.to_graph_payload(),
        "source_alignment": normalized.source_alignment,
        "warnings": list(normalized.warnings),
        "role_counts": role_counts,
        "quality_report": quality,
    }


def round_trip_report(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    before_features = {str(item.get("id")): item for item in before.get("features", [])}
    after_features = {str(item.get("id")): item for item in after.get("features", [])}
    lost = sorted(set(before_features) - set(after_features))
    max_delta = 0.0
    topology_ok = set(before_features) == set(after_features)
    for feature_id in set(before_features) & set(after_features):
        left = list(_iter_pairs((before_features[feature_id].get("geometry") or {}).get("coordinates")))
        right = list(_iter_pairs((after_features[feature_id].get("geometry") or {}).get("coordinates")))
        if len(left) != len(right):
            topology_ok = False
            continue
        for (lon_a, lat_a), (lon_b, lat_b) in zip(left, right):
            mean_lat = math.radians((lat_a + lat_b) * 0.5)
            dx = (lon_b - lon_a) * 111_320.0 * math.cos(mean_lat)
            dy = (lat_b - lat_a) * 110_540.0
            max_delta = max(max_delta, math.hypot(dx, dy))
    return {
        "conversion_ok": not lost and topology_ok,
        "geo_delta": round(max_delta, 6),
        "geo_delta_unit": "m",
        "topology_ok": topology_ok,
        "lost_feature_ids": lost,
        "feature_count_before": len(before_features),
        "feature_count_after": len(after_features),
    }


def osm_features_to_geojson(features: OsmFeatures) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for road in features.roads:
        rows.append({"type": "Feature", "id": f"osm-road-{road.osm_id}", "properties": {"tags": road.tags, "highway": road.highway_type, "road_width_m": road.width_m}, "geometry": {"type": "LineString", "coordinates": [list(point) for point in road.coords]}})
    for building in features.buildings:
        rows.append({"type": "Feature", "id": f"osm-building-{building.osm_id}", "properties": {"tags": building.tags}, "geometry": {"type": "Polygon", "coordinates": [[list(point) for point in building.coords]]}})
    for polygon in features.land_use_polygons:
        rows.append({"type": "Feature", "id": f"osm-zone-{polygon.osm_id}", "properties": {"tags": polygon.tags, "source_type": polygon.source_type}, "geometry": {"type": "Polygon", "coordinates": [[list(point) for point in polygon.coords]]}})
    for kind, points in features.poi_points_by_type.items():
        for index, point in enumerate(points):
            rows.append({"type": "Feature", "id": f"osm-poi-{kind}-{index + 1}", "properties": {"tags": {"amenity": kind}, "poi_type": kind}, "geometry": {"type": "Point", "coordinates": list(point)}})
    return {"type": "FeatureCollection", "features": rows}


def raw_osm_to_geojson(raw: Mapping[str, Any]) -> dict[str, Any]:
    return osm_features_to_geojson(parse_osm_features(dict(raw)))
