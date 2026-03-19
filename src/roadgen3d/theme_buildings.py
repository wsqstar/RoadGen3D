"""Theme inference and surrounding-building planning utilities."""

from __future__ import annotations

import math
import hashlib
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .types import BuildingFootprint, GeneratedLot, StreetComposeConfig, ThemeSegment

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
                    )
                )
            current_theme = theme_name
            current_nodes = [node]
            current_start = start_m
            current_end = end_m
            current_pois = set(getattr(node, "poi_types", ()) or ())
        else:
            current_nodes.append(node)
            current_end = end_m
            current_pois.update(getattr(node, "poi_types", ()) or ())

    if current_nodes:
        merged.append(
            _build_theme_segment(
                idx=len(merged),
                theme_name=current_theme,
                start_m=current_start,
                end_m=current_end,
                nodes=current_nodes,
                dominant_poi_types=tuple(sorted(current_pois)),
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
) -> ThemeSegment:
    spec = theme_profile_style(theme_name)
    notes = []
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
    asymmetry_strength: float = 0.35,
    left_right_bias: float = 0.0,
    front_setback_min_m: float = 1.0,
    front_setback_max_m: float = 2.0,
    zoning_granularity: str = "balanced",
    streetwall_continuity: float = 0.85,
) -> Tuple[BuildingFootprint, ...]:
    """Collect nearby OSM building footprints or fallback proxy footprints."""

    try:
        from shapely.geometry import Polygon as ShapelyPolygon
    except Exception:
        return tuple()

    buildings = list(getattr(projected_features, "buildings", ()) or [])
    road_geom = getattr(placement_context, "carriageway", None)
    footprints: List[BuildingFootprint] = []
    if road_geom is not None and not getattr(road_geom, "is_empty", True):
        road_buffer = road_geom.buffer(float(road_buffer_m))
        for building in buildings:
            coords = tuple((float(x), float(y)) for x, y in getattr(building, "coords", ()) or ())
            if len(coords) < 4:
                continue
            polygon = ShapelyPolygon(coords)
            if polygon.is_empty or polygon.area <= 4.0 or not polygon.intersects(road_buffer):
                continue
            centroid = (float(polygon.centroid.x), float(polygon.centroid.y))
            theme_id = assign_theme_id_for_point(centroid, theme_segments, road_segment_graph)
            theme_name = _resolve_theme_key(theme_id, theme_segments)
            yaw_deg, frontage_width_m, depth_m = oriented_bounds_metrics(polygon)
            fid = f"building_{len(footprints):03d}"
            if height_mode == "theme_random":
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
                    source="osm",
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
    return tuple(_fallback_building_footprints(
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
    ))


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
) -> Dict[str, object]:
    strength = _clamp(float(asymmetry_strength), 0.0, 1.0)
    base_land_use = land_use_for_theme(theme_name)
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
    u = _hash_to_unit(f"{seed}:front_setback:{target_id}")
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


