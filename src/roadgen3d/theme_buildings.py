"""Theme inference and surrounding-building planning utilities."""

from __future__ import annotations

import math
import hashlib
from collections import Counter
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .osm_semantics import semantic_profile_style, semantic_profile_to_theme
from .types import (
    DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    BuildingFootprint,
    GeneratedLot,
    StreetComposeConfig,
    ThemeSegment,
)

THEME_VOCAB: Tuple[str, ...] = ("residential", "commercial", "transit", "green")
THEME_PROFILE_STYLE_MAP: Dict[str, Dict[str, str]] = {
    "residential": {
        "design_rule_profile": "balanced_complete_street_v1",
        "style_preset": "lush_walkable_v1",
    },
    "commercial": {
        "design_rule_profile": "balanced_complete_street_v1",
        "style_preset": "civic_clean_v1",
    },
    "transit": {
        "design_rule_profile": "transit_priority_v1",
        "style_preset": "transit_modern_v1",
    },
    "green": {
        "design_rule_profile": "pedestrian_priority_v1",
        "style_preset": "lush_walkable_v1",
    },
}
_THEME_QUERY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "residential": ("residential", "neighborhood", "community", "housing", "apartment"),
    "commercial": ("commercial", "downtown", "retail", "office", "shopping", "civic", "urban"),
    "transit": ("transit", "bus", "metro", "station", "platform", "commuter"),
    "green": ("green", "park", "tree", "walkable", "pedestrian", "lush"),
}
_THEME_POI_BONUSES: Dict[str, Dict[str, float]] = {
    "residential": {
        "entrance": 1.1,
        "parking_entrance": 0.9,
        "bollard": 0.4,
    },
    "commercial": {
        "entrance": 0.8,
        "post_box": 1.0,
        "waste_basket": 0.8,
        "crossing": 0.6,
        "traffic_signals": 0.5,
    },
    "transit": {
        "bus_stop": 1.8,
        "subway_entrance": 1.8,
        "traffic_signals": 0.6,
        "crossing": 0.5,
    },
    "green": {
        "bollard": 0.4,
        "crossing": 0.3,
    },
}
_GRID_LOT_RULES: Dict[str, Dict[str, float]] = {
    "residential": {
        "target_frontage_m": 7.0,
        "min_frontage_m": 5.0,
        "max_frontage_m": 10.0,
        "gap_threshold_m": 14.0,
    },
    "commercial": {
        "target_frontage_m": 6.0,
        "min_frontage_m": 4.5,
        "max_frontage_m": 8.0,
        "gap_threshold_m": 12.0,
    },
    "transit": {
        "target_frontage_m": 10.0,
        "min_frontage_m": 7.0,
        "max_frontage_m": 14.0,
        "gap_threshold_m": 18.0,
    },
}
_DEFAULT_MIN_SIDE_FRONTAGE_COVERAGE_RATIO = 0.65
_DEFAULT_MAX_LEFT_RIGHT_COVERAGE_GAP = 0.10
_DEFAULT_MAX_BUFFER_WIDTH_GAP_RATIO = 0.10
_DEFAULT_MAX_STREETWALL_REFERENCE_GAP_RATIO = 0.10
_ZONING_GRANULARITY_MULTIPLIERS: Dict[str, float] = {
    "coarse": 1.5,
    "balanced": 1.0,
    "fine": 0.7,
}
_INFILL_POLICY_MULTIPLIERS: Dict[str, float] = {
    "off": float("inf"),
    "large_gap_only": 1.0,
    "balanced": 0.75,
    "aggressive": 0.55,
}
_BUILDING_JUNCTION_BUFFER_M = 1.0
_BUILDING_EXIT_DISTANCE_M = 10.0
_BUILDING_MIN_SEGMENT_SPAN_M = 1.0
_BUILDING_JUNCTION_ANCHOR_TOLERANCE_M = 1.5


def theme_profile_style(theme_name: str) -> Dict[str, str]:
    return dict(THEME_PROFILE_STYLE_MAP.get(str(theme_name), THEME_PROFILE_STYLE_MAP["commercial"]))


def infer_default_theme(query: str, target_street_type: str = "") -> str:
    text = f"{query} {target_street_type}".strip().lower()
    best_theme = "commercial"
    best_score = -1.0
    for theme_name, keywords in _THEME_QUERY_KEYWORDS.items():
        score = float(sum(1.0 for keyword in keywords if keyword in text))
        if theme_name == "commercial":
            score += 0.1
        if score > best_score:
            best_score = score
            best_theme = theme_name
    return best_theme


def infer_theme_segments(
    road_segment_graph: object | None,
    *,
    query: str,
    target_street_type: str = "",
    fallback_length_m: float = 80.0,
) -> Tuple[ThemeSegment, ...]:
    """Infer contiguous themed segments from a road segment graph."""

    if road_segment_graph is None or not getattr(road_segment_graph, "nodes", None):
        theme_name = infer_default_theme(query, target_street_type)
        spec = theme_profile_style(theme_name)
        return (
            ThemeSegment(
                theme_id="theme_000",
                theme_name=theme_name,
                x_start_m=-float(fallback_length_m) / 2.0,
                x_end_m=float(fallback_length_m) / 2.0,
                center_x_m=0.0,
                length_m=float(fallback_length_m),
                design_rule_profile=spec["design_rule_profile"],
                style_preset=spec["style_preset"],
                notes=("fallback_single_theme",),
            ),
        )

    nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
    total_length = sum(float(getattr(node, "length_m", 0.0) or 0.0) for node in nodes)
    if total_length <= 0.0:
        total_length = float(fallback_length_m)
    centered_cursor = -total_length / 2.0
    node_info: List[Tuple[object, str, float, float, float]] = []
    for node in nodes:
        node_length = float(getattr(node, "length_m", 0.0) or 0.0)
        start_m = float(getattr(node, "station_start_m", centered_cursor))
        end_m = float(getattr(node, "station_end_m", start_m + node_length))
        center_m = float(getattr(node, "station_center_m", (start_m + end_m) / 2.0))
        if abs(end_m - start_m) <= 1e-6:
            start_m = centered_cursor
            end_m = centered_cursor + node_length
            center_m = (start_m + end_m) / 2.0
        theme_name = _infer_segment_theme(
            node,
            query=query,
            target_street_type=target_street_type,
        )
        node_info.append((node, theme_name, start_m, end_m, center_m))
        centered_cursor = end_m

    merged: List[ThemeSegment] = []
    current_nodes: List[object] = []
    current_theme = ""
    current_start = 0.0
    current_end = 0.0
    current_pois: set[str] = set()
    current_semantic_profiles: set[str] = set()
    for node, theme_name, start_m, end_m, _center_m in node_info:
        if not current_nodes or theme_name != current_theme:
            if current_nodes:
                merged.append(
                    _build_theme_segment(
                        idx=len(merged),
                        theme_name=current_theme,
                        start_m=current_start,
                        end_m=current_end,
                        nodes=current_nodes,
                        dominant_poi_types=tuple(sorted(current_pois)),
                        semantic_profile_ids=tuple(sorted(current_semantic_profiles)),
                    )
                )
            current_theme = theme_name
            current_nodes = [node]
            current_start = start_m
            current_end = end_m
            current_pois = set(getattr(node, "poi_types", ()) or ())
            current_semantic_profiles = {
                str(getattr(node, "semantic_profile_id", "") or "").strip()
            } - {""}
        else:
            current_nodes.append(node)
            current_end = end_m
            current_pois.update(getattr(node, "poi_types", ()) or ())
            profile_id = str(getattr(node, "semantic_profile_id", "") or "").strip()
            if profile_id:
                current_semantic_profiles.add(profile_id)

    if current_nodes:
        merged.append(
            _build_theme_segment(
                idx=len(merged),
                theme_name=current_theme,
                start_m=current_start,
                end_m=current_end,
                nodes=current_nodes,
                dominant_poi_types=tuple(sorted(current_pois)),
                semantic_profile_ids=tuple(sorted(current_semantic_profiles)),
            )
        )
    return tuple(merged)


def _build_theme_segment(
    *,
    idx: int,
    theme_name: str,
    start_m: float,
    end_m: float,
    nodes: Sequence[object],
    dominant_poi_types: Sequence[str],
    semantic_profile_ids: Sequence[str] = (),
) -> ThemeSegment:
    primary_semantic_profile = str(semantic_profile_ids[0]).strip() if semantic_profile_ids else ""
    spec = semantic_profile_style(primary_semantic_profile) if primary_semantic_profile else theme_profile_style(theme_name)
    notes = []
    if primary_semantic_profile:
        notes.append(f"semantic_profile={primary_semantic_profile}")
    if dominant_poi_types:
        notes.append("poi_driven")
    highway_types = tuple(sorted({str(getattr(node, "highway_type", "")).strip().lower() for node in nodes if str(getattr(node, "highway_type", "")).strip()}))
    if highway_types:
        notes.append(f"road_type={'/'.join(highway_types)}")
    return ThemeSegment(
        theme_id=f"theme_{idx:03d}",
        theme_name=theme_name,
        x_start_m=float(start_m),
        x_end_m=float(end_m),
        center_x_m=float((start_m + end_m) / 2.0),
        length_m=float(max(end_m - start_m, 1.0)),
        segment_ids=tuple(str(getattr(node, "segment_id", "")) for node in nodes),
        dominant_poi_types=tuple(str(item) for item in dominant_poi_types),
        semantic_profile_ids=tuple(str(item) for item in semantic_profile_ids),
        design_rule_profile=spec["design_rule_profile"],
        style_preset=spec["style_preset"],
        notes=tuple(notes),
    )


def _infer_segment_theme(
    node: object,
    *,
    query: str,
    target_street_type: str,
) -> str:
    query_lc = f"{query} {target_street_type}".strip().lower()
    semantic_profile_id = str(getattr(node, "semantic_profile_id", "") or "").strip()
    if semantic_profile_id:
        return semantic_profile_to_theme(semantic_profile_id)
    highway_type = str(getattr(node, "highway_type", "")).strip().lower()
    poi_types = tuple(str(item).strip().lower() for item in getattr(node, "poi_types", ()) or ())
    scores = {theme_name: 0.2 for theme_name in THEME_VOCAB}

    for theme_name, keywords in _THEME_QUERY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in query_lc:
                scores[theme_name] += 0.9
    for theme_name, bonuses in _THEME_POI_BONUSES.items():
        for poi_type in poi_types:
            scores[theme_name] += float(bonuses.get(poi_type, 0.0))

    if highway_type in {"residential", "living_street"}:
        scores["residential"] += 0.8
        scores["green"] += 0.25
    elif highway_type in {"service", "unclassified"}:
        scores["commercial"] += 0.35
        scores["residential"] += 0.2
    elif highway_type in {"primary", "secondary"}:
        scores["transit"] += 0.45
        scores["commercial"] += 0.4
    elif highway_type == "tertiary":
        scores["commercial"] += 0.4
        scores["residential"] += 0.35

    if not poi_types:
        scores["green"] += 0.3
    if len(poi_types) >= 2:
        scores["commercial"] += 0.2
    if {"bus_stop", "subway_entrance"} & set(poi_types):
        scores["transit"] += 0.9
    return max(THEME_VOCAB, key=lambda theme_name: (scores[theme_name], -THEME_VOCAB.index(theme_name)))


def collect_building_footprints(
    projected_features: object | None,
    *,
    placement_context: object | None,
    theme_segments: Sequence[ThemeSegment],
    road_segment_graph: object | None,
    road_buffer_m: float = 35.0,
    seed: int = 0,
    height_mode: str = "theme_random",
    height_profile: str = "urban_default_v1",
    asymmetry_strength: float = 0.0,
    left_right_bias: float = 0.0,
    front_setback_min_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    front_setback_max_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    zoning_granularity: str = "fine",
    streetwall_continuity: float = 0.95,
) -> Tuple[BuildingFootprint, ...]:
    """Collect nearby OSM building footprints or fallback proxy footprints."""

    building_regions = _normalized_building_region_records(placement_context)
    if building_regions:
        return _building_region_footprints(
            placement_context=placement_context,
            theme_segments=theme_segments,
            road_segment_graph=road_segment_graph,
            seed=seed,
            height_mode=height_mode,
            height_profile=height_profile,
        )

    try:
        from shapely.geometry import Polygon as ShapelyPolygon
    except Exception:
        return tuple()

    buildings = list(getattr(projected_features, "buildings", ()) or [])
    road_geom = getattr(placement_context, "carriageway", None)
    graph_streetwall_reference = _explicit_streetwall_reference_from_graph(road_segment_graph)
    fallback_streetwall_reference = _streetwall_reference_widths(
        design_rule_profile=str(
            getattr(placement_context, "design_rule_profile", "balanced_complete_street_v1")
            or "balanced_complete_street_v1"
        ),
        sidewalk_seed_width_m=2.5,
        placement_context=placement_context,
        asymmetry_strength=float(asymmetry_strength),
        force_streetwall_baseline=True,
    )
    buildable_corridor = _build_buildable_corridor_geometry(
        placement_context=placement_context,
        road_segment_graph=road_segment_graph,
        carriageway_width_m=float(getattr(placement_context, "carriageway_width_m", 0.0) or 0.0),
        fallback_left_streetwall_width_m=float(fallback_streetwall_reference["left_total_m"]),
        fallback_right_streetwall_width_m=float(fallback_streetwall_reference["right_total_m"]),
        road_buffer_m=float(road_buffer_m),
    )
    footprints: List[BuildingFootprint] = []
    if road_geom is not None and not getattr(road_geom, "is_empty", True):
        road_buffer = road_geom.buffer(float(road_buffer_m))
        roadside_exclusion_width_m = max(
            float(graph_streetwall_reference.get("left_total_m", 0.0) or 0.0),
            float(graph_streetwall_reference.get("right_total_m", 0.0) or 0.0),
            float(fallback_streetwall_reference.get("left_total_m", 0.0) or 0.0),
            float(fallback_streetwall_reference.get("right_total_m", 0.0) or 0.0),
            0.0,
        )
        building_exclusion_zone = (
            road_geom.buffer(float(roadside_exclusion_width_m))
            if roadside_exclusion_width_m > 1e-6
            else road_geom
        )
        for building in buildings:
            coords = tuple((float(x), float(y)) for x, y in getattr(building, "coords", ()) or ())
            building_tags = {
                str(key): str(value)
                for key, value in dict(getattr(building, "tags", {}) or {}).items()
            }
            is_white_context_massing = building_tags.get("roadgen3d_context_massing", "").lower() == "white"
            if len(coords) < 4:
                continue
            polygon = ShapelyPolygon(coords)
            if polygon.is_empty or polygon.area <= 4.0 or not polygon.intersects(road_buffer):
                continue
            if buildable_corridor is not None and not polygon.intersects(buildable_corridor):
                continue
            if (
                building_exclusion_zone is not None
                and not getattr(building_exclusion_zone, "is_empty", True)
                and float(polygon.intersection(building_exclusion_zone).area) > 1e-4
            ):
                continue
            matched_region = _last_matching_building_region_for_polygon(
                tuple((float(x), float(y)) for x, y in tuple(polygon.exterior.coords)),
                building_regions,
                polygon_geom=polygon,
            )
            if building_regions and matched_region is None:
                continue
            centroid = (float(polygon.centroid.x), float(polygon.centroid.y))
            theme_id = assign_theme_id_for_point(centroid, theme_segments, road_segment_graph)
            theme_name = _resolve_theme_key(theme_id, theme_segments)
            yaw_deg, frontage_width_m, depth_m = oriented_bounds_metrics(polygon)
            if matched_region is not None:
                yaw_deg = float(matched_region.get("yaw_deg", yaw_deg) or yaw_deg)
            fid = f"building_{len(footprints):03d}"
            if is_white_context_massing:
                fid = f"osm_context_{getattr(building, 'osm_id', len(footprints))}"
            if is_white_context_massing:
                raw_height = building_tags.get("height", "").lower().replace("meters", "").replace("meter", "").replace("m", "").strip()
                raw_levels = building_tags.get("building:levels", "").strip()
                try:
                    _th = max(3.0, float(raw_height))
                except (TypeError, ValueError):
                    try:
                        _th = max(3.0, float(raw_levels) * 3.0)
                    except (TypeError, ValueError):
                        _th = 12.0
                _hc = height_class_from_height_m(_th)
            elif height_mode == "theme_random":
                _theme_key = theme_name
                _th = sample_building_target_height(
                    seed=seed,
                    target_id=fid,
                    theme_name=_theme_key,
                    frontage_width_m=float(frontage_width_m),
                    depth_m=float(depth_m),
                    source="osm",
                    height_profile=height_profile,
                )
                _hc = height_class_from_height_m(_th)
            else:
                _th = 0.0
                _hc = _height_class_from_area(float(polygon.area))
            footprints.append(
                BuildingFootprint(
                    footprint_id=fid,
                    source="osm_context_white_massing" if is_white_context_massing else "osm",
                    polygon_xz=tuple((float(x), float(y)) for x, y in tuple(polygon.exterior.coords)),
                    centroid_xz=centroid,
                    frontage_width_m=float(frontage_width_m),
                    depth_m=float(depth_m),
                    yaw_deg=float(yaw_deg),
                    theme_id=theme_id,
                    land_use_type=land_use_for_theme(theme_name),
                    height_class=_hc,
                    target_height_m=_th,
                    anchor_geom_id=str(getattr(building, "osm_id", "")),
                    size_class=_size_class(frontage_width_m, depth_m),
                    street_edge_xz=centroid,
                    placement_xz=centroid,
                    front_setback_m=0.0,
                    placement_strategy="footprint_centroid",
                    building_depth_m=float(depth_m),
                )
            )
    if footprints:
        return tuple(footprints)
    return _apply_building_region_constraints_to_footprints(
        _fallback_building_footprints(
            theme_segments, placement_context, road_segment_graph,
            seed=seed,
            height_mode=height_mode,
            height_profile=height_profile,
            asymmetry_strength=asymmetry_strength,
            left_right_bias=left_right_bias,
            front_setback_min_m=front_setback_min_m,
            front_setback_max_m=front_setback_max_m,
            zoning_granularity=zoning_granularity,
            streetwall_continuity=streetwall_continuity,
        ),
        placement_context=placement_context,
    )


