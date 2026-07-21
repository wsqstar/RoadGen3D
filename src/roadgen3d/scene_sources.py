"""Typed source normalization for student-authored RoadGen3D scenes.

GeoJSON is an interchange/review format.  Every accepted source is converted to
``roadgen3d_reference_annotation_v2`` before graph construction.
"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .reference_annotation import (
    ANNOTATION_SCHEMA_VERSION,
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
    parse_reference_annotation,
)

SCENE_SOURCE_SCHEMA_VERSION = "roadgen3d_scene_source_v1"
MAX_SOURCE_FEATURES = 10_000
MAX_SOURCE_COORDINATES = 200_000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_ALLOWED_PRODUCERS = {"manual", "ai", "import", "catalog", "osm"}
_ALLOWED_COORDINATE_SPACES = {"image_px", "EPSG:4326"}
_SOURCE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass(frozen=True)
class NormalizedSceneSource:
    source: Dict[str, Any]
    geojson: Dict[str, Any]
    annotation: Dict[str, Any]
    warnings: Tuple[str, ...]
    aligned_buildings: Tuple[Dict[str, Any], ...]
    source_alignment: Dict[str, Any]

    def to_graph_payload(self, compose_config: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        config = build_reference_annotation_compose_config(compose_config)
        payload = build_reference_annotation_graph_payload(self.annotation, config=config)
        return {
            **payload,
            "source": dict(self.source),
            "geojson": dict(self.geojson),
            "warnings": list(self.warnings),
            "aligned_buildings": [dict(item) for item in self.aligned_buildings],
            "source_alignment": dict(self.source_alignment),
        }


def normalize_scene_source(source: Mapping[str, Any]) -> NormalizedSceneSource:
    if not isinstance(source, Mapping):
        raise ValueError("source must be an object.")
    kind = str(source.get("kind", "") or "").strip().lower()
    source_id = _source_id(source.get("source_id"))
    producer = str(source.get("producer", "manual") or "manual").strip().lower()
    if producer not in _ALLOWED_PRODUCERS:
        raise ValueError(f"source.producer must be one of {sorted(_ALLOWED_PRODUCERS)}.")

    if kind == "reference_annotation":
        raw_annotation = source.get("annotation")
        if not isinstance(raw_annotation, Mapping):
            raise ValueError("reference_annotation source requires annotation object.")
        annotation = _strict_annotation(raw_annotation).to_dict()
        geojson = annotation_to_geojson(annotation)
        aligned_buildings = tuple(_annotation_aligned_buildings(annotation))
        alignment = {
            "schema_version": "roadgen3d.source_alignment.v1",
            "status": "aligned",
            "kind": "annotation_image_frame",
            "scene_frame": "annotation_image_center_xz_m",
            "pixels_per_meter": float(annotation["pixels_per_meter"]),
            "image_width_px": int(annotation["image_width_px"]),
            "image_height_px": int(annotation["image_height_px"]),
        }
        warnings: Tuple[str, ...] = ()
    elif kind == "geojson":
        coordinate_space = str(source.get("coordinate_space", "") or "").strip()
        if coordinate_space not in _ALLOWED_COORDINATE_SPACES:
            raise ValueError(
                "geojson source.coordinate_space must be 'image_px' or 'EPSG:4326'."
            )
        raw_geojson = source.get("geojson")
        image = source.get("image") or {}
        if not isinstance(raw_geojson, Mapping):
            raise ValueError("geojson source requires a FeatureCollection object.")
        if not isinstance(image, Mapping):
            raise ValueError("source.image must be an object.")
        annotation, geojson, aligned_buildings, alignment, warning_list = _geojson_to_annotation(
            raw_geojson,
            coordinate_space=coordinate_space,
            source_id=source_id,
            image=image,
        )
        annotation = _strict_annotation(annotation).to_dict()
        warnings = tuple(warning_list)
    else:
        raise ValueError("source.kind must be 'reference_annotation' or 'geojson'.")

    digest = sha256(_canonical_source_bytes(annotation)).hexdigest()
    source_metadata = {
        "schema_version": SCENE_SOURCE_SCHEMA_VERSION,
        "source_id": source_id,
        "kind": kind,
        "producer": producer,
        "normalized_annotation_version": ANNOTATION_SCHEMA_VERSION,
        "annotation_sha256": digest,
    }
    return NormalizedSceneSource(
        source=source_metadata,
        geojson=geojson,
        annotation=annotation,
        warnings=warnings,
        aligned_buildings=tuple(aligned_buildings),
        source_alignment=alignment,
    )


def annotation_to_geojson(annotation_payload: Mapping[str, Any]) -> Dict[str, Any]:
    annotation = _strict_annotation(annotation_payload).to_dict()
    features: List[Dict[str, Any]] = []
    for centerline in annotation.get("centerlines", []):
        properties = {
            key: value
            for key, value in centerline.items()
            if key not in {"id", "points"}
        }
        properties["role"] = "centerline"
        features.append({
            "type": "Feature",
            "id": centerline["id"],
            "properties": properties,
            "geometry": {
                "type": "LineString",
                "coordinates": [[float(point["x"]), float(point["y"])] for point in centerline["points"]],
            },
        })
    for marker in annotation.get("junctions", []):
        anchor = marker.get("anchor") if isinstance(marker.get("anchor"), Mapping) else marker
        features.append({
            "type": "Feature",
            "id": marker.get("id"),
            "properties": {"role": "junction", "kind": marker.get("kind", "intersection")},
            "geometry": {"type": "Point", "coordinates": [float(anchor.get("x", 0.0)), float(anchor.get("y", 0.0))]},
        })
    for marker in annotation.get("control_points", []):
        features.append({
            "type": "Feature",
            "id": marker.get("id"),
            "properties": {"role": "control_point", "kind": marker.get("kind", "control_point")},
            "geometry": {"type": "Point", "coordinates": [float(marker.get("x", 0.0)), float(marker.get("y", 0.0))]},
        })
    for region in annotation.get("regions", []):
        ring = [[float(point["x"]), float(point["y"])] for point in region.get("points", [])]
        ring = _closed_ring(ring)
        features.append({
            "type": "Feature",
            "id": region.get("id"),
            "properties": {
                "role": region.get("region_role", "scene_region"),
                "label": region.get("label", ""),
                "kind": region.get("kind", ""),
                "land_use_type": region.get("land_use_type", ""),
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    return {"type": "FeatureCollection", "features": features}


def validate_image_data_url(value: str) -> Tuple[str, bytes]:
    text = str(value or "").strip()
    match = re.fullmatch(r"data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=\s]+)", text)
    if not match:
        raise ValueError("image_data_url must be a base64 PNG or JPEG data URL.")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("image_data_url contains invalid base64 data.") from exc
    if not payload or len(payload) > MAX_IMAGE_BYTES:
        raise ValueError(f"image_data_url must contain 1..{MAX_IMAGE_BYTES} decoded bytes.")
    if match.group(1) == "image/png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("image_data_url MIME type does not match PNG bytes.")
    if match.group(1) == "image/jpeg" and not payload.startswith(b"\xff\xd8"):
        raise ValueError("image_data_url MIME type does not match JPEG bytes.")
    return match.group(1), payload


def _strict_annotation(payload: Mapping[str, Any]):
    version = str(payload.get("version", "") or "").strip()
    if version != ANNOTATION_SCHEMA_VERSION:
        raise ValueError(
            f"annotation.version must be '{ANNOTATION_SCHEMA_VERSION}', got {version or '<missing>'}."
        )
    annotation = parse_reference_annotation(payload)
    if annotation.image_width_px <= 0 or annotation.image_height_px <= 0:
        raise ValueError("annotation image dimensions must be positive.")
    if not math.isfinite(annotation.pixels_per_meter) or annotation.pixels_per_meter <= 0:
        raise ValueError("annotation.pixels_per_meter must be positive and finite.")
    seen: set[str] = set()
    for collection_name, records in (
        ("centerlines", annotation.centerlines),
        ("junctions", annotation.junctions),
        ("roundabouts", annotation.roundabouts),
        ("control_points", annotation.control_points),
        ("regions", annotation.regions),
        ("building_regions", annotation.building_regions),
        ("functional_zones", annotation.functional_zones),
    ):
        for record in records:
            feature_id = str(getattr(record, "feature_id", "") or "").strip()
            if not feature_id:
                raise ValueError(f"{collection_name} contains an empty id.")
            if feature_id in seen:
                raise ValueError(f"Duplicate annotation feature id: {feature_id}")
            seen.add(feature_id)
    for centerline in annotation.centerlines:
        unique = {(round(float(point.x), 9), round(float(point.y), 9)) for point in centerline.points}
        if len(centerline.points) < 2 or len(unique) < 2:
            raise ValueError(f"Centerline '{centerline.feature_id}' is degenerate.")
    return annotation


def _geojson_to_annotation(
    raw_geojson: Mapping[str, Any],
    *,
    coordinate_space: str,
    source_id: str,
    image: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Tuple[Dict[str, Any], ...], Dict[str, Any], List[str]]:
    if raw_geojson.get("type") != "FeatureCollection":
        raise ValueError("geojson.type must be 'FeatureCollection'.")
    raw_features = raw_geojson.get("features")
    if not isinstance(raw_features, Sequence) or isinstance(raw_features, (str, bytes)):
        raise ValueError("geojson.features must be an array.")
    if len(raw_features) > MAX_SOURCE_FEATURES:
        raise ValueError(f"geojson exceeds {MAX_SOURCE_FEATURES} features.")

    ppm = _positive_number(image.get("pixels_per_meter", 1.0), "image.pixels_per_meter")
    width = _positive_int(image.get("width_px", 0), "image.width_px")
    height = _positive_int(image.get("height_px", 0), "image.height_px")
    bbox = _bbox(image.get("bbox_wgs84")) if image.get("bbox_wgs84") is not None else None

    feature_ids: set[str] = set()
    count = 0
    source_features: List[Dict[str, Any]] = []
    coordinates_wgs84: List[Tuple[float, float]] = []
    for index, raw_feature in enumerate(raw_features):
        if not isinstance(raw_feature, Mapping) or raw_feature.get("type") != "Feature":
            raise ValueError(f"geojson.features[{index}] must be a Feature object.")
        feature_id = _source_id(raw_feature.get("id") or (raw_feature.get("properties") or {}).get("id") or f"feature_{index + 1:04d}")
        if feature_id in feature_ids:
            raise ValueError(f"Duplicate GeoJSON feature id: {feature_id}")
        feature_ids.add(feature_id)
        geometry = raw_feature.get("geometry")
        properties = raw_feature.get("properties") or {}
        if not isinstance(geometry, Mapping) or not isinstance(properties, Mapping):
            raise ValueError(f"geojson.features[{index}] requires geometry and properties objects.")
        geometry_type = str(geometry.get("type", "") or "")
        role = str(properties.get("role", "") or "").strip().lower()
        if not role:
            raise ValueError(f"geojson.features[{index}].properties.role is required.")
        allowed = {
            "LineString": {"centerline", "road"},
            "Point": {"junction", "control_point"},
            "Polygon": {"scene_region", "building_region", "functional_zone", "building", "building_footprint"},
        }
        if geometry_type not in allowed or role not in allowed[geometry_type]:
            raise ValueError(f"Unsupported GeoJSON geometry/role: {geometry_type}/{role}.")
        coords = _geometry_coordinates(geometry_type, geometry.get("coordinates"), f"geojson.features[{index}]")
        count += sum(1 for _ in _iter_coordinate_pairs(geometry_type, coords))
        if count > MAX_SOURCE_COORDINATES:
            raise ValueError(f"geojson exceeds {MAX_SOURCE_COORDINATES} coordinate pairs.")
        if coordinate_space == "EPSG:4326":
            for lon, lat in _iter_coordinate_pairs(geometry_type, coords):
                if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                    raise ValueError(f"GeoJSON coordinate out of WGS84 range: {[lon, lat]}.")
                coordinates_wgs84.append((lon, lat))
        source_features.append({
            "type": "Feature",
            "id": feature_id,
            "properties": dict(properties),
            "geometry": {"type": geometry_type, "coordinates": coords},
        })

    if not any(str(item["properties"].get("role", "")).lower() in {"centerline", "road"} for item in source_features):
        raise ValueError("GeoJSON source requires at least one centerline LineString.")

    projector = None
    origin_utm = None
    utm_epsg = None
    if coordinate_space == "EPSG:4326":
        if bbox is None:
            min_lon = min(item[0] for item in coordinates_wgs84)
            max_lon = max(item[0] for item in coordinates_wgs84)
            min_lat = min(item[1] for item in coordinates_wgs84)
            max_lat = max(item[1] for item in coordinates_wgs84)
            bbox = (min_lon, min_lat, max_lon, max_lat)
        from pyproj import Transformer
        from .osm_ingest import auto_detect_utm_epsg

        centre_lon = (bbox[0] + bbox[2]) * 0.5
        centre_lat = (bbox[1] + bbox[3]) * 0.5
        utm_epsg = auto_detect_utm_epsg(centre_lon, centre_lat)
        transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg}", always_xy=True)
        origin_utm = transformer.transform(centre_lon, centre_lat)

        def projector(pair: Sequence[float]) -> Tuple[float, float]:
            easting, northing = transformer.transform(float(pair[0]), float(pair[1]))
            return float(easting - origin_utm[0]), float(northing - origin_utm[1])

    def pair_to_local(pair: Sequence[float]) -> Tuple[float, float]:
        if coordinate_space == "EPSG:4326":
            assert projector is not None
            return projector(pair)
        return ((float(pair[0]) - width * 0.5) / ppm, (height * 0.5 - float(pair[1])) / ppm)

    def pair_to_pixel(pair: Sequence[float]) -> List[float]:
        if coordinate_space == "image_px":
            return [float(pair[0]), float(pair[1])]
        local_x, local_z = pair_to_local(pair)
        return [local_x * ppm + width * 0.5, height * 0.5 - local_z * ppm]

    centerlines: List[Dict[str, Any]] = []
    junctions: List[Dict[str, Any]] = []
    control_points: List[Dict[str, Any]] = []
    regions: List[Dict[str, Any]] = []
    functional_zones: List[Dict[str, Any]] = []
    aligned_buildings: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for feature in source_features:
        feature_id = str(feature["id"])
        properties = feature["properties"]
        role = str(properties.get("role", "")).lower()
        geometry = feature["geometry"]
        coords = geometry["coordinates"]
        if role in {"centerline", "road"}:
            points = [pair_to_pixel(pair) for pair in coords]
            if len({(round(p[0], 9), round(p[1], 9)) for p in points}) < 2:
                raise ValueError(f"Centerline feature '{feature_id}' is degenerate.")
            raw_way_id = properties.get("osm_way_id") or properties.get("osm_id")
            lane_defaults = _osm_lane_defaults(properties) if raw_way_id is not None else {}
            source_refs: Dict[str, Any] = {}
            if raw_way_id is not None:
                source_refs = {
                    "kind": "osm_road",
                    "edit_state": "base",
                    "osm_way_ids": [str(raw_way_id)],
                    "osm_node_ids": [str(item) for item in (properties.get("osm_node_ids") or [])],
                    "logical_road_id": str(properties.get("logical_road_id") or ""),
                    "original_points": [{"x": point[0], "y": point[1]} for point in points],
                }
            centerlines.append({
                "id": feature_id,
                "label": str(properties.get("label") or properties.get("name") or feature_id),
                "road_width_m": _optional_positive(properties.get("road_width_m") or properties.get("width_m"), 8.0),
                "forward_drive_lane_count": _nonnegative_int(properties.get("forward_drive_lane_count"), lane_defaults.get("forward_drive_lane_count", 1)),
                "reverse_drive_lane_count": _nonnegative_int(properties.get("reverse_drive_lane_count"), lane_defaults.get("reverse_drive_lane_count", 1)),
                "bike_lane_count": _nonnegative_int(properties.get("bike_lane_count"), lane_defaults.get("bike_lane_count", 0)),
                "bus_lane_count": _nonnegative_int(properties.get("bus_lane_count"), lane_defaults.get("bus_lane_count", 0)),
                "parking_lane_count": _nonnegative_int(properties.get("parking_lane_count"), lane_defaults.get("parking_lane_count", 0)),
                "highway_type": str(properties.get("highway_type") or properties.get("highway") or "residential"),
                "source_refs": source_refs,
                "points": [{"x": point[0], "y": point[1]} for point in points],
            })
        elif role == "junction":
            point = pair_to_pixel(coords)
            junctions.append({"id": feature_id, "kind": str(properties.get("kind") or "intersection"), "x": point[0], "y": point[1]})
        elif role == "control_point":
            point = pair_to_pixel(coords)
            control_points.append({"id": feature_id, "kind": str(properties.get("kind") or "control_point"), "x": point[0], "y": point[1]})
        else:
            ring = coords[0]
            pixel_ring = _closed_ring([pair_to_pixel(pair) for pair in ring])
            if abs(_signed_area(pixel_ring)) <= 1e-6:
                raise ValueError(f"Polygon feature '{feature_id}' is degenerate.")
            if role in {"building", "building_footprint"}:
                local_ring = _closed_ring([list(pair_to_local(pair)) for pair in ring])
                tags = dict(properties.get("tags") or {})
                regions.append({
                    "id": feature_id,
                    "label": str(properties.get("label") or properties.get("name") or feature_id),
                    "region_role": "building_region",
                    "kind": str(properties.get("building") or tags.get("building") or "building"),
                    "land_use_type": str(properties.get("land_use") or tags.get("landuse") or ""),
                    "source_region_id": str(properties.get("osm_id") or feature_id),
                    "points": [{"x": p[0], "y": p[1]} for p in pixel_ring[:-1]],
                    "material": _building_height_metadata(properties, tags),
                })
                aligned_buildings.append({
                    "osm_id": str(properties.get("osm_id") or feature_id),
                    "polygon_xz": local_ring,
                    "tags": tags,
                    "source_id": feature_id,
                    "editable": False,
                })
            elif role == "functional_zone":
                functional_zones.append({
                    "id": feature_id,
                    "label": str(properties.get("label") or feature_id),
                    "kind": str(properties.get("kind") or "plaza"),
                    "points": [{"x": p[0], "y": p[1]} for p in pixel_ring[:-1]],
                })
            else:
                regions.append({
                    "id": feature_id,
                    "label": str(properties.get("label") or feature_id),
                    "region_role": role,
                    "kind": str(properties.get("kind") or ""),
                    "land_use_type": str(properties.get("land_use_type") or ""),
                    "points": [{"x": p[0], "y": p[1]} for p in pixel_ring[:-1]],
                })

    # Imported OSM roads are segmented at crossings above.  Bind those segment
    # endpoints to the derived explicit junctions in the same pixel frame so
    # the annotation can be edited and generated without topology ambiguity.
    endpoint_tolerance_px = 1e-5
    for junction in junctions:
        connected: List[str] = []
        junction_x = float(junction["x"])
        junction_y = float(junction["y"])
        for centerline in centerlines:
            points = centerline["points"]
            if not points:
                continue
            start = points[0]
            end = points[-1]
            if math.hypot(float(start["x"]) - junction_x, float(start["y"]) - junction_y) <= endpoint_tolerance_px:
                centerline["start_junction_id"] = str(junction["id"])
                connected.append(str(centerline["id"]))
            if math.hypot(float(end["x"]) - junction_x, float(end["y"]) - junction_y) <= endpoint_tolerance_px:
                centerline["end_junction_id"] = str(junction["id"])
                connected.append(str(centerline["id"]))
        if connected:
            junction["connected_centerline_ids"] = list(dict.fromkeys(connected))
            junction["source_mode"] = "explicit"

    annotation = {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": source_id,
        "image_path": str(image.get("path") or ""),
        "image_width_px": width,
        "image_height_px": height,
        "pixels_per_meter": ppm,
        "centerlines": centerlines,
        "junctions": junctions,
        "roundabouts": [],
        "control_points": control_points,
        "regions": regions,
        "building_regions": [],
        "functional_zones": functional_zones,
        "surface_annotations": [],
        "station_strip_patches": [],
        "junction_compositions": [],
    }
    normalized_geojson = {"type": "FeatureCollection", "features": source_features}
    if coordinate_space == "EPSG:4326":
        alignment = {
            "schema_version": "roadgen3d.source_alignment.v1",
            "status": "aligned",
            "kind": "bbox_centered_utm_to_annotation",
            "source_frame": {"crs": "EPSG:4326", "bbox_wgs84": list(bbox or ()), "utm_epsg": utm_epsg, "origin_utm": list(origin_utm or ())},
            "source_to_scene": {"kind": "identity_local_metric", "scale": 1.0, "rotation_deg": 0.0, "translation_xz": [0.0, 0.0]},
        }
    else:
        alignment = {
            "schema_version": "roadgen3d.source_alignment.v1",
            "status": "aligned",
            "kind": "annotation_image_frame",
            "pixels_per_meter": ppm,
            "image_width_px": width,
            "image_height_px": height,
        }
    if not aligned_buildings:
        warnings.append("No aligned building footprints were provided; OSM white massing will be empty.")
    return annotation, normalized_geojson, tuple(aligned_buildings), alignment, warnings


def _annotation_aligned_buildings(annotation: Mapping[str, Any]) -> Iterable[Dict[str, Any]]:
    width = float(annotation["image_width_px"])
    height = float(annotation["image_height_px"])
    ppm = float(annotation["pixels_per_meter"])
    for region in annotation.get("regions", []):
        if str(region.get("region_role", "")) != "building_region":
            continue
        ring = _closed_ring([[float(point["x"]), float(point["y"])] for point in region.get("points", [])])
        yield {
            "osm_id": str(region.get("source_region_id") or region.get("id")),
            "source_id": str(region.get("id")),
            "polygon_xz": [[(x - width * 0.5) / ppm, (height * 0.5 - y) / ppm] for x, y in ring],
            "tags": {},
            "editable": False,
        }


def _geometry_coordinates(geometry_type: str, value: Any, label: str) -> Any:
    if geometry_type == "Point":
        return list(_coordinate_pair(value, f"{label}.geometry.coordinates"))
    if geometry_type == "LineString":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
            raise ValueError(f"{label} LineString requires at least two coordinate pairs.")
        return [list(_coordinate_pair(item, f"{label}.geometry.coordinates")) for item in value]
    if geometry_type == "Polygon":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 1:
            raise ValueError(f"{label} Polygon must contain exactly one exterior ring; holes are unsupported.")
        ring = [list(_coordinate_pair(item, f"{label}.geometry.coordinates[0]")) for item in value[0]]
        ring = _closed_ring(ring)
        if len(ring) < 4 or abs(_signed_area(ring)) <= 1e-12:
            raise ValueError(f"{label} Polygon ring is degenerate.")
        return [ring]
    raise ValueError(f"Unsupported geometry type: {geometry_type}")


def _iter_coordinate_pairs(geometry_type: str, coordinates: Any) -> Iterable[Tuple[float, float]]:
    if geometry_type == "Point":
        yield float(coordinates[0]), float(coordinates[1])
    elif geometry_type == "LineString":
        for pair in coordinates:
            yield float(pair[0]), float(pair[1])
    elif geometry_type == "Polygon":
        for pair in coordinates[0]:
            yield float(pair[0]), float(pair[1])


def _coordinate_pair(value: Any, label: str) -> Tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        raise ValueError(f"{label} must be a coordinate pair.")
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numeric values.") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError(f"{label} must contain finite values.")
    return x, y


def _closed_ring(points: Sequence[Sequence[float]]) -> List[List[float]]:
    ring = [[float(point[0]), float(point[1])] for point in points]
    if ring and ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    return ring


def _signed_area(ring: Sequence[Sequence[float]]) -> float:
    return 0.5 * sum(
        float(ring[index][0]) * float(ring[index + 1][1])
        - float(ring[index + 1][0]) * float(ring[index][1])
        for index in range(max(0, len(ring) - 1))
    )


def _source_id(value: Any) -> str:
    text = _SOURCE_ID_RE.sub("-", str(value or "scene-source").strip()).strip(".-_")
    if not text:
        text = "scene-source"
    return text[:96]


def _positive_number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be positive and finite.")
    return result


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer.") from exc
    if result <= 0 or result > 100_000:
        raise ValueError(f"{label} must be between 1 and 100000.")
    return result


def _optional_positive(value: Any, default: float) -> float:
    if value in {None, ""}:
        return default
    return _positive_number(value, "feature width")


def _building_height_metadata(
    properties: Mapping[str, Any],
    tags: Mapping[str, Any],
) -> Dict[str, Any]:
    """Persist deterministic height inputs with an approved footprint."""

    def _number(*values: Any) -> float | None:
        for value in values:
            if value in (None, ""):
                continue
            text = str(value).lower().replace("meters", "").replace("meter", "").replace("m", "").strip()
            try:
                number = float(text)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0.0:
                return number
        return None

    declared_height_m = _number(properties.get("height"), tags.get("height"))
    levels = _number(
        properties.get("building:levels"),
        properties.get("building_levels"),
        tags.get("building:levels"),
    )
    height_m = declared_height_m
    if height_m is None and levels is not None:
        height_m = max(3.0, levels * 3.0)
    payload: Dict[str, Any] = {"height_source": "class_rule"}
    if height_m is not None:
        payload.update({
            "target_height_m": float(height_m),
            "height_source": "osm.height" if declared_height_m is not None else "osm.building_levels",
        })
    if levels is not None:
        payload["building_levels"] = float(levels)
    return payload


def _nonnegative_int(value: Any, default: int) -> int:
    if value in {None, ""}:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("lane counts must be integers.") from exc
    if result < 0 or result > 32:
        raise ValueError("lane counts must be between 0 and 32.")
    return result


def _osm_lane_defaults(properties: Mapping[str, Any]) -> Dict[str, int]:
    """Translate common OSM lane tags into RoadGen3D's directional profile."""

    tags = properties.get("tags")
    tags = tags if isinstance(tags, Mapping) else {}

    def value(name: str) -> Any:
        return properties.get(name) if properties.get(name) not in {None, ""} else tags.get(name)

    def count(raw: Any) -> int | None:
        if raw in {None, ""}:
            return None
        match = re.search(r"\d+", str(raw))
        return int(match.group(0)) if match else None

    total = count(value("lanes"))
    forward = count(value("lanes:forward"))
    reverse = count(value("lanes:backward"))
    oneway = str(value("oneway") or "").strip().lower() in {"yes", "1", "true"}
    if forward is None and reverse is None:
        if oneway:
            forward, reverse = total or 1, 0
        elif total:
            forward, reverse = max(1, (total + 1) // 2), total // 2
        else:
            forward, reverse = 1, 1
    else:
        forward = forward if forward is not None else max(0, (total or 0) - (reverse or 0))
        reverse = reverse if reverse is not None else max(0, (total or 0) - (forward or 0))
        if not oneway and forward == 0 and reverse == 0:
            forward, reverse = 1, 1

    cycleway = " ".join(str(value(name) or "") for name in ("cycleway", "cycleway:left", "cycleway:right"))
    bus_lanes = value("bus:lanes") or value("busway")
    parking = value("parking:lane") or value("parking:lane:left") or value("parking:lane:right")
    return {
        "forward_drive_lane_count": int(forward),
        "reverse_drive_lane_count": int(reverse),
        "bike_lane_count": 1 if cycleway.strip() and cycleway.strip().lower() not in {"no", "none"} else 0,
        "bus_lane_count": 1 if bus_lanes and str(bus_lanes).lower() not in {"no", "none"} else 0,
        "parking_lane_count": 1 if parking and str(parking).lower() not in {"no", "none"} else 0,
    }


def _bbox(value: Any) -> Tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise ValueError("image.bbox_wgs84 must be [west,south,east,north].")
    try:
        west, south, east, north = (float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError("image.bbox_wgs84 must contain numeric values.") from exc
    if not all(math.isfinite(item) for item in (west, south, east, north)):
        raise ValueError("image.bbox_wgs84 must contain finite values.")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("image.bbox_wgs84 is outside WGS84 bounds or reversed.")
    return west, south, east, north


def _canonical_source_bytes(annotation: Mapping[str, Any]) -> bytes:
    import json

    return json.dumps(annotation, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
