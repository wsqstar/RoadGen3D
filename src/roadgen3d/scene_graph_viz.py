"""Scene-graph payload construction and Plotly rendering helpers."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .placement_field import poi_attraction_score
from .poi_rules import PoiContext, evaluate_repulsion_field, load_rule_set
from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    canonicalize_poi_type,
    get_poi_spec,
    nonempty_poi_points,
)

SCENE_GRAPH_NODE_TYPES: Tuple[str, ...] = (
    "road_segment",
    "poi",
    "slot_plan",
    "placement",
)
SCENE_GRAPH_EDGE_TYPES: Tuple[str, ...] = (
    "road_connects",
    "poi_near_segment",
    "slot_on_segment",
    "placement_realizes_slot",
    "slot_anchors_poi",
    "placement_conflicts_poi",
)
DEFAULT_VISIBLE_EDGE_TYPES: Tuple[str, ...] = (
    "road_connects",
    "placement_realizes_slot",
    "slot_anchors_poi",
    "poi_near_segment",
)

_CATEGORY_COLORS: Dict[str, str] = {
    "bench": "#e6194b",
    "lamp": "#f58231",
    "trash": "#808000",
    "tree": "#3cb44b",
    "bus_stop": "#4363d8",
    "mailbox": "#911eb4",
    "hydrant": "#42d4f4",
    "bollard": "#f032e6",
}
_EDGE_STYLES: Dict[str, Dict[str, Any]] = {
    "road_connects": {"color": "#808080", "width": 2.2, "dash": "solid"},
    "poi_near_segment": {"color": "#b0b0b0", "width": 1.1, "dash": "dot"},
    "slot_on_segment": {"color": "#9c8cff", "width": 1.0, "dash": "dot"},
    "placement_realizes_slot": {"color": "#2a9d8f", "width": 1.3, "dash": "solid"},
    "slot_anchors_poi": {"color": "#ff4d6d", "width": 1.6, "dash": "dash"},
    "placement_conflicts_poi": {"color": "#d90429", "width": 1.8, "dash": "dash"},
}
_PLOTLY_POI_SYMBOLS: Dict[str, str] = {
    "entrance": "triangle-up",
    "bus_stop": "diamond",
    CANONICAL_FIRE_POI: "hexagon",
    "crossing": "cross",
    "traffic_signals": "x",
    "parking_entrance": "square",
    "subway_entrance": "triangle-down",
    "post_box": "pentagon",
    "waste_basket": "star",
    "bollard": "octagon",
}


def _require_plotly() -> Any:
    try:
        import plotly.graph_objects as go
    except Exception:
        return None
    return go


def _require_shapely() -> Any:
    try:
        from shapely.geometry import MultiPolygon, Point as ShapelyPoint, Polygon as ShapelyPolygon, box
        from shapely.ops import unary_union
    except Exception:
        return None
    return {
        "MultiPolygon": MultiPolygon,
        "Point": ShapelyPoint,
        "Polygon": ShapelyPolygon,
        "box": box,
        "unary_union": unary_union,
    }


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _coerce_point(value: Sequence[float] | None) -> Tuple[float, float]:
    if not value or len(value) < 2:
        return (0.0, 0.0)
    return (float(value[0]), float(value[1]))


def _coerce_records(records: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in records or []:
        if isinstance(item, dict):
            normalized.append(dict(item))
        elif hasattr(item, "to_dict"):
            normalized.append(dict(item.to_dict()))
        else:
            normalized.append(dict(getattr(item, "__dict__", {})))
    return normalized


def _spatial_context_points(layout_payload: Mapping[str, Any]) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    summary = dict(layout_payload.get("summary", {}) or {})
    spatial_ctx = dict(summary.get("spatial_context", {}) or {})
    mapping = nonempty_poi_points(spatial_ctx.get("poi_points_by_type_xz", {}) or {})
    if mapping:
        return mapping
    recovered = {
        "entrance": tuple(tuple(point) for point in spatial_ctx.get("entrance_points_xz", []) or []),
        "bus_stop": tuple(tuple(point) for point in spatial_ctx.get("bus_stop_points_xz", []) or []),
        CANONICAL_FIRE_POI: tuple(tuple(point) for point in spatial_ctx.get("fire_points_xz", []) or []),
    }
    return {poi_type: tuple((float(p[0]), float(p[1])) for p in points) for poi_type, points in recovered.items() if points}


def _poi_context_from_layout(layout_payload: Mapping[str, Any]) -> PoiContext:
    points = _spatial_context_points(layout_payload)
    return PoiContext(
        entrance_points_xz=tuple(points.get("entrance", ())),
        bus_stop_points_xz=tuple(points.get("bus_stop", ())),
        fire_points_xz=tuple(points.get(CANONICAL_FIRE_POI, ())),
        poi_points_by_type_xz=points,
    )


def _infer_bounds(layout_payload: Mapping[str, Any]) -> Dict[str, float]:
    summary = dict(layout_payload.get("summary", {}) or {})
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    bbox = osm_geometry.get("aoi_bbox_m")
    if bbox and len(bbox) == 4:
        return {
            "min_x": float(bbox[0]),
            "max_x": float(bbox[2]),
            "min_z": float(bbox[1]),
            "max_z": float(bbox[3]),
        }

    placements = _coerce_records(layout_payload.get("placements", []))
    slot_plans = _coerce_records((layout_payload.get("solver", {}) or {}).get("slot_plans", []))
    xs: List[float] = []
    zs: List[float] = []
    for placement in placements:
        pos = placement.get("position_xyz", [])
        if len(pos) >= 3:
            xs.append(float(pos[0]))
            zs.append(float(pos[2]))
    for slot in slot_plans:
        xs.append(float(slot.get("x_center_m", 0.0)))
        zs.append(float(slot.get("z_center_m", 0.0)))
    if xs and zs:
        margin = 6.0
        return {
            "min_x": min(xs) - margin,
            "max_x": max(xs) + margin,
            "min_z": min(zs) - margin,
            "max_z": max(zs) + margin,
        }

    length_m = float(summary.get("length_m", (layout_payload.get("config", {}) or {}).get("length_m", 80.0)))
    road_width_m = float(summary.get("road_width_m", (layout_payload.get("config", {}) or {}).get("road_width_m", 8.0)))
    left_width = float(summary.get("left_clear_path_width_m", 0.0)) + float(summary.get("left_furnishing_width_m", 0.0))
    right_width = float(summary.get("right_clear_path_width_m", 0.0)) + float(summary.get("right_furnishing_width_m", 0.0))
    fallback_sw = float(summary.get("sidewalk_width_m", (layout_payload.get("config", {}) or {}).get("sidewalk_width_m", 2.5)))
    left_width = left_width or fallback_sw
    right_width = right_width or fallback_sw
    half_row = road_width_m / 2.0 + max(left_width, right_width) + 2.0
    return {
        "min_x": -length_m / 2.0 - 2.0,
        "max_x": length_m / 2.0 + 2.0,
        "min_z": -half_row,
        "max_z": half_row,
    }


def _fallback_road_graph(bounds: Mapping[str, float]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    center_z = (float(bounds["min_z"]) + float(bounds["max_z"])) / 2.0
    start_x = float(bounds["min_x"]) + 2.0
    end_x = float(bounds["max_x"]) - 2.0
    start_node = {
        "node_id": "road_segment:fallback_start",
        "node_type": "road_segment",
        "x": start_x,
        "z": center_z,
        "label": "Road Skeleton Start",
        "category": "",
        "poi_type": "",
        "segment_id": "fallback_start",
        "band_name": "",
        "side": "",
        "required": False,
        "realized": True,
        "conflict": False,
        "anchor_poi_type": "",
    }
    end_node = {
        "node_id": "road_segment:fallback_end",
        "node_type": "road_segment",
        "x": end_x,
        "z": center_z,
        "label": "Road Skeleton End",
        "category": "",
        "poi_type": "",
        "segment_id": "fallback_end",
        "band_name": "",
        "side": "",
        "required": False,
        "realized": True,
        "conflict": False,
        "anchor_poi_type": "",
    }
    edge = {
        "edge_id": "road_connects:fallback",
        "edge_type": "road_connects",
        "source_id": start_node["node_id"],
        "target_id": end_node["node_id"],
        "weight": 1.0,
        "label": "fallback",
    }
    return [start_node, end_node], [edge]


def _nearest_segment_id(
    road_nodes: Sequence[Mapping[str, Any]],
    point: Tuple[float, float],
) -> str:
    if not road_nodes:
        return ""
    best = min(
        road_nodes,
        key=lambda node: _distance(point, (float(node.get("x", 0.0)), float(node.get("z", 0.0)))),
    )
    return str(best.get("segment_id", ""))


def _nearest_segment_node_id(
    road_nodes: Sequence[Mapping[str, Any]],
    point: Tuple[float, float],
) -> str:
    if not road_nodes:
        return ""
    best = min(
        road_nodes,
        key=lambda node: _distance(point, (float(node.get("x", 0.0)), float(node.get("z", 0.0)))),
    )
    return str(best.get("node_id", ""))


def _closest_poi_node(
    poi_nodes: Sequence[Mapping[str, Any]],
    poi_type: str,
    point: Tuple[float, float],
) -> Optional[Mapping[str, Any]]:
    canonical = canonicalize_poi_type(poi_type)
    candidates = [node for node in poi_nodes if canonicalize_poi_type(str(node.get("poi_type", ""))) == canonical]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda node: _distance(point, (float(node.get("x", 0.0)), float(node.get("z", 0.0)))),
    )


def _find_conflict_targets(
    placement: Mapping[str, Any],
    conflict_zones: Sequence[Mapping[str, Any]],
    poi_nodes: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    rules = [str(rule) for rule in placement.get("violated_rules", []) or []]
    if not rules:
        return targets
    position_xyz = placement.get("position_xyz", []) or []
    if len(position_xyz) < 3:
        return targets
    position = (float(position_xyz[0]), float(position_xyz[2]))
    for rule_name in rules:
        rule_zones = [zone for zone in conflict_zones if str(zone.get("rule_name", "")) == rule_name]
        if not rule_zones:
            continue
        zone = min(
            rule_zones,
            key=lambda item: _distance(position, _coerce_point(item.get("position_xz", ()))),
        )
        poi_node = _closest_poi_node(poi_nodes, str(zone.get("poi_type", "")), _coerce_point(zone.get("position_xz", ())))
        if poi_node is None:
            continue
        targets.append(
            {
                "rule_name": rule_name,
                "poi_node_id": str(poi_node["node_id"]),
                "weight": float(placement.get("constraint_penalty", 0.0) or 0.0),
            }
        )
    return targets


def build_scene_graph(
    layout_payload: Mapping[str, Any],
    *,
    road_segment_graph: object | None = None,
) -> Dict[str, Any]:
    """Build a serializable scene_graph payload from layout outputs."""

    bounds = _infer_bounds(layout_payload)
    summary = dict(layout_payload.get("summary", {}) or {})
    solver = dict(layout_payload.get("solver", {}) or {})
    placements = _coerce_records(layout_payload.get("placements", []))
    slot_plans = _coerce_records(solver.get("slot_plans", []))
    poi_points = _spatial_context_points(layout_payload)
    exclusion_zones = list(summary.get("poi_exclusion_zones", []) or [])

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    road_nodes: List[Dict[str, Any]] = []
    road_edges: List[Dict[str, Any]] = []

    if road_segment_graph is not None and getattr(road_segment_graph, "nodes", None):
        for node in getattr(road_segment_graph, "nodes", ()) or ():
            center = getattr(node, "center_xy", (0.0, 0.0))
            road_nodes.append(
                {
                    "node_id": f"road_segment:{getattr(node, 'segment_id', len(road_nodes))}",
                    "node_type": "road_segment",
                    "x": float(center[0]),
                    "z": float(center[1]),
                    "label": str(getattr(node, "segment_id", "segment")),
                    "category": "",
                    "poi_type": "",
                    "segment_id": str(getattr(node, "segment_id", "")),
                    "band_name": "",
                    "side": "",
                    "required": False,
                    "realized": True,
                    "conflict": False,
                    "anchor_poi_type": "",
                }
            )
        for edge in getattr(road_segment_graph, "edges", ()) or ():
            road_edges.append(
                {
                    "edge_id": f"road_connects:{getattr(edge, 'edge_id', len(road_edges))}",
                    "edge_type": "road_connects",
                    "source_id": f"road_segment:{getattr(edge, 'from_segment_id', '')}",
                    "target_id": f"road_segment:{getattr(edge, 'to_segment_id', '')}",
                    "weight": float(getattr(edge, "weight", 1.0) or 1.0),
                    "label": str(getattr(edge, "edge_id", "")),
                }
            )
    else:
        road_nodes, road_edges = _fallback_road_graph(bounds)

    nodes.extend(road_nodes)
    edges.extend(road_edges)

    poi_nodes: List[Dict[str, Any]] = []
    for poi_type, points in sorted(poi_points.items()):
        spec = get_poi_spec(poi_type)
        for idx, point in enumerate(points):
            poi_nodes.append(
                {
                    "node_id": f"poi:{poi_type}:{idx}",
                    "node_type": "poi",
                    "x": float(point[0]),
                    "z": float(point[1]),
                    "label": spec.display_name,
                    "category": "",
                    "poi_type": poi_type,
                    "segment_id": _nearest_segment_id(road_nodes, point),
                    "band_name": "",
                    "side": "",
                    "required": True,
                    "realized": True,
                    "conflict": False,
                    "anchor_poi_type": "",
                }
            )
    nodes.extend(poi_nodes)

    realized_slot_ids = {
        str(placement.get("slot_id", ""))
        for placement in placements
        if str(placement.get("slot_id", ""))
    }
    slot_nodes: List[Dict[str, Any]] = []
    slot_by_id: Dict[str, Dict[str, Any]] = {}
    for slot in slot_plans:
        point = (float(slot.get("x_center_m", 0.0)), float(slot.get("z_center_m", 0.0)))
        node = {
            "node_id": f"slot_plan:{slot.get('slot_id', len(slot_nodes))}",
            "node_type": "slot_plan",
            "x": point[0],
            "z": point[1],
            "label": f"{slot.get('category', '')} @ {slot.get('band_name', '')}",
            "category": str(slot.get("category", "")),
            "poi_type": "",
            "segment_id": _nearest_segment_id(road_nodes, point),
            "band_name": str(slot.get("band_name", "")),
            "side": str(slot.get("side", "")),
            "required": bool(slot.get("required", False)),
            "realized": str(slot.get("slot_id", "")) in realized_slot_ids,
            "conflict": False,
            "anchor_poi_type": str(slot.get("anchor_poi_type", "")),
        }
        slot_nodes.append(node)
        slot_by_id[str(slot.get("slot_id", ""))] = node
    nodes.extend(slot_nodes)

    placement_nodes: List[Dict[str, Any]] = []
    for placement in placements:
        pos = placement.get("position_xyz", []) or []
        point = (float(pos[0]), float(pos[2])) if len(pos) >= 3 else (0.0, 0.0)
        slot_info = slot_by_id.get(str(placement.get("slot_id", "")), {})
        placement_nodes.append(
            {
                "node_id": f"placement:{placement.get('instance_id', len(placement_nodes))}",
                "node_type": "placement",
                "x": point[0],
                "z": point[1],
                "label": str(placement.get("instance_id", "placement")),
                "category": str(placement.get("category", "")),
                "poi_type": "",
                "segment_id": str(slot_info.get("segment_id", _nearest_segment_id(road_nodes, point))),
                "band_name": str(slot_info.get("band_name", "")),
                "side": str(slot_info.get("side", "")),
                "required": bool(slot_info.get("required", False)),
                "realized": True,
                "conflict": bool(placement.get("violated_rules")),
                "anchor_poi_type": str(slot_info.get("anchor_poi_type", "")),
            }
        )
    nodes.extend(placement_nodes)

    for poi_node in poi_nodes:
        point = (float(poi_node["x"]), float(poi_node["z"]))
        target = _nearest_segment_node_id(road_nodes, point)
        if not target:
            continue
        edges.append(
            {
                "edge_id": f"poi_near_segment:{poi_node['node_id']}->{target}",
                "edge_type": "poi_near_segment",
                "source_id": str(poi_node["node_id"]),
                "target_id": target,
                "weight": float(
                    _distance(
                        point,
                        next(
                            (float(node["x"]), float(node["z"]))
                            for node in road_nodes
                            if node["node_id"] == target
                        ),
                    )
                ),
                "label": "nearest_segment",
            }
        )

    for slot_node in slot_nodes:
        point = (float(slot_node["x"]), float(slot_node["z"]))
        target = _nearest_segment_node_id(road_nodes, point)
        if not target:
            continue
        edges.append(
            {
                "edge_id": f"slot_on_segment:{slot_node['node_id']}->{target}",
                "edge_type": "slot_on_segment",
                "source_id": str(slot_node["node_id"]),
                "target_id": target,
                "weight": float(
                    _distance(
                        point,
                        next(
                            (float(node["x"]), float(node["z"]))
                            for node in road_nodes
                            if node["node_id"] == target
                        ),
                    )
                ),
                "label": "nearest_segment",
            }
        )

    for placement, placement_node in zip(placements, placement_nodes):
        slot_id = str(placement.get("slot_id", ""))
        if slot_id and slot_id in slot_by_id:
            slot_node_id = f"slot_plan:{slot_id}"
            edges.append(
                {
                    "edge_id": f"placement_realizes_slot:{placement_node['node_id']}->{slot_node_id}",
                    "edge_type": "placement_realizes_slot",
                    "source_id": str(placement_node["node_id"]),
                    "target_id": slot_node_id,
                    "weight": 1.0,
                    "label": "realizes",
                }
            )

    for slot in slot_plans:
        anchor_type = str(slot.get("anchor_poi_type", ""))
        anchor_position = slot.get("anchor_position_xz")
        if not anchor_type or not anchor_position:
            continue
        poi_node = _closest_poi_node(poi_nodes, anchor_type, _coerce_point(anchor_position))
        if poi_node is None:
            continue
        slot_node_id = f"slot_plan:{slot.get('slot_id', '')}"
        edges.append(
            {
                "edge_id": f"slot_anchors_poi:{slot_node_id}->{poi_node['node_id']}",
                "edge_type": "slot_anchors_poi",
                "source_id": slot_node_id,
                "target_id": str(poi_node["node_id"]),
                "weight": float(_distance(_coerce_point(anchor_position), (float(poi_node["x"]), float(poi_node["z"])))),
                "label": str(anchor_type),
            }
        )

    for placement, placement_node in zip(placements, placement_nodes):
        for target in _find_conflict_targets(placement, exclusion_zones, poi_nodes):
            edges.append(
                {
                    "edge_id": f"placement_conflicts_poi:{placement_node['node_id']}->{target['poi_node_id']}:{target['rule_name']}",
                    "edge_type": "placement_conflicts_poi",
                    "source_id": str(placement_node["node_id"]),
                    "target_id": str(target["poi_node_id"]),
                    "weight": float(target["weight"]),
                    "label": str(target["rule_name"]),
                }
            )

    categories = sorted(
        {
            str(node.get("category", ""))
            for node in nodes
            if node.get("node_type") in {"slot_plan", "placement"} and str(node.get("category", ""))
        }
    )
    poi_types = sorted(
        {
            canonicalize_poi_type(str(node.get("poi_type", "")))
            for node in nodes
            if node.get("node_type") == "poi" and str(node.get("poi_type", ""))
        }
    )
    edge_types = [edge_type for edge_type in SCENE_GRAPH_EDGE_TYPES if any(edge["edge_type"] == edge_type for edge in edges)]
    default_category = "bus_stop" if "bus_stop" in categories else (categories[0] if categories else "")
    return {
        "bounds": bounds,
        "nodes": nodes,
        "edges": edges,
        "heatmap_defaults": {
            "default_category": default_category,
            "default_layer": "combined",
            "resolution_m": 1.0,
            "mask_mode": "road_corridor",
        },
        "filters": {
            "poi_types": poi_types,
            "categories": categories,
            "edge_types": edge_types,
        },
    }


def ensure_scene_graph(layout_payload: Mapping[str, Any]) -> Dict[str, Any]:
    scene_graph = layout_payload.get("scene_graph")
    if isinstance(scene_graph, dict) and scene_graph.get("nodes") is not None and scene_graph.get("edges") is not None:
        return dict(scene_graph)
    return build_scene_graph(layout_payload)


def scene_graph_control_state(layout_payload: Mapping[str, Any]) -> Dict[str, Any]:
    graph = ensure_scene_graph(layout_payload)
    filters = dict(graph.get("filters", {}) or {})
    defaults = dict(graph.get("heatmap_defaults", {}) or {})
    categories = list(filters.get("categories", []) or [])
    poi_types = list(filters.get("poi_types", []) or [])
    edge_types = list(filters.get("edge_types", []) or [])
    return {
        "available_node_layers": list(SCENE_GRAPH_NODE_TYPES),
        "available_poi_types": poi_types,
        "available_categories": categories,
        "available_edge_types": edge_types,
        "node_layers": list(SCENE_GRAPH_NODE_TYPES),
        "poi_types": poi_types,
        "categories": categories,
        "edge_types": [
            edge_type
            for edge_type in DEFAULT_VISIBLE_EDGE_TYPES
            if edge_type in set(edge_types)
        ] or edge_types,
        "heatmap_category": str(defaults.get("default_category", "") or (categories[0] if categories else "")),
        "heatmap_layer": str(defaults.get("default_layer", "combined") or "combined"),
        "show_heatmap": True,
        "heatmap_opacity": 0.55,
    }


def _normalize_grid(grid: np.ndarray) -> np.ndarray:
    finite = grid[np.isfinite(grid)]
    if finite.size == 0:
        return np.full_like(grid, np.nan, dtype=np.float64)
    max_val = float(np.max(finite))
    if max_val <= 1e-8:
        out = np.zeros_like(grid, dtype=np.float64)
        out[np.isnan(grid)] = np.nan
        return out
    return grid / max_val


def _build_corridor_mask(layout_payload: Mapping[str, Any]) -> Any:
    summary = dict(layout_payload.get("summary", {}) or {})
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    shapely_mod = _require_shapely()
    if shapely_mod is None:
        return None

    unary_union = shapely_mod["unary_union"]
    Polygon = shapely_mod["Polygon"]
    box = shapely_mod["box"]

    corridor_geoms: List[Any] = []
    for ring in osm_geometry.get("carriageway_rings", []) or []:
        if len(ring) >= 3:
            corridor_geoms.append(Polygon(ring))
    for ring in osm_geometry.get("sidewalk_rings", []) or []:
        if len(ring) >= 3:
            corridor_geoms.append(Polygon(ring))
    if corridor_geoms:
        return unary_union(corridor_geoms).buffer(2.0)

    length_m = float(summary.get("length_m", (layout_payload.get("config", {}) or {}).get("length_m", 80.0)))
    road_width_m = float(summary.get("road_width_m", (layout_payload.get("config", {}) or {}).get("road_width_m", 8.0)))
    left_total = float(summary.get("left_clear_path_width_m", 0.0)) + float(summary.get("left_furnishing_width_m", 0.0))
    right_total = float(summary.get("right_clear_path_width_m", 0.0)) + float(summary.get("right_furnishing_width_m", 0.0))
    fallback_sw = float(summary.get("sidewalk_width_m", (layout_payload.get("config", {}) or {}).get("sidewalk_width_m", 2.5)))
    left_total = left_total or fallback_sw
    right_total = right_total or fallback_sw
    half_road = road_width_m / 2.0
    corridor_geoms = [
        box(-length_m / 2.0, -half_road, length_m / 2.0, half_road),
        box(-length_m / 2.0, half_road, length_m / 2.0, half_road + left_total),
        box(-length_m / 2.0, -half_road - right_total, length_m / 2.0, -half_road),
    ]
    return unary_union(corridor_geoms).buffer(2.0)


def _mask_contains(mask: Any, x: float, z: float) -> bool:
    if mask is None:
        return True
    shapely_mod = _require_shapely()
    if shapely_mod is None:
        return True
    return bool(mask.contains(shapely_mod["Point"](float(x), float(z))))


def _attraction_field(
    position_xz: Tuple[float, float],
    category: str,
    poi_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
) -> float:
    return float(
        poi_attraction_score(
            str(category),
            position_xz,
            poi_points_by_type,
        )
    )


def compute_scene_graph_heatmap(
    layout_payload: Mapping[str, Any],
    category: str,
    layer: str = "combined",
    *,
    resolution_m: Optional[float] = None,
) -> Dict[str, Any]:
    graph = ensure_scene_graph(layout_payload)
    bounds = dict(graph.get("bounds", {}) or {})
    defaults = dict(graph.get("heatmap_defaults", {}) or {})
    resolution = float(resolution_m or defaults.get("resolution_m", 1.0) or 1.0)

    xs = np.arange(float(bounds["min_x"]), float(bounds["max_x"]) + resolution, resolution, dtype=np.float64)
    zs = np.arange(float(bounds["min_z"]), float(bounds["max_z"]) + resolution, resolution, dtype=np.float64)
    attraction = np.full((len(zs), len(xs)), np.nan, dtype=np.float64)
    repulsion = np.full((len(zs), len(xs)), np.nan, dtype=np.float64)
    poi_context = _poi_context_from_layout(layout_payload)
    poi_points = _spatial_context_points(layout_payload)
    rule_name = str((layout_payload.get("config", {}) or {}).get("poi_rule_set", "entrance_fire_bus_stop_v1"))
    rule_set = load_rule_set(rule_name)
    corridor_mask = _build_corridor_mask(layout_payload)

    for iz, z in enumerate(zs):
        for ix, x in enumerate(xs):
            if not _mask_contains(corridor_mask, float(x), float(z)):
                continue
            attraction[iz, ix] = _attraction_field((float(x), float(z)), category, poi_points)
            repulsion[iz, ix] = evaluate_repulsion_field(
                (float(x), float(z)),
                category,
                rule_set,
                poi_context,
                aggregate="sum",
            )

    attraction_norm = _normalize_grid(attraction)
    repulsion_norm = _normalize_grid(repulsion)
    combined = attraction_norm - repulsion_norm
    combined[np.isnan(attraction_norm) & np.isnan(repulsion_norm)] = np.nan

    return {
        "x": xs.tolist(),
        "z": zs.tolist(),
        "attraction": attraction,
        "repulsion": repulsion,
        "combined": combined,
        "layer": str(layer),
    }


def _filtered_scene_elements(
    scene_graph: Mapping[str, Any],
    *,
    node_layers: Sequence[str],
    poi_types: Sequence[str],
    categories: Sequence[str],
    edge_types: Sequence[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    allowed_node_types = set(node_layers or [])
    allowed_poi_types = {canonicalize_poi_type(poi_type) for poi_type in poi_types or []}
    allowed_categories = {str(category) for category in categories or []}
    allowed_edge_types = set(edge_types or [])

    nodes: List[Dict[str, Any]] = []
    for node in scene_graph.get("nodes", []) or []:
        node_type = str(node.get("node_type", ""))
        if node_type not in allowed_node_types:
            continue
        if node_type == "poi" and allowed_poi_types and canonicalize_poi_type(str(node.get("poi_type", ""))) not in allowed_poi_types:
            continue
        if node_type in {"slot_plan", "placement"} and allowed_categories and str(node.get("category", "")) not in allowed_categories:
            continue
        nodes.append(dict(node))

    node_ids = {str(node["node_id"]) for node in nodes}
    edges = [
        dict(edge)
        for edge in scene_graph.get("edges", []) or []
        if str(edge.get("edge_type", "")) in allowed_edge_types
        and str(edge.get("source_id", "")) in node_ids
        and str(edge.get("target_id", "")) in node_ids
    ]
    return nodes, edges


def plot_scene_graph(
    layout_payload: Mapping[str, Any],
    *,
    node_layers: Sequence[str],
    poi_types: Sequence[str],
    categories: Sequence[str],
    edge_types: Sequence[str],
    heatmap_category: str,
    heatmap_layer: str = "combined",
    show_heatmap: bool = True,
    heatmap_opacity: float = 0.55,
) -> Any:
    """Render the interactive scene graph as a Plotly bird's-eye plot."""

    go = _require_plotly()
    if go is None:
        return None

    scene_graph = ensure_scene_graph(layout_payload)
    bounds = dict(scene_graph.get("bounds", {}) or {})
    nodes, edges = _filtered_scene_elements(
        scene_graph,
        node_layers=node_layers,
        poi_types=poi_types,
        categories=categories,
        edge_types=edge_types,
    )
    node_lookup = {str(node["node_id"]): node for node in nodes}
    fig = go.Figure()

    if show_heatmap and heatmap_category:
        heatmap = compute_scene_graph_heatmap(layout_payload, heatmap_category, layer=heatmap_layer)
        z_values = heatmap.get(str(heatmap_layer), heatmap.get("combined"))
        if z_values is not None:
            color_scale = "RdBu" if str(heatmap_layer) == "combined" else ("YlOrRd" if str(heatmap_layer) == "attraction" else "PuRd")
            heatmap_kwargs = {
                "x": heatmap["x"],
                "y": heatmap["z"],
                "z": z_values,
                "name": f"heatmap:{heatmap_layer}",
                "opacity": float(heatmap_opacity),
                "colorscale": color_scale,
                "hovertemplate": "x=%{x:.2f}<br>z=%{y:.2f}<br>value=%{z:.3f}<extra></extra>",
                "showscale": True,
            }
            if str(heatmap_layer) == "combined":
                heatmap_kwargs["zmid"] = 0.0
            fig.add_trace(go.Heatmap(**heatmap_kwargs))

    for edge_type in SCENE_GRAPH_EDGE_TYPES:
        if edge_type not in {edge["edge_type"] for edge in edges}:
            continue
        edge_style = _EDGE_STYLES.get(edge_type, {"color": "#bbbbbb", "width": 1.0, "dash": "solid"})
        xs: List[float | None] = []
        zs: List[float | None] = []
        texts: List[str] = []
        for edge in [item for item in edges if item["edge_type"] == edge_type]:
            source = node_lookup.get(str(edge.get("source_id", "")))
            target = node_lookup.get(str(edge.get("target_id", "")))
            if source is None or target is None:
                continue
            xs.extend([float(source["x"]), float(target["x"]), None])
            zs.extend([float(source["z"]), float(target["z"]), None])
            texts.extend([str(edge.get("label", edge_type)), str(edge.get("label", edge_type)), ""])
        if not xs:
            continue
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=zs,
                mode="lines",
                name=edge_type,
                line={
                    "color": edge_style["color"],
                    "width": edge_style["width"],
                    "dash": edge_style["dash"],
                },
                opacity=0.45 if edge_type != "road_connects" else 0.75,
                hoverinfo="skip",
            )
        )

    road_nodes = [node for node in nodes if node["node_type"] == "road_segment"]
    if road_nodes:
        fig.add_trace(
            go.Scatter(
                x=[float(node["x"]) for node in road_nodes],
                y=[float(node["z"]) for node in road_nodes],
                mode="markers",
                name="road_segment",
                marker={"size": 7, "color": "#666666", "symbol": "circle"},
                text=[str(node.get("segment_id", "")) for node in road_nodes],
                hovertemplate="road segment: %{text}<br>x=%{x:.2f}<br>z=%{y:.2f}<extra></extra>",
            )
        )

    poi_nodes = [node for node in nodes if node["node_type"] == "poi"]
    for poi_type in sorted({canonicalize_poi_type(str(node.get("poi_type", ""))) for node in poi_nodes}):
        current = [node for node in poi_nodes if canonicalize_poi_type(str(node.get("poi_type", ""))) == poi_type]
        if not current:
            continue
        spec = get_poi_spec(poi_type)
        fig.add_trace(
            go.Scatter(
                x=[float(node["x"]) for node in current],
                y=[float(node["z"]) for node in current],
                mode="markers",
                name=f"poi:{poi_type}",
                marker={
                    "size": 11,
                    "color": spec.color_hex,
                    "symbol": _PLOTLY_POI_SYMBOLS.get(poi_type, "circle"),
                    "line": {"width": 1.2, "color": "#ffffff"},
                },
                text=[spec.display_name for _ in current],
                hovertemplate="%{text}<br>x=%{x:.2f}<br>z=%{y:.2f}<extra></extra>",
            )
        )

    slot_nodes = [node for node in nodes if node["node_type"] == "slot_plan"]
    for category in sorted({str(node.get("category", "")) for node in slot_nodes if str(node.get("category", ""))}):
        current = [node for node in slot_nodes if str(node.get("category", "")) == category]
        fig.add_trace(
            go.Scatter(
                x=[float(node["x"]) for node in current],
                y=[float(node["z"]) for node in current],
                mode="markers",
                name=f"slot:{category}",
                marker={
                    "size": 11,
                    "color": _CATEGORY_COLORS.get(category, "#666666"),
                    "symbol": "square-open",
                    "line": {"width": 2.0, "color": _CATEGORY_COLORS.get(category, "#666666")},
                },
                text=[str(node.get("band_name", "")) for node in current],
                hovertemplate="slot %{text}<br>category=" + category + "<br>x=%{x:.2f}<br>z=%{y:.2f}<extra></extra>",
            )
        )

    placement_nodes = [node for node in nodes if node["node_type"] == "placement"]
    for category in sorted({str(node.get("category", "")) for node in placement_nodes if str(node.get("category", ""))}):
        normal = [node for node in placement_nodes if str(node.get("category", "")) == category and not bool(node.get("conflict", False))]
        conflict = [node for node in placement_nodes if str(node.get("category", "")) == category and bool(node.get("conflict", False))]
        if normal:
            fig.add_trace(
                go.Scatter(
                    x=[float(node["x"]) for node in normal],
                    y=[float(node["z"]) for node in normal],
                    mode="markers",
                    name=f"placement:{category}",
                    marker={
                        "size": 10,
                        "color": _CATEGORY_COLORS.get(category, "#555555"),
                        "symbol": "circle",
                        "line": {"width": 0.8, "color": "#1f1f1f"},
                    },
                    text=[str(node.get("label", "")) for node in normal],
                    hovertemplate="%{text}<br>category=" + category + "<br>x=%{x:.2f}<br>z=%{y:.2f}<extra></extra>",
                )
            )
        if conflict:
            fig.add_trace(
                go.Scatter(
                    x=[float(node["x"]) for node in conflict],
                    y=[float(node["z"]) for node in conflict],
                    mode="markers",
                    name=f"placement_conflict:{category}",
                    marker={
                        "size": 12,
                        "color": _CATEGORY_COLORS.get(category, "#555555"),
                        "symbol": "x",
                        "line": {"width": 2.0, "color": "#d90429"},
                    },
                    text=[str(node.get("label", "")) for node in conflict],
                    hovertemplate="%{text}<br>category=" + category + "<br>conflict=true<br>x=%{x:.2f}<br>z=%{y:.2f}<extra></extra>",
                )
            )

    fig.update_layout(
        title=f"Scene Graph ({heatmap_layer})" if show_heatmap and heatmap_category else "Scene Graph",
        template="plotly_white",
        xaxis_title="X (m)",
        yaxis_title="Z (m)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0.0},
        margin={"l": 36, "r": 16, "t": 56, "b": 36},
        height=540,
    )
    fig.update_xaxes(range=[float(bounds["min_x"]), float(bounds["max_x"])])
    fig.update_yaxes(range=[float(bounds["min_z"]), float(bounds["max_z"])], scaleanchor="x", scaleratio=1.0)
    return fig