def assign_theme_id_for_point(
    point_xz: Tuple[float, float],
    theme_segments: Sequence[ThemeSegment],
    road_segment_graph: object | None,
) -> str:
    if road_segment_graph is not None and getattr(road_segment_graph, "nodes", None):
        nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
        nearest = min(
            nodes,
            key=lambda node: math.hypot(
                float(getattr(node, "center_xy", (0.0, 0.0))[0]) - float(point_xz[0]),
                float(getattr(node, "center_xy", (0.0, 0.0))[1]) - float(point_xz[1]),
            ),
        )
        segment_id = str(getattr(nearest, "segment_id", ""))
        for theme_segment in theme_segments:
            if segment_id in set(theme_segment.segment_ids):
                return theme_segment.theme_id
    return theme_segments[0].theme_id if theme_segments else "theme_000"


def building_query(
    base_query: str,
    *,
    theme_name: str,
    frontage_width_m: float,
    depth_m: float,
    road_type: str = "",
    height_class: str = "",
) -> str:
    size_class = _size_class(frontage_width_m, depth_m)
    road_part = f", {road_type}" if str(road_type).strip() else ""
    height_part = f", {height_class}" if str(height_class).strip() else ""
    return f"{base_query}, {theme_name} building facade, {size_class} frontage{road_part}{height_part}"


def rerank_building_candidates(
    *,
    hits: Sequence[object],
    asset_by_id: Mapping[str, Mapping[str, Any]],
    theme_name: str,
    frontage_width_m: float,
    depth_m: float,
    height_class: str = "",
    limit: int,
) -> List[Tuple[Dict[str, Any], float]]:
    ranked: List[Tuple[Dict[str, Any], float]] = []
    target_size = _size_class(frontage_width_m, depth_m)
    for hit in hits:
        row = asset_by_id.get(str(getattr(hit, "asset_id", "")))
        if row is None:
            continue
        role = str(row.get("asset_role", "street_furniture")).strip().lower()
        if role != "building" and str(row.get("category", "")).strip().lower() != "building":
            continue
        score = float(getattr(hit, "score", 0.0))
        theme_tags = _normalize_tags(row.get("theme_tags"))
        style_tags = _normalize_tags(row.get("style_tags"))
        row_frontage = float(row.get("frontage_width_m", 0.0) or 0.0)
        row_depth = float(row.get("depth_m", 0.0) or 0.0)
        row_height_class = str(row.get("height_class", "") or "").strip().lower()
        if theme_name in theme_tags:
            score += 0.45
        if target_size in theme_tags:
            score += 0.15
        if theme_name in style_tags:
            score += 0.1
        if str(height_class).strip().lower() and row_height_class == str(height_class).strip().lower():
            score += 0.2
        if row_frontage > 0.0:
            score += max(0.0, 0.25 - abs(row_frontage - frontage_width_m) / max(frontage_width_m, 1.0) * 0.25)
        if row_depth > 0.0:
            score += max(0.0, 0.2 - abs(row_depth - depth_m) / max(depth_m, 1.0) * 0.2)
        ranked.append((dict(row), float(score)))
    ranked.sort(key=lambda item: (float(item[1]), bool(item[0].get("hero_asset", False))), reverse=True)
    return ranked[: max(int(limit), 1)]


def oriented_bounds_metrics(polygon: object) -> Tuple[float, float, float]:
    coords = tuple(getattr(polygon.minimum_rotated_rectangle, "exterior").coords)
    if len(coords) < 4:
        return 0.0, 12.0, 10.0
    p0 = coords[0]
    p1 = coords[1]
    p2 = coords[2]
    edge_a = math.hypot(float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1]))
    edge_b = math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))
    frontage = max(edge_a, edge_b)
    depth = min(edge_a, edge_b)
    if edge_a >= edge_b:
        yaw = math.degrees(math.atan2(float(p1[1]) - float(p0[1]), float(p1[0]) - float(p0[0])))
    else:
        yaw = math.degrees(math.atan2(float(p2[1]) - float(p1[1]), float(p2[0]) - float(p1[0])))
    return float(yaw), float(max(frontage, 4.0)), float(max(depth, 4.0))


def oriented_rectangle_points(
    *,
    center_x: float,
    center_z: float,
    yaw_deg: float,
    length_m: float,
    depth_m: float,
) -> Tuple[Tuple[float, float], ...]:
    half_l = float(length_m) / 2.0
    half_d = float(depth_m) / 2.0
    corners = ((-half_l, -half_d), (half_l, -half_d), (half_l, half_d), (-half_l, half_d), (-half_l, -half_d))
    yaw_rad = math.radians(float(yaw_deg))
    cos_y = math.cos(yaw_rad)
    sin_y = math.sin(yaw_rad)
    rotated = []
    for lx, lz in corners:
        rotated.append(
            (
                float(center_x) + lx * cos_y - lz * sin_y,
                float(center_z) + lx * sin_y + lz * cos_y,
            )
        )
    return tuple(rotated)


def _normalized_building_region_records(
    placement_context: object | None,
) -> List[Dict[str, Any]]:
    raw_regions = list(getattr(placement_context, "building_regions", ()) or ())
    if not raw_regions:
        return []
    try:
        from shapely.geometry import Polygon as ShapelyPolygon
    except Exception:
        ShapelyPolygon = None  # type: ignore[assignment]

    regions: List[Dict[str, Any]] = []
    for order_index, region in enumerate(raw_regions):
        polygon_xz = tuple(
            (float(point[0]), float(point[1]))
            for point in (region.get("polygon_xz", ()) if isinstance(region, Mapping) else ())
            if len(point) >= 2
        )
        if len(polygon_xz) < 4:
            center_xz = tuple(region.get("center_xz", (0.0, 0.0))) if isinstance(region, Mapping) else (0.0, 0.0)
            width_m = float(region.get("width_m", 0.0) if isinstance(region, Mapping) else 0.0)
            height_m = float(region.get("height_m", 0.0) if isinstance(region, Mapping) else 0.0)
            yaw_deg = float(region.get("yaw_deg", 0.0) if isinstance(region, Mapping) else 0.0)
            polygon_xz = oriented_rectangle_points(
                center_x=float(center_xz[0]) if len(center_xz) >= 2 else 0.0,
                center_z=float(center_xz[1]) if len(center_xz) >= 2 else 0.0,
                yaw_deg=float(yaw_deg),
                length_m=max(float(width_m), 0.0),
                depth_m=max(float(height_m), 0.0),
            )
        if len(polygon_xz) < 4:
            continue
        regions.append(
            {
                "region_id": str(region.get("region_id", "") if isinstance(region, Mapping) else ""),
                "label": str(region.get("label", "") if isinstance(region, Mapping) else ""),
                "order_index": int(region.get("order_index", order_index) if isinstance(region, Mapping) else order_index),
                "center_xz": tuple(
                    float(value)
                    for value in (
                        region.get("center_xz", _polygon_center(polygon_xz))
                        if isinstance(region, Mapping)
                        else _polygon_center(polygon_xz)
                    )
                ),
                "width_m": float(region.get("width_m", 0.0) if isinstance(region, Mapping) else 0.0),
                "height_m": float(region.get("height_m", 0.0) if isinstance(region, Mapping) else 0.0),
                "yaw_deg": float(region.get("yaw_deg", 0.0) if isinstance(region, Mapping) else 0.0),
                "target_height_m": float(region.get("target_height_m", 0.0) if isinstance(region, Mapping) else 0.0),
                "height_source": str(region.get("height_source", "") if isinstance(region, Mapping) else ""),
                "polygon_xz": polygon_xz,
                "bbox": _polygon_bbox(polygon_xz),
                "geom": (
                    ShapelyPolygon(polygon_xz)
                    if ShapelyPolygon is not None
                    else None
                ),
            }
        )
    regions.sort(key=lambda item: int(item.get("order_index", 0)))
    return regions


def _building_region_union_geometry(
    placement_context: object | None,
) -> Any | None:
    try:
        from shapely.ops import unary_union
    except Exception:
        return None
    regions = _normalized_building_region_records(placement_context)
    geometries = [
        region["geom"]
        for region in regions
        if region.get("geom") is not None and not getattr(region["geom"], "is_empty", True)
    ]
    if not geometries:
        return None
    merged = unary_union(geometries)
    if getattr(merged, "is_empty", True):
        return None
    return merged.buffer(0)


def _last_matching_building_region_for_polygon(
    polygon_xz: Sequence[Tuple[float, float]],
    building_regions: Sequence[Mapping[str, Any]],
    *,
    polygon_geom: Any | None = None,
) -> Mapping[str, Any] | None:
    if not building_regions or len(tuple(polygon_xz)) < 4:
        return None
    bbox = _polygon_bbox(polygon_xz)
    matched_region: Mapping[str, Any] | None = None
    if polygon_geom is None:
        try:
            from shapely.geometry import Polygon as ShapelyPolygon
        except Exception:
            ShapelyPolygon = None  # type: ignore[assignment]
        polygon_geom = ShapelyPolygon(polygon_xz) if ShapelyPolygon is not None else None
    for region in building_regions:
        if not _bbox_intersects(bbox, tuple(region.get("bbox", (0.0, 0.0, 0.0, 0.0)))):
            continue
        intersects = True
        region_geom = region.get("geom")
        if polygon_geom is not None and region_geom is not None:
            intersects = bool(region_geom.intersects(polygon_geom))
        if intersects:
            matched_region = region
    return matched_region


def _apply_building_region_constraints_to_footprints(
    footprints: Sequence[BuildingFootprint],
    *,
    placement_context: object | None,
) -> Tuple[BuildingFootprint, ...]:
    building_regions = _normalized_building_region_records(placement_context)
    if not building_regions:
        return tuple(footprints)
    constrained: List[BuildingFootprint] = []
    for footprint in footprints:
        matched_region = _last_matching_building_region_for_polygon(
            tuple((float(x), float(z)) for x, z in footprint.polygon_xz),
            building_regions,
        )
        if matched_region is None:
            continue
        constrained.append(
            replace(
                footprint,
                yaw_deg=float(matched_region.get("yaw_deg", footprint.yaw_deg) or footprint.yaw_deg),
            )
        )
    return tuple(constrained)


def _region_frontage_depth_metrics(region: Mapping[str, Any]) -> Tuple[float, float]:
    width_m = max(float(region.get("width_m", 0.0) or 0.0), 0.0)
    height_m = max(float(region.get("height_m", 0.0) or 0.0), 0.0)
    polygon_xz = tuple(
        (float(point[0]), float(point[1]))
        for point in region.get("polygon_xz", ()) or ()
        if len(point) >= 2
    )
    if width_m <= 1e-6 or height_m <= 1e-6:
        if len(polygon_xz) >= 4:
            edge_a = math.hypot(
                float(polygon_xz[1][0]) - float(polygon_xz[0][0]),
                float(polygon_xz[1][1]) - float(polygon_xz[0][1]),
            )
            edge_b = math.hypot(
                float(polygon_xz[2][0]) - float(polygon_xz[1][0]),
                float(polygon_xz[2][1]) - float(polygon_xz[1][1]),
            )
            if width_m <= 1e-6:
                width_m = max(edge_a, edge_b)
            if height_m <= 1e-6:
                height_m = min(edge_a, edge_b)
    frontage_width_m = float(max(width_m, height_m, 1.0))
    depth_m = float(max(min(width_m, height_m) if min(width_m, height_m) > 1e-6 else max(width_m, height_m), 4.0))
    return frontage_width_m, depth_m


def _building_region_footprints(
    *,
    placement_context: object | None,
    theme_segments: Sequence[ThemeSegment],
    road_segment_graph: object | None,
    seed: int = 0,
    height_mode: str = "theme_random",
    height_profile: str = "urban_default_v1",
) -> Tuple[BuildingFootprint, ...]:
    building_regions = _normalized_building_region_records(placement_context)
    if not building_regions:
        return tuple()
    footprints: List[BuildingFootprint] = []
    for index, region in enumerate(building_regions):
        polygon_xz = tuple(
            (float(point[0]), float(point[1]))
            for point in region.get("polygon_xz", ()) or ()
            if len(point) >= 2
        )
        if len(polygon_xz) < 4:
            continue
        center_xz_raw = tuple(region.get("center_xz", _polygon_center(polygon_xz)) or _polygon_center(polygon_xz))
        centroid = (
            float(center_xz_raw[0]) if len(center_xz_raw) >= 2 else 0.0,
            float(center_xz_raw[1]) if len(center_xz_raw) >= 2 else 0.0,
        )
        theme_id = assign_theme_id_for_point(centroid, theme_segments, road_segment_graph)
        theme_name = _resolve_theme_key(theme_id, theme_segments)
        frontage_width_m, depth_m = _region_frontage_depth_metrics(region)
        yaw_deg = float(region.get("yaw_deg", 0.0) or 0.0)
        footprint_id = str(region.get("region_id", "") or f"building_region_{index:02d}")
        declared_height_m = max(0.0, float(region.get("target_height_m", 0.0) or 0.0))
        if declared_height_m > 0.0:
            target_height_m = declared_height_m
            height_class = height_class_from_height_m(target_height_m)
        elif height_mode == "theme_random":
            target_height_m = sample_building_target_height(
                seed=seed,
                target_id=footprint_id,
                theme_name=theme_name,
                land_use_type=land_use_for_theme(theme_name),
                frontage_width_m=frontage_width_m,
                depth_m=depth_m,
                source="building_region",
                height_profile=height_profile,
            )
            height_class = height_class_from_height_m(target_height_m)
        else:
            target_height_m = 0.0
            area_m2 = abs(_polygon_signed_area(polygon_xz))
            height_class = _height_class_from_area(area_m2)
        footprints.append(
            BuildingFootprint(
                footprint_id=footprint_id,
                source="building_region",
                polygon_xz=polygon_xz,
                centroid_xz=centroid,
                frontage_width_m=float(frontage_width_m),
                depth_m=float(depth_m),
                yaw_deg=float(yaw_deg),
                theme_id=theme_id,
                land_use_type=land_use_for_theme(theme_name),
                side="",
                height_class=str(height_class),
                target_height_m=float(target_height_m),
                anchor_geom_id=str(region.get("region_id", footprint_id) or footprint_id),
                size_class=_size_class(frontage_width_m, depth_m),
                street_edge_xz=centroid,
                placement_xz=centroid,
                front_setback_m=0.0,
                placement_strategy="building_region",
                building_depth_m=float(depth_m),
            )
        )
    return tuple(footprints)


def land_use_for_theme(theme_name: str) -> str:
    theme_value = str(theme_name).strip().lower()
    return theme_value if theme_value in THEME_VOCAB else "commercial"


def _clamp(value: float, lower: float, upper: float) -> float:
    return float(max(lower, min(upper, value)))


def _quiet_land_use_for_theme(theme_name: str) -> str:
    base = land_use_for_theme(theme_name)
    if base == "commercial":
        return "residential"
    if base == "transit":
        return "commercial"
    if base == "residential":
        return "commercial"
    if base == "green":
        return "residential"
    return "residential"


def _streetwall_base_land_use_for_theme(theme_name: str) -> str:
    base = land_use_for_theme(theme_name)
    if base == "green":
        return "residential"
    return base


def _resolve_active_side(
    *,
    seed: int,
    theme_id: str,
    theme_name: str,
    left_right_bias: float,
) -> str:
    bias = _clamp(float(left_right_bias), -1.0, 1.0)
    if bias > 1e-6:
        return "left"
    if bias < -1e-6:
        return "right"
    u = _hash_to_unit(f"{seed}:active_side:{theme_id}:{theme_name}")
    return "left" if u >= 0.5 else "right"


def _resolve_side_zoning_profile(
    *,
    seed: int,
    theme_id: str,
    theme_name: str,
    asymmetry_strength: float,
    left_right_bias: float,
    force_streetwall_baseline: bool = False,
) -> Dict[str, object]:
    strength = _clamp(float(asymmetry_strength), 0.0, 1.0)
    base_land_use = (
        _streetwall_base_land_use_for_theme(theme_name)
        if bool(force_streetwall_baseline)
        else land_use_for_theme(theme_name)
    )
    quiet_land_use = _quiet_land_use_for_theme(theme_name)
    if strength <= 1e-6:
        return {
            "active_side": "",
            "left_land_use_type": base_land_use,
            "right_land_use_type": base_land_use,
            "left_width_multiplier": 1.0,
            "right_width_multiplier": 1.0,
        }

    active_side = _resolve_active_side(
        seed=seed,
        theme_id=theme_id,
        theme_name=theme_name,
        left_right_bias=left_right_bias,
    )
    delta = 0.25 * strength
    if active_side == "left":
        return {
            "active_side": "left",
            "left_land_use_type": base_land_use,
            "right_land_use_type": quiet_land_use,
            "left_width_multiplier": 1.0 + delta,
            "right_width_multiplier": max(0.5, 1.0 - delta),
        }
    return {
        "active_side": "right",
        "left_land_use_type": quiet_land_use,
        "right_land_use_type": base_land_use,
        "left_width_multiplier": max(0.5, 1.0 - delta),
        "right_width_multiplier": 1.0 + delta,
    }


def _segment_offset_midpoint(
    start_xy: Tuple[float, float],
    end_xy: Tuple[float, float],
    *,
    offset_m: float,
) -> Tuple[float, float]:
    tangent_payload = _segment_tangent_normal(start_xy, end_xy)
    if tangent_payload is None:
        return (
            (float(start_xy[0]) + float(end_xy[0])) / 2.0,
            (float(start_xy[1]) + float(end_xy[1])) / 2.0,
        )
    _tangent, left_normal, _length = tangent_payload
    start = (
        float(start_xy[0]) + left_normal[0] * float(offset_m),
        float(start_xy[1]) + left_normal[1] * float(offset_m),
    )
    end = (
        float(end_xy[0]) + left_normal[0] * float(offset_m),
        float(end_xy[1]) + left_normal[1] * float(offset_m),
    )
    return ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)


