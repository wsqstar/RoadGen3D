"""Rule-based semantic context for OSM multiblock generation."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .osm_ingest import OsmLandUsePolygon, OsmRoad, OsmSemanticBlock, ProjectedFeatures
from .poi_taxonomy import extract_poi_points_by_type
from .street_priors import FURNITURE_SCENE_MAX_COUNTS

OSM_SEMANTIC_RULESET_VERSION = "landuse_rules_v1"
OSM_CONTEXT_FIT_RULESET_VERSION = "socioeconomic_fit_v1"
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

_PROFILE_CONTEXT_RULES: Dict[str, Dict[str, Any]] = {
    "child_friendly_school": {
        "socioeconomic_context": "education_child_serving",
        "design_direction": "child_safety_upgrade",
        "min_sidewalk_width_m": 3.0,
        "facility_groups": (("crossing", "traffic_signals"), ("bollard",)),
        "threshold": 0.76,
    },
    "walkable_commercial": {
        "socioeconomic_context": "active_commercial_frontage",
        "design_direction": "commercial_walkability_upgrade",
        "min_sidewalk_width_m": 3.2,
        "facility_groups": (("crossing", "traffic_signals"), ("entrance", "post_box", "waste_basket")),
        "threshold": 0.74,
    },
    "vehicle_access_commercial": {
        "socioeconomic_context": "commercial_vehicle_access",
        "design_direction": "vehicle_access_upgrade",
        "min_sidewalk_width_m": 2.0,
        "facility_groups": (("parking_entrance",),),
        "threshold": 0.84,
    },
    "transit_priority": {
        "socioeconomic_context": "transit_corridor",
        "design_direction": "transit_access_upgrade",
        "min_sidewalk_width_m": 3.0,
        "facility_groups": (("bus_stop", "subway_entrance"), ("crossing", "traffic_signals")),
        "threshold": 0.72,
    },
    "green_walkable": {
        "socioeconomic_context": "green_recreation",
        "design_direction": "green_walkability_upgrade",
        "min_sidewalk_width_m": 2.8,
        "facility_groups": (("crossing", "bollard"),),
        "threshold": 0.68,
    },
    "quiet_residential": {
        "socioeconomic_context": "residential_neighborhood",
        "design_direction": "residential_comfort_upgrade",
        "min_sidewalk_width_m": 2.4,
        "facility_groups": (),
        "threshold": 0.62,
    },
}

_DIRECTION_PATCHES: Dict[str, Dict[str, Any]] = {
    "child_safety_upgrade": {
        "design_rule_profile": "pedestrian_priority_v1",
        "objective_profile": "greening",
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "medium",
        "vehicle_demand_level": "low",
        "style_preset": "lush_walkable_v1",
        "density": 1.15,
        "minimum_category_presence": ("lamp", "bench", "trash", "bollard"),
        "optional_category_presence": ("tree", "hydrant"),
    },
    "commercial_walkability_upgrade": {
        "design_rule_profile": "pedestrian_priority_v1",
        "objective_profile": "commerce",
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "medium",
        "vehicle_demand_level": "medium",
        "style_preset": "civic_clean_v1",
        "density": 1.25,
        "minimum_category_presence": ("lamp", "bench", "trash"),
        "optional_category_presence": ("tree", "mailbox", "bollard"),
    },
    "vehicle_access_upgrade": {
        "design_rule_profile": "balanced_complete_street_v1",
        "objective_profile": "balanced",
        "ped_demand_level": "medium",
        "bike_demand_level": "low",
        "transit_demand_level": "low",
        "vehicle_demand_level": "high",
        "style_preset": "civic_clean_v1",
        "density": 0.8,
        "minimum_category_presence": ("lamp", "trash"),
        "optional_category_presence": ("hydrant", "mailbox"),
    },
    "transit_access_upgrade": {
        "design_rule_profile": "transit_priority_v1",
        "objective_profile": "transit",
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "high",
        "vehicle_demand_level": "medium",
        "style_preset": "transit_modern_v1",
        "density": 1.1,
        "minimum_category_presence": ("lamp", "bench", "trash"),
        "optional_category_presence": ("bus_stop", "tree"),
    },
    "green_walkability_upgrade": {
        "design_rule_profile": "pedestrian_priority_v1",
        "objective_profile": "greening",
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "low",
        "vehicle_demand_level": "low",
        "style_preset": "lush_walkable_v1",
        "density": 1.0,
        "minimum_category_presence": ("tree", "lamp", "bench", "trash"),
        "optional_category_presence": ("bollard",),
    },
    "residential_comfort_upgrade": {
        "design_rule_profile": "pedestrian_priority_v1",
        "objective_profile": "greening",
        "ped_demand_level": "medium",
        "bike_demand_level": "low",
        "transit_demand_level": "low",
        "vehicle_demand_level": "low",
        "style_preset": "lush_walkable_v1",
        "density": 1.0,
        "minimum_category_presence": ("lamp", "bench", "trash"),
        "optional_category_presence": ("tree", "hydrant"),
    },
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


def road_length_m(road: OsmRoad) -> float:
    coords = list(getattr(road, "coords", []) or [])
    return sum(_distance(coords[idx], coords[idx + 1]) for idx in range(max(0, len(coords) - 1)))


def road_display_name(road: OsmRoad) -> str:
    tags = dict(getattr(road, "tags", {}) or {})
    for key in ("name", "name:zh", "name:zh-Hans", "name:en", "ref"):
        value = str(tags.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _point_at_station(coords: Sequence[Tuple[float, float]], station_m: float) -> Tuple[float, float]:
    if not coords:
        return (0.0, 0.0)
    if len(coords) == 1:
        return (float(coords[0][0]), float(coords[0][1]))
    target = max(0.0, float(station_m))
    travelled = 0.0
    for idx in range(len(coords) - 1):
        start = (float(coords[idx][0]), float(coords[idx][1]))
        end = (float(coords[idx + 1][0]), float(coords[idx + 1][1]))
        span = _distance(start, end)
        if span <= 1e-9:
            continue
        if travelled + span >= target:
            ratio = (target - travelled) / span
            return (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        travelled += span
    last = coords[-1]
    return (float(last[0]), float(last[1]))


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
    return road_length_m(road)


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


def _coerce_text_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    else:
        raw_items = list(value) if isinstance(value, Sequence) else [value]
    return tuple(dict.fromkeys(str(item).strip() for item in raw_items if str(item).strip()))


def _road_name_matches(road: OsmRoad, allowed_names: Sequence[str]) -> bool:
    if not allowed_names:
        return True
    tags = {str(key): str(value).strip() for key, value in dict(getattr(road, "tags", {}) or {}).items()}
    candidates = {
        road_display_name(road),
        tags.get("name", ""),
        tags.get("name:zh", ""),
        tags.get("name:zh-Hans", ""),
        tags.get("name:en", ""),
        tags.get("ref", ""),
    }
    normalized_candidates = {item.strip().lower() for item in candidates if item.strip()}
    return any(str(name).strip().lower() in normalized_candidates for name in allowed_names)


def _nearest_road_distance(point: Tuple[float, float], road: OsmRoad) -> float:
    coords = list(getattr(road, "coords", []) or [])
    if len(coords) < 2:
        return float("inf")
    px, py = float(point[0]), float(point[1])
    best = float("inf")
    for idx in range(len(coords) - 1):
        ax, ay = float(coords[idx][0]), float(coords[idx][1])
        bx, by = float(coords[idx + 1][0]), float(coords[idx + 1][1])
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        ratio = 0.0 if denom <= 1e-9 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / denom))
        cx, cy = ax + ratio * vx, ay + ratio * vy
        best = min(best, math.hypot(px - cx, py - cy))
    return best


def apply_osm_bus_stop_constraints(
    projected_features: ProjectedFeatures,
    config: Any,
) -> Tuple[ProjectedFeatures, Dict[str, Any]]:
    """Constrain OSM/demo bus stops to eligible roads and scene-level caps."""

    eligible_names = _coerce_text_tuple(getattr(config, "bus_stop_eligible_road_names", ()))
    max_count = int(getattr(config, "max_bus_stops_per_scene", 0) or 0)
    if max_count <= 0:
        max_count = int(FURNITURE_SCENE_MAX_COUNTS.get("bus_stop", 2))
    allow_demo = bool(getattr(config, "allow_demo_bus_stop_when_osm_absent", False))
    roads = list(getattr(projected_features, "roads", []) or [])
    eligible_roads = [road for road in roads if _road_name_matches(road, eligible_names)]
    actual_points = [
        (float(point[0]), float(point[1]))
        for point in list(getattr(projected_features, "bus_stops", []) or [])
    ]
    selected_points = list(actual_points)
    provenance: List[Dict[str, Any]] = []

    if eligible_names and eligible_roads and actual_points:
        filtered: List[Tuple[float, float]] = []
        for point in actual_points:
            nearest = min(eligible_roads, key=lambda road: _nearest_road_distance(point, road))
            if _nearest_road_distance(point, nearest) <= 35.0:
                filtered.append(point)
        selected_points = filtered

    selected_points.sort(key=lambda point: (float(point[0]), float(point[1])))
    if max_count > 0:
        selected_points = selected_points[:max_count]
    provenance.extend(
        {
            "source": "osm",
            "xy": [float(point[0]), float(point[1])],
        }
        for point in selected_points
    )

    demo_inferred_count = 0
    if not selected_points and allow_demo and max_count > 0 and eligible_roads:
        road = max(eligible_roads, key=road_length_m)
        length_m = road_length_m(road)
        point = _point_at_station(list(getattr(road, "coords", []) or []), length_m * 0.5)
        selected_points = [point]
        demo_inferred_count = 1
        provenance = [
            {
                "source": "demo_inferred",
                "road_id": int(getattr(road, "osm_id", 0) or 0),
                "road_name": road_display_name(road),
                "xy": [float(point[0]), float(point[1])],
            }
        ]

    poi_points = dict(getattr(projected_features, "poi_points_by_type", {}) or {})
    poi_points["bus_stop"] = list(selected_points)
    projected_features.bus_stops = list(selected_points)
    projected_features.poi_points_by_type = poi_points

    summary = {
        "counts": {
            "osm": int(len(selected_points) - demo_inferred_count),
            "demo_inferred": int(demo_inferred_count),
            "total": int(len(selected_points)),
            "raw_osm": int(len(actual_points)),
        },
        "max_bus_stops_per_scene": int(max_count),
        "eligible_road_names": list(eligible_names),
        "eligible_road_ids": [int(getattr(road, "osm_id", 0) or 0) for road in eligible_roads],
        "provenance": provenance,
    }
    return projected_features, summary


def _facility_group_label(group: Sequence[str]) -> str:
    return "_or_".join(str(item) for item in group)


def _direction_patch_for_config(direction: str, config: Any) -> Dict[str, Any]:
    base_patch = dict(_DIRECTION_PATCHES.get(str(direction), {}))
    if not base_patch:
        return {}
    min_width_by_direction = {
        "child_safety_upgrade": 3.2,
        "commercial_walkability_upgrade": 3.4,
        "vehicle_access_upgrade": 2.2,
        "transit_access_upgrade": 3.2,
        "green_walkability_upgrade": 3.0,
        "residential_comfort_upgrade": 2.8,
    }
    current_sidewalk = float(getattr(config, "sidewalk_width_m", 0.0) or 0.0)
    min_sidewalk = float(min_width_by_direction.get(str(direction), current_sidewalk) or current_sidewalk)
    base_patch["sidewalk_width_m"] = max(current_sidewalk, min_sidewalk)
    if str(direction) == "vehicle_access_upgrade":
        current_density = float(getattr(config, "density", 1.0) or 1.0)
        base_patch["density"] = min(current_density, float(base_patch["density"]))
    else:
        current_density = float(getattr(config, "density", 1.0) or 1.0)
        base_patch["density"] = max(current_density, float(base_patch["density"]))
    return base_patch


def _segment_context_fit_payload(node: Any, config: Any) -> Dict[str, Any] | None:
    profile_id = str(getattr(node, "semantic_profile_id", "") or "").strip()
    if not profile_id:
        return None
    rule = dict(_PROFILE_CONTEXT_RULES.get(profile_id) or {})
    if not rule:
        return None

    poi_types = tuple(sorted({str(item).strip().lower() for item in getattr(node, "poi_types", ()) or () if str(item).strip()}))
    poi_set = set(poi_types)
    sidewalk_width_m = float(getattr(config, "sidewalk_width_m", 0.0) or 0.0)
    road_width_m = float(getattr(node, "road_width_m", 0.0) or getattr(config, "road_width_m", 0.0) or 0.0)
    lane_count = int(getattr(config, "lane_count", 0) or 0)
    min_sidewalk_width_m = float(rule.get("min_sidewalk_width_m", 0.0) or 0.0)

    score = 1.0
    missing_facilities: List[str] = []
    reasons: List[str] = []
    if min_sidewalk_width_m > 0.0 and sidewalk_width_m < min_sidewalk_width_m:
        deficit = min((min_sidewalk_width_m - sidewalk_width_m) / max(min_sidewalk_width_m, 1.0), 1.0)
        score -= 0.28 * max(deficit, 0.35)
        reasons.append(f"sidewalk_width_m<{min_sidewalk_width_m:.1f}")

    facility_groups = tuple(tuple(str(item) for item in group) for group in rule.get("facility_groups", ()) or ())
    for group in facility_groups:
        if not set(group) & poi_set:
            score -= 0.18
            missing_facilities.append(_facility_group_label(group))
    if missing_facilities:
        reasons.append("missing_context_facilities")

    if profile_id == "child_friendly_school" and lane_count > 2:
        score -= 0.12
        reasons.append("school_context_with_multi_lane_carriageway")
    if profile_id == "walkable_commercial" and road_width_m > 10.0 and not {"crossing", "traffic_signals"} & poi_set:
        score -= 0.10
        reasons.append("wide_commercial_road_without_crossing_signal")
    if profile_id == "vehicle_access_commercial" and road_width_m < 5.5:
        score -= 0.10
        reasons.append("vehicle_access_context_with_narrow_service_road")

    score = max(0.0, min(1.0, score))
    threshold = float(rule.get("threshold", 0.7) or 0.7)
    under_provisioned = score < threshold
    direction = str(rule.get("design_direction", "") or "")
    patch = _direction_patch_for_config(direction, config) if under_provisioned else {}
    return {
        "segment_id": str(getattr(node, "segment_id", "")),
        "road_id": int(getattr(node, "road_id", 0) or 0),
        "semantic_profile_id": profile_id,
        "socioeconomic_context": str(rule.get("socioeconomic_context", "") or ""),
        "fit_score": round(score, 4),
        "fit_threshold": round(threshold, 4),
        "is_under_provisioned": bool(under_provisioned),
        "design_direction": direction if under_provisioned else "",
        "missing_facilities": missing_facilities,
        "reasons": reasons,
        "current_supply": {
            "sidewalk_width_m": float(sidewalk_width_m),
            "road_width_m": float(road_width_m),
            "lane_count": int(lane_count),
            "poi_types": list(poi_types),
        },
        "expected_supply": {
            "min_sidewalk_width_m": float(min_sidewalk_width_m),
            "facility_groups": [list(group) for group in facility_groups],
        },
        "recommended_compose_patch": patch,
    }


def evaluate_osm_context_fit(
    road_segment_graph: Any,
    config: Any,
    *,
    include_segments: bool = True,
) -> Dict[str, Any]:
    """Compare OSM road supply with surrounding landuse/POI socioeconomic context."""

    nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
    segment_payloads = [
        item
        for item in (_segment_context_fit_payload(node, config) for node in nodes)
        if item is not None
    ]
    under_segments = [item for item in segment_payloads if bool(item.get("is_under_provisioned"))]
    direction_counts = Counter(str(item.get("design_direction", "")) for item in under_segments if item.get("design_direction"))
    length_by_segment = {
        str(getattr(node, "segment_id", "")): float(getattr(node, "length_m", 0.0) or 0.0)
        for node in nodes
    }
    direction_lengths: Dict[str, float] = {}
    for item in under_segments:
        direction = str(item.get("design_direction", "") or "")
        if not direction:
            continue
        direction_lengths[direction] = direction_lengths.get(direction, 0.0) + float(length_by_segment.get(str(item.get("segment_id")), 0.0))
    dominant_direction = ""
    if direction_lengths:
        dominant_direction = max(direction_lengths, key=lambda direction: (direction_lengths[direction], direction_counts.get(direction, 0), direction))
    scene_patch = _direction_patch_for_config(dominant_direction, config) if dominant_direction else {}

    road_acc: Dict[int, Dict[str, Any]] = {}
    for item in segment_payloads:
        road_id = int(item.get("road_id", 0) or 0)
        acc = road_acc.setdefault(
            road_id,
            {
                "road_id": road_id,
                "segment_count": 0,
                "under_provisioned_segment_count": 0,
                "fit_score_sum": 0.0,
                "directions": Counter(),
                "missing_facilities": Counter(),
            },
        )
        acc["segment_count"] += 1
        acc["fit_score_sum"] += float(item.get("fit_score", 0.0) or 0.0)
        if item.get("is_under_provisioned"):
            acc["under_provisioned_segment_count"] += 1
            direction = str(item.get("design_direction", "") or "")
            if direction:
                acc["directions"][direction] += 1
            for facility in list(item.get("missing_facilities", []) or []):
                acc["missing_facilities"][str(facility)] += 1

    roads: List[Dict[str, Any]] = []
    for road_id, acc in sorted(road_acc.items()):
        segment_count = int(acc["segment_count"])
        dominant = ""
        if acc["directions"]:
            dominant = max(acc["directions"], key=lambda direction: (acc["directions"][direction], direction))
        roads.append(
            {
                "road_id": int(road_id),
                "segment_count": segment_count,
                "under_provisioned_segment_count": int(acc["under_provisioned_segment_count"]),
                "avg_fit_score": round(float(acc["fit_score_sum"]) / float(segment_count), 4) if segment_count else 0.0,
                "dominant_design_direction": dominant,
                "missing_facility_counts": dict(acc["missing_facilities"]),
            }
        )

    assessed_count = len(segment_payloads)
    avg_score = (
        sum(float(item.get("fit_score", 0.0) or 0.0) for item in segment_payloads) / float(assessed_count)
        if assessed_count
        else 0.0
    )
    payload = {
        "ruleset": OSM_CONTEXT_FIT_RULESET_VERSION,
        "mode": str(getattr(config, "osm_context_fit_mode", "auto_design") or "auto_design"),
        "assessed_segment_count": int(assessed_count),
        "under_provisioned_segment_count": int(len(under_segments)),
        "under_provisioned_ratio": round(float(len(under_segments)) / float(assessed_count), 4) if assessed_count else 0.0,
        "avg_fit_score": round(avg_score, 4),
        "direction_counts": dict(direction_counts),
        "direction_length_m": {key: round(float(value), 3) for key, value in sorted(direction_lengths.items())},
        "dominant_design_direction": dominant_direction,
        "scene_recommended_compose_patch": scene_patch,
        "auto_design_would_apply": bool(dominant_direction and str(getattr(config, "osm_context_fit_mode", "auto_design") or "auto_design") == "auto_design"),
        "roads": roads,
    }
    if include_segments:
        payload["segments"] = segment_payloads
    return payload


def segment_semantic_profile_payload(nodes: Sequence[Any]) -> Tuple[Dict[str, Any], ...]:
    return tuple(
        {
            "segment_id": str(getattr(node, "segment_id", "")),
            "road_id": int(getattr(node, "road_id", 0) or 0),
            "semantic_profile_id": str(getattr(node, "semantic_profile_id", "") or ""),
            "skeleton_design_profile": str(getattr(node, "skeleton_design_profile", "") or getattr(node, "semantic_profile_id", "") or ""),
            "skeleton_design_profile_source": str(getattr(node, "skeleton_design_profile_source", "") or ("osm" if getattr(node, "semantic_profile_id", "") else "")),
            "semantic_block_id": str(getattr(node, "semantic_block_id", "") or ""),
            "semantic_confidence": float(getattr(node, "semantic_confidence", 0.0) or 0.0),
            "skeleton_design_profile_confidence": float(getattr(node, "skeleton_design_profile_confidence", 0.0) or getattr(node, "semantic_confidence", 0.0) or 0.0),
            "semantic_reasons": list(getattr(node, "semantic_reasons", ()) or ()),
            "skeleton_design_profile_reasons": list(getattr(node, "skeleton_design_profile_reasons", ()) or getattr(node, "semantic_reasons", ()) or ()),
        }
        for node in nodes
    )