def generate_grid_growth_lots(
    zoning_grid: Sequence[Mapping[str, Any]],
    *,
    road_type: str = "",
    seed: int = 0,
    height_mode: str = "theme_random",
    height_profile: str = "urban_default_v1",
    front_setback_min_m: float = 1.0,
    front_setback_max_m: float = 2.0,
    zoning_granularity: str = "balanced",
    streetwall_continuity: float = 0.85,
) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[GeneratedLot, ...], Dict[str, Any]]:
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
        cell_lot_ids: List[str] = []
        side = _cell_side(cell)
        yaw_deg = _cell_yaw_deg(cell)
        theme_id = str(cell.get("theme_id", "") or "")
        cell_id = str(cell.get("cell_id", "") or "")
        segment_ids = tuple(str(segment_id) for segment_id in (cell.get("segment_ids", []) or []) if str(segment_id))
        parcel_depth_m = _cell_frontage_depth(cell)[1]
        for start_m, end_m in parcel_frontage_intervals:
            polygon_xz = _parcel_polygon_from_cell_interval(
                cell,
                start_m=float(start_m),
                end_m=float(end_m),
            )
            if len(polygon_xz) < 4:
                continue
            lot_id = f"lot_{len(lots):03d}"
            cell_lot_ids.append(lot_id)
            center_xz = _polygon_center(polygon_xz)
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
                    theme_name=str(cell.get("theme_name", "") or ""),
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
            lots.append(
                GeneratedLot(
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
                    source="grid_growth",
                    cell_ids=(cell_id,) if cell_id else (),
                    segment_ids=segment_ids,
                    street_edge_xz=(float(street_edge_xz[0]), float(street_edge_xz[1])),
                    placement_xz=(float(placement_xz[0]), float(placement_xz[1])),
                    front_setback_m=float(front_setback_m),
                    placement_strategy=str(placement_strategy),
                    building_depth_m=float(building_depth_m),
                )
            )
        cell["lot_ids"] = list(cell_lot_ids)
        cell["lot_id"] = str(cell_lot_ids[0]) if cell_lot_ids else ""

    lot_counts = Counter(lot.land_use_type for lot in lots)
    height_counts = Counter(lot.height_class for lot in lots)
    frontage_metrics = summarize_frontage_coverage(
        annotated_cells,
        tuple({"polygon_xz": lot.polygon_xz, "side": lot.side} for lot in lots),
    )
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
        **frontage_metrics,
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
    asymmetry_strength: float = 0.35,
    left_right_bias: float = 0.0,
    front_setback_min_m: float = 1.0,
    front_setback_max_m: float = 2.0,
    zoning_granularity: str = "balanced",
    streetwall_continuity: float = 0.85,
) -> List[BuildingFootprint]:
    footprints: List[BuildingFootprint] = []
    carriageway_width_m = float(
        getattr(placement_context, "carriageway_width_m", 0.0)
        or getattr(placement_context, "road_width_m", 0.0)
        or 8.0
    )
    carriageway_half = carriageway_width_m / 2.0
    left_sidewalk_width_m = float(
        (getattr(placement_context, "left_clear_path_width_m", 0.0) or 0.0)
        + (getattr(placement_context, "left_furnishing_width_m", 0.0) or 0.0)
        or 2.5
    )
    right_sidewalk_width_m = float(
        (getattr(placement_context, "right_clear_path_width_m", 0.0) or 0.0)
        + (getattr(placement_context, "right_furnishing_width_m", 0.0) or 0.0)
        or 2.5
    )
    nodes_by_id = {
        str(getattr(node, "segment_id", "")): node
        for node in getattr(road_segment_graph, "nodes", ()) or ()
    }
    normalized_granularity = _normalize_zoning_granularity(zoning_granularity)
    continuity = _clamp(float(streetwall_continuity), 0.0, 1.0)
    for theme_segment in theme_segments:
        nodes = [nodes_by_id[segment_id] for segment_id in theme_segment.segment_ids if segment_id in nodes_by_id]
        if nodes:
            sample_node = nodes[len(nodes) // 2]
            center_x, center_z = tuple(float(v) for v in getattr(sample_node, "center_xy", (0.0, 0.0)))
            dx = float(getattr(sample_node, "end_xy", (1.0, 0.0))[0]) - float(getattr(sample_node, "start_xy", (0.0, 0.0))[0])
            dz = float(getattr(sample_node, "end_xy", (1.0, 0.0))[1]) - float(getattr(sample_node, "start_xy", (0.0, 0.0))[1])
            yaw_deg = math.degrees(math.atan2(dz, dx)) if abs(dx) + abs(dz) > 1e-6 else 0.0
            start_xy = tuple(float(v) for v in getattr(sample_node, "start_xy", (center_x, center_z)))
            end_xy = tuple(float(v) for v in getattr(sample_node, "end_xy", (center_x + 1.0, center_z)))
        else:
            center_x, center_z, yaw_deg = theme_segment.center_x_m, 0.0, 0.0
            half_span = float(theme_segment.length_m) / 2.0
            yaw_rad = math.radians(yaw_deg)
            start_xy = (
                float(center_x) - math.cos(yaw_rad) * half_span,
                float(center_z) - math.sin(yaw_rad) * half_span,
            )
            end_xy = (
                float(center_x) + math.cos(yaw_rad) * half_span,
                float(center_z) + math.sin(yaw_rad) * half_span,
            )
        profile = _resolve_side_zoning_profile(
            seed=seed,
            theme_id=theme_segment.theme_id,
            theme_name=theme_segment.theme_name,
            asymmetry_strength=asymmetry_strength,
            left_right_bias=left_right_bias,
        )
        base_frontage_m = min(max(theme_segment.length_m * 0.55, 12.0), 24.0)
        for side_name in ("left", "right"):
            land_use_type = str(profile[f"{side_name}_land_use_type"])
            if land_use_type == "green":
                continue
            width_multiplier = float(profile[f"{side_name}_width_multiplier"])
            base_depth_m = 12.0 if land_use_type in {"commercial", "transit"} else 10.0
            parcel_depth_m = max(8.0, base_depth_m * max(width_multiplier, 0.65))
            if side_name == "left":
                sidewalk_outer_offset_m = float(carriageway_half + left_sidewalk_width_m)
                polygon_xz = _band_polygon_from_segment(
                    start_xy,
                    end_xy,
                    inner_offset_m=sidewalk_outer_offset_m,
                    outer_offset_m=sidewalk_outer_offset_m + float(parcel_depth_m),
                )
            else:
                sidewalk_outer_offset_m = -float(carriageway_half + right_sidewalk_width_m)
                polygon_xz = _band_polygon_from_segment(
                    start_xy,
                    end_xy,
                    inner_offset_m=sidewalk_outer_offset_m - float(parcel_depth_m),
                    outer_offset_m=sidewalk_outer_offset_m,
                )
            if len(polygon_xz) < 4:
                continue
            pseudo_cell = {
                "cell_id": f"{theme_segment.theme_id}_{side_name}_fallback_strip",
                "polygon_xz": [[float(x), float(z)] for x, z in polygon_xz],
                "lane_role": f"{side_name}_building_buffer",
                "station_range_m": [0.0, float(theme_segment.length_m)],
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
                footprint_id = f"{theme_segment.theme_id}_{side_name}_{interval_idx:02d}"
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
                        anchor_geom_id=f"{theme_segment.theme_id}:{side_name}:{interval_idx}",
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
    zoning_granularity: str = "balanced",
    streetwall_continuity: float = 0.85,
    infill_policy: str = "large_gap_only",
    front_setback_min_m: float = 1.0,
    front_setback_max_m: float = 2.0,
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
    default_width = min(float(road_buffer_m), max(float(left_sidewalk_width_m), float(right_sidewalk_width_m), 10.0))
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
    asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.35)
    bias_raw = getattr(config, "left_right_bias", 0.0)
    asymmetry_strength = _clamp(float(0.35 if asymmetry_raw is None else asymmetry_raw), 0.0, 1.0)
    left_right_bias = _clamp(float(0.0 if bias_raw is None else bias_raw), -1.0, 1.0)
    theme_by_segment_id = {
        segment_id: theme_segment
        for theme_segment in theme_segments
        for segment_id in theme_segment.segment_ids
    }
    carriageway_width_m = float(
        getattr(placement_context, "carriageway_width_m", 0.0)
        or float(config.road_width_m)
    )
    left_sidewalk_width_m = float(
        (getattr(placement_context, "left_clear_path_width_m", 0.0) or 0.0)
        + (getattr(placement_context, "left_furnishing_width_m", 0.0) or 0.0)
        or float(config.sidewalk_width_m)
    )
    right_sidewalk_width_m = float(
        (getattr(placement_context, "right_clear_path_width_m", 0.0) or 0.0)
        + (getattr(placement_context, "right_furnishing_width_m", 0.0) or 0.0)
        or float(config.sidewalk_width_m)
    )
    left_building_buffer_m, right_building_buffer_m = _estimate_building_buffer_widths(
        building_footprints=building_footprints,
        road_segment_graph=road_segment_graph,
        carriageway_width_m=carriageway_width_m,
        left_sidewalk_width_m=left_sidewalk_width_m,
        right_sidewalk_width_m=right_sidewalk_width_m,
        road_buffer_m=float(road_buffer_m),
    )

    raw_segments: List[Dict[str, Any]] = []
    if road_segment_graph is not None and getattr(road_segment_graph, "nodes", None):
        nodes = sorted(
            list(getattr(road_segment_graph, "nodes", ()) or ()),
            key=lambda node: float(getattr(node, "station_center_m", 0.0) or 0.0),
        )
        for node in nodes:
            raw_segments.append(
                {
                    "segment_id": str(getattr(node, "segment_id", "")),
                    "start_xy": tuple(float(v) for v in getattr(node, "start_xy", (0.0, 0.0))),
                    "end_xy": tuple(float(v) for v in getattr(node, "end_xy", (0.0, 0.0))),
                    "center_xy": tuple(float(v) for v in getattr(node, "center_xy", (0.0, 0.0))),
                    "station_start_m": float(getattr(node, "station_start_m", 0.0) or 0.0),
                    "station_end_m": float(getattr(node, "station_end_m", 0.0) or 0.0),
                    "station_center_m": float(getattr(node, "station_center_m", 0.0) or 0.0),
                    "theme_segment": _theme_segment_for_node(node, theme_segments, theme_by_segment_id),
                }
            )
    else:
        raw_segments = _fallback_zoning_segments(theme_segments=theme_segments, config=config)

    if not raw_segments:
        return tuple(), {
            "enabled": False,
            "cell_count": 0,
            "theme_cell_counts": {},
            "building_cell_counts": {},
            "occupied_building_cells": 0,
            "buildable_cell_count": 0,
            "side_land_use_counts": {"left": {}, "right": {}},
            "active_side_counts": {},
            "building_buffer_width_m": {"left": 0.0, "right": 0.0},
            "asymmetry_strength": float(asymmetry_strength),
            "left_right_bias": float(left_right_bias),
        }

    try:
        from shapely.geometry import Polygon as ShapelyPolygon
    except Exception:
        ShapelyPolygon = None  # type: ignore[assignment]

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
        theme_segment = segment.get("theme_segment")
        theme_id = str(getattr(theme_segment, "theme_id", "") or "")
        theme_name = str(getattr(theme_segment, "theme_name", "") or "commercial")
        side_profile = _resolve_side_zoning_profile(
            seed=int(getattr(config, "seed", 0) or 0),
            theme_id=theme_id or f"seg_{segment_idx:03d}",
            theme_name=theme_name,
            asymmetry_strength=asymmetry_strength,
            left_right_bias=left_right_bias,
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
        lane_specs = (
            ("left_building_buffer", float(carriageway_half + left_sidewalk_width_m), float(carriageway_half + left_sidewalk_width_m + segment_left_building_buffer_m)),
            ("left_sidewalk", float(carriageway_half), float(carriageway_half + left_sidewalk_width_m)),
            ("carriageway", -float(carriageway_half), float(carriageway_half)),
            ("right_sidewalk", -float(carriageway_half + right_sidewalk_width_m), -float(carriageway_half)),
            ("right_building_buffer", -float(carriageway_half + right_sidewalk_width_m + segment_right_building_buffer_m), -float(carriageway_half + right_sidewalk_width_m)),
        )
        segment_ids = [str(segment["segment_id"])]
        for lane_role, inner_offset_m, outer_offset_m in lane_specs:
            polygon_xz = _band_polygon_from_segment(
                tuple(segment["start_xy"]),
                tuple(segment["end_xy"]),
                inner_offset_m=float(inner_offset_m),
                outer_offset_m=float(outer_offset_m),
            )
            if not polygon_xz:
                continue
            cell_geom = ShapelyPolygon(polygon_xz) if ShapelyPolygon is not None else None
            cell_bbox = _polygon_bbox(polygon_xz)
            footprint_ids: List[str] = []
            footprint_source_counts: Dict[str, int] = {}
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
            if lane_role == "left_building_buffer":
                land_use_type = str(side_profile.get("left_land_use_type", land_use_for_theme(theme_name)))
                street_edge_xz = _segment_offset_midpoint(
                    tuple(segment["start_xy"]),
                    tuple(segment["end_xy"]),
                    offset_m=float(carriageway_half + left_sidewalk_width_m),
                )
                side_land_use_counts["left"][land_use_type] = side_land_use_counts["left"].get(land_use_type, 0) + 1
            elif lane_role == "right_building_buffer":
                land_use_type = str(side_profile.get("right_land_use_type", land_use_for_theme(theme_name)))
                street_edge_xz = _segment_offset_midpoint(
                    tuple(segment["start_xy"]),
                    tuple(segment["end_xy"]),
                    offset_m=-float(carriageway_half + right_sidewalk_width_m),
                )
                side_land_use_counts["right"][land_use_type] = side_land_use_counts["right"].get(land_use_type, 0) + 1
            else:
                land_use_type = ""
                street_edge_xz = None
            buildable = bool(lane_role in building_roles and land_use_type and land_use_type != "green")
            cell_center = _polygon_center(polygon_xz)
            building_buffer_width_for_cell = (
                float(segment_left_building_buffer_m)
                if lane_role == "left_building_buffer"
                else float(segment_right_building_buffer_m)
                if lane_role == "right_building_buffer"
                else 0.0
            )
            cells.append(
                {
                    "cell_id": f"zone_{segment_idx:03d}_{lane_role}",
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
                        float(segment.get("station_start_m", 0.0) or 0.0),
                        float(segment.get("station_end_m", 0.0) or 0.0),
                    ],
                    "street_edge_xz": [float(street_edge_xz[0]), float(street_edge_xz[1])] if street_edge_xz is not None else [],
                    "building_buffer_width_m": float(building_buffer_width_for_cell),
                    "active_side": str(side_profile.get("active_side", "") or ""),
                }
            )
            theme_cell_counts[theme_name] = theme_cell_counts.get(theme_name, 0) + 1

    summary = {
        "enabled": True,
        "cell_count": int(len(cells)),
        "theme_cell_counts": theme_cell_counts,
        "building_cell_counts": building_cell_counts,
        "occupied_building_cells": int(occupied_building_cells),
        "buildable_cell_count": int(sum(1 for cell in cells if bool(cell.get("buildable", False)))),
        "building_buffer_width_m": {
            "left": round(sum(buffer_width_accum["left"]) / len(buffer_width_accum["left"]), 3) if buffer_width_accum["left"] else 0.0,
            "right": round(sum(buffer_width_accum["right"]) / len(buffer_width_accum["right"]), 3) if buffer_width_accum["right"] else 0.0,
        },
        "side_land_use_counts": {
            side: {key: int(value) for key, value in sorted(counts.items())}
            for side, counts in side_land_use_counts.items()
        },
        "active_side_counts": {key: int(value) for key, value in sorted(active_side_counts.items())},
        "asymmetry_strength": float(asymmetry_strength),
        "left_right_bias": float(left_right_bias),
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