def _average_points(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    samples = [(float(x), float(z)) for x, z in points]
    if not samples:
        return (0.0, 0.0)
    return (
        float(sum(point[0] for point in samples) / len(samples)),
        float(sum(point[1] for point in samples) / len(samples)),
    )


def _sample_front_setback_m(
    *,
    seed: int,
    target_id: str,
    minimum_m: float,
    maximum_m: float,
) -> float:
    lo = max(0.0, float(minimum_m))
    hi = max(lo, float(maximum_m))
    if hi - lo <= 1e-6:
        return round(lo, 3)
    u = _hash_to_unit(f"{seed}:front_setback:{target_id}") ** 1.75
    return round(lo + (hi - lo) * u, 3)


def _resolve_frontage_placement(
    *,
    street_edge_xz: Tuple[float, float],
    side: str,
    yaw_deg: float,
    parcel_depth_m: float,
    front_setback_m: float,
) -> Tuple[Tuple[float, float], float, str]:
    parcel_depth = max(float(parcel_depth_m), 1.5)
    setback = max(float(front_setback_m), 0.0)
    desired_depth = max(4.0, parcel_depth * 0.68)
    max_depth_without_clamp = max(parcel_depth - setback - 0.75, 1.5)
    if max_depth_without_clamp >= 4.0:
        building_depth = min(desired_depth, max_depth_without_clamp)
        placement_strategy = "frontage_setback"
    else:
        building_depth = max(parcel_depth - setback, 1.5)
        placement_strategy = "frontage_clamped"
        if building_depth <= 1.5:
            building_depth = max(parcel_depth * 0.5, 1.0)
            placement_strategy = "lot_center"
            setback = 0.0
    if building_depth > parcel_depth - setback:
        building_depth = max(parcel_depth - setback, 1.0)
        placement_strategy = "frontage_clamped"

    center_offset_m = max(setback + building_depth / 2.0, building_depth / 2.0)
    yaw_rad = math.radians(float(yaw_deg))
    left_normal = (-math.sin(yaw_rad), math.cos(yaw_rad))
    sign = 1.0 if str(side).strip().lower() == "left" else -1.0
    placement_xz = (
        float(street_edge_xz[0]) + left_normal[0] * sign * center_offset_m,
        float(street_edge_xz[1]) + left_normal[1] * sign * center_offset_m,
    )
    return placement_xz, float(building_depth), placement_strategy


def _normalize_zoning_granularity(value: str) -> str:
    key = str(value or "balanced").strip().lower()
    return key if key in _ZONING_GRANULARITY_MULTIPLIERS else "balanced"


def _normalize_infill_policy(value: str) -> str:
    key = str(value or "large_gap_only").strip().lower()
    return key if key in _INFILL_POLICY_MULTIPLIERS else "large_gap_only"


def _frontage_rule(
    land_use_type: str,
    *,
    zoning_granularity: str,
) -> Dict[str, float]:
    base_rule = _GRID_LOT_RULES.get(str(land_use_type or "").strip().lower(), _GRID_LOT_RULES["commercial"])
    multiplier = _ZONING_GRANULARITY_MULTIPLIERS[_normalize_zoning_granularity(zoning_granularity)]
    return {
        "target_frontage_m": float(base_rule["target_frontage_m"]) * multiplier,
        "min_frontage_m": float(base_rule["min_frontage_m"]) * multiplier,
        "max_frontage_m": float(base_rule["max_frontage_m"]) * multiplier,
        "gap_threshold_m": float(base_rule["gap_threshold_m"]),
    }


def _frontage_intervals_for_length(
    total_frontage_m: float,
    *,
    land_use_type: str,
    zoning_granularity: str,
    streetwall_continuity: float,
) -> Tuple[Tuple[float, float], ...]:
    total = float(max(total_frontage_m, 0.0))
    if total <= 1e-6:
        return tuple()
    rule = _frontage_rule(land_use_type, zoning_granularity=zoning_granularity)
    continuity = _clamp(float(streetwall_continuity), 0.0, 1.0)
    usable_frontage = min(
        total,
        max(
            min(float(rule["min_frontage_m"]), total),
            total * max(0.35, continuity),
        ),
    )
    edge_gap = max(0.0, (total - usable_frontage) / 2.0)
    return tuple(
        (float(start) + edge_gap, float(end) + edge_gap)
        for start, end in _partition_frontage_interval(
            usable_frontage,
            target_frontage_m=float(rule["target_frontage_m"]),
            min_frontage_m=float(rule["min_frontage_m"]),
            max_frontage_m=float(rule["max_frontage_m"]),
        )
    )


def _preview_frontage_intervals_for_length(
    total_frontage_m: float,
    *,
    land_use_type: str,
    zoning_granularity: str,
    streetwall_continuity: float,
) -> Tuple[Tuple[float, float], ...]:
    land_use = str(land_use_type or "").strip().lower()
    if land_use in {"residential", "commercial", "transit"}:
        intervals = _frontage_intervals_for_length(
            total_frontage_m,
            land_use_type=land_use,
            zoning_granularity=zoning_granularity,
            streetwall_continuity=streetwall_continuity,
        )
        if intervals:
            return intervals
    fallback_rule = _frontage_rule(
        "residential" if land_use == "green" else "commercial",
        zoning_granularity=zoning_granularity,
    )
    return _partition_frontage_interval(
        total_frontage_m,
        target_frontage_m=float(fallback_rule["target_frontage_m"]),
        min_frontage_m=float(fallback_rule["min_frontage_m"]),
        max_frontage_m=float(fallback_rule["max_frontage_m"]),
    )


def _split_frontage_interval(
    start_m: float,
    end_m: float,
    *,
    max_length_m: float,
) -> Tuple[Tuple[float, float], ...]:
    start = float(start_m)
    end = float(end_m)
    span = end - start
    if span <= 1e-6:
        return tuple()
    max_length = max(float(max_length_m), 1.0)
    if span <= max_length + 1e-6:
        return ((start, end),)
    count = max(1, int(math.ceil(span / max_length)))
    step = span / float(count)
    return tuple(
        (
            start + float(idx) * step,
            end if idx == count - 1 else start + float(idx + 1) * step,
        )
        for idx in range(count)
    )


def _large_gap_threshold_m(
    land_use_type: str,
    *,
    infill_policy: str,
    streetwall_continuity: float,
) -> float:
    policy = _normalize_infill_policy(infill_policy)
    if policy == "off":
        return float("inf")
    base = float(_GRID_LOT_RULES.get(str(land_use_type or "").strip().lower(), _GRID_LOT_RULES["commercial"]).get("gap_threshold_m", 12.0))
    continuity = _clamp(float(streetwall_continuity), 0.0, 1.0)
    continuity_multiplier = 1.15 - 0.35 * continuity
    return max(3.0, base * _INFILL_POLICY_MULTIPLIERS[policy] * continuity_multiplier)


def _lerp_point(a: Tuple[float, float], b: Tuple[float, float], t: float) -> Tuple[float, float]:
    return (
        float(a[0]) + (float(b[0]) - float(a[0])) * float(t),
        float(a[1]) + (float(b[1]) - float(a[1])) * float(t),
    )


def _cell_frontage_edges(
    cell: Mapping[str, Any],
) -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]] | None:
    polygon = [
        (float(point[0]), float(point[1]))
        for point in cell.get("polygon_xz", []) or []
        if len(point) >= 2
    ]
    if len(polygon) < 4:
        return None
    side = _cell_side(cell)
    if side == "right":
        street_a = polygon[3]
        street_b = polygon[2]
        far_a = polygon[0]
        far_b = polygon[1]
    else:
        street_a = polygon[0]
        street_b = polygon[1]
        far_a = polygon[3]
        far_b = polygon[2]
    return street_a, street_b, far_a, far_b


def _parcel_polygon_from_cell_interval(
    cell: Mapping[str, Any],
    *,
    start_m: float,
    end_m: float,
) -> Tuple[Tuple[float, float], ...]:
    edge_payload = _cell_frontage_edges(cell)
    if edge_payload is None:
        return tuple()
    street_a, street_b, far_a, far_b = edge_payload
    frontage_m, _depth_m = _cell_frontage_depth(cell)
    if frontage_m <= 1e-6:
        return tuple()
    t0 = _clamp(float(start_m) / frontage_m, 0.0, 1.0)
    t1 = _clamp(float(end_m) / frontage_m, 0.0, 1.0)
    if t1 - t0 <= 1e-6:
        return tuple()
    far_0 = _lerp_point(far_a, far_b, t0)
    far_1 = _lerp_point(far_a, far_b, t1)
    street_1 = _lerp_point(street_a, street_b, t1)
    street_0 = _lerp_point(street_a, street_b, t0)
    return (far_0, far_1, street_1, street_0, far_0)


def _street_edge_midpoint_for_interval(
    cell: Mapping[str, Any],
    *,
    start_m: float,
    end_m: float,
) -> Tuple[float, float]:
    edge_payload = _cell_frontage_edges(cell)
    if edge_payload is None:
        return (0.0, 0.0)
    street_a, street_b, _far_a, _far_b = edge_payload
    frontage_m, _depth_m = _cell_frontage_depth(cell)
    if frontage_m <= 1e-6:
        return _average_points((street_a, street_b))
    t0 = _clamp(float(start_m) / frontage_m, 0.0, 1.0)
    t1 = _clamp(float(end_m) / frontage_m, 0.0, 1.0)
    return _average_points((_lerp_point(street_a, street_b, t0), _lerp_point(street_a, street_b, t1)))


def _partition_frontage_interval(
    total_frontage_m: float,
    *,
    target_frontage_m: float,
    min_frontage_m: float,
    max_frontage_m: float,
) -> Tuple[Tuple[float, float], ...]:
    total = float(max(total_frontage_m, 0.0))
    if total <= 1e-6:
        return tuple()
    target = max(float(target_frontage_m), 1.0)
    min_frontage = max(float(min_frontage_m), 1.0)
    max_frontage = max(float(max_frontage_m), min_frontage)
    count = max(1, int(round(total / target)))
    while total / count > max_frontage + 1e-6:
        count += 1
    while count > 1 and total / count < min_frontage - 1e-6:
        count -= 1
    width = total / count
    if width > max_frontage + 1e-6:
        count = max(count, int(math.ceil(total / max_frontage)))
        width = total / count
    if width < min_frontage - 1e-6 and count > 1:
        count = max(1, int(math.floor(total / min_frontage)))
        width = total / max(count, 1)
    intervals: List[Tuple[float, float]] = []
    cursor = 0.0
    for idx in range(max(count, 1)):
        next_cursor = total if idx == count - 1 else min(total, cursor + width)
        intervals.append((float(cursor), float(next_cursor)))
        cursor = float(next_cursor)
    return tuple(intervals)


def _merge_intervals(
    intervals: Sequence[Tuple[float, float]],
    *,
    minimum_width_m: float = 0.0,
) -> Tuple[Tuple[float, float], ...]:
    cleaned = sorted(
        (
            (float(start), float(end))
            for start, end in intervals
            if float(end) - float(start) > max(float(minimum_width_m), 1e-6)
        ),
        key=lambda item: (item[0], item[1]),
    )
    if not cleaned:
        return tuple()
    merged: List[List[float]] = [[cleaned[0][0], cleaned[0][1]]]
    for start, end in cleaned[1:]:
        last = merged[-1]
        if start <= last[1] + 1e-6:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return tuple((float(start), float(end)) for start, end in merged)


def _invert_intervals(
    total_frontage_m: float,
    intervals: Sequence[Tuple[float, float]],
) -> Tuple[Tuple[float, float], ...]:
    total = float(max(total_frontage_m, 0.0))
    if total <= 1e-6:
        return tuple()
    merged = _merge_intervals(intervals)
    if not merged:
        return ((0.0, total),)
    gaps: List[Tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor > 1e-6:
            gaps.append((float(cursor), float(start)))
        cursor = max(cursor, float(end))
    if total - cursor > 1e-6:
        gaps.append((float(cursor), float(total)))
    return tuple(gaps)


def _project_polygon_to_cell_frontage_interval(
    cell: Mapping[str, Any],
    polygon_xz: Sequence[Tuple[float, float]],
) -> Tuple[float, float] | None:
    edge_payload = _cell_frontage_edges(cell)
    if edge_payload is None:
        return None
    street_a, street_b, _far_a, _far_b = edge_payload
    direction = (
        float(street_b[0]) - float(street_a[0]),
        float(street_b[1]) - float(street_a[1]),
    )
    frontage_m = math.hypot(direction[0], direction[1])
    if frontage_m <= 1e-6:
        return None
    unit = (direction[0] / frontage_m, direction[1] / frontage_m)
    values = []
    for x, z in polygon_xz:
        offset = (float(x) - float(street_a[0]), float(z) - float(street_a[1]))
        values.append(offset[0] * unit[0] + offset[1] * unit[1])
    if not values:
        return None
    start = _clamp(min(values), 0.0, frontage_m)
    end = _clamp(max(values), 0.0, frontage_m)
    if end - start <= 0.25:
        return None
    return (float(start), float(end))


def _frontage_gap_stats(
    total_frontage_m: float,
    intervals: Sequence[Tuple[float, float]],
) -> Dict[str, float]:
    gaps = _invert_intervals(total_frontage_m, intervals)
    lengths = [float(end) - float(start) for start, end in gaps if float(end) - float(start) > 1e-6]
    if not lengths:
        return {
            "gap_count": 0,
            "max_gap_m": 0.0,
            "mean_gap_m": 0.0,
            "uncovered_length_m": 0.0,
        }
    uncovered = sum(lengths)
    return {
        "gap_count": int(len(lengths)),
        "max_gap_m": round(max(lengths), 3),
        "mean_gap_m": round(uncovered / len(lengths), 3),
        "uncovered_length_m": round(uncovered, 3),
    }


def summarize_frontage_coverage(
    zoning_grid: Sequence[Mapping[str, Any]],
    coverage_items: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, float]]:
    buildable_cells: List[Dict[str, Any]] = []
    for cell in zoning_grid:
        if not bool(cell.get("buildable", False)):
            continue
        if "building_buffer" not in str(cell.get("lane_role", "") or ""):
            continue
        polygon = [
            (float(point[0]), float(point[1]))
            for point in cell.get("polygon_xz", []) or []
            if len(point) >= 2
        ]
        if len(polygon) < 4:
            continue
        buildable_cells.append(
            {
                "cell": cell,
                "cell_id": str(cell.get("cell_id", "") or ""),
                "side": _cell_side(cell),
                "frontage_m": _cell_frontage_depth(cell)[0],
                "bbox": _polygon_bbox(polygon),
            }
        )

    intervals_by_cell: Dict[str, List[Tuple[float, float]]] = {
        entry["cell_id"]: [] for entry in buildable_cells if entry["cell_id"]
    }
    for item in coverage_items:
        polygon = [
            (float(point[0]), float(point[1]))
            for point in item.get("polygon_xz", []) or []
            if len(point) >= 2
        ]
        if len(polygon) < 4:
            continue
        side_hint = str(item.get("side", "") or "")
        bbox = _polygon_bbox(polygon)
        for entry in buildable_cells:
            if side_hint and side_hint != entry["side"]:
                continue
            if not _bbox_intersects(entry["bbox"], bbox):
                continue
            interval = _project_polygon_to_cell_frontage_interval(entry["cell"], polygon)
            if interval is None:
                continue
            intervals_by_cell.setdefault(entry["cell_id"], []).append(interval)

    coverage_by_side: Dict[str, Dict[str, float]] = {}
    gap_stats_by_side: Dict[str, Dict[str, float]] = {}
    for side_name in ("left", "right"):
        side_cells = [entry for entry in buildable_cells if entry["side"] == side_name]
        total_length = sum(float(entry["frontage_m"]) for entry in side_cells)
        covered_length = 0.0
        gap_count = 0
        max_gap = 0.0
        uncovered_total = 0.0
        for entry in side_cells:
            merged = _merge_intervals(intervals_by_cell.get(entry["cell_id"], ()))
            covered_length += sum(float(end) - float(start) for start, end in merged)
            gap_stats = _frontage_gap_stats(float(entry["frontage_m"]), merged)
            gap_count += int(gap_stats.get("gap_count", 0) or 0)
            max_gap = max(max_gap, float(gap_stats.get("max_gap_m", 0.0) or 0.0))
            uncovered_total += float(gap_stats.get("uncovered_length_m", 0.0) or 0.0)
        coverage_by_side[side_name] = {
            "covered_length_m": round(covered_length, 3),
            "total_length_m": round(total_length, 3),
            "coverage_ratio": round(covered_length / total_length, 3) if total_length > 1e-6 else 0.0,
        }
        gap_stats_by_side[side_name] = {
            "gap_count": int(gap_count),
            "max_gap_m": round(max_gap, 3),
            "mean_gap_m": round(uncovered_total / gap_count, 3) if gap_count > 0 else 0.0,
            "uncovered_length_m": round(uncovered_total, 3),
        }
    return {
        "frontage_coverage_by_side": coverage_by_side,
        "frontage_gap_stats_by_side": gap_stats_by_side,
    }


def _buildable_building_cells(
    zoning_grid: Sequence[Mapping[str, Any]],
    *,
    side: str = "",
) -> List[Dict[str, Any]]:
    requested_side = str(side or "").strip().lower()
    results: List[Dict[str, Any]] = []
    for cell in zoning_grid:
        if not bool(cell.get("buildable", False)):
            continue
        if "building_buffer" not in str(cell.get("lane_role", "") or ""):
            continue
        cell_side = _cell_side(cell)
        if requested_side and cell_side != requested_side:
            continue
        results.append(cell if isinstance(cell, dict) else dict(cell))
    return results


def _buildable_frontage_by_side(
    zoning_grid: Sequence[Mapping[str, Any]],
) -> Dict[str, float]:
    frontage_by_side = {"left": 0.0, "right": 0.0}
    for cell in _buildable_building_cells(zoning_grid):
        side = _cell_side(cell)
        if side not in frontage_by_side:
            continue
        frontage_by_side[side] += float(_cell_frontage_depth(cell)[0])
    return {
        side: round(float(frontage_m), 3)
        for side, frontage_m in frontage_by_side.items()
    }


