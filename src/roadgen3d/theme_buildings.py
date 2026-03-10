"""Theme inference and surrounding-building planning utilities."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .types import BuildingFootprint, StreetComposeConfig, ThemeSegment

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
            yaw_deg, frontage_width_m, depth_m = oriented_bounds_metrics(polygon)
            footprints.append(
                BuildingFootprint(
                    footprint_id=f"building_{len(footprints):03d}",
                    source="osm",
                    polygon_xz=tuple((float(x), float(y)) for x, y in tuple(polygon.exterior.coords)),
                    centroid_xz=centroid,
                    frontage_width_m=float(frontage_width_m),
                    depth_m=float(depth_m),
                    yaw_deg=float(yaw_deg),
                    theme_id=theme_id,
                    height_class=_height_class_from_area(float(polygon.area)),
                    anchor_geom_id=str(getattr(building, "osm_id", "")),
                    size_class=_size_class(frontage_width_m, depth_m),
                )
            )
    if footprints:
        return tuple(footprints)
    return tuple(_fallback_building_footprints(theme_segments, placement_context, road_segment_graph))


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
) -> str:
    size_class = _size_class(frontage_width_m, depth_m)
    road_part = f", {road_type}" if str(road_type).strip() else ""
    return f"{base_query}, {theme_name} building facade, {size_class} frontage{road_part}"


def rerank_building_candidates(
    *,
    hits: Sequence[object],
    asset_by_id: Mapping[str, Mapping[str, Any]],
    theme_name: str,
    frontage_width_m: float,
    depth_m: float,
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
        if theme_name in theme_tags:
            score += 0.45
        if target_size in theme_tags:
            score += 0.15
        if theme_name in style_tags:
            score += 0.1
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


def _fallback_building_footprints(
    theme_segments: Sequence[ThemeSegment],
    placement_context: object | None,
    road_segment_graph: object | None,
) -> List[BuildingFootprint]:
    footprints: List[BuildingFootprint] = []
    row_half = float(getattr(placement_context, "row_width_m", 16.0) or 16.0) / 2.0
    nodes_by_id = {
        str(getattr(node, "segment_id", "")): node
        for node in getattr(road_segment_graph, "nodes", ()) or ()
    }
    for theme_segment in theme_segments:
        nodes = [nodes_by_id[segment_id] for segment_id in theme_segment.segment_ids if segment_id in nodes_by_id]
        if nodes:
            sample_node = nodes[len(nodes) // 2]
            center_x, center_z = tuple(float(v) for v in getattr(sample_node, "center_xy", (0.0, 0.0)))
            dx = float(getattr(sample_node, "end_xy", (1.0, 0.0))[0]) - float(getattr(sample_node, "start_xy", (0.0, 0.0))[0])
            dz = float(getattr(sample_node, "end_xy", (1.0, 0.0))[1]) - float(getattr(sample_node, "start_xy", (0.0, 0.0))[1])
            yaw_deg = math.degrees(math.atan2(dz, dx)) if abs(dx) + abs(dz) > 1e-6 else 0.0
        else:
            center_x, center_z, yaw_deg = theme_segment.center_x_m, 0.0, 0.0
        length_m = min(max(theme_segment.length_m * 0.55, 12.0), 24.0)
        depth_m = 12.0 if theme_segment.theme_name in {"commercial", "transit"} else 10.0
        lateral_offset = row_half + depth_m / 2.0 + 6.0
        yaw_rad = math.radians(yaw_deg)
        left_normal = (-math.sin(yaw_rad), math.cos(yaw_rad))
        for side_name, sign in (("left", 1.0), ("right", -1.0)):
            offset_x = center_x + left_normal[0] * lateral_offset * sign
            offset_z = center_z + left_normal[1] * lateral_offset * sign
            footprints.append(
                BuildingFootprint(
                    footprint_id=f"{theme_segment.theme_id}_{side_name}",
                    source="fallback",
                    polygon_xz=oriented_rectangle_points(
                        center_x=float(offset_x),
                        center_z=float(offset_z),
                        yaw_deg=float(yaw_deg),
                        length_m=float(length_m),
                        depth_m=float(depth_m),
                    ),
                    centroid_xz=(float(offset_x), float(offset_z)),
                    frontage_width_m=float(length_m),
                    depth_m=float(depth_m),
                    yaw_deg=float(yaw_deg),
                    theme_id=theme_segment.theme_id,
                    height_class="midrise",
                    anchor_geom_id=f"{theme_segment.theme_id}:{side_name}",
                    size_class=_size_class(length_m, depth_m),
                )
            )
    return footprints


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
    lane_specs = (
        ("left_building_buffer", float(carriageway_half + left_sidewalk_width_m), float(carriageway_half + left_sidewalk_width_m + left_building_buffer_m)),
        ("left_sidewalk", float(carriageway_half), float(carriageway_half + left_sidewalk_width_m)),
        ("carriageway", -float(carriageway_half), float(carriageway_half)),
        ("right_sidewalk", -float(carriageway_half + right_sidewalk_width_m), -float(carriageway_half)),
        ("right_building_buffer", -float(carriageway_half + right_sidewalk_width_m + right_building_buffer_m), -float(carriageway_half + right_sidewalk_width_m)),
    )
    building_roles = {"left_building_buffer", "right_building_buffer"}

    cells: List[Dict[str, Any]] = []
    theme_cell_counts: Dict[str, int] = {}
    building_cell_counts: Dict[str, int] = {}
    occupied_building_cells = 0
    for segment_idx, segment in enumerate(raw_segments):
        theme_segment = segment.get("theme_segment")
        theme_id = str(getattr(theme_segment, "theme_id", "") or "")
        theme_name = str(getattr(theme_segment, "theme_name", "") or "commercial")
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
            cell_center = _polygon_center(polygon_xz)
            cells.append(
                {
                    "cell_id": f"zone_{segment_idx:03d}_{lane_role}",
                    "polygon_xz": [[float(x), float(z)] for x, z in polygon_xz],
                    "center_xz": [float(cell_center[0]), float(cell_center[1])],
                    "lane_role": lane_role,
                    "theme_id": theme_id,
                    "theme_name": theme_name,
                    "segment_ids": segment_ids,
                    "footprint_ids": footprint_ids,
                    "footprint_count": int(len(footprint_ids)),
                    "has_fallback_footprints": bool(footprint_source_counts.get("fallback", 0)),
                    "footprint_source_counts": footprint_source_counts,
                    "station_range_m": [
                        float(segment.get("station_start_m", 0.0) or 0.0),
                        float(segment.get("station_end_m", 0.0) or 0.0),
                    ],
                }
            )
            theme_cell_counts[theme_name] = theme_cell_counts.get(theme_name, 0) + 1

    summary = {
        "enabled": True,
        "cell_count": int(len(cells)),
        "theme_cell_counts": theme_cell_counts,
        "building_cell_counts": building_cell_counts,
        "occupied_building_cells": int(occupied_building_cells),
        "building_buffer_width_m": {
            "left": float(left_building_buffer_m),
            "right": float(right_building_buffer_m),
        },
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


def _size_class(frontage_width_m: float, depth_m: float) -> str:
    major = max(float(frontage_width_m), float(depth_m))
    if major >= 24.0:
        return "large"
    if major >= 14.0:
        return "medium"
    return "small"
