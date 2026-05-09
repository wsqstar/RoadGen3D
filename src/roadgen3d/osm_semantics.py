"""Rule-based semantic context for OSM multiblock generation."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .osm_ingest import OsmLandUsePolygon, OsmRoad, OsmSemanticBlock, ProjectedFeatures
from .poi_taxonomy import extract_poi_points_by_type

OSM_SEMANTIC_RULESET_VERSION = "landuse_rules_v1"
DEFAULT_MULTIBLOCK_MAX_ROADS = 12
DEFAULT_MULTIBLOCK_MAX_EXTENT_M = 350.0

_EDUCATION_AMENITIES = {"kindergarten", "school", "childcare", "college", "university"}
_COMMERCIAL_LANDUSES = {"commercial", "retail"}
_COMMERCIAL_AMENITIES = {"bar", "bank", "cafe", "fast_food", "food_court", "marketplace", "pharmacy", "pub", "restaurant"}
_GREEN_LANDUSES = {"forest", "grass", "greenfield", "meadow", "recreation_ground", "village_green"}
_GREEN_LEISURE = {"garden", "park", "pitch", "playground", "recreation_ground"}
_VEHICLE_AMENITIES = {"car_rental", "car_sharing", "car_wash", "fuel", "parking", "parking_entrance"}

_PROFILE_THEME = {
    "child_friendly_school": "green",
    "walkable_commercial": "commercial",
    "vehicle_access_commercial": "commercial",
    "transit_priority": "transit",
    "green_walkable": "green",
    "quiet_residential": "residential",
}

_PROFILE_STYLE = {
    "child_friendly_school": {"design_rule_profile": "pedestrian_priority_v1", "style_preset": "lush_walkable_v1"},
    "walkable_commercial": {"design_rule_profile": "pedestrian_priority_v1", "style_preset": "civic_clean_v1"},
    "vehicle_access_commercial": {"design_rule_profile": "balanced_complete_street_v1", "style_preset": "civic_clean_v1"},
    "transit_priority": {"design_rule_profile": "transit_priority_v1", "style_preset": "transit_modern_v1"},
    "green_walkable": {"design_rule_profile": "pedestrian_priority_v1", "style_preset": "lush_walkable_v1"},
    "quiet_residential": {"design_rule_profile": "pedestrian_priority_v1", "style_preset": "lush_walkable_v1"},
}


def semantic_profile_to_theme(profile_id: str) -> str:
    return _PROFILE_THEME.get(str(profile_id or "").strip(), "commercial")


def semantic_profile_style(profile_id: str) -> Dict[str, str]:
    return dict(_PROFILE_STYLE.get(str(profile_id or "").strip(), _PROFILE_STYLE["walkable_commercial"]))


def _clean_tags(tags: Mapping[str, Any]) -> Dict[str, str]:
    return {str(key): str(value).strip().lower() for key, value in dict(tags or {}).items()}


def _polygon_area(coords: Sequence[Tuple[float, float]]) -> float:
    if len(coords) < 3:
        return 0.0
    area = 0.0
    ring = list(coords)
    for idx, point in enumerate(ring):
        nxt = ring[(idx + 1) % len(ring)]
        area += float(point[0]) * float(nxt[1]) - float(nxt[0]) * float(point[1])
    return abs(area) / 2.0


def _centroid(coords: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    if not coords:
        return (0.0, 0.0)
    ring = list(coords)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    return (
        sum(float(point[0]) for point in ring) / float(len(ring)),
        sum(float(point[1]) for point in ring) / float(len(ring)),
    )


def _point_in_polygon(point: Tuple[float, float], polygon: Sequence[Tuple[float, float]]) -> bool:
    x, y = float(point[0]), float(point[1])
    inside = False
    ring = list(polygon)
    if len(ring) < 3:
        return False
    j = len(ring) - 1
    for i, pi in enumerate(ring):
        xi, yi = float(pi[0]), float(pi[1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _semantic_counts_in_polygon(
    polygon: Sequence[Tuple[float, float]],
    semantic_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for point_type, points in semantic_points_by_type.items():
        counts[str(point_type)] = sum(1 for point in points if _point_in_polygon(point, polygon))
    return counts


def _classify_from_tags_and_counts(
    *,
    tags: Mapping[str, Any],
    counts: Mapping[str, int],
    highway_type: str = "",
    poi_types: Sequence[str] = (),
) -> Tuple[str, List[str], float]:
    clean = _clean_tags(tags)
    reasons: List[str] = []
    amenity = clean.get("amenity", "")
    landuse = clean.get("landuse", "")
    leisure = clean.get("leisure", "")
    highway = str(highway_type or "").strip().lower()
    poi_set = {str(item).strip().lower() for item in poi_types}

    education_count = int(counts.get("education", 0) or 0)
    commercial_count = int(counts.get("commercial", 0) or 0)
    transit_count = int(counts.get("transit", 0) or 0)
    green_count = int(counts.get("green", 0) or 0)
    vehicle_count = int(counts.get("vehicle_access", 0) or 0)

    if amenity in _EDUCATION_AMENITIES or education_count > 0:
        reasons.append("education amenity or school/kindergarten POI")
        return "child_friendly_school", reasons, 0.94

    if transit_count > 0 or {"bus_stop", "subway_entrance"} & poi_set:
        reasons.append("transit POI present")
        return "transit_priority", reasons, 0.88

    commercial_tag = bool(
        landuse in _COMMERCIAL_LANDUSES
        or amenity in _COMMERCIAL_AMENITIES
        or clean.get("shop")
        or clean.get("office")
        or clean.get("tourism")
    )
    if commercial_tag and (vehicle_count > 0 or commercial_count < 2):
        reasons.append("commercial context with sparse pedestrian POI or vehicle access")
        return "vehicle_access_commercial", reasons, 0.78
    if commercial_tag or commercial_count >= 2:
        reasons.append("commercial land use or dense commercial POI")
        return "walkable_commercial", reasons, 0.84

    if landuse in _GREEN_LANDUSES or leisure in _GREEN_LEISURE or green_count > 0:
        reasons.append("green or leisure land use")
        return "green_walkable", reasons, 0.82

    if landuse == "residential" or highway in {"residential", "living_street"}:
        reasons.append("residential context")
        return "quiet_residential", reasons, 0.74

    if highway in {"primary", "secondary"} and not poi_set:
        reasons.append("higher-order road with sparse local POI")
        return "vehicle_access_commercial", reasons, 0.62

    reasons.append("default residential-like neighborhood profile")
    return "quiet_residential", reasons, 0.55


def classify_semantic_block(
    block: OsmSemanticBlock,
    *,
    semantic_points_by_type: Mapping[str, Sequence[Tuple[float, float]]] | None = None,
) -> OsmSemanticBlock:
    counts = _semantic_counts_in_polygon(block.coords, semantic_points_by_type or {})
    profile_id, reasons, confidence = _classify_from_tags_and_counts(tags=block.tags, counts=counts)
    return replace(
        block,
        semantic_profile_id=profile_id,
        semantic_reasons=reasons,
        confidence=float(confidence),
        poi_counts=counts,
    )


def classify_projected_semantic_blocks(projected_features: ProjectedFeatures) -> Tuple[OsmSemanticBlock, ...]:
    blocks = list(getattr(projected_features, "semantic_blocks", []) or [])
    if not blocks:
        blocks = [
            OsmSemanticBlock(
                block_id=f"osm_block_{idx:03d}",
                osm_id=int(polygon.osm_id),
                source_type=polygon.source_type,
                coords=list(polygon.coords),
                centroid=_centroid(polygon.coords),
                tags=dict(polygon.tags),
            )
            for idx, polygon in enumerate(getattr(projected_features, "land_use_polygons", []) or [])
        ]
    if not blocks:
        blocks = list(_fallback_blocks_from_roads(projected_features))
    semantic_points = getattr(projected_features, "semantic_points_by_type", {}) or {}
    return tuple(
        classify_semantic_block(block, semantic_points_by_type=semantic_points)
        for block in blocks
        if _polygon_area(block.coords) >= 1.0
    )


def semantic_profile_for_segment(
    *,
    highway_type: str,
    poi_types: Sequence[str],
    semantic_block: OsmSemanticBlock | None = None,
) -> Tuple[str, Tuple[str, ...], float, str]:
    if semantic_block is not None and semantic_block.semantic_profile_id:
        return (
            str(semantic_block.semantic_profile_id),
            tuple(str(item) for item in semantic_block.semantic_reasons),
            float(semantic_block.confidence),
            str(semantic_block.block_id),
        )
    profile_id, reasons, confidence = _classify_from_tags_and_counts(
        tags={},
        counts={},
        highway_type=highway_type,
        poi_types=poi_types,
    )
    return profile_id, tuple(reasons), float(confidence), ""


def nearest_semantic_block(
    point: Tuple[float, float],
    blocks: Sequence[OsmSemanticBlock],
    *,
    max_distance_m: float = 90.0,
) -> OsmSemanticBlock | None:
    if not blocks:
        return None
    inside = [block for block in blocks if _point_in_polygon(point, block.coords)]
    if inside:
        return max(inside, key=lambda block: float(block.confidence))
    nearest = min(blocks, key=lambda block: _distance(point, block.centroid))
    if _distance(point, nearest.centroid) <= float(max_distance_m):
        return nearest
    return None


def _fallback_blocks_from_roads(projected_features: ProjectedFeatures) -> Tuple[OsmSemanticBlock, ...]:
    buffer_m = 35.0
    blocks: List[OsmSemanticBlock] = []
    for index, road in enumerate(list(getattr(projected_features, "roads", []) or [])):
        coords = list(getattr(road, "coords", []) or [])
        if len(coords) < 2:
            continue
        xs = [float(point[0]) for point in coords]
        ys = [float(point[1]) for point in coords]
        minx, maxx = min(xs) - buffer_m, max(xs) + buffer_m
        miny, maxy = min(ys) - buffer_m, max(ys) + buffer_m
        polygon = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
        blocks.append(
            OsmSemanticBlock(
                block_id=f"road_buffer_block_{index:03d}",
                osm_id=int(getattr(road, "osm_id", index)),
                source_type="road_buffer",
                coords=polygon,
                centroid=_centroid(polygon),
                tags={"source": "road_buffer", "highway": str(getattr(road, "highway_type", ""))},
            )
        )
    return tuple(blocks)


def _road_center(road: OsmRoad) -> Tuple[float, float]:
    coords = list(getattr(road, "coords", []) or [])
    if not coords:
        return (0.0, 0.0)
    return (
        sum(float(point[0]) for point in coords) / float(len(coords)),
        sum(float(point[1]) for point in coords) / float(len(coords)),
    )


def _road_length(road: OsmRoad) -> float:
    coords = list(getattr(road, "coords", []) or [])
    return sum(_distance(coords[idx], coords[idx + 1]) for idx in range(max(0, len(coords) - 1)))


def _extent_ok(roads: Sequence[OsmRoad], max_extent_m: float) -> bool:
    coords = [point for road in roads for point in list(getattr(road, "coords", []) or [])]
    if not coords:
        return False
    xs = [float(point[0]) for point in coords]
    ys = [float(point[1]) for point in coords]
    return max(max(xs) - min(xs), max(ys) - min(ys)) <= float(max_extent_m)


def _roads_touch(a: OsmRoad, b: OsmRoad, tolerance_m: float = 8.0) -> bool:
    a_coords = list(getattr(a, "coords", []) or [])
    b_coords = list(getattr(b, "coords", []) or [])
    if len(a_coords) < 2 or len(b_coords) < 2:
        return False
    a_ends = (a_coords[0], a_coords[-1])
    b_ends = (b_coords[0], b_coords[-1])
    return min(_distance(pa, pb) for pa in a_ends for pb in b_ends) <= float(tolerance_m)


def select_multiblock_roads(
    roads: Sequence[OsmRoad],
    *,
    bbox_m: Tuple[float, float, float, float],
    max_roads: int = DEFAULT_MULTIBLOCK_MAX_ROADS,
    max_extent_m: float = DEFAULT_MULTIBLOCK_MAX_EXTENT_M,
) -> Tuple[OsmRoad, ...]:
    candidates = [road for road in roads if len(getattr(road, "coords", []) or []) >= 2]
    if not candidates:
        return tuple()
    center = ((float(bbox_m[0]) + float(bbox_m[2])) / 2.0, (float(bbox_m[1]) + float(bbox_m[3])) / 2.0)
    ordered = sorted(candidates, key=lambda road: (_distance(_road_center(road), center), -_road_length(road), int(getattr(road, "osm_id", 0))))
    selected: List[OsmRoad] = [ordered[0]]
    remaining = ordered[1:]
    max_count = max(1, int(max_roads))
    while remaining and len(selected) < max_count:
        connected = [road for road in remaining if any(_roads_touch(road, item) for item in selected)]
        pool = connected or remaining
        added = False
        for road in list(pool):
            candidate_selection = [*selected, road]
            if len(selected) == 0 or _extent_ok(candidate_selection, max_extent_m):
                selected.append(road)
                remaining.remove(road)
                added = True
                break
            if road in remaining:
                remaining.remove(road)
        if not added and not connected:
            break
    return tuple(selected)


def prepare_multiblock_projected_features(
    projected_features: ProjectedFeatures,
    config: Any,
) -> Tuple[ProjectedFeatures, Dict[str, Any]]:
    max_roads = int(getattr(config, "osm_multiblock_max_roads", DEFAULT_MULTIBLOCK_MAX_ROADS) or DEFAULT_MULTIBLOCK_MAX_ROADS)
    max_extent_m = float(getattr(config, "osm_multiblock_max_extent_m", DEFAULT_MULTIBLOCK_MAX_EXTENT_M) or DEFAULT_MULTIBLOCK_MAX_EXTENT_M)
    selected_roads = select_multiblock_roads(
        list(getattr(projected_features, "roads", []) or []),
        bbox_m=projected_features.bbox_m,
        max_roads=max_roads,
        max_extent_m=max_extent_m,
    )
    prepared = replace(projected_features, roads=list(selected_roads))
    semantic_blocks = classify_projected_semantic_blocks(prepared)
    prepared.semantic_blocks = list(semantic_blocks)
    profile_counts = Counter(str(block.semantic_profile_id) for block in semantic_blocks if block.semantic_profile_id)
    return prepared, {
        "semantic_mode": OSM_SEMANTIC_RULESET_VERSION,
        "input_road_count": int(len(getattr(projected_features, "roads", []) or [])),
        "selected_road_count": int(len(selected_roads)),
        "selected_road_osm_ids": [int(getattr(road, "osm_id", 0) or 0) for road in selected_roads],
        "semantic_block_count": int(len(semantic_blocks)),
        "semantic_profile_counts": dict(profile_counts),
        "max_roads": int(max_roads),
        "max_extent_m": float(max_extent_m),
    }


def segment_semantic_profile_payload(nodes: Sequence[Any]) -> Tuple[Dict[str, Any], ...]:
    return tuple(
        {
            "segment_id": str(getattr(node, "segment_id", "")),
            "road_id": int(getattr(node, "road_id", 0) or 0),
            "semantic_profile_id": str(getattr(node, "semantic_profile_id", "") or ""),
            "semantic_block_id": str(getattr(node, "semantic_block_id", "") or ""),
            "semantic_confidence": float(getattr(node, "semantic_confidence", 0.0) or 0.0),
            "semantic_reasons": list(getattr(node, "semantic_reasons", ()) or ()),
        }
        for node in nodes
    )