def _summarize_building_balance(
    *,
    zoning_grid: Sequence[Mapping[str, Any]],
    frontage_metrics: Mapping[str, Any],
    min_side_frontage_coverage_ratio: float = _DEFAULT_MIN_SIDE_FRONTAGE_COVERAGE_RATIO,
    max_left_right_coverage_gap: float = _DEFAULT_MAX_LEFT_RIGHT_COVERAGE_GAP,
) -> Dict[str, Any]:
    buildable_frontage_by_side = _buildable_frontage_by_side(zoning_grid)
    coverage_by_side = dict(frontage_metrics.get("frontage_coverage_by_side", {}) or {})
    left_total = float(buildable_frontage_by_side.get("left", 0.0) or 0.0)
    right_total = float(buildable_frontage_by_side.get("right", 0.0) or 0.0)
    left_ratio = float((coverage_by_side.get("left", {}) or {}).get("coverage_ratio", 0.0) or 0.0)
    right_ratio = float((coverage_by_side.get("right", {}) or {}).get("coverage_ratio", 0.0) or 0.0)
    balance_gap = abs(left_ratio - right_ratio)
    reason = ""
    balance_ok = False
    if left_total <= 1e-6 and right_total <= 1e-6:
        reason = "no buildable frontage"
    elif left_total <= 1e-6:
        reason = "no buildable left frontage"
    elif right_total <= 1e-6:
        reason = "no buildable right frontage"
    else:
        balance_ok = (
            left_ratio >= float(min_side_frontage_coverage_ratio)
            and right_ratio >= float(min_side_frontage_coverage_ratio)
            and balance_gap <= float(max_left_right_coverage_gap)
        )
        if not balance_ok:
            reason = (
                f"coverage targets unmet: left={left_ratio:.2f}, "
                f"right={right_ratio:.2f}, gap={balance_gap:.2f}"
            )
    return {
        "building_balance_policy": "balanced_default",
        "building_balance_ok": bool(balance_ok),
        "building_balance_reason": str(reason),
        "frontage_balance_gap": round(float(balance_gap), 3),
        "buildable_frontage_by_side": buildable_frontage_by_side,
        "min_side_frontage_coverage_ratio": float(round(float(min_side_frontage_coverage_ratio), 3)),
        "max_left_right_coverage_gap": float(round(float(max_left_right_coverage_gap), 3)),
    }


def _coverage_gaps_by_buildable_cell(
    zoning_grid: Sequence[Mapping[str, Any]],
    coverage_items: Sequence[Mapping[str, Any]],
    *,
    side: str,
) -> List[Tuple[Dict[str, Any], Tuple[Tuple[float, float], ...]]]:
    buildable_cells = _buildable_building_cells(zoning_grid, side=side)
    if not buildable_cells:
        return []
    cell_entries: List[Dict[str, Any]] = []
    for cell in buildable_cells:
        polygon = [
            (float(point[0]), float(point[1]))
            for point in cell.get("polygon_xz", []) or []
            if len(point) >= 2
        ]
        if len(polygon) < 4:
            continue
        cell_entries.append(
            {
                "cell": cell,
                "cell_id": str(cell.get("cell_id", "") or ""),
                "bbox": _polygon_bbox(polygon),
                "frontage_m": float(_cell_frontage_depth(cell)[0]),
            }
        )
    intervals_by_cell: Dict[str, List[Tuple[float, float]]] = {
        entry["cell_id"]: [] for entry in cell_entries if entry["cell_id"]
    }
    for item in coverage_items:
        polygon = [
            (float(point[0]), float(point[1]))
            for point in item.get("polygon_xz", []) or []
            if len(point) >= 2
        ]
        if len(polygon) < 4:
            continue
        item_side = str(item.get("side", "") or "")
        if item_side and item_side != side:
            continue
        bbox = _polygon_bbox(polygon)
        for entry in cell_entries:
            if not _bbox_intersects(entry["bbox"], bbox):
                continue
            interval = _project_polygon_to_cell_frontage_interval(entry["cell"], polygon)
            if interval is None:
                continue
            intervals_by_cell.setdefault(entry["cell_id"], []).append(interval)
    results: List[Tuple[Dict[str, Any], Tuple[Tuple[float, float], ...]]] = []
    for entry in cell_entries:
        merged = _merge_intervals(intervals_by_cell.get(entry["cell_id"], ()))
        gaps = _invert_intervals(float(entry["frontage_m"]), merged)
        useful_gaps = tuple(
            (float(start), float(end))
            for start, end in gaps
            if float(end) - float(start) > 0.35
        )
        if useful_gaps:
            results.append((entry["cell"], useful_gaps))
    results.sort(
        key=lambda item: max(float(end) - float(start) for start, end in item[1]),
        reverse=True,
    )
    return results


def infer_grid_height_class(land_use_type: str, *, road_type: str = "") -> str:
    road_type_lc = str(road_type).strip().lower()
    major_road = road_type_lc in {"primary", "secondary"} or any(
        keyword in road_type_lc for keyword in ("primary", "secondary")
    )
    land_use = land_use_for_theme(land_use_type)
    if land_use == "residential":
        return "midrise" if major_road else "lowrise"
    if land_use == "commercial":
        return "midrise"
    if land_use == "transit":
        return "highrise" if major_road else "midrise"
    return ""


def summarize_land_use_grid(zoning_grid: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    land_use_counts = Counter(
        str(cell.get("land_use_type", "") or "")
        for cell in zoning_grid
        if str(cell.get("land_use_type", "") or "")
    )
    buildable_counts = Counter(
        str(cell.get("land_use_type", "") or "")
        for cell in zoning_grid
        if bool(cell.get("buildable", False)) and str(cell.get("land_use_type", "") or "")
    )
    lane_role_counts = Counter(str(cell.get("lane_role", "") or "") for cell in zoning_grid)
    buildable_cell_count = sum(1 for cell in zoning_grid if bool(cell.get("buildable", False)))
    return {
        "cell_counts": {key: int(value) for key, value in sorted(land_use_counts.items())},
        "buildable_cell_counts": {key: int(value) for key, value in sorted(buildable_counts.items())},
        "lane_role_counts": {key: int(value) for key, value in sorted(lane_role_counts.items()) if key},
        "buildable_cell_count": int(buildable_cell_count),
        "non_buildable_cell_count": int(len(zoning_grid) - buildable_cell_count),
    }


def _cell_side(cell: Mapping[str, Any]) -> str:
    lane_role = str(cell.get("lane_role", "") or "")
    if lane_role.startswith("left_"):
        return "left"
    if lane_role.startswith("right_"):
        return "right"
    return "center"


def _cell_frontage_depth(cell: Mapping[str, Any]) -> Tuple[float, float]:
    station_range = cell.get("station_range_m", ()) or ()
    if len(station_range) >= 2:
        frontage = abs(float(station_range[1]) - float(station_range[0]))
    else:
        frontage = 0.0
    polygon = [
        (float(point[0]), float(point[1]))
        for point in cell.get("polygon_xz", []) or []
        if len(point) >= 2
    ]
    depth = 0.0
    if len(polygon) >= 4:
        depth = math.hypot(float(polygon[3][0]) - float(polygon[0][0]), float(polygon[3][1]) - float(polygon[0][1]))
        if frontage <= 1e-6:
            frontage = math.hypot(float(polygon[1][0]) - float(polygon[0][0]), float(polygon[1][1]) - float(polygon[0][1]))
    return float(max(frontage, 1.0)), float(max(depth, 4.0))


def _cell_yaw_deg(cell: Mapping[str, Any]) -> float:
    region_yaw_deg = cell.get("building_region_yaw_deg")
    if region_yaw_deg is not None:
        try:
            parsed_region_yaw = float(region_yaw_deg)
        except (TypeError, ValueError):
            parsed_region_yaw = None
        if parsed_region_yaw is not None and math.isfinite(parsed_region_yaw):
            return float(parsed_region_yaw)
    polygon = [
        (float(point[0]), float(point[1]))
        for point in cell.get("polygon_xz", []) or []
        if len(point) >= 2
    ]
    if len(polygon) < 2:
        return 0.0
    dx = float(polygon[1][0]) - float(polygon[0][0])
    dz = float(polygon[1][1]) - float(polygon[0][1])
    if abs(dx) + abs(dz) <= 1e-6:
        return 0.0
    return float(math.degrees(math.atan2(dz, dx)))


def _polygon_from_bbox(
    bbox: Tuple[float, float, float, float],
) -> Tuple[Tuple[float, float], ...]:
    return (
        (float(bbox[0]), float(bbox[2])),
        (float(bbox[1]), float(bbox[2])),
        (float(bbox[1]), float(bbox[3])),
        (float(bbox[0]), float(bbox[3])),
        (float(bbox[0]), float(bbox[2])),
    )


def _merge_polygons(
    polygons: Sequence[Sequence[Tuple[float, float]]],
) -> Tuple[Tuple[float, float], ...]:
    polygon_list = [tuple((float(x), float(z)) for x, z in polygon) for polygon in polygons if len(polygon) >= 4]
    if not polygon_list:
        return tuple()
    try:
        from shapely.geometry import Polygon as ShapelyPolygon
        from shapely.ops import unary_union
    except Exception:
        boxes = [_polygon_bbox(polygon) for polygon in polygon_list]
        return _polygon_from_bbox(
            (
                min(box[0] for box in boxes),
                max(box[1] for box in boxes),
                min(box[2] for box in boxes),
                max(box[3] for box in boxes),
            )
        )
    merged = unary_union([ShapelyPolygon(polygon) for polygon in polygon_list])
    if getattr(merged, "geom_type", "") == "MultiPolygon":
        merged = max(getattr(merged, "geoms", []) or [], key=lambda geom: float(getattr(geom, "area", 0.0)), default=None)
    if merged is None or getattr(merged, "is_empty", True):
        boxes = [_polygon_bbox(polygon) for polygon in polygon_list]
        return _polygon_from_bbox(
            (
                min(box[0] for box in boxes),
                max(box[1] for box in boxes),
                min(box[2] for box in boxes),
                max(box[3] for box in boxes),
            )
        )
    return tuple((float(x), float(y)) for x, y in tuple(merged.exterior.coords))


def _generate_lot_from_cell_interval(
    cell: Mapping[str, Any],
    *,
    lot_id: str,
    start_m: float,
    end_m: float,
    road_type: str,
    seed: int,
    height_mode: str,
    height_profile: str,
    front_setback_min_m: float,
    front_setback_max_m: float,
) -> GeneratedLot | None:
    polygon_xz = _parcel_polygon_from_cell_interval(
        cell,
        start_m=float(start_m),
        end_m=float(end_m),
    )
    if len(polygon_xz) < 4:
        return None
    center_xz = _polygon_center(polygon_xz)
    side = _cell_side(cell)
    yaw_deg = _cell_yaw_deg(cell)
    theme_id = str(cell.get("theme_id", "") or "")
    theme_name = str(cell.get("theme_name", "") or "")
    cell_id = str(cell.get("cell_id", "") or "")
    land_use_type = str(cell.get("land_use_type", "") or "")
    segment_ids = tuple(str(segment_id) for segment_id in (cell.get("segment_ids", []) or []) if str(segment_id))
    parcel_depth_m = _cell_frontage_depth(cell)[1]
    street_edge_xz = _street_edge_midpoint_for_interval(
        cell,
        start_m=float(start_m),
        end_m=float(end_m),
    )
    frontage_width_m = float(max(float(end_m) - float(start_m), 3.0))
    front_setback_m = _sample_front_setback_m(
        seed=seed,
        target_id=lot_id,
        minimum_m=front_setback_min_m,
        maximum_m=front_setback_max_m,
    )
    placement_xz, building_depth_m, placement_strategy = _resolve_frontage_placement(
        street_edge_xz=street_edge_xz,
        side=side,
        yaw_deg=float(yaw_deg),
        parcel_depth_m=float(parcel_depth_m),
        front_setback_m=float(front_setback_m),
    )
    if height_mode == "theme_random":
        target_height_m = sample_building_target_height(
            seed=seed,
            target_id=lot_id,
            theme_name=theme_name,
            land_use_type=land_use_type,
            frontage_width_m=float(frontage_width_m),
            depth_m=float(max(building_depth_m, 4.0)),
            source="grid_growth",
            height_profile=height_profile,
        )
        height_class = height_class_from_height_m(target_height_m)
    else:
        target_height_m = 0.0
        height_class = str(infer_grid_height_class(land_use_type, road_type=road_type) or "midrise")
    return GeneratedLot(
        lot_id=lot_id,
        polygon_xz=tuple((float(x), float(z)) for x, z in polygon_xz),
        center_xz=(float(center_xz[0]), float(center_xz[1])),
        side=side,
        land_use_type=land_use_type,
        theme_id=theme_id,
        frontage_width_m=float(frontage_width_m),
        depth_m=float(max(parcel_depth_m, 4.0)),
        height_class=height_class,
        target_height_m=float(target_height_m),
        yaw_deg=float(yaw_deg),
        source=str(cell.get("generation_source", "") or "road_buffer"),
        cell_ids=(cell_id,) if cell_id else (),
        segment_ids=segment_ids,
        street_edge_xz=(float(street_edge_xz[0]), float(street_edge_xz[1])),
        placement_xz=(float(placement_xz[0]), float(placement_xz[1])),
        front_setback_m=float(front_setback_m),
        placement_strategy=str(placement_strategy),
        building_depth_m=float(building_depth_m),
    )


def _append_generated_lot_to_cell(cell: Dict[str, Any], lot: GeneratedLot) -> None:
    lot_ids = [str(value) for value in (cell.get("lot_ids", []) or []) if str(value)]
    if lot.lot_id not in lot_ids:
        lot_ids.append(str(lot.lot_id))
    cell["lot_ids"] = lot_ids
    cell["lot_id"] = str(lot_ids[0]) if lot_ids else ""


def _apply_building_balance_pass(
    annotated_cells: Sequence[Dict[str, Any]],
    lots: Sequence[GeneratedLot],
    *,
    road_type: str,
    seed: int,
    height_mode: str,
    height_profile: str,
    front_setback_min_m: float,
    front_setback_max_m: float,
    zoning_granularity: str,
    min_side_frontage_coverage_ratio: float = _DEFAULT_MIN_SIDE_FRONTAGE_COVERAGE_RATIO,
    max_left_right_coverage_gap: float = _DEFAULT_MAX_LEFT_RIGHT_COVERAGE_GAP,
) -> Tuple[Tuple[GeneratedLot, ...], Dict[str, Any]]:
    lot_list = list(lots)
    coverage_items = tuple({"polygon_xz": lot.polygon_xz, "side": lot.side} for lot in lot_list)
    frontage_metrics = summarize_frontage_coverage(annotated_cells, coverage_items)
    balance_summary = _summarize_building_balance(
        zoning_grid=annotated_cells,
        frontage_metrics=frontage_metrics,
        min_side_frontage_coverage_ratio=min_side_frontage_coverage_ratio,
        max_left_right_coverage_gap=max_left_right_coverage_gap,
    )
    buildable_frontage_by_side = dict(balance_summary.get("buildable_frontage_by_side", {}) or {})
    if (
        float(buildable_frontage_by_side.get("left", 0.0) or 0.0) <= 1e-6
        or float(buildable_frontage_by_side.get("right", 0.0) or 0.0) <= 1e-6
        or bool(balance_summary.get("building_balance_ok", False))
    ):
        return tuple(lot_list), {**frontage_metrics, **balance_summary}

    coverage_by_side = dict(frontage_metrics.get("frontage_coverage_by_side", {}) or {})
    left_ratio = float((coverage_by_side.get("left", {}) or {}).get("coverage_ratio", 0.0) or 0.0)
    right_ratio = float((coverage_by_side.get("right", {}) or {}).get("coverage_ratio", 0.0) or 0.0)
    target_side = "left" if left_ratio <= right_ratio else "right"
    gap_entries = _coverage_gaps_by_buildable_cell(annotated_cells, coverage_items, side=target_side)
    next_lot_index = len(lot_list)
    for cell, gaps in gap_entries:
        if bool(balance_summary.get("building_balance_ok", False)):
            break
        normalized_land_use = str(cell.get("land_use_type", "") or "")
        for gap_start_m, gap_end_m in gaps:
            gap_length_m = float(gap_end_m) - float(gap_start_m)
            if gap_length_m <= 0.35:
                continue
            supplemental_intervals = _frontage_intervals_for_length(
                gap_length_m,
                land_use_type=normalized_land_use,
                zoning_granularity=zoning_granularity,
                streetwall_continuity=1.0,
            )
            if not supplemental_intervals:
                supplemental_intervals = ((0.0, float(gap_length_m)),)
            for local_start_m, local_end_m in supplemental_intervals:
                lot_id = f"lot_{next_lot_index:03d}"
                lot = _generate_lot_from_cell_interval(
                    cell,
                    lot_id=lot_id,
                    start_m=float(gap_start_m) + float(local_start_m),
                    end_m=float(gap_start_m) + float(local_end_m),
                    road_type=road_type,
                    seed=seed,
                    height_mode=height_mode,
                    height_profile=height_profile,
                    front_setback_min_m=front_setback_min_m,
                    front_setback_max_m=front_setback_max_m,
                )
                if lot is None:
                    continue
                lot_list.append(lot)
                next_lot_index += 1
                _append_generated_lot_to_cell(cell, lot)
                coverage_items = tuple({"polygon_xz": item.polygon_xz, "side": item.side} for item in lot_list)
                frontage_metrics = summarize_frontage_coverage(annotated_cells, coverage_items)
                balance_summary = _summarize_building_balance(
                    zoning_grid=annotated_cells,
                    frontage_metrics=frontage_metrics,
                    min_side_frontage_coverage_ratio=min_side_frontage_coverage_ratio,
                    max_left_right_coverage_gap=max_left_right_coverage_gap,
                )
                if bool(balance_summary.get("building_balance_ok", False)):
                    break
            if bool(balance_summary.get("building_balance_ok", False)):
                break
    return tuple(lot_list), {**frontage_metrics, **balance_summary}


def generate_grid_growth_lots(
    zoning_grid: Sequence[Mapping[str, Any]],
    *,
    road_type: str = "",
    seed: int = 0,
    height_mode: str = "theme_random",
    height_profile: str = "urban_default_v1",
    front_setback_min_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    front_setback_max_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    zoning_granularity: str = "fine",
    streetwall_continuity: float = 0.95,
    max_frontage_lot_length_m: float = 18.0,
) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[GeneratedLot, ...], Dict[str, Any]]:
    min_side_frontage_coverage_ratio = _DEFAULT_MIN_SIDE_FRONTAGE_COVERAGE_RATIO
    max_left_right_coverage_gap = _DEFAULT_MAX_LEFT_RIGHT_COVERAGE_GAP
    annotated_cells: List[Dict[str, Any]] = []
    for cell in zoning_grid:
        payload = dict(cell)
        payload.setdefault("land_use_type", "")
        payload.setdefault("buildable", False)
        payload.setdefault("lot_id", "")
        payload.setdefault("lot_ids", [])
        annotated_cells.append(payload)
    normalized_granularity = _normalize_zoning_granularity(zoning_granularity)
    continuity = _clamp(float(streetwall_continuity), 0.0, 1.0)
    max_lot_length = max(float(max_frontage_lot_length_m), 1.0)
    candidate_cells = sorted(
        [
            cell
            for cell in annotated_cells
            if "building_buffer" in str(cell.get("lane_role", "") or "")
            and bool(cell.get("buildable", False))
            and str(cell.get("land_use_type", "") or "") in {"residential", "commercial", "transit"}
        ],
        key=lambda cell: (
            _cell_side(cell),
            float((cell.get("station_range_m", [0.0, 0.0]) or [0.0, 0.0])[0]),
            str(cell.get("cell_id", "") or ""),
        ),
    )

    lots: List[GeneratedLot] = []
    for cell in candidate_cells:
        land_use_type = str(cell.get("land_use_type", "") or "")
        parcel_frontage_intervals = _frontage_intervals_for_length(
            _cell_frontage_depth(cell)[0],
            land_use_type=land_use_type,
            zoning_granularity=normalized_granularity,
            streetwall_continuity=continuity,
        )
        if not parcel_frontage_intervals:
            continue
        parcel_frontage_intervals = tuple(
            sub_interval
            for start_m, end_m in parcel_frontage_intervals
            for sub_interval in _split_frontage_interval(
                float(start_m),
                float(end_m),
                max_length_m=float(max_lot_length),
            )
        )
        cell_lot_ids: List[str] = []
        side = _cell_side(cell)
        for start_m, end_m in parcel_frontage_intervals:
            lot_id = f"lot_{len(lots):03d}"
            lot = _generate_lot_from_cell_interval(
                cell,
                lot_id=lot_id,
                start_m=float(start_m),
                end_m=float(end_m),
                road_type=road_type,
                seed=seed,
                height_mode=height_mode,
                height_profile=height_profile,
                front_setback_min_m=front_setback_min_m,
                front_setback_max_m=front_setback_max_m,
            )
            if lot is None:
                continue
            cell_lot_ids.append(lot_id)
            lots.append(lot)
        cell["lot_ids"] = list(cell_lot_ids)
        cell["lot_id"] = str(cell_lot_ids[0]) if cell_lot_ids else ""

    balanced_lots, balance_metrics = _apply_building_balance_pass(
        annotated_cells,
        tuple(lots),
        road_type=road_type,
        seed=seed,
        height_mode=height_mode,
        height_profile=height_profile,
        front_setback_min_m=front_setback_min_m,
        front_setback_max_m=front_setback_max_m,
        zoning_granularity=normalized_granularity,
        min_side_frontage_coverage_ratio=min_side_frontage_coverage_ratio,
        max_left_right_coverage_gap=max_left_right_coverage_gap,
    )
    lots = list(balanced_lots)

    lot_counts = Counter(lot.land_use_type for lot in lots)
    height_counts = Counter(lot.height_class for lot in lots)
    frontage_metrics = {
        "frontage_coverage_by_side": dict(balance_metrics.get("frontage_coverage_by_side", {}) or {}),
        "frontage_gap_stats_by_side": dict(balance_metrics.get("frontage_gap_stats_by_side", {}) or {}),
    }
    summary = {
        "lot_count": int(len(lots)),
        "frontage_parcel_count": int(len(lots)),
        "lot_counts": {key: int(value) for key, value in sorted(lot_counts.items())},
        "height_class_counts": {key: int(value) for key, value in sorted(height_counts.items())},
        "buildable_cell_count": int(sum(1 for cell in annotated_cells if bool(cell.get("buildable", False)))),
        "occupied_lot_cells": int(
            sum(
                1
                for cell in annotated_cells
                if "building_buffer" in str(cell.get("lane_role", "") or "")
                and (((cell.get("lot_ids", []) or [])) or str(cell.get("lot_id", "") or ""))
            )
        ),
        "placement_strategy_counts": {
            key: int(value)
            for key, value in sorted(Counter(lot.placement_strategy for lot in lots).items())
        },
        "zoning_granularity": normalized_granularity,
        "streetwall_continuity": float(round(continuity, 3)),
        "max_frontage_lot_length_m": float(max_lot_length),
        **frontage_metrics,
        "building_balance_policy": str(balance_metrics.get("building_balance_policy", "balanced_default") or "balanced_default"),
        "building_balance_ok": bool(balance_metrics.get("building_balance_ok", False)),
        "building_balance_reason": str(balance_metrics.get("building_balance_reason", "") or ""),
        "frontage_balance_gap": float(balance_metrics.get("frontage_balance_gap", 0.0) or 0.0),
        "buildable_frontage_by_side": dict(balance_metrics.get("buildable_frontage_by_side", {}) or {}),
        "min_side_frontage_coverage_ratio": float(
            balance_metrics.get("min_side_frontage_coverage_ratio", min_side_frontage_coverage_ratio) or min_side_frontage_coverage_ratio
        ),
        "max_left_right_coverage_gap": float(
            balance_metrics.get("max_left_right_coverage_gap", max_left_right_coverage_gap) or max_left_right_coverage_gap
        ),
    }
    _front_setbacks = [lot.front_setback_m for lot in lots if lot.front_setback_m > 0.0]
    if _front_setbacks:
        summary["front_setback_stats"] = {
            "min_m": round(min(_front_setbacks), 3),
            "max_m": round(max(_front_setbacks), 3),
            "mean_m": round(sum(_front_setbacks) / len(_front_setbacks), 3),
        }
    # Add target height stats when in theme_random mode
    _heights = [lot.target_height_m for lot in lots if lot.target_height_m > 0.0]
    if _heights:
        summary["target_height_stats"] = {
            "min_m": round(min(_heights), 1),
            "max_m": round(max(_heights), 1),
            "mean_m": round(sum(_heights) / len(_heights), 1),
        }
    return tuple(annotated_cells), tuple(lots), summary


def _fallback_building_footprints(
    theme_segments: Sequence[ThemeSegment],
    placement_context: object | None,
    road_segment_graph: object | None,
    *,
    seed: int = 0,
    height_mode: str = "theme_random",
    height_profile: str = "urban_default_v1",
    asymmetry_strength: float = 0.0,
    left_right_bias: float = 0.0,
    front_setback_min_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    front_setback_max_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    zoning_granularity: str = "fine",
    streetwall_continuity: float = 0.95,
) -> List[BuildingFootprint]:
    footprints: List[BuildingFootprint] = []
    force_streetwall_baseline = True
    carriageway_width_m = float(
        getattr(placement_context, "carriageway_width_m", 0.0)
        or getattr(placement_context, "road_width_m", 0.0)
        or 8.0
    )
    carriageway_half = carriageway_width_m / 2.0
    streetwall_reference = _streetwall_reference_widths(
        design_rule_profile=str(
            getattr(placement_context, "design_rule_profile", "balanced_complete_street_v1")
            or "balanced_complete_street_v1"
        ),
        sidewalk_seed_width_m=2.5,
        placement_context=placement_context,
        asymmetry_strength=float(asymmetry_strength),
        force_streetwall_baseline=bool(force_streetwall_baseline),
    )
    fallback_left_streetwall_width_m = float(streetwall_reference["left_total_m"])
    fallback_right_streetwall_width_m = float(streetwall_reference["right_total_m"])
    nodes_by_id = {
        str(getattr(node, "segment_id", "")): node
        for node in getattr(road_segment_graph, "nodes", ()) or ()
    }
    theme_by_segment_id = {
        segment_id: theme_segment
        for theme_segment in theme_segments
        for segment_id in theme_segment.segment_ids
    }
    junction_anchors = _junction_anchor_points(placement_context, road_segment_graph)
    terminal_flags = _road_terminal_segment_flags(
        road_segment_graph,
        enable_terminal_trims=bool(junction_anchors),
    )
    normalized_granularity = _normalize_zoning_granularity(zoning_granularity)
    continuity = _clamp(float(streetwall_continuity), 0.0, 1.0)
    if nodes_by_id:
        raw_segments = _road_graph_raw_segments(road_segment_graph)
        for segment in raw_segments:
            source_node = segment.get("source_node")
            segment["theme_segment"] = _theme_segment_for_node(source_node, theme_segments, theme_by_segment_id)
    else:
        raw_segments = []
        for theme_segment in theme_segments:
            half_span = float(theme_segment.length_m) / 2.0
            raw_segments.append(
                {
                    "segment_id": f"{theme_segment.theme_id}_fallback",
                    "start_xy": (float(theme_segment.center_x_m) - half_span, 0.0),
                    "end_xy": (float(theme_segment.center_x_m) + half_span, 0.0),
                    "center_xy": (float(theme_segment.center_x_m), 0.0),
                    "station_start_m": float(theme_segment.x_start_m),
                    "station_end_m": float(theme_segment.x_end_m),
                    "station_center_m": float(theme_segment.center_x_m),
                    "source_node": None,
                    "theme_segment": theme_segment,
                }
            )
    for segment_idx, segment in enumerate(raw_segments):
        theme_segment = segment.get("theme_segment")
        if theme_segment is None:
            continue
        buildable_segment = _trim_segment_record_for_buildings(
            segment,
            terminal_flags=terminal_flags,
            junction_anchors=junction_anchors,
        )
        if buildable_segment is None:
            continue
        start_xy = tuple(float(v) for v in buildable_segment["start_xy"])
        end_xy = tuple(float(v) for v in buildable_segment["end_xy"])
        tangent_payload = _segment_tangent_normal(start_xy, end_xy)
        if tangent_payload is None:
            continue
        _tangent, _left_normal, _length_m = tangent_payload
        yaw_deg = math.degrees(math.atan2(float(end_xy[1]) - float(start_xy[1]), float(end_xy[0]) - float(start_xy[0])))
        node_streetwall_reference = _explicit_streetwall_reference_from_node(buildable_segment.get("source_node"))
        segment_id = str(buildable_segment.get("segment_id", "") or f"fallback_{segment_idx:03d}")
        profile = _resolve_side_zoning_profile(
            seed=seed,
            theme_id=theme_segment.theme_id,
            theme_name=theme_segment.theme_name,
            asymmetry_strength=asymmetry_strength,
            left_right_bias=left_right_bias,
        )
        for side_name in ("left", "right"):
            land_use_type = str(profile[f"{side_name}_land_use_type"])
            if land_use_type == "green":
                continue
            width_multiplier = float(profile[f"{side_name}_width_multiplier"])
            base_depth_m = 12.0 if land_use_type in {"commercial", "transit"} else 10.0
            parcel_depth_m = max(8.0, base_depth_m * max(width_multiplier, 0.65))
            if side_name == "left":
                roadside_outer_offset_m = float(
                    carriageway_half
                    + float(node_streetwall_reference.get("left_total_m", fallback_left_streetwall_width_m))
                )
                polygon_xz = _band_polygon_from_segment(
                    start_xy,
                    end_xy,
                    inner_offset_m=roadside_outer_offset_m,
                    outer_offset_m=roadside_outer_offset_m + float(parcel_depth_m),
                )
            else:
                roadside_outer_offset_m = -float(
                    carriageway_half
                    + float(node_streetwall_reference.get("right_total_m", fallback_right_streetwall_width_m))
                )
                polygon_xz = _band_polygon_from_segment(
                    start_xy,
                    end_xy,
                    inner_offset_m=roadside_outer_offset_m - float(parcel_depth_m),
                    outer_offset_m=roadside_outer_offset_m,
                )
            if len(polygon_xz) < 4:
                continue
            pseudo_cell = {
                "cell_id": f"{theme_segment.theme_id}_{segment_id}_{side_name}_fallback_strip",
                "polygon_xz": [[float(x), float(z)] for x, z in polygon_xz],
                "lane_role": f"{side_name}_building_buffer",
                "station_range_m": [
                    float(buildable_segment.get("station_start_m", 0.0) or 0.0),
                    float(buildable_segment.get("station_end_m", 0.0) or 0.0),
                ],
                "theme_id": theme_segment.theme_id,
                "theme_name": theme_segment.theme_name,
                "land_use_type": land_use_type,
                "buildable": True,
            }
            frontage_intervals = _frontage_intervals_for_length(
                _cell_frontage_depth(pseudo_cell)[0],
                land_use_type=land_use_type,
                zoning_granularity=normalized_granularity,
                streetwall_continuity=continuity,
            )
            for interval_idx, (start_m, end_m) in enumerate(frontage_intervals):
                footprint_id = f"{theme_segment.theme_id}_{segment_id}_{side_name}_{interval_idx:02d}"
                street_edge_xz = _street_edge_midpoint_for_interval(
                    pseudo_cell,
                    start_m=float(start_m),
                    end_m=float(end_m),
                )
                frontage_m = float(max(float(end_m) - float(start_m), 3.0))
                front_setback_m = _sample_front_setback_m(
                    seed=seed,
                    target_id=footprint_id,
                    minimum_m=front_setback_min_m,
                    maximum_m=front_setback_max_m,
                )
                placement_xz, building_depth_m, placement_strategy = _resolve_frontage_placement(
                    street_edge_xz=street_edge_xz,
                    side=side_name,
                    yaw_deg=float(yaw_deg),
                    parcel_depth_m=float(parcel_depth_m),
                    front_setback_m=float(front_setback_m),
                )
                if height_mode == "theme_random":
                    target_height_m = sample_building_target_height(
                        seed=seed,
                        target_id=footprint_id,
                        theme_name=theme_segment.theme_name,
                        land_use_type=land_use_type,
                        frontage_width_m=float(frontage_m),
                        depth_m=float(building_depth_m),
                        source="fallback",
                        height_profile=height_profile,
                    )
                    height_class = height_class_from_height_m(target_height_m)
                else:
                    target_height_m = 0.0
                    height_class = str(infer_grid_height_class(land_use_type) or "midrise")
                footprints.append(
                    BuildingFootprint(
                        footprint_id=footprint_id,
                        source="fallback",
                        polygon_xz=oriented_rectangle_points(
                            center_x=float(placement_xz[0]),
                            center_z=float(placement_xz[1]),
                            yaw_deg=float(yaw_deg),
                            length_m=float(frontage_m),
                            depth_m=float(building_depth_m),
                        ),
                        centroid_xz=(float(placement_xz[0]), float(placement_xz[1])),
                        frontage_width_m=float(frontage_m),
                        depth_m=float(building_depth_m),
                        yaw_deg=float(yaw_deg),
                        theme_id=theme_segment.theme_id,
                        land_use_type=land_use_type,
                        side=side_name,
                        height_class=height_class,
                        target_height_m=float(target_height_m),
                        anchor_geom_id=f"{theme_segment.theme_id}:{segment_id}:{side_name}:{interval_idx}",
                        size_class=_size_class(frontage_m, building_depth_m),
                        street_edge_xz=(float(street_edge_xz[0]), float(street_edge_xz[1])),
                        placement_xz=(float(placement_xz[0]), float(placement_xz[1])),
                        front_setback_m=float(front_setback_m),
                        placement_strategy=str(placement_strategy),
                        building_depth_m=float(building_depth_m),
                    )
                )
    return footprints


def generate_frontage_infill_footprints(
    zoning_grid: Sequence[Mapping[str, Any]],
    existing_footprints: Sequence[BuildingFootprint],
    *,
    seed: int = 0,
    height_mode: str = "theme_random",
    height_profile: str = "urban_default_v1",
    zoning_granularity: str = "fine",
    streetwall_continuity: float = 0.95,
    infill_policy: str = "aggressive",
    front_setback_min_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    front_setback_max_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
) -> Tuple[Tuple[BuildingFootprint, ...], Dict[str, Any]]:
    normalized_granularity = _normalize_zoning_granularity(zoning_granularity)
    normalized_policy = _normalize_infill_policy(infill_policy)
    continuity = _clamp(float(streetwall_continuity), 0.0, 1.0)
    real_footprints = tuple(footprint for footprint in existing_footprints if str(footprint.source) == "osm")
    existing_items = tuple(
        {
            "footprint_id": str(footprint.footprint_id),
            "polygon_xz": tuple((float(x), float(z)) for x, z in footprint.polygon_xz),
            "side": str(footprint.side or ""),
        }
        for footprint in existing_footprints
    )
    if not real_footprints or normalized_policy == "off":
        coverage_summary = summarize_frontage_coverage(zoning_grid, existing_items)
        return tuple(), {
            "real_footprint_count": int(len(real_footprints)),
            "infill_footprint_count": 0,
            "infill_policy": normalized_policy,
            **coverage_summary,
        }

    infill_footprints: List[BuildingFootprint] = []
    for cell in zoning_grid:
        if not bool(cell.get("buildable", False)):
            continue
        if "building_buffer" not in str(cell.get("lane_role", "") or ""):
            continue
        land_use_type = str(cell.get("land_use_type", "") or "")
        if land_use_type not in {"residential", "commercial", "transit"}:
            continue
        cell_polygon = [
            (float(point[0]), float(point[1]))
            for point in cell.get("polygon_xz", []) or []
            if len(point) >= 2
        ]
        if len(cell_polygon) < 4:
            continue
        cell_bbox = _polygon_bbox(cell_polygon)
        frontage_m, parcel_depth_m = _cell_frontage_depth(cell)
        occupied_intervals: List[Tuple[float, float]] = []
        for footprint in existing_footprints:
            footprint_polygon = tuple((float(x), float(z)) for x, z in footprint.polygon_xz)
            if len(footprint_polygon) < 4:
                continue
            if not _bbox_intersects(cell_bbox, _polygon_bbox(footprint_polygon)):
                continue
            interval = _project_polygon_to_cell_frontage_interval(cell, footprint_polygon)
            if interval is not None:
                occupied_intervals.append(interval)
        gaps = _invert_intervals(frontage_m, occupied_intervals)
        threshold_m = _large_gap_threshold_m(
            land_use_type,
            infill_policy=normalized_policy,
            streetwall_continuity=continuity,
        )
        if not math.isfinite(threshold_m):
            continue
        side = _cell_side(cell)
        yaw_deg = _cell_yaw_deg(cell)
        theme_id = str(cell.get("theme_id", "") or "")
        theme_name = str(cell.get("theme_name", "") or "")
        for gap_idx, (gap_start_m, gap_end_m) in enumerate(gaps):
            gap_length_m = float(gap_end_m) - float(gap_start_m)
            if gap_length_m < threshold_m:
                continue
            gap_intervals = _frontage_intervals_for_length(
                gap_length_m,
                land_use_type=land_use_type,
                zoning_granularity=normalized_granularity,
                streetwall_continuity=continuity,
            )
            for interval_idx, (local_start_m, local_end_m) in enumerate(gap_intervals):
                start_m = float(gap_start_m) + float(local_start_m)
                end_m = float(gap_start_m) + float(local_end_m)
                polygon_xz = _parcel_polygon_from_cell_interval(cell, start_m=start_m, end_m=end_m)
                if len(polygon_xz) < 4:
                    continue
                footprint_id = f"infill_{len(infill_footprints):03d}"
                street_edge_xz = _street_edge_midpoint_for_interval(cell, start_m=start_m, end_m=end_m)
                frontage_width_m = float(max(end_m - start_m, 3.0))
                front_setback_m = _sample_front_setback_m(
                    seed=seed,
                    target_id=footprint_id,
                    minimum_m=front_setback_min_m,
                    maximum_m=front_setback_max_m,
                )
                placement_xz, building_depth_m, placement_strategy = _resolve_frontage_placement(
                    street_edge_xz=street_edge_xz,
                    side=side,
                    yaw_deg=float(yaw_deg),
                    parcel_depth_m=float(parcel_depth_m),
                    front_setback_m=float(front_setback_m),
                )
                if height_mode == "theme_random":
                    target_height_m = sample_building_target_height(
                        seed=seed,
                        target_id=footprint_id,
                        theme_name=theme_name,
                        land_use_type=land_use_type,
                        frontage_width_m=float(frontage_width_m),
                        depth_m=float(max(building_depth_m, 4.0)),
                        source="infill",
                        height_profile=height_profile,
                    )
                    height_class = height_class_from_height_m(target_height_m)
                else:
                    target_height_m = 0.0
                    height_class = str(infer_grid_height_class(land_use_type) or "midrise")
                infill_footprints.append(
                    BuildingFootprint(
                        footprint_id=footprint_id,
                        source="infill",
                        polygon_xz=tuple((float(x), float(z)) for x, z in polygon_xz),
                        centroid_xz=(float(placement_xz[0]), float(placement_xz[1])),
                        frontage_width_m=float(frontage_width_m),
                        depth_m=float(building_depth_m),
                        yaw_deg=float(yaw_deg),
                        theme_id=theme_id,
                        land_use_type=land_use_type,
                        side=side,
                        height_class=height_class,
                        target_height_m=float(target_height_m),
                        anchor_geom_id=f"{theme_id}:{side}:{gap_idx}:{interval_idx}",
                        size_class=_size_class(frontage_width_m, building_depth_m),
                        street_edge_xz=(float(street_edge_xz[0]), float(street_edge_xz[1])),
                        placement_xz=(float(placement_xz[0]), float(placement_xz[1])),
                        front_setback_m=float(front_setback_m),
                        placement_strategy=str(placement_strategy),
                        building_depth_m=float(building_depth_m),
                    )
                )

    combined_items = existing_items + tuple(
        {
            "footprint_id": str(footprint.footprint_id),
            "polygon_xz": tuple((float(x), float(z)) for x, z in footprint.polygon_xz),
            "side": str(footprint.side or ""),
        }
        for footprint in infill_footprints
    )
    coverage_summary = summarize_frontage_coverage(zoning_grid, combined_items)
    return tuple(infill_footprints), {
        "real_footprint_count": int(len(real_footprints)),
        "infill_footprint_count": int(len(infill_footprints)),
        "infill_policy": normalized_policy,
        **coverage_summary,
    }


def _segment_tangent_normal(
    start_xy: Tuple[float, float],
    end_xy: Tuple[float, float],
) -> Tuple[Tuple[float, float], Tuple[float, float], float] | None:
    dx = float(end_xy[0]) - float(start_xy[0])
    dz = float(end_xy[1]) - float(start_xy[1])
    length = math.hypot(dx, dz)
    if length <= 1e-6:
        return None
    tangent = (dx / length, dz / length)
    left_normal = (-tangent[1], tangent[0])
    return tangent, left_normal, float(length)


def _junction_anchor_points(
    placement_context: object | None,
    road_segment_graph: object | None,
) -> Tuple[Tuple[float, float], ...]:
    anchors: List[Tuple[float, float]] = []
    for junction in getattr(road_segment_graph, "junctions", ()) or ():
        anchor_xy = tuple(float(v) for v in getattr(junction, "anchor_xy", (0.0, 0.0))[:2])
        if len(anchor_xy) == 2:
            anchors.append(anchor_xy)
    for geometry in getattr(placement_context, "junction_geometries", ()) or ():
        raw_anchor = geometry.get("anchor_xy") if isinstance(geometry, Mapping) else None
        if isinstance(raw_anchor, Sequence) and len(raw_anchor) >= 2:
            anchors.append((float(raw_anchor[0]), float(raw_anchor[1])))
    deduped: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for anchor_xy in anchors:
        deduped[(int(round(anchor_xy[0] * 1000.0)), int(round(anchor_xy[1] * 1000.0)))] = anchor_xy
    return tuple(deduped.values())


def _road_terminal_segment_flags(
    road_segment_graph: object | None,
    *,
    enable_terminal_trims: bool,
) -> Dict[str, Dict[str, bool]]:
    flags: Dict[str, Dict[str, bool]] = {}
    nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
    if not nodes:
        return flags
    nodes_by_road: Dict[int, List[object]] = {}
    for node in nodes:
        nodes_by_road.setdefault(int(getattr(node, "road_id", 0) or 0), []).append(node)
    for road_nodes in nodes_by_road.values():
        ordered_nodes = sorted(
            road_nodes,
            key=lambda node: (
                float(getattr(node, "station_start_m", 0.0) or 0.0),
                float(getattr(node, "station_end_m", 0.0) or 0.0),
                str(getattr(node, "segment_id", "") or ""),
            ),
        )
        if not ordered_nodes:
            continue
        if enable_terminal_trims:
            first_segment_id = str(getattr(ordered_nodes[0], "segment_id", "") or "")
            last_segment_id = str(getattr(ordered_nodes[-1], "segment_id", "") or "")
            if first_segment_id:
                flags.setdefault(first_segment_id, {})["start"] = True
            if last_segment_id:
                flags.setdefault(last_segment_id, {})["end"] = True
        for node in ordered_nodes:
            segment_id = str(getattr(node, "segment_id", "") or "")
            if not segment_id:
                continue
            if str(getattr(node, "start_junction_id", "") or ""):
                flags.setdefault(segment_id, {})["start"] = True
            if str(getattr(node, "end_junction_id", "") or ""):
                flags.setdefault(segment_id, {})["end"] = True
    return flags


def _point_near_anchor(
    point_xy: Tuple[float, float],
    anchors: Sequence[Tuple[float, float]],
    *,
    tolerance_m: float = _BUILDING_JUNCTION_ANCHOR_TOLERANCE_M,
) -> bool:
    threshold_sq = float(tolerance_m) * float(tolerance_m)
    return any(
        (float(point_xy[0]) - float(anchor[0])) ** 2 + (float(point_xy[1]) - float(anchor[1])) ** 2 <= threshold_sq
        for anchor in anchors
    )


def _trim_segment_record_for_buildings(
    segment: Mapping[str, Any],
    *,
    terminal_flags: Mapping[str, Mapping[str, bool]],
    junction_anchors: Sequence[Tuple[float, float]],
    exit_distance_m: float = _BUILDING_EXIT_DISTANCE_M,
) -> Dict[str, Any] | None:
    start_xy = tuple(float(v) for v in segment.get("start_xy", (0.0, 0.0)))
    end_xy = tuple(float(v) for v in segment.get("end_xy", (0.0, 0.0)))
    tangent_payload = _segment_tangent_normal(start_xy, end_xy)
    if tangent_payload is None:
        return None
    tangent, _left_normal, length_m = tangent_payload
    segment_id = str(segment.get("segment_id", "") or "")
    source_node = segment.get("source_node")
    endpoint_flags = terminal_flags.get(segment_id, {})
    start_protected = bool(endpoint_flags.get("start", False)) or _point_near_anchor(start_xy, junction_anchors)
    end_protected = bool(endpoint_flags.get("end", False)) or _point_near_anchor(end_xy, junction_anchors)
    if source_node is not None:
        start_protected = start_protected or bool(str(getattr(source_node, "start_junction_id", "") or ""))
        end_protected = end_protected or bool(str(getattr(source_node, "end_junction_id", "") or ""))

    start_trim_m = min(float(exit_distance_m), max(length_m - _BUILDING_MIN_SEGMENT_SPAN_M, 0.0)) if start_protected else 0.0
    remaining_after_start_m = max(length_m - start_trim_m, 0.0)
    end_trim_m = min(
        float(exit_distance_m),
        max(remaining_after_start_m - _BUILDING_MIN_SEGMENT_SPAN_M, 0.0),
    ) if end_protected else 0.0
    buildable_length_m = float(length_m - start_trim_m - end_trim_m)
    if buildable_length_m < _BUILDING_MIN_SEGMENT_SPAN_M:
        return None

    trimmed_start_xy = (
        float(start_xy[0]) + tangent[0] * float(start_trim_m),
        float(start_xy[1]) + tangent[1] * float(start_trim_m),
    )
    trimmed_end_xy = (
        float(end_xy[0]) - tangent[0] * float(end_trim_m),
        float(end_xy[1]) - tangent[1] * float(end_trim_m),
    )
    station_start_m = float(segment.get("station_start_m", 0.0) or 0.0) + float(start_trim_m)
    station_end_m = float(segment.get("station_end_m", 0.0) or 0.0) - float(end_trim_m)
    return {
        **segment,
        "start_xy": trimmed_start_xy,
        "end_xy": trimmed_end_xy,
        "center_xy": (
            float((trimmed_start_xy[0] + trimmed_end_xy[0]) / 2.0),
            float((trimmed_start_xy[1] + trimmed_end_xy[1]) / 2.0),
        ),
        "station_start_m": float(station_start_m),
        "station_end_m": float(station_end_m),
        "station_center_m": float((station_start_m + station_end_m) / 2.0),
        "start_exit_trim_m": float(start_trim_m),
        "end_exit_trim_m": float(end_trim_m),
        "buildable_length_m": float(buildable_length_m),
    }


def _band_polygon_from_segment(
    start_xy: Tuple[float, float],
    end_xy: Tuple[float, float],
    *,
    inner_offset_m: float,
    outer_offset_m: float,
) -> Tuple[Tuple[float, float], ...]:
    tangent_payload = _segment_tangent_normal(start_xy, end_xy)
    if tangent_payload is None:
        return tuple()
    _tangent, left_normal, _length = tangent_payload
    inner = float(inner_offset_m)
    outer = float(outer_offset_m)

    def _offset(point: Tuple[float, float], distance_m: float) -> Tuple[float, float]:
        return (
            float(point[0]) + left_normal[0] * float(distance_m),
            float(point[1]) + left_normal[1] * float(distance_m),
        )

    polygon = (
        _offset(start_xy, inner),
        _offset(end_xy, inner),
        _offset(end_xy, outer),
        _offset(start_xy, outer),
        _offset(start_xy, inner),
    )
    return tuple((float(x), float(z)) for x, z in polygon)


def _build_buildable_corridor_geometry(
    *,
    placement_context: object | None,
    road_segment_graph: object | None,
    carriageway_width_m: float,
    fallback_left_streetwall_width_m: float,
    fallback_right_streetwall_width_m: float,
    road_buffer_m: float,
) -> Any | None:
    try:
        from shapely.geometry import Point as ShapelyPoint
        from shapely.geometry import Polygon as ShapelyPolygon
        from shapely.ops import unary_union
    except Exception:
        return None

    raw_segments = _road_graph_raw_segments(road_segment_graph)
    if not raw_segments:
        return None
    junction_anchors = _junction_anchor_points(placement_context, road_segment_graph)
    terminal_flags = _road_terminal_segment_flags(
        road_segment_graph,
        enable_terminal_trims=bool(junction_anchors),
    )
    carriageway_half = float(carriageway_width_m) * 0.5
    band_geometries: List[Any] = []
    for segment in raw_segments:
        buildable_segment = _trim_segment_record_for_buildings(
            segment,
            terminal_flags=terminal_flags,
            junction_anchors=junction_anchors,
        )
        if buildable_segment is None:
            continue
        segment_reference = _explicit_streetwall_reference_from_node(buildable_segment.get("source_node"))
        left_total_m = float(segment_reference.get("left_total_m", fallback_left_streetwall_width_m))
        right_total_m = float(segment_reference.get("right_total_m", fallback_right_streetwall_width_m))
        for polygon_xz in (
            _band_polygon_from_segment(
                tuple(buildable_segment["start_xy"]),
                tuple(buildable_segment["end_xy"]),
                inner_offset_m=float(carriageway_half + left_total_m),
                outer_offset_m=float(carriageway_half + left_total_m + float(road_buffer_m)),
            ),
            _band_polygon_from_segment(
                tuple(buildable_segment["start_xy"]),
                tuple(buildable_segment["end_xy"]),
                inner_offset_m=-float(carriageway_half + right_total_m + float(road_buffer_m)),
                outer_offset_m=-float(carriageway_half + right_total_m),
            ),
        ):
            if len(polygon_xz) >= 4:
                band_geometries.append(ShapelyPolygon(polygon_xz))
    if not band_geometries:
        return None
    buildable_corridor = unary_union(band_geometries)
    if junction_anchors:
        junction_buffer = unary_union(
            [ShapelyPoint(float(anchor[0]), float(anchor[1])).buffer(_BUILDING_JUNCTION_BUFFER_M) for anchor in junction_anchors]
        )
        buildable_corridor = buildable_corridor.difference(junction_buffer)
    building_region_union = _building_region_union_geometry(placement_context)
    if building_region_union is not None and not getattr(building_region_union, "is_empty", True):
        buildable_corridor = buildable_corridor.intersection(building_region_union)
    if getattr(buildable_corridor, "is_empty", True):
        return None
    return buildable_corridor.buffer(0)


def _theme_segment_for_station(
    station_m: float,
    theme_segments: Sequence[ThemeSegment],
) -> ThemeSegment | None:
    if not theme_segments:
        return None
    for theme_segment in theme_segments:
        if float(theme_segment.x_start_m) - 1e-6 <= float(station_m) <= float(theme_segment.x_end_m) + 1e-6:
            return theme_segment
    return min(theme_segments, key=lambda item: abs(float(item.center_x_m) - float(station_m)))


def _theme_segment_for_node(
    node: object,
    theme_segments: Sequence[ThemeSegment],
    theme_by_segment_id: Mapping[str, ThemeSegment],
) -> ThemeSegment | None:
    segment_id = str(getattr(node, "segment_id", ""))
    if segment_id in theme_by_segment_id:
        return theme_by_segment_id[segment_id]
    return _theme_segment_for_station(float(getattr(node, "station_center_m", 0.0) or 0.0), theme_segments)


def _road_graph_raw_segments(road_segment_graph: object | None) -> List[Dict[str, Any]]:
    if road_segment_graph is None or not getattr(road_segment_graph, "nodes", None):
        return []
    nodes = sorted(
        list(getattr(road_segment_graph, "nodes", ()) or ()),
        key=lambda node: (
            int(getattr(node, "road_id", 0) or 0),
            float(getattr(node, "station_start_m", 0.0) or 0.0),
            float(getattr(node, "station_end_m", 0.0) or 0.0),
            str(getattr(node, "segment_id", "") or ""),
        ),
    )
    return [
        {
            "segment_id": str(getattr(node, "segment_id", "")),
            "start_xy": tuple(float(v) for v in getattr(node, "start_xy", (0.0, 0.0))),
            "end_xy": tuple(float(v) for v in getattr(node, "end_xy", (0.0, 0.0))),
            "center_xy": tuple(float(v) for v in getattr(node, "center_xy", (0.0, 0.0))),
            "station_start_m": float(getattr(node, "station_start_m", 0.0) or 0.0),
            "station_end_m": float(getattr(node, "station_end_m", 0.0) or 0.0),
            "station_center_m": float(getattr(node, "station_center_m", 0.0) or 0.0),
            "source_node": node,
        }
        for node in nodes
    ]


def _polygon_center(polygon_xz: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    points = list(polygon_xz[:-1] if len(polygon_xz) >= 2 and polygon_xz[0] == polygon_xz[-1] else polygon_xz)
    if not points:
        return (0.0, 0.0)
    return (
        float(sum(float(point[0]) for point in points) / len(points)),
        float(sum(float(point[1]) for point in points) / len(points)),
    )


def _polygon_bbox(polygon_xz: Sequence[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [float(point[0]) for point in polygon_xz]
    zs = [float(point[1]) for point in polygon_xz]
    return (min(xs), max(xs), min(zs), max(zs))


def _bbox_intersects(
    left: Tuple[float, float, float, float],
    right: Tuple[float, float, float, float],
) -> bool:
    return not (
        float(left[1]) <= float(right[0])
        or float(right[1]) <= float(left[0])
        or float(left[3]) <= float(right[2])
        or float(right[3]) <= float(left[2])
    )


def _estimate_building_buffer_widths(
    *,
    building_footprints: Sequence[BuildingFootprint],
    road_segment_graph: object | None,
    carriageway_width_m: float,
    left_sidewalk_width_m: float,
    right_sidewalk_width_m: float,
    road_buffer_m: float,
) -> Tuple[float, float]:
    default_width = min(float(road_buffer_m), max(float(left_sidewalk_width_m), float(right_sidewalk_width_m), 8.0))
    nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
    if not nodes or not building_footprints:
        return float(default_width), float(default_width)

    carriageway_half = float(carriageway_width_m) / 2.0
    left_buffer = float(default_width)
    right_buffer = float(default_width)
    for footprint in building_footprints:
        centroid = (float(footprint.centroid_xz[0]), float(footprint.centroid_xz[1]))
        nearest = min(
            nodes,
            key=lambda node: math.hypot(
                float(getattr(node, "center_xy", (0.0, 0.0))[0]) - centroid[0],
                float(getattr(node, "center_xy", (0.0, 0.0))[1]) - centroid[1],
            ),
        )
        tangent_payload = _segment_tangent_normal(
            tuple(float(v) for v in getattr(nearest, "start_xy", (0.0, 0.0))),
            tuple(float(v) for v in getattr(nearest, "end_xy", (0.0, 0.0))),
        )
        if tangent_payload is None:
            continue
        _tangent, left_normal, _segment_length = tangent_payload
        dx = centroid[0] - float(getattr(nearest, "center_xy", (0.0, 0.0))[0])
        dz = centroid[1] - float(getattr(nearest, "center_xy", (0.0, 0.0))[1])
        lateral = dx * left_normal[0] + dz * left_normal[1]
        sidewalk_width_m = float(left_sidewalk_width_m) if lateral >= 0.0 else float(right_sidewalk_width_m)
        extent = max(float(footprint.depth_m) * 0.65, 4.0)
        needed = max(abs(lateral) - carriageway_half - sidewalk_width_m + extent, default_width)
        if lateral >= 0.0:
            left_buffer = max(left_buffer, min(float(road_buffer_m), float(needed)))
        else:
            right_buffer = max(right_buffer, min(float(road_buffer_m), float(needed)))
    return float(left_buffer), float(right_buffer)


def _rebalance_building_buffer_widths(
    *,
    left_width_m: float,
    right_width_m: float,
    road_buffer_m: float,
    default_width_m: float,
    asymmetry_strength: float,
    force_streetwall_baseline: bool,
    max_gap_ratio: float = _DEFAULT_MAX_BUFFER_WIDTH_GAP_RATIO,
) -> Tuple[float, float]:
    left = _clamp(float(left_width_m), 8.0, float(road_buffer_m))
    right = _clamp(float(right_width_m), 8.0, float(road_buffer_m))
    baseline = _clamp(float(default_width_m), 8.0, float(road_buffer_m))

    if bool(force_streetwall_baseline) and float(asymmetry_strength) <= 1e-6:
        return float(baseline), float(baseline)

    max_gap = max(0.0, float(max_gap_ratio)) * max(left, right, 1e-6)
    current_gap = abs(left - right)
    if current_gap <= max_gap + 1e-6:
        return float(left), float(right)

    mean_width = (left + right) / 2.0
    half_gap = max_gap / 2.0
    if left >= right:
        left = mean_width + half_gap
        right = mean_width - half_gap
    else:
        left = mean_width - half_gap
        right = mean_width + half_gap
    return (
        _clamp(float(left), 8.0, float(road_buffer_m)),
        _clamp(float(right), 8.0, float(road_buffer_m)),
    )


def _streetwall_reference_caps(
    *,
    design_rule_profile: str,
    sidewalk_seed_width_m: float,
) -> Tuple[float, float]:
    profile_name = str(design_rule_profile or "balanced_complete_street_v1").strip().lower()
    clear_cap = max(float(sidewalk_seed_width_m), 3.0)
    edge_cap = 2.0
    if profile_name == "pedestrian_priority_v1":
        clear_cap = max(clear_cap, 3.6)
        edge_cap = 2.2
    elif profile_name == "transit_priority_v1":
        clear_cap = max(clear_cap, 3.0)
        edge_cap = 2.2
    return float(clear_cap), float(edge_cap)


def _rebalance_gap_ratio(
    *,
    left_value: float,
    right_value: float,
    lower_bound: float,
    upper_bound: float,
    max_gap_ratio: float,
    force_equal: bool,
) -> Tuple[float, float]:
    left = _clamp(float(left_value), float(lower_bound), float(upper_bound))
    right = _clamp(float(right_value), float(lower_bound), float(upper_bound))
    if bool(force_equal):
        mean_value = _clamp((left + right) / 2.0, float(lower_bound), float(upper_bound))
        return float(mean_value), float(mean_value)

    max_gap = max(0.0, float(max_gap_ratio)) * max(left, right, 1e-6)
    current_gap = abs(left - right)
    if current_gap <= max_gap + 1e-6:
        return float(left), float(right)

    mean_value = (left + right) / 2.0
    half_gap = max_gap / 2.0
    if left >= right:
        left = mean_value + half_gap
        right = mean_value - half_gap
    else:
        left = mean_value - half_gap
        right = mean_value + half_gap
    return (
        _clamp(float(left), float(lower_bound), float(upper_bound)),
        _clamp(float(right), float(lower_bound), float(upper_bound)),
    )


def _streetwall_reference_widths(
    *,
    design_rule_profile: str,
    sidewalk_seed_width_m: float,
    placement_context: object | None,
    asymmetry_strength: float,
    force_streetwall_baseline: bool,
) -> Dict[str, float]:
    raw_left_clear = float(getattr(placement_context, "left_clear_path_width_m", 0.0) or 0.0)
    raw_right_clear = float(getattr(placement_context, "right_clear_path_width_m", 0.0) or 0.0)
    raw_left_edge = float(getattr(placement_context, "left_furnishing_width_m", 0.0) or 0.0)
    raw_right_edge = float(getattr(placement_context, "right_furnishing_width_m", 0.0) or 0.0)
    if raw_left_clear <= 0.0:
        raw_left_clear = float(sidewalk_seed_width_m)
    if raw_right_clear <= 0.0:
        raw_right_clear = float(sidewalk_seed_width_m)

    clear_cap_m, edge_cap_m = _streetwall_reference_caps(
        design_rule_profile=str(design_rule_profile),
        sidewalk_seed_width_m=float(sidewalk_seed_width_m),
    )
    min_clear_m = min(float(clear_cap_m), max(float(sidewalk_seed_width_m), 2.2))
    min_edge_m = 0.8
    left_clear = _clamp(float(raw_left_clear), float(min_clear_m), float(clear_cap_m))
    right_clear = _clamp(float(raw_right_clear), float(min_clear_m), float(clear_cap_m))
    left_edge = _clamp(float(raw_left_edge if raw_left_edge > 0.0 else min_edge_m), float(min_edge_m), float(edge_cap_m))
    right_edge = _clamp(float(raw_right_edge if raw_right_edge > 0.0 else min_edge_m), float(min_edge_m), float(edge_cap_m))

    force_equal = bool(force_streetwall_baseline) and float(asymmetry_strength) <= 1e-6
    left_total, right_total = _rebalance_gap_ratio(
        left_value=float(left_clear + left_edge),
        right_value=float(right_clear + right_edge),
        lower_bound=float(min_clear_m + min_edge_m),
        upper_bound=float(clear_cap_m + edge_cap_m),
        max_gap_ratio=_DEFAULT_MAX_STREETWALL_REFERENCE_GAP_RATIO,
        force_equal=force_equal,
    )
    return {
        "raw_left_total_m": float(raw_left_clear + raw_left_edge),
        "raw_right_total_m": float(raw_right_clear + raw_right_edge),
        "left_total_m": float(left_total),
        "right_total_m": float(right_total),
        "clear_cap_m": float(clear_cap_m),
        "edge_cap_m": float(edge_cap_m),
    }


def _explicit_streetwall_reference_from_strips(
    strips: Sequence[object],
) -> Dict[str, float]:
    summary: Dict[str, Dict[str, float]] = {
        "left": {"total_m": 0.0, "frontage_reserve_m": 0.0},
        "right": {"total_m": 0.0, "frontage_reserve_m": 0.0},
    }
    for strip in strips:
        zone = str(getattr(strip, "zone", "") or "").strip().lower()
        if zone not in {"left", "right"}:
            continue
        width_m = max(float(getattr(strip, "width_m", 0.0) or 0.0), 0.0)
        if width_m <= 0.0:
            continue
        kind = str(getattr(strip, "kind", "") or "").strip().lower()
        summary[zone]["total_m"] += float(width_m)
        if kind == "frontage_reserve":
            summary[zone]["frontage_reserve_m"] += float(width_m)
    if (
        summary["left"]["total_m"] <= 1e-6
        and summary["right"]["total_m"] <= 1e-6
    ):
        return {}
    return {
        "left_total_m": float(summary["left"]["total_m"]),
        "right_total_m": float(summary["right"]["total_m"]),
        "left_frontage_reserve_m": float(summary["left"]["frontage_reserve_m"]),
        "right_frontage_reserve_m": float(summary["right"]["frontage_reserve_m"]),
    }


def _explicit_streetwall_reference_from_node(node: object | None) -> Dict[str, float]:
    if node is None:
        return {}
    return _explicit_streetwall_reference_from_strips(
        tuple(getattr(node, "cross_section_strips", ()) or ())
    )


def _explicit_streetwall_reference_from_graph(
    road_segment_graph: object | None,
) -> Dict[str, float]:
    nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
    if not nodes:
        return {}
    left_totals: List[float] = []
    right_totals: List[float] = []
    left_frontage: List[float] = []
    right_frontage: List[float] = []
    for node in nodes:
        reference = _explicit_streetwall_reference_from_node(node)
        if not reference:
            continue
        if float(reference.get("left_total_m", 0.0) or 0.0) > 0.0:
            left_totals.append(float(reference["left_total_m"]))
        if float(reference.get("right_total_m", 0.0) or 0.0) > 0.0:
            right_totals.append(float(reference["right_total_m"]))
        if float(reference.get("left_frontage_reserve_m", 0.0) or 0.0) > 0.0:
            left_frontage.append(float(reference["left_frontage_reserve_m"]))
        if float(reference.get("right_frontage_reserve_m", 0.0) or 0.0) > 0.0:
            right_frontage.append(float(reference["right_frontage_reserve_m"]))
    if not left_totals and not right_totals:
        return {}
    return {
        "left_total_m": float(max(left_totals) if left_totals else 0.0),
        "right_total_m": float(max(right_totals) if right_totals else 0.0),
        "left_frontage_reserve_m": float(max(left_frontage) if left_frontage else 0.0),
        "right_frontage_reserve_m": float(max(right_frontage) if right_frontage else 0.0),
    }


def _fallback_zoning_segments(
    *,
    theme_segments: Sequence[ThemeSegment],
    config: StreetComposeConfig,
) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    segment_counter = 0
    for theme_segment in theme_segments:
        span = max(float(theme_segment.length_m), 1.0)
        subdivisions = max(1, int(math.ceil(span / max(float(config.segment_length_m), 1.0))))
        start_m = float(theme_segment.x_start_m)
        end_m = float(theme_segment.x_end_m)
        step = (end_m - start_m) / float(subdivisions)
        for idx in range(subdivisions):
            seg_start = start_m + float(idx) * step
            seg_end = start_m + float(idx + 1) * step
            segment_id = f"zoning_seg_{segment_counter:04d}"
            segment_counter += 1
            segments.append(
                {
                    "segment_id": segment_id,
                    "start_xy": (float(seg_start), 0.0),
                    "end_xy": (float(seg_end), 0.0),
                    "center_xy": (float((seg_start + seg_end) / 2.0), 0.0),
                    "station_start_m": float(seg_start),
                    "station_end_m": float(seg_end),
                    "station_center_m": float((seg_start + seg_end) / 2.0),
                    "theme_segment": theme_segment,
                    "source_node": None,
                }
            )
    return segments


def build_zoning_grid_preview(
    *,
    config: StreetComposeConfig,
    placement_context: object | None,
    road_segment_graph: object | None,
    theme_segments: Sequence[ThemeSegment],
    building_footprints: Sequence[BuildingFootprint],
    road_buffer_m: float = 35.0,
) -> Tuple[Tuple[Dict[str, Any], ...], Dict[str, Any]]:
    auto_land_use_mode = str(getattr(config, "auto_land_use_mode", "road_buffer") or "road_buffer").strip().lower()
    if auto_land_use_mode == "off":
        return tuple(), {
            "enabled": False,
            "auto_land_use_enabled": False,
            "cell_count": 0,
            "theme_cell_counts": {},
            "building_cell_counts": {},
            "occupied_building_cells": 0,
            "buildable_cell_count": 0,
            "side_land_use_counts": {"left": {}, "right": {}},
            "active_side_counts": {},
            "building_buffer_width_m": {"left": 0.0, "right": 0.0},
            "streetwall_reference_width_m": {"left": 0.0, "right": 0.0},
            "streetwall_reference_gap_ratio": 0.0,
            "zoning_preview_mode": "disabled",
            "frontage_cell_count": 0,
            "theme_segment_count": int(len(theme_segments)),
            "buildable_frontage_by_side": {"left": 0.0, "right": 0.0},
            "merged_land_use_polygon_count": 0,
            "road_split_count": 0,
            "sliver_removed_count": 0,
            "junction_clip_count": 0,
        }
    asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.0)
    bias_raw = getattr(config, "left_right_bias", 0.0)
    zoning_granularity_raw = getattr(config, "zoning_granularity", "fine")
    streetwall_continuity_raw = getattr(config, "streetwall_continuity", 0.95)
    asymmetry_strength = _clamp(float(0.0 if asymmetry_raw is None else asymmetry_raw), 0.0, 1.0)
    left_right_bias = _clamp(float(0.0 if bias_raw is None else bias_raw), -1.0, 1.0)
    normalized_granularity = _normalize_zoning_granularity(
        str("fine" if zoning_granularity_raw is None else zoning_granularity_raw)
    )
    continuity = _clamp(float(0.95 if streetwall_continuity_raw is None else streetwall_continuity_raw), 0.0, 1.0)
    force_streetwall_baseline = str(
        getattr(config, "surrounding_building_mode", "grid_growth") or "grid_growth"
    ).strip().lower() == "grid_growth"
    theme_by_segment_id = {
        segment_id: theme_segment
        for theme_segment in theme_segments
        for segment_id in theme_segment.segment_ids
    }
    carriageway_width_m = float(
        getattr(placement_context, "carriageway_width_m", 0.0)
        or float(config.road_width_m)
    )
    streetwall_reference = _streetwall_reference_widths(
        design_rule_profile=str(getattr(config, "design_rule_profile", "balanced_complete_street_v1") or "balanced_complete_street_v1"),
        sidewalk_seed_width_m=float(getattr(config, "sidewalk_width_m", 2.4) or 2.4),
        placement_context=placement_context,
        asymmetry_strength=float(asymmetry_strength),
        force_streetwall_baseline=bool(force_streetwall_baseline),
    )
    explicit_streetwall_reference = _explicit_streetwall_reference_from_graph(road_segment_graph)
    reference_left_streetwall_width_m = float(
        explicit_streetwall_reference.get("left_total_m", streetwall_reference["left_total_m"])
    )
    reference_right_streetwall_width_m = float(
        explicit_streetwall_reference.get("right_total_m", streetwall_reference["right_total_m"])
    )
    left_building_buffer_m, right_building_buffer_m = _estimate_building_buffer_widths(
        building_footprints=building_footprints,
        road_segment_graph=road_segment_graph,
        carriageway_width_m=carriageway_width_m,
        left_sidewalk_width_m=reference_left_streetwall_width_m,
        right_sidewalk_width_m=reference_right_streetwall_width_m,
        road_buffer_m=float(road_buffer_m),
    )
    default_buffer_width_m = min(
        float(road_buffer_m),
        max(float(reference_left_streetwall_width_m), float(reference_right_streetwall_width_m), 8.0),
    )
    left_building_buffer_m, right_building_buffer_m = _rebalance_building_buffer_widths(
        left_width_m=float(left_building_buffer_m),
        right_width_m=float(right_building_buffer_m),
        road_buffer_m=float(road_buffer_m),
        default_width_m=float(default_buffer_width_m),
        asymmetry_strength=float(asymmetry_strength),
        force_streetwall_baseline=bool(force_streetwall_baseline),
    )

    raw_segments: List[Dict[str, Any]] = []
    if road_segment_graph is not None and getattr(road_segment_graph, "nodes", None):
        raw_segments = _road_graph_raw_segments(road_segment_graph)
        for segment in raw_segments:
            segment["theme_segment"] = _theme_segment_for_node(
                segment.get("source_node"),
                theme_segments,
                theme_by_segment_id,
            )
    else:
        raw_segments = _fallback_zoning_segments(theme_segments=theme_segments, config=config)
    junction_anchors = _junction_anchor_points(placement_context, road_segment_graph)
    terminal_flags = _road_terminal_segment_flags(
        road_segment_graph,
        enable_terminal_trims=bool(junction_anchors),
    )

    if not raw_segments:
        return tuple(), {
            "enabled": False,
            "auto_land_use_enabled": True,
            "auto_land_use_mode": "road_buffer",
            "cell_count": 0,
            "theme_cell_counts": {},
            "building_cell_counts": {},
            "occupied_building_cells": 0,
            "buildable_cell_count": 0,
            "side_land_use_counts": {"left": {}, "right": {}},
            "active_side_counts": {},
            "building_buffer_width_m": {"left": 0.0, "right": 0.0},
            "streetwall_reference_width_m": {"left": 0.0, "right": 0.0},
            "streetwall_reference_gap_ratio": 0.0,
            "asymmetry_strength": float(asymmetry_strength),
            "left_right_bias": float(left_right_bias),
            "zoning_preview_mode": "parcel_first",
            "frontage_cell_count": 0,
            "theme_segment_count": int(len(theme_segments)),
            "buildable_frontage_by_side": {"left": 0.0, "right": 0.0},
            "merged_land_use_polygon_count": 0,
            "road_split_count": 0,
            "sliver_removed_count": 0,
            "junction_clip_count": 0,
        }

    try:
        from shapely.geometry import Polygon as ShapelyPolygon
    except Exception:
        ShapelyPolygon = None  # type: ignore[assignment]
    building_regions = _normalized_building_region_records(placement_context)
    min_land_use_polygon_area_m2 = max(float(getattr(config, "min_land_use_polygon_area_m2", 12.0) or 12.0), 0.0)

    footprint_records: List[Dict[str, Any]] = []
    for footprint in building_footprints:
        polygon_xz = tuple((float(x), float(z)) for x, z in footprint.polygon_xz)
        footprint_records.append(
            {
                "footprint_id": str(footprint.footprint_id),
                "theme_id": str(footprint.theme_id),
                "source": str(footprint.source),
                "polygon_xz": polygon_xz,
                "bbox": _polygon_bbox(polygon_xz),
                "geom": ShapelyPolygon(polygon_xz) if ShapelyPolygon is not None and len(polygon_xz) >= 4 else None,
            }
        )

    carriageway_half = float(carriageway_width_m) / 2.0
    building_roles = {"left_building_buffer", "right_building_buffer"}

    cells: List[Dict[str, Any]] = []
    theme_cell_counts: Dict[str, int] = {}
    building_cell_counts: Dict[str, int] = {}
    side_land_use_counts: Dict[str, Dict[str, int]] = {"left": {}, "right": {}}
    active_side_counts: Dict[str, int] = {}
    buffer_width_accum: Dict[str, List[float]] = {"left": [], "right": []}
    occupied_building_cells = 0
    for segment_idx, segment in enumerate(raw_segments):
        buildable_segment = _trim_segment_record_for_buildings(
            segment,
            terminal_flags=terminal_flags,
            junction_anchors=junction_anchors,
        )
        if buildable_segment is None:
            continue
        segment = buildable_segment
        theme_segment = segment.get("theme_segment")
        theme_id = str(getattr(theme_segment, "theme_id", "") or "")
        theme_name = str(getattr(theme_segment, "theme_name", "") or "commercial")
        side_profile = _resolve_side_zoning_profile(
            seed=int(getattr(config, "seed", 0) or 0),
            theme_id=theme_id or f"seg_{segment_idx:03d}",
            theme_name=theme_name,
            asymmetry_strength=asymmetry_strength,
            left_right_bias=left_right_bias,
            force_streetwall_baseline=force_streetwall_baseline,
        )
        if str(side_profile.get("active_side", "") or ""):
            active_side = str(side_profile["active_side"])
            active_side_counts[active_side] = active_side_counts.get(active_side, 0) + 1
        segment_left_building_buffer_m = _clamp(
            float(left_building_buffer_m) * float(side_profile.get("left_width_multiplier", 1.0) or 1.0),
            8.0,
            float(road_buffer_m),
        )
        segment_right_building_buffer_m = _clamp(
            float(right_building_buffer_m) * float(side_profile.get("right_width_multiplier", 1.0) or 1.0),
            8.0,
            float(road_buffer_m),
        )
        buffer_width_accum["left"].append(float(segment_left_building_buffer_m))
        buffer_width_accum["right"].append(float(segment_right_building_buffer_m))
        explicit_segment_reference = _explicit_streetwall_reference_from_node(segment.get("source_node"))
        segment_left_streetwall_width_m = float(
            explicit_segment_reference.get("left_total_m", reference_left_streetwall_width_m)
        )
        segment_right_streetwall_width_m = float(
            explicit_segment_reference.get("right_total_m", reference_right_streetwall_width_m)
        )
        segment_left_sidewalk_width_m = float(streetwall_reference["left_total_m"])
        segment_right_sidewalk_width_m = float(streetwall_reference["right_total_m"])
        lane_specs = (
            ("left_building_buffer", float(carriageway_half + segment_left_streetwall_width_m), float(carriageway_half + segment_left_streetwall_width_m + segment_left_building_buffer_m)),
            ("left_sidewalk", float(carriageway_half), float(carriageway_half + segment_left_sidewalk_width_m)),
            ("carriageway", -float(carriageway_half), float(carriageway_half)),
            ("right_sidewalk", -float(carriageway_half + segment_right_sidewalk_width_m), -float(carriageway_half)),
            ("right_building_buffer", -float(carriageway_half + segment_right_streetwall_width_m + segment_right_building_buffer_m), -float(carriageway_half + segment_right_streetwall_width_m)),
        )
        segment_ids = [str(segment["segment_id"])]
        for lane_role, inner_offset_m, outer_offset_m in lane_specs:
            band_polygon_xz = _band_polygon_from_segment(
                tuple(segment["start_xy"]),
                tuple(segment["end_xy"]),
                inner_offset_m=float(inner_offset_m),
                outer_offset_m=float(outer_offset_m),
            )
            if not band_polygon_xz:
                continue
            if lane_role == "left_building_buffer":
                land_use_type = str(side_profile.get("left_land_use_type", land_use_for_theme(theme_name)))
                building_buffer_width_for_cell = float(segment_left_building_buffer_m)
            elif lane_role == "right_building_buffer":
                land_use_type = str(side_profile.get("right_land_use_type", land_use_for_theme(theme_name)))
                building_buffer_width_for_cell = float(segment_right_building_buffer_m)
            else:
                land_use_type = ""
                building_buffer_width_for_cell = 0.0

            subcells: List[Tuple[Tuple[Tuple[float, float], ...], float, float, Tuple[float, float] | None, str]] = []
            if lane_role in building_roles:
                template_cell = {
                    "lane_role": lane_role,
                    "polygon_xz": [[float(x), float(z)] for x, z in band_polygon_xz],
                    "station_range_m": [
                        float(segment.get("station_start_m", 0.0) or 0.0),
                        float(segment.get("station_end_m", 0.0) or 0.0),
                    ],
                }
                frontage_m, _depth_m = _cell_frontage_depth(template_cell)
                frontage_intervals = _preview_frontage_intervals_for_length(
                    frontage_m,
                    land_use_type=land_use_type,
                    zoning_granularity=normalized_granularity,
                    streetwall_continuity=continuity,
                )
                if not frontage_intervals:
                    frontage_intervals = ((0.0, float(frontage_m)),)
                station_start_m = float(segment.get("station_start_m", 0.0) or 0.0)
                station_end_m = float(segment.get("station_end_m", 0.0) or 0.0)
                station_span_m = float(station_end_m - station_start_m)
                for interval_idx, (start_m, end_m) in enumerate(frontage_intervals):
                    polygon_xz = _parcel_polygon_from_cell_interval(
                        template_cell,
                        start_m=float(start_m),
                        end_m=float(end_m),
                    )
                    if not polygon_xz:
                        continue
                    if frontage_m > 1e-6 and abs(station_span_m) > 1e-6:
                        cell_station_start_m = station_start_m + station_span_m * (float(start_m) / frontage_m)
                        cell_station_end_m = station_start_m + station_span_m * (float(end_m) / frontage_m)
                    else:
                        cell_station_start_m = station_start_m
                        cell_station_end_m = station_end_m
                    subcells.append(
                        (
                            polygon_xz,
                            float(cell_station_start_m),
                            float(cell_station_end_m),
                            _street_edge_midpoint_for_interval(
                                template_cell,
                                start_m=float(start_m),
                                end_m=float(end_m),
                            ),
                            f"zone_{segment_idx:03d}_{lane_role}_{interval_idx:02d}",
                        )
                    )
            else:
                subcells.append(
                    (
                        band_polygon_xz,
                        float(segment.get("station_start_m", 0.0) or 0.0),
                        float(segment.get("station_end_m", 0.0) or 0.0),
                        None,
                        f"zone_{segment_idx:03d}_{lane_role}",
                    )
                )

            for polygon_xz, cell_station_start_m, cell_station_end_m, street_edge_xz, cell_id in subcells:
                cell_geom = ShapelyPolygon(polygon_xz) if ShapelyPolygon is not None else None
                if lane_role in building_roles and cell_geom is not None and float(getattr(cell_geom, "area", 0.0) or 0.0) < min_land_use_polygon_area_m2:
                    continue
                cell_bbox = _polygon_bbox(polygon_xz)
                footprint_ids: List[str] = []
                footprint_source_counts: Dict[str, int] = {}
                matched_region = (
                    _last_matching_building_region_for_polygon(
                        polygon_xz,
                        building_regions,
                        polygon_geom=cell_geom,
                    )
                    if lane_role in building_roles
                    else None
                )
                if lane_role in building_roles:
                    for footprint in footprint_records:
                        intersects = False
                        if cell_geom is not None and footprint["geom"] is not None:
                            intersects = bool(footprint["geom"].intersects(cell_geom))
                        else:
                            intersects = _bbox_intersects(cell_bbox, footprint["bbox"])
                        if not intersects:
                            continue
                        footprint_ids.append(str(footprint["footprint_id"]))
                        source_name = str(footprint["source"])
                        footprint_source_counts[source_name] = footprint_source_counts.get(source_name, 0) + 1
                    building_cell_counts[lane_role] = building_cell_counts.get(lane_role, 0) + 1
                    if footprint_ids:
                        occupied_building_cells += 1
                    side_name = "left" if lane_role == "left_building_buffer" else "right"
                    side_land_use_counts[side_name][land_use_type] = side_land_use_counts[side_name].get(land_use_type, 0) + 1

                base_buildable = bool(lane_role in building_roles and land_use_type and land_use_type != "green")
                buildable = bool(base_buildable)
                cell_center = _polygon_center(polygon_xz)
                cells.append(
                    {
                        "cell_id": cell_id,
                        "polygon_xz": [[float(x), float(z)] for x, z in polygon_xz],
                        "center_xz": [float(cell_center[0]), float(cell_center[1])],
                        "lane_role": lane_role,
                        "side": _cell_side({"lane_role": lane_role}),
                        "theme_id": theme_id,
                        "theme_name": theme_name,
                        "land_use_type": land_use_type,
                        "buildable": bool(buildable),
                        "lot_id": "",
                        "lot_ids": [],
                        "segment_ids": segment_ids,
                        "footprint_ids": footprint_ids,
                        "footprint_count": int(len(footprint_ids)),
                        "has_fallback_footprints": bool(footprint_source_counts.get("fallback", 0)),
                        "footprint_source_counts": footprint_source_counts,
                        "station_range_m": [
                            float(cell_station_start_m),
                            float(cell_station_end_m),
                        ],
                        "street_edge_xz": [float(street_edge_xz[0]), float(street_edge_xz[1])] if street_edge_xz is not None else [],
                        "building_buffer_width_m": float(building_buffer_width_for_cell),
                        "active_side": str(side_profile.get("active_side", "") or ""),
                        "building_region_id": str(matched_region.get("region_id", "") or "") if matched_region is not None else "",
                        "building_region_label": str(matched_region.get("label", "") or "") if matched_region is not None else "",
                        "building_region_yaw_deg": (
                            float(matched_region.get("yaw_deg", 0.0) or 0.0)
                            if matched_region is not None
                            else None
                        ),
                    }
                )
                theme_cell_counts[theme_name] = theme_cell_counts.get(theme_name, 0) + 1

    mean_left_buffer = round(sum(buffer_width_accum["left"]) / len(buffer_width_accum["left"]), 3) if buffer_width_accum["left"] else 0.0
    mean_right_buffer = round(sum(buffer_width_accum["right"]) / len(buffer_width_accum["right"]), 3) if buffer_width_accum["right"] else 0.0
    buffer_gap_ratio = (
        abs(float(mean_left_buffer) - float(mean_right_buffer)) / max(float(mean_left_buffer), float(mean_right_buffer), 1e-6)
        if float(mean_left_buffer) > 0.0 or float(mean_right_buffer) > 0.0
        else 0.0
    )
    streetwall_gap_ratio = (
        abs(float(reference_left_streetwall_width_m) - float(reference_right_streetwall_width_m))
        / max(float(reference_left_streetwall_width_m), float(reference_right_streetwall_width_m), 1e-6)
        if float(reference_left_streetwall_width_m) > 0.0 or float(reference_right_streetwall_width_m) > 0.0
        else 0.0
    )
    summary = {
        "enabled": True,
        "cell_count": int(len(cells)),
        "theme_cell_counts": {key: int(value) for key, value in sorted(theme_cell_counts.items())},
        "building_cell_counts": {key: int(value) for key, value in sorted(building_cell_counts.items())},
        "occupied_building_cells": int(occupied_building_cells),
        "buildable_cell_count": int(sum(1 for cell in cells if bool(cell.get("buildable", False)))),
        "building_buffer_width_m": {
            "left": float(mean_left_buffer),
            "right": float(mean_right_buffer),
        },
        "building_buffer_gap_ratio": round(float(buffer_gap_ratio), 3),
        "streetwall_reference_width_m": {
            "left": round(float(reference_left_streetwall_width_m), 3),
            "right": round(float(reference_right_streetwall_width_m), 3),
        },
        "streetwall_reference_gap_ratio": round(float(streetwall_gap_ratio), 3),
        "streetwall_reference_raw_width_m": {
            "left": round(float(streetwall_reference["raw_left_total_m"]), 3),
            "right": round(float(streetwall_reference["raw_right_total_m"]), 3),
        },
        "side_land_use_counts": {
            side: {key: int(value) for key, value in sorted(counts.items())}
            for side, counts in side_land_use_counts.items()
        },
        "active_side_counts": {key: int(value) for key, value in sorted(active_side_counts.items())},
        "asymmetry_strength": float(asymmetry_strength),
        "left_right_bias": float(left_right_bias),
        "building_region_count": int(len(building_regions)),
        "active_building_region_count": int(
            len({str(cell.get("building_region_id", "") or "") for cell in cells if str(cell.get("building_region_id", "") or "")})
        ),
        "zoning_preview_mode": "parcel_first",
        "frontage_cell_count": int(
            sum(1 for cell in cells if "building_buffer" in str(cell.get("lane_role", "") or ""))
        ),
        "theme_segment_count": int(len(theme_segments)),
        "buildable_frontage_by_side": _buildable_frontage_by_side(cells),
        "auto_land_use_enabled": True,
        "auto_land_use_mode": "road_buffer",
        "merged_land_use_polygon_count": int(
            len(
                {
                    (
                        str(cell.get("side", "") or ""),
                        str(cell.get("theme_id", "") or ""),
                        str(cell.get("land_use_type", "") or ""),
                    )
                    for cell in cells
                    if "building_buffer" in str(cell.get("lane_role", "") or "")
                }
            )
        ),
        "road_split_count": int(len(raw_segments)),
        "sliver_removed_count": 0,
        "junction_clip_count": int(len(junction_anchors)),
    }
    return tuple(cells), summary


def _normalize_tags(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip().lower() for item in value.split(",")]
    else:
        items = [str(item).strip().lower() for item in value]
    return tuple(sorted({item for item in items if item}))


def _height_class_from_area(area_m2: float) -> str:
    if area_m2 >= 260.0:
        return "highrise"
    if area_m2 >= 120.0:
        return "midrise"
    return "lowrise"


# ---------------------------------------------------------------------------
# Continuous building-height sampling (theme_random mode)
# ---------------------------------------------------------------------------

_HEIGHT_PROFILES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "urban_default_v1": {
        "residential": (9.0, 22.0),
        "commercial": (14.0, 38.0),
        "transit": (18.0, 54.0),
        "green": (8.0, 16.0),
        "_fallback": (12.0, 28.0),
    },
}

_AREA_HEIGHT_CAPS: Tuple[Tuple[float, float], ...] = (
    (100.0, 18.0),
    (220.0, 30.0),
    (420.0, 45.0),
)


def height_class_from_height_m(height_m: float) -> str:
    """Derive a discrete height class from a continuous meter height."""
    if height_m < 12.0:
        return "lowrise"
    if height_m < 25.0:
        return "midrise"
    return "highrise"


def _hash_to_unit(key: str) -> float:
    """Deterministic hash → float in [0, 1)."""
    digest = hashlib.md5(key.encode()).digest()
    return (int.from_bytes(digest[:4], "little") & 0xFFFFFFFF) / 0x100000000


def sample_building_target_height(
    *,
    seed: int,
    target_id: str,
    theme_name: str = "",
    land_use_type: str = "",
    frontage_width_m: float = 10.0,
    depth_m: float = 10.0,
    source: str = "",
    height_profile: str = "urban_default_v1",
) -> float:
    """Sample a deterministic continuous building height in metres.

    The result is reproducible for a given *seed* + *target_id* pair.
    Heights are drawn from the theme range, capped by lot area, with
    a two-level randomness model (segment baseline + per-building jitter)
    so that adjacent buildings share a similar base height but are not
    identical.
    """
    profile = _HEIGHT_PROFILES.get(height_profile, _HEIGHT_PROFILES["urban_default_v1"])

    # Resolve theme key --------------------------------------------------
    key = (theme_name or land_use_type or "").strip().lower()
    min_h, max_h = profile.get(key, profile["_fallback"])

    # Area cap -----------------------------------------------------------
    area = max(float(frontage_width_m) * float(depth_m), 1.0)
    cap = float("inf")
    for threshold, limit in _AREA_HEIGHT_CAPS:
        if area < threshold:
            cap = limit
            break
    effective_max = min(max_h, cap)
    if effective_max < min_h:
        effective_max = min_h

    # Segment baseline (40 %–60 % of range) ------------------------------
    seg_u = _hash_to_unit(f"{seed}:seg:{key}")
    baseline_pct = 0.4 + seg_u * 0.2  # [0.4, 0.6)
    baseline = min_h + (effective_max - min_h) * baseline_pct

    # Per-building jitter (±30 % of half-range) --------------------------
    bld_u = _hash_to_unit(f"{seed}:bld:{target_id}")
    jitter_range = (effective_max - min_h) * 0.3
    jitter = (bld_u - 0.5) * 2.0 * jitter_range  # [-jitter_range, +jitter_range)
    height = baseline + jitter

    # Clamp to valid range and round to 0.1 m ----------------------------
    height = max(min_h, min(effective_max, height))
    return round(height, 1)


def _resolve_theme_key(
    theme_id: str,
    theme_segments: Sequence[ThemeSegment],
) -> str:
    """Map a *theme_id* to its *theme_name* (land-use label) for height sampling."""
    for seg in theme_segments:
        if seg.theme_id == theme_id:
            return seg.theme_name
    return ""


def _size_class(frontage_width_m: float, depth_m: float) -> str:
    major = max(float(frontage_width_m), float(depth_m))
    if major >= 24.0:
        return "large"
    if major >= 14.0:
        return "medium"
    return "small"
