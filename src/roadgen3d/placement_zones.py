"""Placement zone construction from OSM road geometry for M5."""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .cross_section_synthesis import synthesize_poi_driven_cross_section
from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    core_poi_count,
    extract_poi_points_by_type,
    nonempty_poi_points,
    normalize_poi_counts,
    normalize_poi_points_by_type,
    poi_breakdown_string,
    poi_weighted_score,
)
from .street_band_semantics import detailed_strip_band_name

logger = logging.getLogger(__name__)
EFFECTIVE_POI_EVALUATOR_VERSION = "v2"
CENTER_PLANTING_STRIP_ZONE_KEYS = frozenset({"center_grass_belt", "center_median_green"})
CENTER_PLANTING_JUNCTION_SETBACK_M = 0.25


def _require_shapely():
    try:
        import shapely
    except ImportError as exc:
        raise RuntimeError(
            "`shapely` is required for M5 placement zones. Install requirements-m5.txt."
        ) from exc
    return shapely


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class PlacementContext:
    """Geometric context for OSM-based placement."""

    sidewalk_zone: Any  # shapely (Multi)Polygon – region where furniture can be placed
    carriageway: Any  # shapely (Multi)Polygon – road surface
    left_sidewalk_zone: Any = None  # shapely (Multi)Polygon – left-side pedestrian corridor
    right_sidewalk_zone: Any = None  # shapely (Multi)Polygon – right-side pedestrian corridor
    entrance_points: List[Tuple[float, float]] = field(default_factory=list)  # (x, y) local metres
    bus_stop_points: List[Tuple[float, float]] = field(default_factory=list)
    fire_points: List[Tuple[float, float]] = field(default_factory=list)
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    aoi_polygon: Any = None  # shapely Polygon – bounding box polygon
    origin_offset: Tuple[float, float] = (0.0, 0.0)
    carriageway_polygon: Any = None
    road_reference: Any = None
    road_references: List[Any] = field(default_factory=list)
    carriageway_width_m: float = 0.0
    left_clear_path_width_m: float = 0.0
    right_clear_path_width_m: float = 0.0
    left_furnishing_width_m: float = 0.0
    right_furnishing_width_m: float = 0.0
    row_width_m: float = 0.0
    width_expanded: bool = False
    width_reallocation_reason: str = ""
    poi_fit_feasible: bool = True
    poi_fit_report: Dict[str, Any] = field(default_factory=dict)
    required_left_width_m: float = 0.0
    required_right_width_m: float = 0.0
    junction_geometries: List[Dict[str, Any]] = field(default_factory=list)
    road_arm_geometries: List[Any] = field(default_factory=list)
    building_regions: List[Dict[str, Any]] = field(default_factory=list)
    regions: List[Dict[str, Any]] = field(default_factory=list)
    derived_regions: List[Dict[str, Any]] = field(default_factory=list)
    region_derivation_summary: Dict[str, Any] = field(default_factory=dict)
    detailed_strip_profiles: List[Dict[str, Any]] = field(default_factory=list)
    strip_zones: Dict[str, Any] = field(default_factory=dict)
    segment_strip_zones: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    functional_zones: List[Dict[str, Any]] = field(default_factory=list)
    surface_annotations: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Polygon construction
# ---------------------------------------------------------------------------

def build_carriageway_polygon(roads: list) -> Any:
    """Union of road centreline buffers → carriageway MultiPolygon.

    *roads* is a list of ``OsmRoad`` (from osm_ingest).
    """
    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import unary_union

    polygons = []
    for road in roads:
        if len(road.coords) < 2:
            continue
        line = LineString(road.coords)
        half_w = max(road.width_m / 2.0, 0.5)
        poly = line.buffer(half_w, cap_style="flat")
        if not poly.is_empty:
            polygons.append(poly)

    if not polygons:
        return MultiPolygon()

    merged = unary_union(polygons)
    if merged.geom_type == "Polygon":
        return MultiPolygon([merged])
    return merged


def build_carriageway_polygon_with_width(
    roads: list,
    carriageway_width_m: float,
) -> Any:
    """Union of road centreline buffers using an explicit carriageway width."""

    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import unary_union

    polygons = []
    half_w = max(float(carriageway_width_m) / 2.0, 0.5)
    for road in roads:
        if len(road.coords) < 2:
            continue
        line = LineString(road.coords)
        poly = line.buffer(half_w, cap_style="flat")
        if not poly.is_empty:
            polygons.append(poly)

    if not polygons:
        return MultiPolygon()

    merged = unary_union(polygons)
    if merged.geom_type == "Polygon":
        return MultiPolygon([merged])
    return merged


def build_sidewalk_zone(
    carriageway: Any,
    sidewalk_width_m: float,
    aoi_polygon: Any,
) -> Any:
    """Sidewalk zone = (carriageway outer buffer) – carriageway, clipped to AOI."""
    from shapely.geometry import MultiPolygon

    if carriageway.is_empty:
        return MultiPolygon()

    outer = carriageway.buffer(sidewalk_width_m)
    sidewalk = outer.difference(carriageway)
    sidewalk = sidewalk.intersection(aoi_polygon)

    if sidewalk.is_empty:
        return MultiPolygon()
    if sidewalk.geom_type == "Polygon":
        return MultiPolygon([sidewalk])
    # Filter out non-polygon geometries from collections
    if sidewalk.geom_type == "GeometryCollection":
        from shapely.geometry import Polygon as ShapelyPolygon
        polys = [g for g in sidewalk.geoms if isinstance(g, ShapelyPolygon)]
        return MultiPolygon(polys) if polys else MultiPolygon()
    return sidewalk


def build_sidewalk_zones_from_roads(
    roads: list,
    *,
    carriageway_width_m: float,
    left_sidewalk_width_m: float,
    right_sidewalk_width_m: float,
    aoi_polygon: Any,
) -> Tuple[Any, Any, Any]:
    """Build asymmetric left/right sidewalk corridors from road centerlines."""

    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import unary_union

    left_polygons = []
    right_polygons = []
    carriageway_half = max(float(carriageway_width_m) / 2.0, 0.5)
    left_total = max(float(left_sidewalk_width_m), 0.0)
    right_total = max(float(right_sidewalk_width_m), 0.0)

    for road in roads:
        if len(road.coords) < 2:
            continue
        line = LineString(road.coords)
        if left_total > 0.0:
            outer_left = line.buffer(carriageway_half + left_total, cap_style="flat", single_sided=True)
            inner_left = line.buffer(carriageway_half, cap_style="flat", single_sided=True)
            left_zone = outer_left.difference(inner_left)
            if not left_zone.is_empty:
                left_polygons.append(left_zone)
        if right_total > 0.0:
            outer_right = line.buffer(-(carriageway_half + right_total), cap_style="flat", single_sided=True)
            inner_right = line.buffer(-carriageway_half, cap_style="flat", single_sided=True)
            right_zone = outer_right.difference(inner_right)
            if not right_zone.is_empty:
                right_polygons.append(right_zone)

    def _merge(polygons: List[Any]) -> Any:
        if not polygons:
            return MultiPolygon()
        merged = unary_union(polygons)
        return _clip_to_aoi(merged, aoi_polygon)

    left_sidewalk = _merge(left_polygons)
    right_sidewalk = _merge(right_polygons)
    union_sidewalk = left_sidewalk.union(right_sidewalk)
    union_sidewalk = _clip_to_aoi(union_sidewalk, aoi_polygon)
    return left_sidewalk, right_sidewalk, union_sidewalk


def _clip_to_aoi(geometry: Any, aoi_polygon: Any) -> Any:
    """Clip a geometry to the AOI polygon, returning a MultiPolygon."""
    from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon

    if geometry.is_empty:
        return MultiPolygon()
    clipped = geometry.intersection(aoi_polygon)
    if clipped.is_empty:
        return MultiPolygon()
    if clipped.geom_type == "Polygon":
        return MultiPolygon([clipped])
    if clipped.geom_type == "GeometryCollection":
        polys = [g for g in clipped.geoms if isinstance(g, ShapelyPolygon)]
        return MultiPolygon(polys) if polys else MultiPolygon()
    return clipped


def _dedupe_adjacent_points(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    deduped: List[Tuple[float, float]] = []
    for point in points:
        xy = (float(point[0]), float(point[1]))
        if not deduped or math.hypot(deduped[-1][0] - xy[0], deduped[-1][1] - xy[1]) > 1e-6:
            deduped.append(xy)
    return deduped


def _normalize_angle_deg(value: float) -> float:
    normalized = math.fmod(float(value), 360.0)
    if normalized < 0.0:
        normalized += 360.0
    return normalized


def _angle_deg(from_point: Tuple[float, float], to_point: Tuple[float, float]) -> float:
    return _normalize_angle_deg(
        math.degrees(float(math.atan2(float(to_point[1]) - float(from_point[1]), float(to_point[0]) - float(from_point[0]))))
    )


def _circular_angle_diffs_deg(angles_deg: Sequence[float]) -> List[float]:
    if not angles_deg:
        return []
    ordered = sorted(_normalize_angle_deg(value) for value in angles_deg)
    diffs: List[float] = []
    for index, value in enumerate(ordered):
        next_value = ordered[(index + 1) % len(ordered)]
        raw_diff = next_value - value
        if index == len(ordered) - 1:
            raw_diff += 360.0
        diffs.append(float(raw_diff))
    return diffs


def _classify_junction_kind(angles_deg: Sequence[float]) -> str:
    arm_count = len(tuple(angles_deg))
    diffs = _circular_angle_diffs_deg(angles_deg)
    if arm_count == 4 and diffs and max(abs(diff - 90.0) for diff in diffs) <= 35.0:
        return "cross_junction"
    if arm_count == 3 and diffs and any(diff >= 145.0 for diff in diffs):
        return "t_junction"
    return "complex_junction"


def _road_profile_widths_from_graph(road_segment_graph: Any | None) -> Dict[int, Dict[str, Any]]:
    profiles: Dict[int, Dict[str, Any]] = {}
    if road_segment_graph is None:
        return profiles
    is_annotation_graph = str(getattr(road_segment_graph, "mode", "") or "") == "annotation"
    for node in getattr(road_segment_graph, "nodes", ()) or ():
        road_id = int(getattr(node, "road_id", 0) or 0)
        if road_id <= 0 or road_id in profiles:
            continue
        strips = tuple(getattr(node, "cross_section_strips", ()) or ())
        width_by_kind: Dict[str, List[float]] = {}
        for strip in strips:
            kind = str(getattr(strip, "kind", "") or "")
            zone = str(getattr(strip, "zone", "") or "")
            if zone not in {"left", "right"}:
                continue
            width_by_kind.setdefault(kind, []).append(float(getattr(strip, "width_m", 0.0) or 0.0))
        def _avg(kind: str) -> float:
            values = [float(value) for value in width_by_kind.get(kind, []) if float(value) > 0.0]
            return float(sum(values) / len(values)) if values else 0.0
        nearroad_buffer_width_m = _avg("nearroad_buffer")
        nearroad_furnishing_width_m = _avg("nearroad_furnishing")
        clear_sidewalk_width_m = _avg("clear_sidewalk")
        farfromroad_buffer_width_m = _avg("farfromroad_buffer")
        frontage_reserve_width_m = _avg("frontage_reserve")
        if is_annotation_graph and not strips:
            nearroad_buffer_width_m = 0.0
            nearroad_furnishing_width_m = 1.5
            clear_sidewalk_width_m = 2.5
            farfromroad_buffer_width_m = 0.0
            frontage_reserve_width_m = 2.0
        center_width_m = sum(
            max(float(getattr(strip, "width_m", 0.0) or 0.0), 0.0)
            for strip in strips
            if str(getattr(strip, "zone", "") or "") == "center"
        )
        half_carriageway_m = center_width_m * 0.5
        side_strip_layouts: Dict[str, List[Dict[str, float | str]]] = {"left": [], "right": []}
        for zone, sign in (("left", 1.0), ("right", -1.0)):
            zone_strips = sorted(
                (
                    strip
                    for strip in strips
                    if str(getattr(strip, "zone", "") or "") == zone
                ),
                key=lambda item: int(getattr(item, "order_index", 0) or 0),
            )
            offset_from_carriageway_m = 0.0
            for strip in zone_strips:
                width_m = max(float(getattr(strip, "width_m", 0.0) or 0.0), 0.0)
                inner_abs_m = half_carriageway_m + offset_from_carriageway_m
                outer_abs_m = inner_abs_m + width_m
                center_abs_m = (inner_abs_m + outer_abs_m) * 0.5
                side_strip_layouts[zone].append(
                    {
                        "strip_id": str(getattr(strip, "strip_id", "") or ""),
                        "kind": str(getattr(strip, "kind", "") or ""),
                        "zone": zone,
                        "width_m": width_m,
                        "center_offset_m": center_abs_m * sign,
                        "inner_offset_m": inner_abs_m * sign,
                        "outer_offset_m": outer_abs_m * sign,
                    }
                )
                offset_from_carriageway_m += width_m
        profiles[road_id] = {
            "carriageway_width_m": float(getattr(node, "road_width_m", 0.0) or 0.0),
            "nearroad_buffer_width_m": nearroad_buffer_width_m,
            "nearroad_furnishing_width_m": nearroad_furnishing_width_m,
            "clear_sidewalk_width_m": clear_sidewalk_width_m,
            "farfromroad_buffer_width_m": farfromroad_buffer_width_m,
            "frontage_reserve_width_m": frontage_reserve_width_m,
            "side_strip_layouts": side_strip_layouts,
            "center_strip_layouts": _node_center_strip_layouts(node),
        }
    return profiles


def _node_side_strip_layouts(node: Any) -> Tuple[float, Dict[str, List[Dict[str, Any]]]]:
    strips = tuple(getattr(node, "cross_section_strips", ()) or ())
    center_width_m = sum(
        max(float(getattr(strip, "width_m", 0.0) or 0.0), 0.0)
        for strip in strips
        if str(getattr(strip, "zone", "") or "").strip().lower() == "center"
    )
    if center_width_m <= 0.0:
        center_width_m = max(float(getattr(node, "road_width_m", 0.0) or 0.0), 0.0)
    half_carriageway_m = center_width_m * 0.5
    layouts: Dict[str, List[Dict[str, Any]]] = {"left": [], "right": []}
    for side, sign in (("left", 1.0), ("right", -1.0)):
        side_strips = sorted(
            (
                strip
                for strip in strips
                if str(getattr(strip, "zone", "") or "").strip().lower() == side
            ),
            key=lambda item: int(getattr(item, "order_index", 0) or 0),
        )
        offset_from_carriageway_m = 0.0
        for strip in side_strips:
            width_m = max(float(getattr(strip, "width_m", 0.0) or 0.0), 0.0)
            if width_m <= 0.0:
                continue
            inner_abs_m = half_carriageway_m + offset_from_carriageway_m
            outer_abs_m = inner_abs_m + width_m
            center_abs_m = (inner_abs_m + outer_abs_m) * 0.5
            strip_kind = str(getattr(strip, "kind", "") or "").strip().lower()
            layouts[side].append(
                {
                    "segment_id": str(getattr(node, "segment_id", "") or ""),
                    "road_id": int(getattr(node, "road_id", 0) or 0),
                    "strip_id": str(getattr(strip, "strip_id", "") or ""),
                    "side": side,
                    "kind": strip_kind,
                    "direction": str(getattr(strip, "direction", "none") or "none").strip().lower() or "none",
                    "band_name": detailed_strip_band_name(side, strip_kind),
                    "width_m": float(width_m),
                    "order_index": int(getattr(strip, "order_index", 0) or 0),
                    "inner_abs_m": float(inner_abs_m),
                    "outer_abs_m": float(outer_abs_m),
                    "center_abs_m": float(center_abs_m),
                    "inner_offset_m": float(inner_abs_m * sign),
                    "outer_offset_m": float(outer_abs_m * sign),
                    "center_offset_m": float(center_abs_m * sign),
                }
            )
            offset_from_carriageway_m += width_m
    return float(center_width_m), layouts


def _node_center_strip_layouts(node: Any) -> List[Dict[str, Any]]:
    strips = tuple(getattr(node, "cross_section_strips", ()) or ())
    center_width_m = sum(
        max(float(getattr(strip, "width_m", 0.0) or 0.0), 0.0)
        for strip in strips
        if str(getattr(strip, "zone", "") or "").strip().lower() == "center"
    )
    if center_width_m <= 0.0:
        center_width_m = max(float(getattr(node, "road_width_m", 0.0) or 0.0), 0.0)
    half_carriageway_m = center_width_m * 0.5
    center_strips = sorted(
        (
            strip
            for strip in strips
            if str(getattr(strip, "zone", "") or "").strip().lower() == "center"
        ),
        key=lambda item: int(getattr(item, "order_index", 0) or 0),
    )
    layouts: List[Dict[str, Any]] = []
    offset_m = -half_carriageway_m
    for strip in center_strips:
        width_m = max(float(getattr(strip, "width_m", 0.0) or 0.0), 0.0)
        if width_m <= 0.0:
            continue
        inner_m = offset_m
        outer_m = offset_m + width_m
        center_m = (inner_m + outer_m) * 0.5
        strip_kind = str(getattr(strip, "kind", "") or "").strip().lower()
        layouts.append(
            {
                "segment_id": str(getattr(node, "segment_id", "") or ""),
                "road_id": int(getattr(node, "road_id", 0) or 0),
                "strip_id": str(getattr(strip, "strip_id", "") or ""),
                "side": "center",
                "kind": strip_kind,
                "direction": str(getattr(strip, "direction", "none") or "none").strip().lower() or "none",
                "band_name": f"center_{strip_kind}",
                "width_m": float(width_m),
                "order_index": int(getattr(strip, "order_index", 0) or 0),
                "inner_m": float(inner_m),
                "outer_m": float(outer_m),
                "center_m": float(center_m),
            }
        )
        offset_m += width_m
    return layouts


def _merge_geometry_map(entries: Mapping[str, List[Any]], *, aoi_polygon: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key, geometries in entries.items():
        merged[str(key)] = _merge_polygon_geometries(tuple(geometries), aoi_polygon=aoi_polygon)
    return merged


def _build_graph_strip_context(
    road_segment_graph: Any | None,
    *,
    aoi_polygon: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, float]]:
    if road_segment_graph is None:
        return [], {}, {}, {}

    try:
        from shapely.geometry import LineString
    except Exception:
        return [], {}, {}, {}

    profiles: List[Dict[str, Any]] = []
    global_geometries: Dict[str, List[Any]] = {}
    segment_geometries: Dict[str, Dict[str, List[Any]]] = {}
    segment_widths: Dict[str, Dict[str, float]] = {}

    for node in getattr(road_segment_graph, "nodes", ()) or ():
        segment_id = str(getattr(node, "segment_id", "") or "")
        if not segment_id:
            continue
        center_width_m, side_layouts = _node_side_strip_layouts(node)
        center_layouts = _node_center_strip_layouts(node)
        if center_width_m <= 0.0:
            continue
        start_xy = tuple(float(value) for value in getattr(node, "start_xy", (0.0, 0.0)))
        end_xy = tuple(float(value) for value in getattr(node, "end_xy", (0.0, 0.0)))
        if math.hypot(float(end_xy[0]) - float(start_xy[0]), float(end_xy[1]) - float(start_xy[1])) <= 1e-6:
            continue
        line = LineString([start_xy, end_xy])
        segment_widths[segment_id] = {
            "carriageway_width_m": float(center_width_m),
            "left_total_width_m": float(sum(float(item["width_m"]) for item in side_layouts.get("left", ()))),
            "right_total_width_m": float(sum(float(item["width_m"]) for item in side_layouts.get("right", ()))),
        }
        for side in ("left", "right"):
            sign = 1.0 if side == "left" else -1.0
            for layout in side_layouts.get(side, ()):
                outer = line.buffer(sign * float(layout["outer_abs_m"]), cap_style="flat", single_sided=True)
                inner = line.buffer(sign * float(layout["inner_abs_m"]), cap_style="flat", single_sided=True)
                strip_zone = _clip_to_aoi(outer.difference(inner), aoi_polygon)
                if getattr(strip_zone, "is_empty", True):
                    continue
                band_name = str(layout["band_name"])
                profile_record = {
                    **dict(layout),
                    "carriageway_width_m": float(center_width_m),
                }
                profiles.append(profile_record)
                global_geometries.setdefault(band_name, []).append(strip_zone)
                segment_geometries.setdefault(segment_id, {}).setdefault(band_name, []).append(strip_zone)

        for layout in center_layouts:
            inner_m = float(layout["inner_m"])
            outer_m = float(layout["outer_m"])
            if outer_m <= 0.0:
                outer_zone = line.buffer(outer_m, cap_style="flat", single_sided=True)
                inner_zone = line.buffer(inner_m, cap_style="flat", single_sided=True)
                strip_zone = _clip_to_aoi(inner_zone.difference(outer_zone), aoi_polygon)
            elif inner_m >= 0.0:
                outer_zone = line.buffer(outer_m, cap_style="flat", single_sided=True)
                inner_zone = line.buffer(inner_m, cap_style="flat", single_sided=True)
                strip_zone = _clip_to_aoi(outer_zone.difference(inner_zone), aoi_polygon)
            else:
                negative_zone = line.buffer(inner_m, cap_style="flat", single_sided=True)
                positive_zone = line.buffer(outer_m, cap_style="flat", single_sided=True)
                strip_zone = _clip_to_aoi(negative_zone.union(positive_zone), aoi_polygon)
            if getattr(strip_zone, "is_empty", True):
                continue
            band_name = str(layout["band_name"])
            profile_record = {
                **dict(layout),
                "carriageway_width_m": float(center_width_m),
            }
            profiles.append(profile_record)
            global_geometries.setdefault(band_name, []).append(strip_zone)
            segment_geometries.setdefault(segment_id, {}).setdefault(band_name, []).append(strip_zone)

    summary: Dict[str, float] = {}
    if segment_widths:
        summary["carriageway_width_m"] = float(
            max(float(values.get("carriageway_width_m", 0.0) or 0.0) for values in segment_widths.values())
        )
        summary["left_total_width_m"] = float(
            max(float(values.get("left_total_width_m", 0.0) or 0.0) for values in segment_widths.values())
        )
        summary["right_total_width_m"] = float(
            max(float(values.get("right_total_width_m", 0.0) or 0.0) for values in segment_widths.values())
        )
    for side in ("left", "right"):
        for strip_kind in ("nearroad_furnishing", "clear_sidewalk", "frontage_reserve"):
            key = f"{side}_{strip_kind}_width_m"
            values = [
                float(profile["width_m"])
                for profile in profiles
                if str(profile.get("side", "")) == side and str(profile.get("kind", "")) == strip_kind
            ]
            if values:
                summary[key] = float(max(values))

    return (
        profiles,
        _merge_geometry_map(global_geometries, aoi_polygon=aoi_polygon),
        {
            segment_id: _merge_geometry_map(entries, aoi_polygon=aoi_polygon)
            for segment_id, entries in segment_geometries.items()
        },
        summary,
    )


def _merge_polygon_geometries(polygons: Sequence[Any], *, aoi_polygon: Any | None = None) -> Any:
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    valid = [poly for poly in polygons if poly is not None and not getattr(poly, "is_empty", True)]
    if not valid:
        return MultiPolygon()
    merged = unary_union(valid)
    if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
        merged = merged.intersection(aoi_polygon)
    if merged.is_empty:
        return MultiPolygon()
    return merged


def _clean_polygonal_geometry(geometry: Any, *, aoi_polygon: Any | None = None) -> Any:
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon as ShapelyPolygon

    if geometry is None or getattr(geometry, "is_empty", True):
        return MultiPolygon()
    cleaned = geometry
    if not getattr(cleaned, "is_valid", True):
        try:
            cleaned = cleaned.buffer(0)
        except Exception:
            return MultiPolygon()
    if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
        cleaned = cleaned.intersection(aoi_polygon)
    if getattr(cleaned, "is_empty", True):
        return MultiPolygon()
    if getattr(cleaned, "geom_type", "") == "Polygon":
        return MultiPolygon([cleaned])
    if getattr(cleaned, "geom_type", "") == "MultiPolygon":
        return cleaned
    if getattr(cleaned, "geom_type", "") == "GeometryCollection":
        polygons = [
            item
            for item in getattr(cleaned, "geoms", ())
            if isinstance(item, ShapelyPolygon) and not getattr(item, "is_empty", True)
        ]
        return MultiPolygon(polygons) if polygons else MultiPolygon()
    if isinstance(cleaned, GeometryCollection):
        return MultiPolygon()
    return MultiPolygon()


def _patch_polygonal_geometry(patch: Mapping[str, Any], *, aoi_polygon: Any | None = None) -> Any:
    geometry = patch.get("geometry")
    if geometry is None:
        rings = patch.get("rings")
        if isinstance(rings, (list, tuple)) and rings:
            try:
                from shapely.geometry import Polygon as ShapelyPolygon

                exterior = rings[0]
                holes = list(rings[1:])
                geometry = ShapelyPolygon(exterior, holes)
            except Exception:
                geometry = None
    return _clean_polygonal_geometry(geometry, aoi_polygon=aoi_polygon)


def _center_planting_junction_trim_geometry(
    junction_geometries: Sequence[Mapping[str, Any]],
    *,
    aoi_polygon: Any | None = None,
    setback_m: float = CENTER_PLANTING_JUNCTION_SETBACK_M,
) -> Any:
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    trim_sources: List[Any] = []
    for junction in junction_geometries or ():
        for key in ("carriageway_core", "junction_core_rect"):
            geometry = _clean_polygonal_geometry(junction.get(key), aoi_polygon=aoi_polygon)
            if not getattr(geometry, "is_empty", True):
                trim_sources.append(geometry)
        for patch in junction.get("crosswalk_patches", []) or ():
            if isinstance(patch, Mapping):
                geometry = _patch_polygonal_geometry(patch, aoi_polygon=aoi_polygon)
                if not getattr(geometry, "is_empty", True):
                    trim_sources.append(geometry)
        for patch in junction.get("normalized_surface_patches", []) or ():
            if not isinstance(patch, Mapping):
                continue
            role = str(patch.get("surface_role", "") or "").strip().lower()
            if role not in {"carriageway", "crossing"}:
                continue
            geometry = _patch_polygonal_geometry(patch, aoi_polygon=aoi_polygon)
            if not getattr(geometry, "is_empty", True):
                trim_sources.append(geometry)

    if not trim_sources:
        return MultiPolygon()
    trim_geometry = _clean_polygonal_geometry(unary_union(trim_sources), aoi_polygon=aoi_polygon)
    if getattr(trim_geometry, "is_empty", True):
        return MultiPolygon()
    if float(setback_m) > 0.0:
        trim_geometry = _clean_polygonal_geometry(trim_geometry.buffer(float(setback_m)), aoi_polygon=aoi_polygon)
    return trim_geometry


def trim_center_planting_strips_for_junctions(
    placement_context: PlacementContext,
    junction_geometries: Sequence[Mapping[str, Any]] | None = None,
    *,
    setback_m: float = CENTER_PLANTING_JUNCTION_SETBACK_M,
) -> None:
    """Clip center planting strip zones away from junction core and crossing surfaces."""

    if placement_context is None:
        return
    resolved_junctions = list(junction_geometries if junction_geometries is not None else placement_context.junction_geometries)
    if not resolved_junctions:
        return
    aoi_polygon = getattr(placement_context, "aoi_polygon", None)
    trim_geometry = _center_planting_junction_trim_geometry(
        resolved_junctions,
        aoi_polygon=aoi_polygon,
        setback_m=float(setback_m),
    )
    if getattr(trim_geometry, "is_empty", True):
        return

    def trim_geometry_value(geometry: Any) -> Any:
        source = _clean_polygonal_geometry(geometry, aoi_polygon=aoi_polygon)
        if getattr(source, "is_empty", True):
            return source
        return _clean_polygonal_geometry(source.difference(trim_geometry), aoi_polygon=aoi_polygon)

    strip_zones = dict(getattr(placement_context, "strip_zones", {}) or {})
    for key in tuple(CENTER_PLANTING_STRIP_ZONE_KEYS):
        if key not in strip_zones:
            continue
        trimmed = trim_geometry_value(strip_zones.get(key))
        if getattr(trimmed, "is_empty", True):
            strip_zones.pop(key, None)
        else:
            strip_zones[key] = trimmed
    placement_context.strip_zones = strip_zones

    segment_strip_zones: Dict[str, Dict[str, Any]] = {}
    for segment_id, entries in (getattr(placement_context, "segment_strip_zones", {}) or {}).items():
        next_entries = dict(entries or {})
        for key in tuple(CENTER_PLANTING_STRIP_ZONE_KEYS):
            if key not in next_entries:
                continue
            trimmed = trim_geometry_value(next_entries.get(key))
            if getattr(trimmed, "is_empty", True):
                next_entries.pop(key, None)
            else:
                next_entries[key] = trimmed
        segment_strip_zones[str(segment_id)] = next_entries
    placement_context.segment_strip_zones = segment_strip_zones


def _sector_patch(
    *,
    anchor: Tuple[float, float],
    start_angle_deg: float,
    end_angle_deg: float,
    inner_radius_m: float,
    outer_radius_m: float,
    steps: int = 12,
) -> Any:
    from shapely.geometry import Polygon

    if outer_radius_m <= inner_radius_m or outer_radius_m <= 0.0:
        return Polygon()
    start = _normalize_angle_deg(start_angle_deg)
    end = _normalize_angle_deg(end_angle_deg)
    sweep = end - start
    if sweep <= 0.0:
        sweep += 360.0
    if sweep > 180.0:
        start = end
        sweep = 360.0 - sweep
    if sweep <= 1e-3 or sweep >= 179.0:
        return Polygon()
    point_count = max(int(steps), 3)
    outer_points: List[Tuple[float, float]] = []
    inner_points: List[Tuple[float, float]] = []
    for index in range(point_count + 1):
        ratio = float(index) / float(point_count)
        angle_deg = start + sweep * ratio
        angle_rad = math.radians(angle_deg)
        outer_points.append(
            (
                float(anchor[0]) + math.cos(angle_rad) * float(outer_radius_m),
                float(anchor[1]) + math.sin(angle_rad) * float(outer_radius_m),
            )
        )
        inner_points.append(
            (
                float(anchor[0]) + math.cos(angle_rad) * float(inner_radius_m),
                float(anchor[1]) + math.sin(angle_rad) * float(inner_radius_m),
            )
        )
    ring = [*outer_points, *reversed(inner_points)]
    return Polygon(ring)


def _rectangle_patch(
    *,
    center: Tuple[float, float],
    tangent: Tuple[float, float],
    normal: Tuple[float, float],
    length_m: float,
    width_m: float,
) -> Any:
    from shapely.geometry import Polygon

    half_length = max(float(length_m) * 0.5, 0.05)
    half_width = max(float(width_m) * 0.5, 0.05)
    corners = [
        (
            center[0] - tangent[0] * half_length - normal[0] * half_width,
            center[1] - tangent[1] * half_length - normal[1] * half_width,
        ),
        (
            center[0] - tangent[0] * half_length + normal[0] * half_width,
            center[1] - tangent[1] * half_length + normal[1] * half_width,
        ),
        (
            center[0] + tangent[0] * half_length + normal[0] * half_width,
            center[1] + tangent[1] * half_length + normal[1] * half_width,
        ),
        (
            center[0] + tangent[0] * half_length - normal[0] * half_width,
            center[1] + tangent[1] * half_length - normal[1] * half_width,
        ),
    ]
    return Polygon(corners)


def _angle_distance_deg(a_deg: float, b_deg: float) -> float:
    diff = abs(_normalize_angle_deg(float(a_deg) - float(b_deg)))
    return float(min(diff, abs(diff - 360.0)))


def _axis_distance_deg(angle_deg: float, axis_angle_deg: float) -> float:
    diff = _angle_distance_deg(angle_deg, axis_angle_deg)
    return float(min(diff, abs(diff - 180.0)))


def _unit_vector_from_angle(angle_deg: float) -> Tuple[float, float]:
    angle_rad = math.radians(float(angle_deg))
    return (math.cos(angle_rad), math.sin(angle_rad))


def _principal_junction_axis(arms: Sequence[Dict[str, Any]]) -> Tuple[float, float]:
    if not arms:
        return (1.0, 0.0)
    best_pair: Tuple[Dict[str, Any], Dict[str, Any]] | None = None
    best_score = float("inf")
    for index, arm in enumerate(arms):
        for other in arms[index + 1 :]:
            diff = _angle_distance_deg(float(arm["angle_deg"]), float(other["angle_deg"]))
            score = abs(diff - 180.0)
            if score < best_score:
                best_score = score
                best_pair = (arm, other)
    if best_pair is not None and best_score <= 45.0:
        return tuple(float(value) for value in best_pair[0]["tangent"])
    return tuple(float(value) for value in arms[0]["tangent"])


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _ray_rectangle_exit_distance(
    direction: Tuple[float, float],
    axis_u: Tuple[float, float],
    axis_v: Tuple[float, float],
    half_u_m: float,
    half_v_m: float,
) -> float:
    candidates: List[float] = []
    dot_u = abs(float(direction[0]) * float(axis_u[0]) + float(direction[1]) * float(axis_u[1]))
    dot_v = abs(float(direction[0]) * float(axis_v[0]) + float(direction[1]) * float(axis_v[1]))
    if dot_u > 1e-6:
        candidates.append(float(half_u_m) / dot_u)
    if dot_v > 1e-6:
        candidates.append(float(half_v_m) / dot_v)
    return max(min(candidates) if candidates else 0.0, 0.05)


def _junction_rectangle_patch(
    *,
    anchor: Tuple[float, float],
    axis_u: Tuple[float, float],
    axis_v: Tuple[float, float],
    half_u_m: float,
    half_v_m: float,
) -> Any:
    from shapely.geometry import Polygon

    corners = [
        (
            float(anchor[0]) - float(axis_u[0]) * float(half_u_m) - float(axis_v[0]) * float(half_v_m),
            float(anchor[1]) - float(axis_u[1]) * float(half_u_m) - float(axis_v[1]) * float(half_v_m),
        ),
        (
            float(anchor[0]) - float(axis_u[0]) * float(half_u_m) + float(axis_v[0]) * float(half_v_m),
            float(anchor[1]) - float(axis_u[1]) * float(half_u_m) + float(axis_v[1]) * float(half_v_m),
        ),
        (
            float(anchor[0]) + float(axis_u[0]) * float(half_u_m) + float(axis_v[0]) * float(half_v_m),
            float(anchor[1]) + float(axis_u[1]) * float(half_u_m) + float(axis_v[1]) * float(half_v_m),
        ),
        (
            float(anchor[0]) + float(axis_u[0]) * float(half_u_m) - float(axis_v[0]) * float(half_v_m),
            float(anchor[1]) + float(axis_u[1]) * float(half_u_m) - float(axis_v[1]) * float(half_v_m),
        ),
    ]
    return Polygon(corners)


def _junction_carriageway_surface(
    *,
    anchor: Tuple[float, float],
    arms: Sequence[Dict[str, Any]],
    junction_core_rect: Any,
    aoi_polygon: Any | None = None,
) -> Any:
    """Build the drivable junction surface from the center core plus approach throats."""

    patches: List[Any] = [junction_core_rect]
    for arm in arms:
        split_distance_m = float(
            arm.get("split_distance_m", arm.get("core_exit_distance_m", 0.0)) or 0.0
        )
        if split_distance_m <= 1e-6:
            continue
        tangent = tuple(float(value) for value in arm.get("tangent", (0.0, 0.0)))
        normal = tuple(float(value) for value in arm.get("normal", (0.0, 0.0)))
        width_m = max(float(arm.get("carriageway_width_m", 0.0) or 0.0), 1.0)
        center = (
            float(anchor[0]) + float(tangent[0]) * split_distance_m * 0.5,
            float(anchor[1]) + float(tangent[1]) * split_distance_m * 0.5,
        )
        patches.append(
            _rectangle_patch(
                center=center,
                tangent=tangent,
                normal=normal,
                length_m=split_distance_m,
                width_m=width_m,
            )
        )
    return _merge_polygon_geometries(patches, aoi_polygon=aoi_polygon)


def _line_intersection(
    point_a: Tuple[float, float],
    direction_a: Tuple[float, float],
    point_b: Tuple[float, float],
    direction_b: Tuple[float, float],
) -> Tuple[float, float] | None:
    ax, ay = float(point_a[0]), float(point_a[1])
    adx, ady = float(direction_a[0]), float(direction_a[1])
    bx, by = float(point_b[0]), float(point_b[1])
    bdx, bdy = float(direction_b[0]), float(direction_b[1])
    determinant = adx * bdy - ady * bdx
    if abs(determinant) <= 1e-6:
        return None
    delta_x = bx - ax
    delta_y = by - ay
    t_value = (delta_x * bdy - delta_y * bdx) / determinant
    return (ax + adx * t_value, ay + ady * t_value)


def _facing_zone_for_corner(
    arm: Dict[str, Any],
    corner_center: Tuple[float, float],
) -> str:
    boundary_center = tuple(float(value) for value in arm["split_boundary_center"])
    normal = tuple(float(value) for value in arm["normal"])
    vector = (float(corner_center[0]) - boundary_center[0], float(corner_center[1]) - boundary_center[1])
    dot_value = vector[0] * normal[0] + vector[1] * normal[1]
    return "left" if dot_value >= 0.0 else "right"


def _generic_strip_offset_range_for_kind(
    arm: Dict[str, Any],
    kind: str,
    zone: str,
) -> Tuple[float, float, float] | None:
    sign = 1.0 if zone == "left" else -1.0
    half_carriageway_m = max(float(arm["carriageway_width_m"]) * 0.5, 0.0)
    nearroad_buffer = max(float(arm.get("nearroad_buffer_width_m", 0.0) or 0.0), 0.0)
    nearroad_furnishing = max(float(arm.get("nearroad_furnishing_width_m", 0.0) or 0.0), 0.0)
    clear_sidewalk = max(float(arm.get("clear_sidewalk_width_m", 0.0) or 0.0), 0.0)
    farfromroad_buffer = max(float(arm.get("farfromroad_buffer_width_m", 0.0) or 0.0), 0.0)
    frontage_reserve = max(float(arm.get("frontage_reserve_width_m", 0.0) or 0.0), 0.0)
    inner_abs_m = None
    outer_abs_m = None
    if kind == "nearroad_furnishing" and nearroad_furnishing > 0.0:
        inner_abs_m = half_carriageway_m + nearroad_buffer
        outer_abs_m = inner_abs_m + nearroad_furnishing
    elif kind == "clear_sidewalk" and clear_sidewalk > 0.0:
        inner_abs_m = half_carriageway_m + nearroad_buffer + nearroad_furnishing
        outer_abs_m = inner_abs_m + clear_sidewalk
    elif kind == "frontage_reserve" and frontage_reserve > 0.0:
        inner_abs_m = half_carriageway_m + nearroad_buffer + nearroad_furnishing + clear_sidewalk + farfromroad_buffer
        outer_abs_m = inner_abs_m + frontage_reserve
    if inner_abs_m is None or outer_abs_m is None:
        return None
    center_abs_m = (inner_abs_m + outer_abs_m) * 0.5
    return (center_abs_m * sign, inner_abs_m * sign, outer_abs_m * sign)


def _corner_strip_offset_range(
    arm: Dict[str, Any],
    corner_center: Tuple[float, float],
    kind: str,
) -> Tuple[str, float, float, float] | None:
    zone = _facing_zone_for_corner(arm, corner_center)
    for strip in tuple((arm.get("side_strip_layouts", {}) or {}).get(zone, ())):
        if str(strip.get("kind", "") or "") != kind:
            continue
        return (
            zone,
            float(strip.get("center_offset_m", 0.0) or 0.0),
            float(strip.get("inner_offset_m", 0.0) or 0.0),
            float(strip.get("outer_offset_m", 0.0) or 0.0),
        )
    generic = _generic_strip_offset_range_for_kind(arm, kind, zone)
    if generic is None:
        return None
    return (zone, generic[0], generic[1], generic[2])


def _point_on_boundary_with_offset(
    boundary_center: Tuple[float, float],
    normal: Tuple[float, float],
    offset_m: float,
) -> Tuple[float, float]:
    return (
        float(boundary_center[0]) + float(normal[0]) * float(offset_m),
        float(boundary_center[1]) + float(normal[1]) * float(offset_m),
    )


def _connector_join_point(
    point_a: Tuple[float, float],
    tangent_a: Tuple[float, float],
    point_b: Tuple[float, float],
    tangent_b: Tuple[float, float],
) -> Tuple[float, float]:
    join_point = _line_intersection(point_a, tangent_a, point_b, tangent_b)
    if join_point is not None:
        return join_point
    return (
        (float(point_a[0]) + float(point_b[0])) * 0.5,
        (float(point_a[1]) + float(point_b[1])) * 0.5,
    )


def _should_trim_outside_corner(kind: str, sweep_deg: float) -> bool:
    _ = kind
    return abs(float(sweep_deg) - 90.0) <= 30.0


def _corner_connector_patch(
    *,
    corner_center: Tuple[float, float],
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    kind: str,
    junction_core_rect: Any,
    trim_outside_corner: bool = False,
    aoi_polygon: Any | None = None,
) -> Any:
    from shapely.geometry import Polygon

    connector_a = _corner_strip_offset_range(arm, corner_center, kind)
    connector_b = _corner_strip_offset_range(next_arm, corner_center, kind)
    if connector_a is None or connector_b is None:
        return Polygon()
    _, _, inner_offset_a, outer_offset_a = connector_a
    _, _, inner_offset_b, outer_offset_b = connector_b
    boundary_center_a = tuple(float(value) for value in arm["split_boundary_center"])
    boundary_center_b = tuple(float(value) for value in next_arm["split_boundary_center"])
    normal_a = tuple(float(value) for value in arm["normal"])
    normal_b = tuple(float(value) for value in next_arm["normal"])
    tangent_a = tuple(float(value) for value in arm["tangent"])
    tangent_b = tuple(float(value) for value in next_arm["tangent"])
    inner_point_a = _point_on_boundary_with_offset(boundary_center_a, normal_a, inner_offset_a)
    inner_point_b = _point_on_boundary_with_offset(boundary_center_b, normal_b, inner_offset_b)
    outer_point_a = _point_on_boundary_with_offset(boundary_center_a, normal_a, outer_offset_a)
    outer_point_b = _point_on_boundary_with_offset(boundary_center_b, normal_b, outer_offset_b)
    inner_join = _connector_join_point(inner_point_a, tangent_a, inner_point_b, tangent_b)
    outer_join = _connector_join_point(outer_point_a, tangent_a, outer_point_b, tangent_b)
    patch = Polygon(
        [
            outer_point_a,
            outer_join,
            outer_point_b,
            inner_point_b,
            inner_join,
            inner_point_a,
        ]
    )
    if not patch.is_valid:
        patch = patch.buffer(0)
    if trim_outside_corner:
        outer_corner = Polygon([outer_point_a, outer_join, outer_point_b])
        if not outer_corner.is_valid:
            outer_corner = outer_corner.buffer(0)
        if not getattr(outer_corner, "is_empty", True):
            patch = patch.difference(outer_corner)
    patch = patch.difference(junction_core_rect)
    if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
        patch = patch.intersection(aoi_polygon)
    return patch


def _corner_connector_polyline(
    *,
    corner_center: Tuple[float, float],
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    kind: str,
) -> Dict[str, Any] | None:
    connector_a = _corner_strip_offset_range(arm, corner_center, kind)
    connector_b = _corner_strip_offset_range(next_arm, corner_center, kind)
    if connector_a is None or connector_b is None:
        return None
    _zone_a, center_offset_a, inner_offset_a, outer_offset_a = connector_a
    _zone_b, center_offset_b, inner_offset_b, outer_offset_b = connector_b
    boundary_center_a = tuple(float(value) for value in arm["split_boundary_center"])
    boundary_center_b = tuple(float(value) for value in next_arm["split_boundary_center"])
    normal_a = tuple(float(value) for value in arm["normal"])
    normal_b = tuple(float(value) for value in next_arm["normal"])
    tangent_a = tuple(float(value) for value in arm["tangent"])
    tangent_b = tuple(float(value) for value in next_arm["tangent"])
    center_point_a = _point_on_boundary_with_offset(boundary_center_a, normal_a, center_offset_a)
    center_point_b = _point_on_boundary_with_offset(boundary_center_b, normal_b, center_offset_b)
    join_point = _connector_join_point(center_point_a, tangent_a, center_point_b, tangent_b)
    width_m = max(
        (abs(float(outer_offset_a) - float(inner_offset_a)) + abs(float(outer_offset_b) - float(inner_offset_b))) * 0.5,
        0.05,
    )
    return {
        "points_xy": [
            [round(float(center_point_a[0]), 3), round(float(center_point_a[1]), 3)],
            [round(float(join_point[0]), 3), round(float(join_point[1]), 3)],
            [round(float(center_point_b[0]), 3), round(float(center_point_b[1]), 3)],
        ],
        "width_m": round(float(width_m), 3),
    }


def _normalize_vector(vector: Tuple[float, float]) -> Tuple[float, float] | None:
    length = math.hypot(float(vector[0]), float(vector[1]))
    if length <= 1e-6:
        return None
    return (float(vector[0]) / length, float(vector[1]) / length)


def _midpoint(point_a: Tuple[float, float], point_b: Tuple[float, float]) -> Tuple[float, float]:
    return (
        (float(point_a[0]) + float(point_b[0])) * 0.5,
        (float(point_a[1]) + float(point_b[1])) * 0.5,
    )


def _offset_point_along_normal(
    point: Tuple[float, float],
    normal: Tuple[float, float],
    offset_m: float,
) -> Tuple[float, float]:
    return (
        float(point[0]) + float(normal[0]) * float(offset_m),
        float(point[1]) + float(normal[1]) * float(offset_m),
    )


def _limited_miter_join_point(
    point: Tuple[float, float],
    prev_normal: Tuple[float, float],
    next_normal: Tuple[float, float],
    offset_m: float,
    max_join_distance: float,
) -> Tuple[float, float] | None:
    bisector = _normalize_vector(
        (float(prev_normal[0]) + float(next_normal[0]), float(prev_normal[1]) + float(next_normal[1]))
    )
    if bisector is None:
        return None
    projection = float(bisector[0]) * float(prev_normal[0]) + float(bisector[1]) * float(prev_normal[1])
    if abs(projection) <= 1e-6:
        return None
    raw_miter_length = float(offset_m) / projection
    if not math.isfinite(raw_miter_length):
        return None
    clamped_miter_length = max(min(raw_miter_length, float(max_join_distance)), -float(max_join_distance))
    return (
        float(point[0]) + float(bisector[0]) * clamped_miter_length,
        float(point[1]) + float(bisector[1]) * clamped_miter_length,
    )


def _offset_polyline(
    points: Sequence[Tuple[float, float]],
    offset_m: float,
) -> List[Tuple[float, float]]:
    if abs(float(offset_m)) <= 1e-6 or len(points) < 2:
        return [(float(point[0]), float(point[1])) for point in points]
    segments: List[Dict[str, Any]] = []
    for index in range(len(points) - 1):
        start = points[index]
        end = points[index + 1]
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            continue
        tangent = (dx / length, dy / length)
        normal = (dy / length, -dx / length)
        segments.append(
            {
                "start_index": index,
                "end_index": index + 1,
                "tangent": tangent,
                "normal": normal,
                "offset_start": _offset_point_along_normal(start, normal, offset_m),
                "offset_end": _offset_point_along_normal(end, normal, offset_m),
            }
        )
    if not segments:
        return [(float(point[0]), float(point[1])) for point in points]
    previous_segment_indices = [-1] * len(points)
    next_segment_indices = [-1] * len(points)
    previous_cursor = 0
    previous_segment_index = -1
    for point_index in range(len(points)):
        while previous_cursor < len(segments) and int(segments[previous_cursor]["end_index"]) <= point_index:
            previous_segment_index = previous_cursor
            previous_cursor += 1
        previous_segment_indices[point_index] = previous_segment_index
    next_cursor = 0
    for point_index in range(len(points)):
        while next_cursor < len(segments) and int(segments[next_cursor]["start_index"]) < point_index:
            next_cursor += 1
        next_segment_indices[point_index] = next_cursor if next_cursor < len(segments) else -1
    first_segment = segments[0]
    last_segment = segments[-1]
    max_join_distance = abs(float(offset_m)) * 4.0
    offset_points: List[Tuple[float, float]] = []
    for index, point in enumerate(points):
        if index == 0:
            offset_points.append(tuple(first_segment["offset_start"]))
            continue
        if index == len(points) - 1:
            offset_points.append(tuple(last_segment["offset_end"]))
            continue
        previous_index = previous_segment_indices[index]
        next_index = next_segment_indices[index]
        if previous_index < 0 and next_index < 0:
            offset_points.append((float(point[0]), float(point[1])))
            continue
        if previous_index < 0:
            offset_points.append(_offset_point_along_normal(point, tuple(segments[next_index]["normal"]), offset_m))
            continue
        if next_index < 0:
            offset_points.append(_offset_point_along_normal(point, tuple(segments[previous_index]["normal"]), offset_m))
            continue
        previous_segment = segments[previous_index]
        next_segment = segments[next_index]
        if previous_index == next_index:
            offset_points.append(_offset_point_along_normal(point, tuple(previous_segment["normal"]), offset_m))
            continue
        previous_offset_point = _offset_point_along_normal(point, tuple(previous_segment["normal"]), offset_m)
        next_offset_point = _offset_point_along_normal(point, tuple(next_segment["normal"]), offset_m)
        join_point = _line_intersection(
            previous_offset_point,
            tuple(previous_segment["tangent"]),
            next_offset_point,
            tuple(next_segment["tangent"]),
        )
        if join_point is not None and _distance(join_point, point) <= max_join_distance + 1e-6:
            offset_points.append(join_point)
            continue
        limited_join = _limited_miter_join_point(
            point,
            tuple(previous_segment["normal"]),
            tuple(next_segment["normal"]),
            offset_m,
            max_join_distance,
        )
        offset_points.append(limited_join or previous_offset_point)
    return offset_points


def _polyline_from_shared_spine(
    *,
    shared_spine_points: Sequence[Tuple[float, float]],
    start_point: Tuple[float, float],
    end_point: Tuple[float, float],
    offset_delta_m: float,
) -> List[Tuple[float, float]]:
    derived_points = (
        [(float(point[0]), float(point[1])) for point in shared_spine_points]
        if abs(float(offset_delta_m)) <= 1e-6
        else _offset_polyline(shared_spine_points, offset_delta_m)
    )
    if len(derived_points) < 3:
        derived_points = [
            (float(start_point[0]), float(start_point[1])),
            _midpoint(start_point, end_point),
            (float(end_point[0]), float(end_point[1])),
        ]
    derived_points[0] = (float(start_point[0]), float(start_point[1]))
    derived_points[-1] = (float(end_point[0]), float(end_point[1]))
    return derived_points


def _round_xy(point: Tuple[float, float]) -> List[float]:
    return [round(float(point[0]), 3), round(float(point[1]), 3)]


def _vector_dot(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1])


def _vector_subtract(a: Tuple[float, float], b: Tuple[float, float]) -> Tuple[float, float]:
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _perpendicular_directions(direction: Tuple[float, float]) -> List[Tuple[float, float]]:
    return [
        (-float(direction[1]), float(direction[0])),
        (float(direction[1]), -float(direction[0])),
    ]


def _heading_deg_for_vector(direction: Tuple[float, float]) -> float:
    return _normalize_angle_deg(math.degrees(math.atan2(float(direction[1]), float(direction[0]))))


def _arc_sweep_radians(start_angle: float, end_angle: float, clockwise: bool) -> float:
    sweep = float(start_angle) - float(end_angle) if clockwise else float(end_angle) - float(start_angle)
    while sweep <= 0.0:
        sweep += math.tau
    return sweep


def _sample_circular_arc_points(
    *,
    center: Tuple[float, float],
    radius_m: float,
    start_angle: float,
    end_angle: float,
    clockwise: bool,
    start_point: Tuple[float, float],
    end_point: Tuple[float, float],
    target_segment_length_m: float = 0.75,
    min_points: int = 8,
    max_points: int = 24,
) -> List[Tuple[float, float]]:
    sweep = _arc_sweep_radians(start_angle, end_angle, clockwise)
    arc_length = max(float(radius_m) * float(sweep), 0.0)
    point_count = int(math.ceil(arc_length / max(float(target_segment_length_m), 1e-6))) + 1
    point_count = max(int(min_points), min(int(max_points), point_count))
    point_count = max(point_count, 3)
    direction = -1.0 if clockwise else 1.0
    sampled_points: List[Tuple[float, float]] = []
    for index in range(point_count):
        ratio = float(index) / float(point_count - 1)
        angle = float(start_angle) + direction * float(sweep) * ratio
        sampled_points.append(
            (
                float(center[0]) + math.cos(angle) * float(radius_m),
                float(center[1]) + math.sin(angle) * float(radius_m),
            )
        )
    sampled_points[0] = (float(start_point[0]), float(start_point[1]))
    sampled_points[-1] = (float(end_point[0]), float(end_point[1]))
    return sampled_points


def _fallback_corner_kernel(
    *,
    start_point: Tuple[float, float],
    end_point: Tuple[float, float],
    start_tangent: Tuple[float, float],
    end_tangent: Tuple[float, float],
) -> Dict[str, Any]:
    join_point = _connector_join_point(start_point, start_tangent, end_point, end_tangent)
    return {
        "kernel_kind": "polyline_fallback",
        "center": join_point,
        "radius_m": 0.0,
        "start_heading_deg": _heading_deg_for_vector(start_tangent),
        "end_heading_deg": _heading_deg_for_vector(end_tangent),
        "clockwise": None,
        "sampled_points": [
            (float(start_point[0]), float(start_point[1])),
            (float(join_point[0]), float(join_point[1])),
            (float(end_point[0]), float(end_point[1])),
        ],
    }


def _corner_lane_kernel(
    *,
    start_point: Tuple[float, float],
    end_point: Tuple[float, float],
    start_tangent: Tuple[float, float],
    end_tangent: Tuple[float, float],
    corner_center: Tuple[float, float] | None = None,
    min_radius_m: float = 0.25,
    target_segment_length_m: float = 0.75,
) -> Dict[str, Any]:
    normalized_start = _normalize_vector(start_tangent)
    normalized_end = _normalize_vector(end_tangent)
    fallback = _fallback_corner_kernel(
        start_point=start_point,
        end_point=end_point,
        start_tangent=start_tangent,
        end_tangent=end_tangent,
    )
    if normalized_start is None or normalized_end is None:
        return fallback

    best_candidate: Dict[str, Any] | None = None
    for normal_start in _perpendicular_directions(normalized_start):
        for normal_end in _perpendicular_directions(normalized_end):
            center = _line_intersection(start_point, normal_start, end_point, normal_end)
            if center is None:
                continue
            radius_start = _distance(center, start_point)
            radius_end = _distance(center, end_point)
            radius_m = (float(radius_start) + float(radius_end)) * 0.5
            if radius_m < float(min_radius_m):
                continue
            if abs(float(radius_start) - float(radius_end)) > max(0.05, radius_m * 0.05):
                continue

            radial_start = _normalize_vector(_vector_subtract(start_point, center))
            radial_end = _normalize_vector(_vector_subtract(end_point, center))
            if radial_start is None or radial_end is None:
                continue

            start_angle = math.atan2(float(start_point[1]) - float(center[1]), float(start_point[0]) - float(center[0]))
            end_angle = math.atan2(float(end_point[1]) - float(center[1]), float(end_point[0]) - float(center[0]))
            for clockwise, tangent_start, tangent_end in (
                (
                    True,
                    (float(radial_start[1]), float(-radial_start[0])),
                    (float(radial_end[1]), float(-radial_end[0])),
                ),
                (
                    False,
                    (float(-radial_start[1]), float(radial_start[0])),
                    (float(-radial_end[1]), float(radial_end[0])),
                ),
            ):
                align_start = abs(_vector_dot(tangent_start, normalized_start))
                align_end = abs(_vector_dot(tangent_end, normalized_end))
                min_align = min(float(align_start), float(align_end))
                if min_align < 0.5:
                    continue
                sweep = _arc_sweep_radians(start_angle, end_angle, clockwise)
                if sweep <= 1e-6 or sweep > math.pi + math.radians(5.0):
                    continue
                midpoint_angle = float(start_angle) + (-0.5 if clockwise else 0.5) * float(sweep)
                midpoint = (
                    float(center[0]) + math.cos(midpoint_angle) * radius_m,
                    float(center[1]) + math.sin(midpoint_angle) * radius_m,
                )
                score = min_align * 10.0 + float(align_start) + float(align_end) - radius_m * 0.05
                if corner_center is not None:
                    score -= _distance(midpoint, corner_center) * 0.5
                if best_candidate is None or score > float(best_candidate["score"]):
                    best_candidate = {
                        "score": score,
                        "center": center,
                        "radius_m": radius_m,
                        "start_angle": start_angle,
                        "end_angle": end_angle,
                        "start_heading_deg": _heading_deg_for_vector(tangent_start),
                        "end_heading_deg": _heading_deg_for_vector(tangent_end),
                        "clockwise": clockwise,
                    }

    if best_candidate is None:
        return fallback

    sampled_points = _sample_circular_arc_points(
        center=tuple(best_candidate["center"]),
        radius_m=float(best_candidate["radius_m"]),
        start_angle=float(best_candidate["start_angle"]),
        end_angle=float(best_candidate["end_angle"]),
        clockwise=bool(best_candidate["clockwise"]),
        start_point=start_point,
        end_point=end_point,
        target_segment_length_m=target_segment_length_m,
    )
    return {
        "kernel_kind": "circular_arc",
        "center": tuple(best_candidate["center"]),
        "radius_m": float(best_candidate["radius_m"]),
        "start_heading_deg": float(best_candidate["start_heading_deg"]),
        "end_heading_deg": float(best_candidate["end_heading_deg"]),
        "clockwise": bool(best_candidate["clockwise"]),
        "sampled_points": sampled_points,
    }


_CENTER_TURN_LANE_KINDS = frozenset({"drive_lane", "bus_lane", "bike_lane", "parking_lane"})
_SIDE_TURN_LANE_KINDS = ("nearroad_furnishing", "clear_sidewalk", "frontage_reserve")
_SKIP_TURN_LANE_KINDS = frozenset({"median", "grass_belt"})


def _turn_lane_surface_role(strip_kind: str, stack_kind: str) -> str:
    kind = str(strip_kind or "").strip().lower()
    if kind == "bike_lane":
        return "bike_lane"
    if kind == "bus_lane":
        return "bus_lane"
    if kind == "parking_lane":
        return "parking_lane"
    if kind == "drive_lane" or stack_kind == "center":
        return "carriageway"
    if kind == "clear_sidewalk":
        return "sidewalk"
    if kind == "frontage_reserve":
        return "context_ground"
    if "buffer" in kind or kind == "nearroad_furnishing":
        return "furnishing"
    return "sidewalk"


def _signed_offset_edge_distance(edge_offset_m: float, offset_m: float) -> float:
    return abs(float(offset_m) - float(edge_offset_m))


def _strip_range_for_offsets(
    *,
    arm: Dict[str, Any],
    corner_center: Tuple[float, float],
    strip_kind: str,
    strip_id: str,
    direction: str,
    inner_offset_m: float,
    outer_offset_m: float,
    stack_kind: str,
    order_index: int,
) -> Dict[str, Any] | None:
    boundary_center = tuple(float(value) for value in arm.get("split_boundary_center", (0.0, 0.0)))
    normal = tuple(float(value) for value in arm.get("normal", (0.0, 0.0)))
    edge_offset_m = (
        (float(corner_center[0]) - float(boundary_center[0])) * float(normal[0])
        + (float(corner_center[1]) - float(boundary_center[1])) * float(normal[1])
    )
    offsets = [float(inner_offset_m), float(outer_offset_m)]
    offsets.sort(key=lambda value: _signed_offset_edge_distance(edge_offset_m, value))
    near_offset, far_offset = offsets
    width_m = abs(float(far_offset) - float(near_offset))
    if width_m <= 1e-6:
        return None
    return {
        "strip_kind": str(strip_kind or "").strip().lower(),
        "strip_id": str(strip_id or ""),
        "direction": str(direction or "none").strip().lower() or "none",
        "near_offset_m": float(near_offset),
        "far_offset_m": float(far_offset),
        "near_distance_m": _signed_offset_edge_distance(edge_offset_m, near_offset),
        "far_distance_m": _signed_offset_edge_distance(edge_offset_m, far_offset),
        "width_m": float(width_m),
        "stack_kind": str(stack_kind),
        "surface_role": _turn_lane_surface_role(strip_kind, stack_kind),
        "order_index": int(order_index),
    }


def _corner_turn_strip_stack(
    arm: Dict[str, Any],
    corner_center: Tuple[float, float],
    *,
    stack_kind: str,
) -> List[Dict[str, Any]]:
    zone = _facing_zone_for_corner(arm, corner_center)
    records: List[Dict[str, Any]] = []
    if stack_kind == "side":
        for strip in tuple((arm.get("side_strip_layouts", {}) or {}).get(zone, ())):
            kind = str(strip.get("kind", "") or "").strip().lower()
            if not kind or kind in _SKIP_TURN_LANE_KINDS:
                continue
            record = _strip_range_for_offsets(
                arm=arm,
                corner_center=corner_center,
                strip_kind=kind,
                strip_id=str(strip.get("strip_id", "") or ""),
                direction=str(strip.get("direction", "none") or "none"),
                inner_offset_m=float(strip.get("inner_offset_m", 0.0) or 0.0),
                outer_offset_m=float(strip.get("outer_offset_m", 0.0) or 0.0),
                stack_kind=stack_kind,
                order_index=int(strip.get("order_index", len(records)) or 0),
            )
            if record is not None:
                records.append(record)
    else:
        boundary_center = tuple(float(value) for value in arm.get("split_boundary_center", (0.0, 0.0)))
        normal = tuple(float(value) for value in arm.get("normal", (0.0, 0.0)))
        edge_offset_m = (
            (float(corner_center[0]) - float(boundary_center[0])) * float(normal[0])
            + (float(corner_center[1]) - float(boundary_center[1])) * float(normal[1])
        )
        side_sign = 1.0 if edge_offset_m >= 0.0 else -1.0
        for strip in tuple(arm.get("center_strip_layouts", []) or ()):
            kind = str(strip.get("kind", "") or "").strip().lower()
            direction = str(strip.get("direction", "") or "").strip().lower()
            center_offset_m = float(strip.get("center_m", 0.0) or 0.0)
            if kind not in _CENTER_TURN_LANE_KINDS or direction == "none":
                continue
            if center_offset_m == 0.0 or math.copysign(1.0, center_offset_m) != side_sign:
                continue
            record = _strip_range_for_offsets(
                arm=arm,
                corner_center=corner_center,
                strip_kind=kind,
                strip_id=str(strip.get("strip_id", "") or ""),
                direction=direction,
                inner_offset_m=float(strip.get("inner_m", 0.0) or 0.0),
                outer_offset_m=float(strip.get("outer_m", 0.0) or 0.0),
                stack_kind=stack_kind,
                order_index=int(strip.get("order_index", len(records)) or 0),
            )
            if record is not None:
                records.append(record)
    records.sort(key=lambda item: (float(item["near_distance_m"]), float(item["far_distance_m"]), int(item["order_index"])))
    return records


def _short_arc_sweep(start_angle: float, end_angle: float) -> Tuple[float, float]:
    ccw_sweep = float(end_angle) - float(start_angle)
    while ccw_sweep <= 0.0:
        ccw_sweep += math.tau
    clockwise_sweep = float(start_angle) - float(end_angle)
    while clockwise_sweep <= 0.0:
        clockwise_sweep += math.tau
    if ccw_sweep <= clockwise_sweep:
        return ccw_sweep, 1.0
    return clockwise_sweep, -1.0


def _sample_tapered_arc(
    *,
    center: Tuple[float, float],
    start_point: Tuple[float, float],
    end_point: Tuple[float, float],
    target_segment_length_m: float = 0.75,
    min_points: int = 4,
    max_points: int = 24,
) -> List[Tuple[float, float]]:
    start_radius = _distance(center, start_point)
    end_radius = _distance(center, end_point)
    if start_radius <= 1e-6 and end_radius <= 1e-6:
        return [(float(center[0]), float(center[1]))]
    start_angle = math.atan2(float(start_point[1]) - float(center[1]), float(start_point[0]) - float(center[0]))
    end_angle = math.atan2(float(end_point[1]) - float(center[1]), float(end_point[0]) - float(center[0]))
    sweep, direction = _short_arc_sweep(start_angle, end_angle)
    arc_length = max((float(start_radius) + float(end_radius)) * 0.5 * float(sweep), 0.0)
    point_count = int(math.ceil(arc_length / max(float(target_segment_length_m), 1e-6))) + 1
    point_count = max(int(min_points), min(int(max_points), point_count))
    point_count = max(point_count, 2)
    points: List[Tuple[float, float]] = []
    for index in range(point_count):
        ratio = float(index) / float(point_count - 1)
        radius = float(start_radius) + (float(end_radius) - float(start_radius)) * ratio
        angle = float(start_angle) + float(direction) * float(sweep) * ratio
        points.append((float(center[0]) + math.cos(angle) * radius, float(center[1]) + math.sin(angle) * radius))
    points[0] = (float(start_point[0]), float(start_point[1]))
    points[-1] = (float(end_point[0]), float(end_point[1]))
    return points


def _corner_fillet_turn_radius(
    *,
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    target_segment_length_m: float,
    strip_width_m: float = 0.0,
) -> float:
    split_a = float(arm.get("split_distance_m", arm.get("core_exit_distance_m", 0.0)) or 0.0)
    split_b = float(next_arm.get("split_distance_m", next_arm.get("core_exit_distance_m", 0.0)) or 0.0)
    max_radius_m = max(min(split_a, split_b) * 0.62, 1.0)
    preferred_radius_m = max(2.8, float(strip_width_m) * 1.35, float(target_segment_length_m) * 3.0)
    return max(min(preferred_radius_m, max_radius_m), 0.5)


def _corner_quadrant_fillet_turn_radius(
    *,
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    corner_center: Tuple[float, float],
    target_segment_length_m: float,
) -> float:
    widths: List[float] = []
    for strip_kind in _SIDE_TURN_LANE_KINDS:
        offsets_a = _corner_strip_offset_range(arm, corner_center, strip_kind)
        offsets_b = _corner_strip_offset_range(next_arm, corner_center, strip_kind)
        if offsets_a is None or offsets_b is None:
            continue
        widths.append(
            max(
                (
                    abs(float(offsets_a[3]) - float(offsets_a[2]))
                    + abs(float(offsets_b[3]) - float(offsets_b[2]))
                ) * 0.5,
                0.0,
            )
        )
    return _corner_fillet_turn_radius(
        arm=arm,
        next_arm=next_arm,
        target_segment_length_m=target_segment_length_m,
        strip_width_m=max(widths, default=0.0),
    )


def _corner_edge_join_point(
    *,
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    offset_a_m: float,
    offset_b_m: float,
) -> Tuple[float, float] | None:
    boundary_a = tuple(float(value) for value in arm["split_boundary_center"])
    normal_a = tuple(float(value) for value in arm["normal"])
    tangent_a = tuple(float(value) for value in arm["tangent"])
    boundary_b = tuple(float(value) for value in next_arm["split_boundary_center"])
    normal_b = tuple(float(value) for value in next_arm["normal"])
    tangent_b = tuple(float(value) for value in next_arm["tangent"])
    point_a = _point_on_boundary_with_offset(boundary_a, normal_a, float(offset_a_m))
    point_b = _point_on_boundary_with_offset(boundary_b, normal_b, float(offset_b_m))
    return _line_intersection(point_a, tangent_a, point_b, tangent_b)


def _corner_fillet_arc_kernel(
    *,
    corner_point: Tuple[float, float],
    tangent_a: Tuple[float, float],
    tangent_b: Tuple[float, float],
    radius_m: float,
    target_segment_length_m: float,
) -> Dict[str, Any] | None:
    ray_a = _normalize_vector((float(tangent_a[0]), float(tangent_a[1])))
    ray_b = _normalize_vector((float(tangent_b[0]), float(tangent_b[1])))
    if ray_a is None or ray_b is None:
        return None
    dot = max(min(float(ray_a[0]) * float(ray_b[0]) + float(ray_a[1]) * float(ray_b[1]), 1.0), -1.0)
    theta = math.acos(dot)
    if theta <= math.radians(5.0) or theta >= math.radians(175.0):
        return None
    bisector = _normalize_vector((float(ray_a[0]) + float(ray_b[0]), float(ray_a[1]) + float(ray_b[1])))
    if bisector is None:
        return None
    radius = max(float(radius_m), 0.1)
    tangent_distance = radius / max(math.tan(theta * 0.5), 1e-6)
    center_distance = radius / max(math.sin(theta * 0.5), 1e-6)
    start_point = (
        float(corner_point[0]) + float(ray_a[0]) * tangent_distance,
        float(corner_point[1]) + float(ray_a[1]) * tangent_distance,
    )
    end_point = (
        float(corner_point[0]) + float(ray_b[0]) * tangent_distance,
        float(corner_point[1]) + float(ray_b[1]) * tangent_distance,
    )
    center = (
        float(corner_point[0]) + float(bisector[0]) * center_distance,
        float(corner_point[1]) + float(bisector[1]) * center_distance,
    )
    start_angle = math.atan2(float(start_point[1]) - float(center[1]), float(start_point[0]) - float(center[0]))
    end_angle = math.atan2(float(end_point[1]) - float(center[1]), float(end_point[0]) - float(center[0]))
    _sweep, direction = _short_arc_sweep(start_angle, end_angle)
    return {
        "kernel_kind": "circular_arc",
        "center": center,
        "radius_m": radius,
        "start_point": start_point,
        "end_point": end_point,
        "clockwise": direction < 0.0,
        "sampled_points": _sample_tapered_arc(
            center=center,
            start_point=start_point,
            end_point=end_point,
            target_segment_length_m=target_segment_length_m,
        ),
    }


def _corner_offset_fillet_kernel(
    *,
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    offset_a_m: float,
    offset_b_m: float,
    radius_m: float,
    target_segment_length_m: float,
) -> Dict[str, Any] | None:
    corner_point = _corner_edge_join_point(
        arm=arm,
        next_arm=next_arm,
        offset_a_m=float(offset_a_m),
        offset_b_m=float(offset_b_m),
    )
    if corner_point is None:
        return None
    return _corner_fillet_arc_kernel(
        corner_point=corner_point,
        tangent_a=tuple(float(value) for value in arm["tangent"]),
        tangent_b=tuple(float(value) for value in next_arm["tangent"]),
        radius_m=radius_m,
        target_segment_length_m=target_segment_length_m,
    )


def _dedupe_ring_points(points: Sequence[Tuple[float, float]], tolerance_m: float = 1e-6) -> List[Tuple[float, float]]:
    deduped: List[Tuple[float, float]] = []
    for point in points:
        candidate = (float(point[0]), float(point[1]))
        if deduped and _distance(deduped[-1], candidate) <= float(tolerance_m):
            continue
        deduped.append(candidate)
    if len(deduped) > 1 and _distance(deduped[0], deduped[-1]) <= float(tolerance_m):
        deduped.pop()
    return deduped


def _corner_strip_ribbon_patch(
    *,
    corner_center: Tuple[float, float],
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    strip_a: Dict[str, Any],
    strip_b: Dict[str, Any],
    turn_radius_m: float,
    target_segment_length_m: float,
) -> Any:
    from shapely.geometry import Polygon

    _ = corner_center
    near_kernel = _corner_offset_fillet_kernel(
        arm=arm,
        next_arm=next_arm,
        offset_a_m=float(strip_a["near_offset_m"]),
        offset_b_m=float(strip_b["near_offset_m"]),
        radius_m=turn_radius_m,
        target_segment_length_m=target_segment_length_m,
    )
    far_kernel = _corner_offset_fillet_kernel(
        arm=arm,
        next_arm=next_arm,
        offset_a_m=float(strip_a["far_offset_m"]),
        offset_b_m=float(strip_b["far_offset_m"]),
        radius_m=turn_radius_m,
        target_segment_length_m=target_segment_length_m,
    )
    if near_kernel is None or far_kernel is None:
        return Polygon()
    near_arc = list(near_kernel["sampled_points"])
    far_arc = list(far_kernel["sampled_points"])
    ring = _dedupe_ring_points([*near_arc, *reversed(far_arc)])
    if len(ring) < 3:
        return Polygon()
    patch = Polygon([_round_xy(point) for point in ring])
    if not patch.is_valid:
        patch = patch.buffer(0)
    return patch


def _build_cross_corner_strip_patches(
    junction_id: str,
    quadrant_id: str,
    arm_index: int,
    arm: Dict[str, Any],
    next_arm: Dict[str, Any],
    corner_center: Tuple[float, float],
    *,
    target_segment_length_m: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    patches: List[Dict[str, Any]] = []
    debug_records: List[Dict[str, Any]] = []
    stack_a = {
        str(strip["strip_kind"]): strip
        for strip in _corner_turn_strip_stack(arm, corner_center, stack_kind="side")
        if str(strip.get("strip_kind", "") or "") in _SIDE_TURN_LANE_KINDS
    }
    stack_b = {
        str(strip["strip_kind"]): strip
        for strip in _corner_turn_strip_stack(next_arm, corner_center, stack_kind="side")
        if str(strip.get("strip_kind", "") or "") in _SIDE_TURN_LANE_KINDS
    }
    turn_radius_m = _corner_quadrant_fillet_turn_radius(
        arm=arm,
        next_arm=next_arm,
        corner_center=corner_center,
        target_segment_length_m=target_segment_length_m,
    )
    for strip_kind in _SIDE_TURN_LANE_KINDS:
        strip_a = stack_a.get(strip_kind)
        strip_b = stack_b.get(strip_kind)
        if strip_a is None or strip_b is None:
            debug_records.append(
                {
                    "quadrant_id": quadrant_id,
                    "strip_kind": strip_kind,
                    "reason": "missing_side_strip",
                    "has_arm_a": strip_a is not None,
                    "has_arm_b": strip_b is not None,
                }
            )
            continue
        patch = _corner_strip_ribbon_patch(
            corner_center=corner_center,
            arm=arm,
            next_arm=next_arm,
            strip_a=strip_a,
            strip_b=strip_b,
            turn_radius_m=turn_radius_m,
            target_segment_length_m=target_segment_length_m,
        )
        if getattr(patch, "is_empty", True) or float(getattr(patch, "area", 0.0) or 0.0) <= 1e-6:
            debug_records.append(
                {
                    "quadrant_id": quadrant_id,
                    "strip_kind": strip_kind,
                    "reason": "empty_or_invalid_corner_strip",
                }
            )
            continue
        patch_prefix = {
            "nearroad_furnishing": "nearroad",
            "clear_sidewalk": "sidewalk",
            "frontage_reserve": "frontage",
        }.get(strip_kind, strip_kind)
        patches.append(
            {
                "patch_id": f"{junction_id}_{patch_prefix}_{arm_index:02d}",
                "quadrant_id": quadrant_id,
                "strip_kind": strip_kind,
                "strip_id_a": str(strip_a.get("strip_id", "") or ""),
                "strip_id_b": str(strip_b.get("strip_id", "") or ""),
                "surface_role": str(strip_a.get("surface_role", "sidewalk") or "sidewalk"),
                "geometry": patch,
            }
        )
    return patches, debug_records


def _build_cross_corner_kernel_geometries(
    junction_id: str,
    ordered_arms: Sequence[Dict[str, Any]],
    *,
    target_segment_length_m: float = 0.75,
) -> Dict[str, List[Dict[str, Any]]]:
    quadrant_corner_kernels: List[Dict[str, Any]] = []
    sidewalk_corner_polylines: List[Dict[str, Any]] = []
    nearroad_corner_polylines: List[Dict[str, Any]] = []
    frontage_corner_polylines: List[Dict[str, Any]] = []
    sidewalk_corner_patches: List[Dict[str, Any]] = []
    nearroad_corner_patches: List[Dict[str, Any]] = []
    frontage_corner_patches: List[Dict[str, Any]] = []
    turn_lane_patches: List[Dict[str, Any]] = []
    turn_lane_debug_records: List[Dict[str, Any]] = []
    arm_skeletons_by_index: Dict[int, Dict[str, Any]] = {}

    for arm_index, arm in enumerate(ordered_arms):
        split_center = tuple(float(value) for value in arm.get("split_boundary_center", (0.0, 0.0)))
        split_start = tuple(float(value) for value in arm.get("split_boundary_start", split_center))
        split_end = tuple(float(value) for value in arm.get("split_boundary_end", split_center))
        arm_skeletons_by_index[arm_index] = {
            "arm_skeleton_id": f"{junction_id}_arm_{arm_index:02d}",
            "arm_index": int(arm_index),
            "road_id": int(arm.get("road_id", 0) or 0),
            "centerline_id": str(arm.get("centerline_id", "") or ""),
            "angle_deg": round(float(arm.get("angle_deg", 0.0) or 0.0), 3),
            "tangent_xy": _round_xy(tuple(float(value) for value in arm.get("tangent", (0.0, 0.0)))),
            "normal_xy": _round_xy(tuple(float(value) for value in arm.get("normal", (0.0, 0.0)))),
            "split_center_xy": _round_xy(split_center),
            "split_start_xy": _round_xy(split_start),
            "split_end_xy": _round_xy(split_end),
            "split_distance_m": round(float(arm.get("split_distance_m", 0.0) or 0.0), 3),
            "core_exit_distance_m": round(float(arm.get("core_exit_distance_m", 0.0) or 0.0), 3),
            "corner_facing_sides": [],
        }

    for arm_index, arm in enumerate(ordered_arms):
        next_index = (arm_index + 1) % len(ordered_arms)
        next_arm = ordered_arms[next_index]
        start_angle = float(arm["angle_deg"])
        end_angle = float(next_arm["angle_deg"])
        sweep = end_angle - start_angle
        if sweep <= 0.0:
            sweep += 360.0
        if sweep <= 5.0 or sweep >= 175.0:
            turn_lane_debug_records.append(
                {
                    "quadrant_id": f"{junction_id}_quadrant_{arm_index:02d}",
                    "reason": "unsupported_quadrant_sweep",
                    "sweep_deg": round(float(sweep), 3),
                }
            )
            continue
        corner_center = _line_intersection(
            tuple(float(value) for value in arm["split_boundary_center"]),
            tuple(float(value) for value in arm["normal"]),
            tuple(float(value) for value in next_arm["split_boundary_center"]),
            tuple(float(value) for value in next_arm["normal"]),
        )
        if corner_center is None:
            turn_lane_debug_records.append(
                {
                    "quadrant_id": f"{junction_id}_quadrant_{arm_index:02d}",
                    "reason": "parallel_split_normals",
                }
            )
            continue

        quadrant_id = f"{junction_id}_quadrant_{arm_index:02d}"
        kernel_id = f"{quadrant_id}_kernel"
        if arm_index in arm_skeletons_by_index:
            arm_skeletons_by_index[arm_index]["corner_facing_sides"].append(
                {
                    "quadrant_id": quadrant_id,
                    "role": "start_arm",
                    "side": _facing_zone_for_corner(arm, corner_center),
                }
            )
        if next_index in arm_skeletons_by_index:
            arm_skeletons_by_index[next_index]["corner_facing_sides"].append(
                {
                    "quadrant_id": quadrant_id,
                    "role": "end_arm",
                    "side": _facing_zone_for_corner(next_arm, corner_center),
                }
            )
        quadrant_corner_patches, quadrant_turn_debug = _build_cross_corner_strip_patches(
            junction_id,
            quadrant_id,
            arm_index,
            arm,
            next_arm,
            corner_center,
            target_segment_length_m=target_segment_length_m,
        )
        turn_lane_debug_records.extend(quadrant_turn_debug)
        for patch in quadrant_corner_patches:
            strip_kind = str(patch.get("strip_kind", "") or "")
            if strip_kind == "nearroad_furnishing":
                nearroad_corner_patches.append(patch)
            elif strip_kind == "clear_sidewalk":
                sidewalk_corner_patches.append(patch)
            elif strip_kind == "frontage_reserve":
                frontage_corner_patches.append(patch)
        quadrant_turn_radius_m = _corner_quadrant_fillet_turn_radius(
            arm=arm,
            next_arm=next_arm,
            corner_center=corner_center,
            target_segment_length_m=target_segment_length_m,
        )

        canonical_kernel = None
        for canonical_kind in ("clear_sidewalk", "nearroad_furnishing", "frontage_reserve"):
            canonical_offsets_a = _corner_strip_offset_range(arm, corner_center, canonical_kind)
            canonical_offsets_b = _corner_strip_offset_range(next_arm, corner_center, canonical_kind)
            if canonical_offsets_a is None or canonical_offsets_b is None:
                continue
            canonical_kernel = _corner_offset_fillet_kernel(
                arm=arm,
                next_arm=next_arm,
                offset_a_m=float(canonical_offsets_a[1]),
                offset_b_m=float(canonical_offsets_b[1]),
                radius_m=quadrant_turn_radius_m,
                target_segment_length_m=target_segment_length_m,
            )
            if canonical_kernel is None:
                continue
            break
        if canonical_kernel is None:
            continue

        quadrant_corner_kernels.append(
            {
                "kernel_id": kernel_id,
                "quadrant_id": quadrant_id,
                "road_a_id": int(arm["road_id"]),
                "road_b_id": int(next_arm["road_id"]),
                "centerline_a_id": str(arm.get("centerline_id", "") or ""),
                "centerline_b_id": str(next_arm.get("centerline_id", "") or ""),
                "kernel_kind": str(canonical_kernel["kernel_kind"]),
                "center_xy": _round_xy(tuple(canonical_kernel["center"])),
                "radius_m": round(float(canonical_kernel["radius_m"]), 3),
                "start_xy": _round_xy(tuple(canonical_kernel["start_point"])),
                "end_xy": _round_xy(tuple(canonical_kernel["end_point"])),
                "start_heading_deg": round(_heading_deg_for_vector(tuple(float(value) for value in arm["tangent"])), 3),
                "end_heading_deg": round(_heading_deg_for_vector(tuple(float(value) for value in next_arm["tangent"])), 3),
                "clockwise": canonical_kernel["clockwise"],
                "sampled_points_xy": [
                    _round_xy(point)
                    for point in tuple(canonical_kernel["sampled_points"])
                ],
            }
        )

        for kind, prefix, bucket in (
            ("nearroad_furnishing", "nearroad", nearroad_corner_polylines),
            ("clear_sidewalk", "sidewalk", sidewalk_corner_polylines),
            ("frontage_reserve", "frontage", frontage_corner_polylines),
        ):
            offsets_a = _corner_strip_offset_range(arm, corner_center, kind)
            offsets_b = _corner_strip_offset_range(next_arm, corner_center, kind)
            if offsets_a is None or offsets_b is None:
                continue
            width_m = max(
                (
                    abs(float(offsets_a[3]) - float(offsets_a[2]))
                    + abs(float(offsets_b[3]) - float(offsets_b[2]))
                ) * 0.5,
                0.05,
            )
            polyline_kernel = _corner_offset_fillet_kernel(
                arm=arm,
                next_arm=next_arm,
                offset_a_m=float(offsets_a[1]),
                offset_b_m=float(offsets_b[1]),
                radius_m=quadrant_turn_radius_m,
                target_segment_length_m=target_segment_length_m,
            )
            if polyline_kernel is None:
                continue
            bucket.append(
                {
                    "polyline_id": f"{junction_id}_{prefix}_{arm_index:02d}",
                    "quadrant_id": quadrant_id,
                    "kernel_id": kernel_id,
                    "points_xy": [
                        _round_xy(point)
                        for point in tuple(polyline_kernel["sampled_points"])
                    ],
                    "width_m": round(float(width_m), 3),
                }
            )

    return {
        "quadrant_corner_kernels": quadrant_corner_kernels,
        "sidewalk_corner_polylines": sidewalk_corner_polylines,
        "nearroad_corner_polylines": nearroad_corner_polylines,
        "frontage_corner_polylines": frontage_corner_polylines,
        "sidewalk_corner_patches": sidewalk_corner_patches,
        "nearroad_corner_patches": nearroad_corner_patches,
        "frontage_corner_patches": frontage_corner_patches,
        "turn_lane_patches": turn_lane_patches,
        "turn_lane_debug": turn_lane_debug_records,
        "arm_skeletons": [arm_skeletons_by_index[index] for index in sorted(arm_skeletons_by_index)],
    }


def _build_explicit_graph_junction_geometries(
    roads: Sequence[Any],
    *,
    road_segment_graph: Any,
    aoi_polygon: Any | None = None,
) -> List[Dict[str, Any]]:
    from shapely.geometry import LineString

    road_profiles = _road_profile_widths_from_graph(road_segment_graph)
    roads_by_id = {
        int(getattr(road, "osm_id", 0) or 0): road
        for road in roads
        if int(getattr(road, "osm_id", 0) or 0) > 0
    }
    explicit_junctions = [
        item
        for item in (getattr(road_segment_graph, "junctions", ()) or ())
        if str(getattr(item, "source_mode", "") or "") == "explicit"
        and tuple(getattr(item, "connected_road_ids", ()) or ())
    ]
    junctions: List[Dict[str, Any]] = []
    for junction in explicit_junctions:
        anchor = tuple(float(value) for value in getattr(junction, "anchor_xy", (0.0, 0.0))[:2])
        arms: List[Dict[str, Any]] = []
        seen_road_ids: set[int] = set()
        for road_id, centerline_id in zip(
            tuple(getattr(junction, "connected_road_ids", ()) or ()),
            tuple(getattr(junction, "connected_centerline_ids", ()) or ()),
        ):
            road_id = int(road_id)
            if road_id <= 0 or road_id in seen_road_ids:
                continue
            road = roads_by_id.get(road_id)
            if road is None:
                continue
            points = _dedupe_adjacent_points(getattr(road, "coords", ()) or ())
            if len(points) < 2:
                continue
            if _distance(anchor, points[0]) <= 0.5:
                neighbor = points[1]
            elif _distance(anchor, points[-1]) <= 0.5:
                neighbor = points[-2]
            else:
                continue
            length_m = math.hypot(float(neighbor[0]) - anchor[0], float(neighbor[1]) - anchor[1])
            if length_m <= 1e-6:
                continue
            tangent = (
                (float(neighbor[0]) - anchor[0]) / length_m,
                (float(neighbor[1]) - anchor[1]) / length_m,
            )
            profile = road_profiles.get(road_id, {})
            arms.append(
                {
                    "road_id": road_id,
                    "centerline_id": str(centerline_id),
                    "angle_deg": _angle_deg(anchor, neighbor),
                    "tangent": tangent,
                    "normal": (float(tangent[1]), float(-tangent[0])),
                    "carriageway_width_m": max(float(profile.get("carriageway_width_m", getattr(road, "width_m", 8.0) or 8.0)), 1.0),
                    "nearroad_buffer_width_m": float(profile.get("nearroad_buffer_width_m", 0.0) or 0.0),
                    "nearroad_furnishing_width_m": float(profile.get("nearroad_furnishing_width_m", 0.0) or 0.0),
                    "clear_sidewalk_width_m": float(profile.get("clear_sidewalk_width_m", 0.0) or 0.0),
                    "farfromroad_buffer_width_m": float(profile.get("farfromroad_buffer_width_m", 0.0) or 0.0),
                    "frontage_reserve_width_m": float(profile.get("frontage_reserve_width_m", 0.0) or 0.0),
                    "side_strip_layouts": dict(profile.get("side_strip_layouts", {}) or {}),
                    "center_strip_layouts": list(profile.get("center_strip_layouts", []) or ()),
                }
            )
            seen_road_ids.add(road_id)
        if len(arms) < 3:
            continue
        kind = str(getattr(junction, "kind", "") or _classify_junction_kind([float(item["angle_deg"]) for item in arms]))
        if kind not in {"t_junction", "cross_junction"}:
            continue

        axis_u = _principal_junction_axis(arms)
        axis_u_length = max(math.hypot(float(axis_u[0]), float(axis_u[1])), 1e-6)
        axis_u = (float(axis_u[0]) / axis_u_length, float(axis_u[1]) / axis_u_length)
        axis_v = (float(-axis_u[1]), float(axis_u[0]))
        axis_u_angle = _angle_deg((0.0, 0.0), axis_u)
        arms_on_u: List[Dict[str, Any]] = []
        arms_on_v: List[Dict[str, Any]] = []
        for arm in arms:
            along_u = _axis_distance_deg(float(arm["angle_deg"]), axis_u_angle)
            along_v = _axis_distance_deg(float(arm["angle_deg"]), axis_u_angle + 90.0)
            if along_v + 1e-6 < along_u:
                arms_on_v.append(arm)
            else:
                arms_on_u.append(arm)

        def _max_half_width(items: Sequence[Dict[str, Any]], fallback: Sequence[Dict[str, Any]]) -> float:
            values = [float(item["carriageway_width_m"]) * 0.5 for item in items if float(item["carriageway_width_m"]) > 0.0]
            if not values:
                values = [float(item["carriageway_width_m"]) * 0.5 for item in fallback if float(item["carriageway_width_m"]) > 0.0]
            return max(values or [2.0])

        half_u_m = _max_half_width(arms_on_v, arms)
        half_v_m = _max_half_width(arms_on_u, arms)
        local_crosswalk_depth_m = max(float(getattr(junction, "crosswalk_depth_m", 3.0) or 3.0), 0.5)
        junction_core_rect = _junction_rectangle_patch(
            anchor=anchor,
            axis_u=axis_u,
            axis_v=axis_v,
            half_u_m=half_u_m,
            half_v_m=half_v_m,
        )
        if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
            junction_core_rect = junction_core_rect.intersection(aoi_polygon)

        crosswalk_patches = []
        approach_boundaries = []
        skeleton_foot_points = []
        sub_lane_control_points = []
        sidewalk_trim_polygons = []
        for arm_index, arm in enumerate(arms):
            half_width = float(arm["carriageway_width_m"]) * 0.5
            core_exit_distance_m = _ray_rectangle_exit_distance(
                tuple(arm["tangent"]),
                axis_u,
                axis_v,
                half_u_m,
                half_v_m,
            )
            split_distance_m = float(core_exit_distance_m) + float(local_crosswalk_depth_m)
            boundary_center = (
                anchor[0] + float(arm["tangent"][0]) * split_distance_m,
                anchor[1] + float(arm["tangent"][1]) * split_distance_m,
            )
            boundary_start = (
                boundary_center[0] - float(arm["normal"][0]) * half_width,
                boundary_center[1] - float(arm["normal"][1]) * half_width,
            )
            boundary_end = (
                boundary_center[0] + float(arm["normal"][0]) * half_width,
                boundary_center[1] + float(arm["normal"][1]) * half_width,
            )
            approach_boundaries.append(
                {
                    "boundary_id": f"{junction.junction_id}_approach_{arm_index:02d}",
                    "road_id": int(arm["road_id"]),
                    "centerline_id": str(arm["centerline_id"]),
                    "center_xy": [round(boundary_center[0], 3), round(boundary_center[1], 3)],
                    "start_xy": [round(boundary_start[0], 3), round(boundary_start[1], 3)],
                    "end_xy": [round(boundary_end[0], 3), round(boundary_end[1], 3)],
                    "exit_distance_m": round(float(split_distance_m), 3),
                }
            )
            skeleton_foot_points.append(
                {
                    "foot_id": f"{junction.junction_id}_foot_{arm_index:02d}",
                    "road_id": int(arm["road_id"]),
                    "centerline_id": str(arm["centerline_id"]),
                    "xy": [round(boundary_center[0], 3), round(boundary_center[1], 3)],
                }
            )
            for zone in ("left", "right"):
                for strip in tuple((arm.get("side_strip_layouts", {}) or {}).get(zone, ())):
                    strip_kind = str(strip.get("kind", "") or "")
                    if strip_kind not in {"clear_sidewalk", "nearroad_furnishing", "frontage_reserve"}:
                        continue
                    for point_kind, offset_key in (
                        ("center_control_point", "center_offset_m"),
                        ("inner_edge_control_point", "inner_offset_m"),
                        ("outer_edge_control_point", "outer_offset_m"),
                    ):
                        offset_value = float(strip.get(offset_key, 0.0) or 0.0)
                        sub_lane_control_points.append(
                            {
                                "control_id": f"{junction.junction_id}_{arm_index:02d}_{strip_kind}_{zone}_{point_kind}",
                                "road_id": int(arm["road_id"]),
                                "centerline_id": str(arm["centerline_id"]),
                                "strip_kind": strip_kind,
                                "strip_zone": zone,
                                "point_kind": point_kind,
                                "xy": [
                                    round(boundary_center[0] + float(arm["normal"][0]) * offset_value, 3),
                                    round(boundary_center[1] + float(arm["normal"][1]) * offset_value, 3),
                                ],
                            }
                        )

            center = (
                anchor[0] + float(arm["tangent"][0]) * (float(core_exit_distance_m) + float(local_crosswalk_depth_m) * 0.5),
                anchor[1] + float(arm["tangent"][1]) * (float(core_exit_distance_m) + float(local_crosswalk_depth_m) * 0.5),
            )
            patch = _rectangle_patch(
                center=center,
                tangent=tuple(arm["tangent"]),
                normal=tuple(arm["normal"]),
                length_m=float(local_crosswalk_depth_m),
                width_m=float(arm["carriageway_width_m"]),
            )
            if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
                patch = patch.intersection(aoi_polygon)
            crosswalk_patches.append(
                {
                    "patch_id": f"{junction.junction_id}_crosswalk_{arm_index:02d}",
                    "road_id": int(arm["road_id"]),
                    "centerline_id": str(arm["centerline_id"]),
                    "horizontal_axes": [
                        [float(arm["tangent"][0]), float(arm["tangent"][1])],
                        [float(arm["normal"][0]), float(arm["normal"][1])],
                    ],
                    "geometry": patch,
                }
            )
            side_total_width_m = (
                float(arm["nearroad_buffer_width_m"])
                + float(arm["nearroad_furnishing_width_m"])
                + float(arm["clear_sidewalk_width_m"])
                + float(arm["farfromroad_buffer_width_m"])
                + float(arm["frontage_reserve_width_m"])
            )
            trim_half_width = max(half_width + side_total_width_m, half_width)
            trim_extent_m = max(float(split_distance_m), float(local_crosswalk_depth_m))
            trim_polygon = LineString(
                [
                    anchor,
                    (
                        anchor[0] + float(arm["tangent"][0]) * trim_extent_m,
                        anchor[1] + float(arm["tangent"][1]) * trim_extent_m,
                    ),
                ]
            ).buffer(trim_half_width, cap_style="flat")
            sidewalk_trim_polygons.append(trim_polygon)
            arm["core_exit_distance_m"] = float(core_exit_distance_m)
            arm["split_distance_m"] = float(split_distance_m)
            arm["split_boundary_center"] = boundary_center
            arm["split_boundary_start"] = boundary_start
            arm["split_boundary_end"] = boundary_end

        carriageway_core = _junction_carriageway_surface(
            anchor=anchor,
            arms=arms,
            junction_core_rect=junction_core_rect,
            aoi_polygon=aoi_polygon,
        )
        sidewalk_corner_patches = []
        nearroad_corner_patches = []
        frontage_corner_patches = []
        quadrant_corner_kernels = []
        sidewalk_corner_polylines = []
        nearroad_corner_polylines = []
        frontage_corner_polylines = []
        turn_lane_patches = []
        turn_lane_debug = []
        arm_skeletons = []
        ordered_arms = sorted(arms, key=lambda item: float(item["angle_deg"]))
        if kind == "cross_junction":
            cross_corner_data = _build_cross_corner_kernel_geometries(
                str(junction.junction_id),
                ordered_arms,
            )
            quadrant_corner_kernels = list(cross_corner_data["quadrant_corner_kernels"])
            sidewalk_corner_polylines = list(cross_corner_data["sidewalk_corner_polylines"])
            nearroad_corner_polylines = list(cross_corner_data["nearroad_corner_polylines"])
            frontage_corner_polylines = list(cross_corner_data["frontage_corner_polylines"])
            sidewalk_corner_patches = list(cross_corner_data["sidewalk_corner_patches"])
            nearroad_corner_patches = list(cross_corner_data["nearroad_corner_patches"])
            frontage_corner_patches = list(cross_corner_data["frontage_corner_patches"])
            turn_lane_patches = list(cross_corner_data.get("turn_lane_patches", []) or ())
            turn_lane_debug = list(cross_corner_data.get("turn_lane_debug", []) or ())
            arm_skeletons = list(cross_corner_data.get("arm_skeletons", []) or ())
        else:
            for arm_index, arm in enumerate(ordered_arms):
                next_arm = ordered_arms[(arm_index + 1) % len(ordered_arms)]
                start_angle = float(arm["angle_deg"])
                end_angle = float(next_arm["angle_deg"])
                sweep = end_angle - start_angle
                if sweep <= 0.0:
                    sweep += 360.0
                if sweep <= 5.0 or sweep >= 175.0:
                    continue
                trim_outside_corner = _should_trim_outside_corner(kind, sweep)
                corner_center = _line_intersection(
                    tuple(float(value) for value in arm["split_boundary_center"]),
                    tuple(float(value) for value in arm["normal"]),
                    tuple(float(value) for value in next_arm["split_boundary_center"]),
                    tuple(float(value) for value in next_arm["normal"]),
                )
                if corner_center is None:
                    continue
                nearroad_patch = _corner_connector_patch(
                    corner_center=corner_center,
                    arm=arm,
                    next_arm=next_arm,
                    kind="nearroad_furnishing",
                    junction_core_rect=junction_core_rect,
                    trim_outside_corner=trim_outside_corner,
                    aoi_polygon=aoi_polygon,
                )
                if not getattr(nearroad_patch, "is_empty", True):
                    nearroad_corner_patches.append(
                        {
                            "patch_id": f"{junction.junction_id}_nearroad_{arm_index:02d}",
                            "geometry": nearroad_patch,
                        }
                    )
                sidewalk_patch = _corner_connector_patch(
                    corner_center=corner_center,
                    arm=arm,
                    next_arm=next_arm,
                    kind="clear_sidewalk",
                    junction_core_rect=junction_core_rect,
                    trim_outside_corner=trim_outside_corner,
                    aoi_polygon=aoi_polygon,
                )
                if not getattr(sidewalk_patch, "is_empty", True):
                    sidewalk_corner_patches.append(
                        {
                            "patch_id": f"{junction.junction_id}_sidewalk_{arm_index:02d}",
                            "geometry": sidewalk_patch,
                        }
                    )
                frontage_patch = _corner_connector_patch(
                    corner_center=corner_center,
                    arm=arm,
                    next_arm=next_arm,
                    kind="frontage_reserve",
                    junction_core_rect=junction_core_rect,
                    trim_outside_corner=trim_outside_corner,
                    aoi_polygon=aoi_polygon,
                )
                if not getattr(frontage_patch, "is_empty", True):
                    frontage_corner_patches.append(
                        {
                            "patch_id": f"{junction.junction_id}_frontage_{arm_index:02d}",
                            "geometry": frontage_patch,
                        }
                    )

        junction_geometry = {
            "junction_id": str(junction.junction_id),
            "kind": kind,
            "anchor_xy": [round(anchor[0], 3), round(anchor[1], 3)],
            "arm_count": int(len(arms)),
            "connected_road_ids": sorted(int(item["road_id"]) for item in arms),
            "connected_centerline_ids": sorted(str(item["centerline_id"]) for item in arms),
            "junction_core_rect": junction_core_rect,
            "carriageway_core": carriageway_core,
            "approach_boundaries": approach_boundaries,
            "approach_split_lines": list(approach_boundaries),
            "skeleton_foot_points": skeleton_foot_points,
            "sub_lane_control_points": sub_lane_control_points,
            "crosswalk_patches": crosswalk_patches,
            "sidewalk_trim_zone": _merge_polygon_geometries(sidewalk_trim_polygons, aoi_polygon=aoi_polygon),
        }
        if kind == "cross_junction":
            junction_geometry["quadrant_corner_kernels"] = quadrant_corner_kernels
            junction_geometry["sidewalk_corner_polylines"] = sidewalk_corner_polylines
            junction_geometry["nearroad_corner_polylines"] = nearroad_corner_polylines
            junction_geometry["frontage_corner_polylines"] = frontage_corner_polylines
            junction_geometry["sidewalk_corner_patches"] = sidewalk_corner_patches
            junction_geometry["nearroad_corner_patches"] = nearroad_corner_patches
            junction_geometry["frontage_corner_patches"] = frontage_corner_patches
            junction_geometry["turn_lane_patches"] = turn_lane_patches
            junction_geometry["turn_lane_debug"] = turn_lane_debug
            junction_geometry["arm_skeletons"] = arm_skeletons
        else:
            junction_geometry["sidewalk_corner_patches"] = sidewalk_corner_patches
            junction_geometry["nearroad_corner_patches"] = nearroad_corner_patches
            junction_geometry["frontage_corner_patches"] = frontage_corner_patches
        junctions.append(junction_geometry)
    return junctions


def build_junction_geometries(
    roads: Sequence[Any],
    *,
    road_segment_graph: Any | None = None,
    aoi_polygon: Any | None = None,
    crosswalk_depth_m: float = 3.0,
    tolerance_m: float = 0.25,
) -> List[Dict[str, Any]]:
    from shapely.geometry import LineString, MultiPolygon

    explicit_graph_junctions = list(getattr(road_segment_graph, "junctions", ()) or ()) if road_segment_graph is not None else []
    if any(
        str(getattr(item, "source_mode", "") or "") == "explicit"
        and tuple(getattr(item, "connected_road_ids", ()) or ())
        for item in explicit_graph_junctions
    ):
        return _build_explicit_graph_junction_geometries(
            roads,
            road_segment_graph=road_segment_graph,
            aoi_polygon=aoi_polygon,
        )

    road_profiles = _road_profile_widths_from_graph(road_segment_graph)
    clusters: List[Dict[str, Any]] = []
    road_widths_by_id: Dict[int, float] = {}
    for road in roads:
        road_id = int(getattr(road, "osm_id", 0) or 0)
        points = _dedupe_adjacent_points(getattr(road, "coords", ()) or ())
        if len(points) < 2:
            continue
        road_widths_by_id[road_id] = float(getattr(road, "width_m", 8.0) or 8.0)
        for vertex_index, point in enumerate(points):
            matched = None
            for cluster in clusters:
                if math.hypot(float(cluster["point"][0]) - float(point[0]), float(cluster["point"][1]) - float(point[1])) <= tolerance_m:
                    matched = cluster
                    break
            if matched is None:
                matched = {"point": tuple(point), "count": 0, "members": []}
                clusters.append(matched)
            count = int(matched["count"]) + 1
            anchor = (
                (float(matched["point"][0]) * float(matched["count"]) + float(point[0])) / float(count),
                (float(matched["point"][1]) * float(matched["count"]) + float(point[1])) / float(count),
            )
            matched["point"] = anchor
            matched["count"] = count
            matched["members"].append({"road_id": road_id, "vertex_index": int(vertex_index), "points": tuple(points)})

    junctions: List[Dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        members = list(cluster.get("members", []))
        connected_road_ids = sorted({int(member["road_id"]) for member in members if int(member["road_id"]) > 0})
        if len(connected_road_ids) < 2:
            continue
        anchor = (float(cluster["point"][0]), float(cluster["point"][1]))
        arms: List[Dict[str, Any]] = []
        seen_arm_keys: set[Tuple[int, int, int]] = set()
        for member in members:
            points = tuple(member["points"])
            vertex_index = int(member["vertex_index"])
            road_id = int(member["road_id"])
            profile = road_profiles.get(road_id, {})
            carriageway_width_m = float(
                profile.get("carriageway_width_m", float(road_widths_by_id.get(road_id, 8.0)))
            )
            for neighbor_index in (vertex_index - 1, vertex_index + 1):
                if neighbor_index < 0 or neighbor_index >= len(points):
                    continue
                neighbor = points[neighbor_index]
                length_m = math.hypot(float(neighbor[0]) - anchor[0], float(neighbor[1]) - anchor[1])
                if length_m <= max(float(tolerance_m) * 0.25, 0.05):
                    continue
                arm_key = (
                    road_id,
                    int(round(float(neighbor[0]) * 1000.0)),
                    int(round(float(neighbor[1]) * 1000.0)),
                )
                if arm_key in seen_arm_keys:
                    continue
                seen_arm_keys.add(arm_key)
                tangent = (
                    (float(neighbor[0]) - anchor[0]) / length_m,
                    (float(neighbor[1]) - anchor[1]) / length_m,
                )
                arms.append(
                    {
                        "road_id": road_id,
                        "angle_deg": _angle_deg(anchor, neighbor),
                        "tangent": tangent,
                        "normal": (float(tangent[1]), float(-tangent[0])),
                        "carriageway_width_m": max(carriageway_width_m, 1.0),
                        "nearroad_buffer_width_m": float(profile.get("nearroad_buffer_width_m", 0.0) or 0.0),
                        "nearroad_furnishing_width_m": float(profile.get("nearroad_furnishing_width_m", 0.0) or 0.0),
                        "clear_sidewalk_width_m": float(profile.get("clear_sidewalk_width_m", 0.0) or 0.0),
                        "farfromroad_buffer_width_m": float(profile.get("farfromroad_buffer_width_m", 0.0) or 0.0),
                        "frontage_reserve_width_m": float(profile.get("frontage_reserve_width_m", 0.0) or 0.0),
                        "side_strip_layouts": dict(profile.get("side_strip_layouts", {}) or {}),
                        "center_strip_layouts": list(profile.get("center_strip_layouts", []) or ()),
                    }
                )
        arm_angles = [float(item["angle_deg"]) for item in arms]
        arm_count = len(arm_angles)
        if arm_count < 3:
            continue
        kind = _classify_junction_kind(arm_angles)
        if kind not in {"t_junction", "cross_junction"}:
            carriageway_core = MultiPolygon()
            approach_polygons = []
            for arm in arms:
                extent_m = max(float(crosswalk_depth_m) + 6.0, float(arm["carriageway_width_m"]))
                approach = LineString(
                    [
                        anchor,
                        (
                            anchor[0] + float(arm["tangent"][0]) * extent_m,
                            anchor[1] + float(arm["tangent"][1]) * extent_m,
                        ),
                    ]
                ).buffer(float(arm["carriageway_width_m"]) * 0.5, cap_style="flat")
                approach_polygons.append(approach)
            carriageway_core = _merge_polygon_geometries(approach_polygons, aoi_polygon=aoi_polygon)
            junctions.append(
                {
                    "junction_id": f"junction_{index:02d}",
                    "kind": "complex_junction",
                    "anchor_xy": [round(anchor[0], 3), round(anchor[1], 3)],
                    "arm_count": int(arm_count),
                    "connected_road_ids": connected_road_ids,
                    "carriageway_core": carriageway_core,
                    "crosswalk_patches": [],
                    "sidewalk_corner_patches": [],
                    "frontage_corner_patches": [],
                }
            )
            continue

        axis_u = _principal_junction_axis(arms)
        axis_u_length = max(math.hypot(float(axis_u[0]), float(axis_u[1])), 1e-6)
        axis_u = (float(axis_u[0]) / axis_u_length, float(axis_u[1]) / axis_u_length)
        axis_v = (float(-axis_u[1]), float(axis_u[0]))
        axis_u_angle = _angle_deg((0.0, 0.0), axis_u)

        arms_on_u: List[Dict[str, Any]] = []
        arms_on_v: List[Dict[str, Any]] = []
        for arm in arms:
            along_u = _axis_distance_deg(float(arm["angle_deg"]), axis_u_angle)
            along_v = _axis_distance_deg(float(arm["angle_deg"]), axis_u_angle + 90.0)
            if along_v + 1e-6 < along_u:
                arms_on_v.append(arm)
            else:
                arms_on_u.append(arm)

        def _max_half_width(items: Sequence[Dict[str, Any]], fallback: Sequence[Dict[str, Any]]) -> float:
            values = [float(item["carriageway_width_m"]) * 0.5 for item in items if float(item["carriageway_width_m"]) > 0.0]
            if not values:
                values = [
                    float(item["carriageway_width_m"]) * 0.5
                    for item in fallback
                    if float(item["carriageway_width_m"]) > 0.0
                ]
            return max(values or [2.0])

        half_u_m = _max_half_width(arms_on_v, arms)
        half_v_m = _max_half_width(arms_on_u, arms)
        junction_core_rect = _junction_rectangle_patch(
            anchor=anchor,
            axis_u=axis_u,
            axis_v=axis_v,
            half_u_m=half_u_m,
            half_v_m=half_v_m,
        )
        if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
            junction_core_rect = junction_core_rect.intersection(aoi_polygon)

        crosswalk_patches = []
        approach_boundaries = []
        sidewalk_trim_polygons = []
        for arm_index, arm in enumerate(arms):
            half_width = float(arm["carriageway_width_m"]) * 0.5
            exit_distance_m = _ray_rectangle_exit_distance(
                tuple(arm["tangent"]),
                axis_u,
                axis_v,
                half_u_m,
                half_v_m,
            )
            boundary_center = (
                anchor[0] + float(arm["tangent"][0]) * exit_distance_m,
                anchor[1] + float(arm["tangent"][1]) * exit_distance_m,
            )
            boundary_start = (
                boundary_center[0] - float(arm["normal"][0]) * half_width,
                boundary_center[1] - float(arm["normal"][1]) * half_width,
            )
            boundary_end = (
                boundary_center[0] + float(arm["normal"][0]) * half_width,
                boundary_center[1] + float(arm["normal"][1]) * half_width,
            )
            approach_boundaries.append(
                {
                    "boundary_id": f"junction_{index:02d}_approach_{arm_index:02d}",
                    "road_id": int(arm["road_id"]),
                    "center_xy": [round(boundary_center[0], 3), round(boundary_center[1], 3)],
                    "start_xy": [round(boundary_start[0], 3), round(boundary_start[1], 3)],
                    "end_xy": [round(boundary_end[0], 3), round(boundary_end[1], 3)],
                    "exit_distance_m": round(float(exit_distance_m), 3),
                }
            )

            center = (
                anchor[0] + float(arm["tangent"][0]) * (float(exit_distance_m) + float(crosswalk_depth_m) * 0.5),
                anchor[1] + float(arm["tangent"][1]) * (float(exit_distance_m) + float(crosswalk_depth_m) * 0.5),
            )
            patch = _rectangle_patch(
                center=center,
                tangent=tuple(arm["tangent"]),
                normal=tuple(arm["normal"]),
                length_m=float(crosswalk_depth_m),
                width_m=float(arm["carriageway_width_m"]),
            )
            if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
                patch = patch.intersection(aoi_polygon)
            crosswalk_patches.append(
                {
                    "patch_id": f"junction_{index:02d}_crosswalk_{arm_index:02d}",
                    "road_id": int(arm["road_id"]),
                    "horizontal_axes": [
                        [float(arm["tangent"][0]), float(arm["tangent"][1])],
                        [float(arm["normal"][0]), float(arm["normal"][1])],
                    ],
                    "geometry": patch,
                }
            )

            side_total_width_m = (
                float(arm["nearroad_buffer_width_m"])
                + float(arm["nearroad_furnishing_width_m"])
                + float(arm["clear_sidewalk_width_m"])
                + float(arm["farfromroad_buffer_width_m"])
                + float(arm["frontage_reserve_width_m"])
            )
            trim_half_width = max(half_width + side_total_width_m, half_width)
            trim_extent_m = max(float(exit_distance_m) + float(crosswalk_depth_m), float(crosswalk_depth_m))
            trim_polygon = LineString(
                [
                    anchor,
                    (
                        anchor[0] + float(arm["tangent"][0]) * trim_extent_m,
                        anchor[1] + float(arm["tangent"][1]) * trim_extent_m,
                    ),
                ]
            ).buffer(trim_half_width, cap_style="flat")
            sidewalk_trim_polygons.append(trim_polygon)
            split_boundary_center = (
                anchor[0] + float(arm["tangent"][0]) * (float(exit_distance_m) + float(crosswalk_depth_m)),
                anchor[1] + float(arm["tangent"][1]) * (float(exit_distance_m) + float(crosswalk_depth_m)),
            )
            arm["core_exit_distance_m"] = float(exit_distance_m)
            arm["split_distance_m"] = float(exit_distance_m) + float(crosswalk_depth_m)
            arm["split_boundary_center"] = split_boundary_center
            arm["split_boundary_start"] = (
                split_boundary_center[0] - float(arm["normal"][0]) * half_width,
                split_boundary_center[1] - float(arm["normal"][1]) * half_width,
            )
            arm["split_boundary_end"] = (
                split_boundary_center[0] + float(arm["normal"][0]) * half_width,
                split_boundary_center[1] + float(arm["normal"][1]) * half_width,
            )

        carriageway_core = _junction_carriageway_surface(
            anchor=anchor,
            arms=arms,
            junction_core_rect=junction_core_rect,
            aoi_polygon=aoi_polygon,
        )
        sidewalk_corner_patches = []
        nearroad_corner_patches = []
        frontage_corner_patches = []
        quadrant_corner_kernels = []
        sidewalk_corner_polylines = []
        nearroad_corner_polylines = []
        frontage_corner_polylines = []
        turn_lane_patches = []
        turn_lane_debug = []
        arm_skeletons = []
        ordered_arms = sorted(arms, key=lambda item: float(item["angle_deg"]))
        if kind == "cross_junction":
            cross_corner_data = _build_cross_corner_kernel_geometries(
                f"junction_{index:02d}",
                ordered_arms,
            )
            quadrant_corner_kernels = list(cross_corner_data["quadrant_corner_kernels"])
            sidewalk_corner_polylines = list(cross_corner_data["sidewalk_corner_polylines"])
            nearroad_corner_polylines = list(cross_corner_data["nearroad_corner_polylines"])
            frontage_corner_polylines = list(cross_corner_data["frontage_corner_polylines"])
            sidewalk_corner_patches = list(cross_corner_data["sidewalk_corner_patches"])
            nearroad_corner_patches = list(cross_corner_data["nearroad_corner_patches"])
            frontage_corner_patches = list(cross_corner_data["frontage_corner_patches"])
            turn_lane_patches = list(cross_corner_data.get("turn_lane_patches", []) or ())
            turn_lane_debug = list(cross_corner_data.get("turn_lane_debug", []) or ())
            arm_skeletons = list(cross_corner_data.get("arm_skeletons", []) or ())
        else:
            for arm_index, arm in enumerate(ordered_arms):
                next_arm = ordered_arms[(arm_index + 1) % len(ordered_arms)]
                start_angle = float(arm["angle_deg"])
                end_angle = float(next_arm["angle_deg"])
                sweep = end_angle - start_angle
                if sweep <= 0.0:
                    sweep += 360.0
                if sweep <= 5.0 or sweep >= 175.0:
                    continue
                trim_outside_corner = _should_trim_outside_corner(kind, sweep)
                corner_center = _line_intersection(
                    tuple(float(value) for value in arm["split_boundary_center"]),
                    tuple(float(value) for value in arm["normal"]),
                    tuple(float(value) for value in next_arm["split_boundary_center"]),
                    tuple(float(value) for value in next_arm["normal"]),
                )
                if corner_center is None:
                    continue
                nearroad_patch = _corner_connector_patch(
                    corner_center=corner_center,
                    arm=arm,
                    next_arm=next_arm,
                    kind="nearroad_furnishing",
                    junction_core_rect=junction_core_rect,
                    trim_outside_corner=trim_outside_corner,
                    aoi_polygon=aoi_polygon,
                )
                if not getattr(nearroad_patch, "is_empty", True):
                    nearroad_corner_patches.append(
                        {
                            "patch_id": f"junction_{index:02d}_nearroad_{arm_index:02d}",
                            "geometry": nearroad_patch,
                        }
                    )
                sidewalk_patch = _corner_connector_patch(
                    corner_center=corner_center,
                    arm=arm,
                    next_arm=next_arm,
                    kind="clear_sidewalk",
                    junction_core_rect=junction_core_rect,
                    trim_outside_corner=trim_outside_corner,
                    aoi_polygon=aoi_polygon,
                )
                if not getattr(sidewalk_patch, "is_empty", True):
                    sidewalk_corner_patches.append(
                        {
                            "patch_id": f"junction_{index:02d}_sidewalk_{arm_index:02d}",
                            "geometry": sidewalk_patch,
                        }
                    )
                frontage_patch = _corner_connector_patch(
                    corner_center=corner_center,
                    arm=arm,
                    next_arm=next_arm,
                    kind="frontage_reserve",
                    junction_core_rect=junction_core_rect,
                    trim_outside_corner=trim_outside_corner,
                    aoi_polygon=aoi_polygon,
                )
                if not getattr(frontage_patch, "is_empty", True):
                    frontage_corner_patches.append(
                        {
                            "patch_id": f"junction_{index:02d}_frontage_{arm_index:02d}",
                            "geometry": frontage_patch,
                        }
                    )

        junction_geometry = {
            "junction_id": f"junction_{index:02d}",
            "kind": kind,
            "anchor_xy": [round(anchor[0], 3), round(anchor[1], 3)],
            "arm_count": int(arm_count),
            "connected_road_ids": connected_road_ids,
            "junction_core_rect": junction_core_rect,
            "carriageway_core": carriageway_core,
            "approach_boundaries": approach_boundaries,
            "crosswalk_patches": crosswalk_patches,
            "sidewalk_trim_zone": _merge_polygon_geometries(sidewalk_trim_polygons, aoi_polygon=aoi_polygon),
        }
        if kind == "cross_junction":
            junction_geometry["quadrant_corner_kernels"] = quadrant_corner_kernels
            junction_geometry["sidewalk_corner_polylines"] = sidewalk_corner_polylines
            junction_geometry["nearroad_corner_polylines"] = nearroad_corner_polylines
            junction_geometry["frontage_corner_polylines"] = frontage_corner_polylines
            junction_geometry["sidewalk_corner_patches"] = sidewalk_corner_patches
            junction_geometry["nearroad_corner_patches"] = nearroad_corner_patches
            junction_geometry["frontage_corner_patches"] = frontage_corner_patches
            junction_geometry["turn_lane_patches"] = turn_lane_patches
            junction_geometry["turn_lane_debug"] = turn_lane_debug
            junction_geometry["arm_skeletons"] = arm_skeletons
        else:
            junction_geometry["sidewalk_corner_patches"] = sidewalk_corner_patches
            junction_geometry["nearroad_corner_patches"] = nearroad_corner_patches
            junction_geometry["frontage_corner_patches"] = frontage_corner_patches
        junctions.append(junction_geometry)
    return junctions


# ---------------------------------------------------------------------------
# Road selection
# ---------------------------------------------------------------------------

_HIERARCHY_RANK: Dict[str, int] = {
    "primary": 0,
    "secondary": 1,
    "tertiary": 2,
    "residential": 3,
    "unclassified": 4,
    "service": 5,
    "living_street": 6,
}

WALKABLE_NEIGHBORHOOD_HIGHWAY_TYPES: Tuple[str, ...] = ("tertiary", "unclassified", "residential")


def is_walkable_neighborhood_highway(highway_type: str) -> bool:
    return str(highway_type or "").strip().lower() in WALKABLE_NEIGHBORHOOD_HIGHWAY_TYPES


def summarize_road_selection(
    *,
    strategy: str,
    selected_highway_type: str,
) -> Dict[str, str]:
    requested = str(strategy or "primary_road").strip().lower()
    highway_type = str(selected_highway_type or "").strip().lower()
    used = requested
    fallback_reason = ""
    if requested == "walkable_neighborhood" and highway_type and not is_walkable_neighborhood_highway(highway_type):
        used = "walkable_neighborhood_fallback"
        fallback_reason = (
            "no tertiary/unclassified/residential candidate survived selection; "
            f"fell back to {highway_type or 'unknown'}"
        )
    return {
        "road_selection_requested": requested,
        "road_selection_used": used,
        "selected_highway_type": highway_type,
        "road_selection_fallback_reason": fallback_reason,
    }


def select_primary_road(
    roads: list,
    bbox_m: Tuple[float, float, float, float],
    strategy: str = "primary_road",
    selected_osm_id: int | None = None,
) -> list:
    """Select a single road from *roads* based on *strategy*.

    Strategies
    ----------
    ``"all"``          – return all roads unchanged.
    ``"primary_road"`` – pick the highest-hierarchy road closest to AOI centre.
    ``"walkable_neighborhood"`` – prefer tertiary/unclassified/residential before broader hierarchy fallback.
    ``"longest"``      – pick the longest road regardless of hierarchy.
    ``selected_osm_id`` – if provided, prefer the exact road match.
    """
    if not roads or strategy == "all":
        return list(roads)

    from shapely.geometry import LineString, Point as ShapelyPoint

    center = ((bbox_m[0] + bbox_m[2]) / 2.0, (bbox_m[1] + bbox_m[3]) / 2.0)
    center_pt = ShapelyPoint(center)

    def _road_length(road) -> float:
        return LineString(road.coords).length if len(road.coords) >= 2 else 0.0

    if selected_osm_id is not None:
        for road in roads:
            if int(getattr(road, "osm_id", -1)) == int(selected_osm_id):
                logger.info(
                    "Road selection (selected_osm_id): %d roads -> osm_id=%d (%s, %.0fm)",
                    len(roads), road.osm_id, road.highway_type, _road_length(road),
                )
                return [road]
        logger.warning(
            "Selected road osm_id=%s not found in %d roads; falling back to strategy=%s",
            selected_osm_id, len(roads), strategy,
        )

    if strategy == "longest":
        best = max(roads, key=_road_length)
        logger.info(
            "Road selection (longest): %d roads -> osm_id=%d (%s, %.0fm)",
            len(roads), best.osm_id, best.highway_type, _road_length(best),
        )
        return [best]

    def _sort_key(road):
        rank = _HIERARCHY_RANK.get(road.highway_type, 99)
        dist = (
            LineString(road.coords).distance(center_pt)
            if len(road.coords) >= 2
            else 9999.0
        )
        length = _road_length(road)
        return (rank, dist, -length)

    if strategy == "walkable_neighborhood":
        preferred_roads = [
            road
            for road in roads
            if is_walkable_neighborhood_highway(str(getattr(road, "highway_type", "") or ""))
        ]

        def _walkable_sort_key(road):
            highway_type = str(getattr(road, "highway_type", "") or "").strip().lower()
            pref_rank = (
                WALKABLE_NEIGHBORHOOD_HIGHWAY_TYPES.index(highway_type)
                if highway_type in WALKABLE_NEIGHBORHOOD_HIGHWAY_TYPES
                else 99
            )
            dist = (
                LineString(road.coords).distance(center_pt)
                if len(road.coords) >= 2
                else 9999.0
            )
            length = _road_length(road)
            return (pref_rank, dist, -length)

        candidate_roads = preferred_roads if preferred_roads else list(roads)
        sorted_roads = sorted(candidate_roads, key=_walkable_sort_key if preferred_roads else _sort_key)
        best = sorted_roads[0]
        logger.info(
            "Road selection (walkable_neighborhood): %d roads -> osm_id=%d (%s, %.0fm)%s",
            len(roads),
            best.osm_id,
            best.highway_type,
            _road_length(best),
            "" if preferred_roads else " [fallback]",
        )
        return [best]

    # strategy == "primary_road"
    sorted_roads = sorted(roads, key=_sort_key)
    best = sorted_roads[0]
    logger.info(
        "Road selection (primary_road): %d roads -> osm_id=%d (%s, %.0fm)",
        len(roads), best.osm_id, best.highway_type, _road_length(best),
    )
    return [best]


def apply_road_selection(projected_features: Any, config: Any) -> Any:
    """Return a copy of *projected_features* with roads filtered by config.road_selection."""
    from .osm_ingest import ProjectedFeatures

    strategy = str(getattr(config, "road_selection", "walkable_neighborhood"))
    selected_osm_id = getattr(config, "selected_road_osm_id", None)
    layout_mode = str(getattr(config, "layout_mode", "") or "").strip().lower()
    if (strategy == "all" or layout_mode == "osm_multiblock") and selected_osm_id is None:
        return projected_features

    filtered = select_primary_road(
        projected_features.roads,
        projected_features.bbox_m,
        strategy,
        selected_osm_id=selected_osm_id,
    )
    return ProjectedFeatures(
        roads=filtered,
        buildings=projected_features.buildings,
        land_use_polygons=list(getattr(projected_features, "land_use_polygons", []) or []),
        semantic_blocks=list(getattr(projected_features, "semantic_blocks", []) or []),
        entrances=projected_features.entrances,
        bus_stops=projected_features.bus_stops,
        fire_points=projected_features.fire_points,
        poi_points_by_type=extract_poi_points_by_type(projected_features),
        semantic_points_by_type=dict(getattr(projected_features, "semantic_points_by_type", {}) or {}),
        bbox_m=projected_features.bbox_m,
        origin_utm=projected_features.origin_utm,
        utm_epsg=projected_features.utm_epsg,
    )


def count_placement_context_pois(context: PlacementContext) -> Dict[str, int]:
    return normalize_poi_counts({
        poi_type: len(points)
        for poi_type, points in extract_poi_points_by_type(context).items()
    })


def total_poi_count(counts: Dict[str, int]) -> int:
    return int(sum(int(value) for value in counts.values()))


def poi_score(counts: Dict[str, int]) -> float:
    return poi_weighted_score(counts)


def poi_core_count(counts: Dict[str, int]) -> int:
    return core_poi_count(counts)


def evaluate_projected_road_context(
    projected_features: Any,
    config: Any,
    *,
    road_segment_graph: Any | None = None,
) -> Tuple[Any, PlacementContext, Dict[str, int]]:
    filtered_projected = apply_road_selection(projected_features, config)
    placement_ctx = build_placement_context(filtered_projected, config, road_segment_graph=road_segment_graph)
    return filtered_projected, placement_ctx, count_placement_context_pois(placement_ctx)


def build_placement_context(
    projected_features: Any,
    config: Any,
    *,
    road_segment_graph: Any | None = None,
    aoi_polygon: Any | None = None,
) -> PlacementContext:
    """Build the full placement context from projected OSM features and config."""
    from shapely.geometry import box
    from shapely.prepared import prep

    from .street_program import profile_defaults

    bbox_m = projected_features.bbox_m
    if aoi_polygon is None:
        aoi_polygon = box(bbox_m[0], bbox_m[1], bbox_m[2], bbox_m[3])
    elif not getattr(aoi_polygon, "is_valid", True):
        aoi_polygon = aoi_polygon.buffer(0)
    detailed_strip_profiles, strip_zones, segment_strip_zones, strip_summary = _build_graph_strip_context(
        road_segment_graph,
        aoi_polygon=aoi_polygon,
    )
    poi_points_by_type = extract_poi_points_by_type(projected_features)
    defaults = profile_defaults(str(getattr(config, "design_rule_profile", "balanced_complete_street_v1")))
    min_clear_path_width_m = float(defaults["min_clear_path_width_m"])
    min_furnishing_width_m = float(defaults["furnishing_width_m"])
    right_edge_width_m = float(defaults.get("right_edge_width_m", min_furnishing_width_m))

    cross_section = synthesize_poi_driven_cross_section(
        roads=projected_features.roads,
        poi_points_by_type=poi_points_by_type,
        road_width_m=float(config.road_width_m),
        lane_count=int(getattr(config, "lane_count", 1)),
        sidewalk_seed_width_m=float(config.sidewalk_width_m),
        base_lane_width_m=getattr(config, "base_lane_width_m", None),
        min_clear_path_width_m=min_clear_path_width_m,
        left_furnishing_min_width_m=min_furnishing_width_m,
        right_furnishing_min_width_m=right_edge_width_m,
    )

    carriageway_width_m = float(cross_section.carriageway_width_m)
    left_sidewalk_width_m = float(cross_section.left_sidewalk_width_m)
    right_sidewalk_width_m = float(cross_section.right_sidewalk_width_m)
    left_clear_path_width_m = float(cross_section.left_clear_path_width_m)
    right_clear_path_width_m = float(cross_section.right_clear_path_width_m)
    left_furnishing_width_m = float(cross_section.left_furnishing_width_m)
    right_furnishing_width_m = float(cross_section.right_furnishing_width_m)
    row_width_m = float(cross_section.row_width_m)
    required_left_width_m = float(cross_section.required_left_width_m)
    required_right_width_m = float(cross_section.required_right_width_m)

    if strip_summary:
        carriageway_width_m = float(strip_summary.get("carriageway_width_m", carriageway_width_m) or carriageway_width_m)
        left_furnishing_width_m = float(
            strip_summary.get("left_nearroad_furnishing_width_m", left_furnishing_width_m) or left_furnishing_width_m
        )
        right_furnishing_width_m = float(
            strip_summary.get("right_nearroad_furnishing_width_m", right_furnishing_width_m) or right_furnishing_width_m
        )
        left_clear_path_width_m = float(
            strip_summary.get("left_clear_sidewalk_width_m", left_clear_path_width_m) or left_clear_path_width_m
        )
        right_clear_path_width_m = float(
            strip_summary.get("right_clear_sidewalk_width_m", right_clear_path_width_m) or right_clear_path_width_m
        )
        left_sidewalk_width_m = float(strip_summary.get("left_total_width_m", left_sidewalk_width_m) or left_sidewalk_width_m)
        right_sidewalk_width_m = float(
            strip_summary.get("right_total_width_m", right_sidewalk_width_m) or right_sidewalk_width_m
        )
        row_width_m = float(carriageway_width_m + left_sidewalk_width_m + right_sidewalk_width_m)
        required_left_width_m = float(left_sidewalk_width_m)
        required_right_width_m = float(right_sidewalk_width_m)

    carriageway_raw = build_carriageway_polygon_with_width(
        projected_features.roads,
        carriageway_width_m,
    )
    # Clip carriageway to AOI so roads don't extend far beyond the scene
    carriageway = _clip_to_aoi(carriageway_raw, aoi_polygon)
    left_sidewalk_zone, right_sidewalk_zone, sidewalk_zone = build_sidewalk_zones_from_roads(
        projected_features.roads,
        carriageway_width_m=carriageway_width_m,
        left_sidewalk_width_m=left_sidewalk_width_m,
        right_sidewalk_width_m=right_sidewalk_width_m,
        aoi_polygon=aoi_polygon,
    )

    # Filter POIs to those retained by the synthesized road corridor.
    if not carriageway.is_empty or not sidewalk_zone.is_empty:
        from shapely.geometry import Point as ShapelyPoint

        corridor = carriageway.union(sidewalk_zone)
        relevance_zone = prep(corridor.buffer(0.25))
        filtered_poi_points_by_type = {
            poi_type: [pt for pt in points if relevance_zone.contains(ShapelyPoint(pt))]
            for poi_type, points in cross_section.candidate_poi_points_by_type.items()
        }
    else:
        filtered_poi_points_by_type = {
            poi_type: list(points)
            for poi_type, points in cross_section.candidate_poi_points_by_type.items()
        }

    filtered_entrances = list(filtered_poi_points_by_type.get("entrance", []))
    filtered_bus_stops = list(filtered_poi_points_by_type.get("bus_stop", []))
    filtered_fire_points = list(filtered_poi_points_by_type.get(CANONICAL_FIRE_POI, []))

    logger.info(
        "POI filtering: %s -> %s",
        poi_breakdown_string({
            poi_type: len(points)
            for poi_type, points in cross_section.candidate_poi_points_by_type.items()
        }),
        poi_breakdown_string({
            poi_type: len(points)
            for poi_type, points in filtered_poi_points_by_type.items()
        }),
    )

    junction_geometries = build_junction_geometries(
        projected_features.roads,
        road_segment_graph=road_segment_graph,
        aoi_polygon=aoi_polygon,
    )

    from shapely.geometry import LineString

    if junction_geometries:
        carriageway_trim = _merge_polygon_geometries(
            [item.get("carriageway_core") or item.get("junction_core_rect") for item in junction_geometries],
            aoi_polygon=aoi_polygon,
        )
        if carriageway_trim is not None and not getattr(carriageway_trim, "is_empty", True):
            carriageway = _clip_to_aoi(carriageway.difference(carriageway_trim), aoi_polygon)

        sidewalk_trim = _merge_polygon_geometries(
            [item.get("sidewalk_trim_zone") for item in junction_geometries],
            aoi_polygon=aoi_polygon,
        )
        if sidewalk_trim is not None and not getattr(sidewalk_trim, "is_empty", True):
            left_sidewalk_zone = _clip_to_aoi(left_sidewalk_zone.difference(sidewalk_trim), aoi_polygon)
            right_sidewalk_zone = _clip_to_aoi(right_sidewalk_zone.difference(sidewalk_trim), aoi_polygon)
            sidewalk_zone = _clip_to_aoi(sidewalk_zone.difference(sidewalk_trim), aoi_polygon)

    # Build individual road arm geometries for granular 3D mesh naming
    road_arm_geometries: List[Any] = []
    half_w = max(float(carriageway_width_m) / 2.0, 0.5)
    for road in projected_features.roads:
        if len(road.coords) < 2:
            continue
        line = LineString(road.coords)
        poly = line.buffer(half_w, cap_style="flat")
        if poly.is_empty:
            continue
        poly = _clip_to_aoi(poly, aoi_polygon)
        if junction_geometries:
            carriageway_trim = _merge_polygon_geometries(
                [item.get("carriageway_core") or item.get("junction_core_rect") for item in junction_geometries],
                aoi_polygon=aoi_polygon,
            )
            if carriageway_trim is not None and not getattr(carriageway_trim, "is_empty", True):
                poly = _clip_to_aoi(poly.difference(carriageway_trim), aoi_polygon)
        if not poly.is_empty:
            road_arm_geometries.append(poly)

    placement_context = PlacementContext(
        sidewalk_zone=sidewalk_zone,
        carriageway=carriageway,
        left_sidewalk_zone=left_sidewalk_zone,
        right_sidewalk_zone=right_sidewalk_zone,
        entrance_points=filtered_entrances,
        bus_stop_points=filtered_bus_stops,
        fire_points=filtered_fire_points,
        poi_points_by_type=filtered_poi_points_by_type,
        aoi_polygon=aoi_polygon,
        origin_offset=projected_features.origin_utm,
        carriageway_polygon=carriageway,
        road_reference=projected_features.roads[0] if projected_features.roads else None,
        road_references=list(projected_features.roads),
        carriageway_width_m=float(carriageway_width_m),
        left_clear_path_width_m=float(left_clear_path_width_m),
        right_clear_path_width_m=float(right_clear_path_width_m),
        left_furnishing_width_m=float(left_furnishing_width_m),
        right_furnishing_width_m=float(right_furnishing_width_m),
        row_width_m=float(row_width_m),
        width_expanded=bool(cross_section.width_expanded),
        width_reallocation_reason=str(cross_section.width_reallocation_reason),
        road_arm_geometries=road_arm_geometries,
        poi_fit_feasible=bool(cross_section.poi_fit_feasible),
        poi_fit_report=dict(cross_section.poi_fit_report),
        required_left_width_m=float(required_left_width_m),
        required_right_width_m=float(required_right_width_m),
        junction_geometries=list(junction_geometries),
        detailed_strip_profiles=list(detailed_strip_profiles),
        strip_zones=dict(strip_zones),
        segment_strip_zones={key: dict(value) for key, value in segment_strip_zones.items()},
    )
    trim_center_planting_strips_for_junctions(
        placement_context,
        list(junction_geometries),
    )
    return placement_context


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------

def sample_slot_on_sidewalk(
    zone: Any,
    rng: random.Random,
    max_attempts: int = 100,
) -> Optional[Tuple[float, float]]:
    """Uniformly sample a point inside *zone* via rejection sampling.

    Returns ``(x, y)`` in local metres or ``None`` if all attempts fail.
    """
    if zone.is_empty:
        return None

    from shapely.geometry import Point as ShapelyPoint
    from shapely.prepared import prep

    prepared = prep(zone)
    minx, miny, maxx, maxy = zone.bounds
    for _ in range(max_attempts):
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        if prepared.contains(ShapelyPoint(x, y)):
            return (x, y)
    return None


def compute_facing_angle(
    point: Tuple[float, float],
    carriageway: Any,
) -> float:
    """Compute yaw (degrees) of *point* facing the nearest carriageway edge.

    Returns angle in degrees [0, 360).
    """
    from shapely.geometry import Point as ShapelyPoint

    if carriageway.is_empty:
        return 0.0

    sp = ShapelyPoint(point)
    nearest_pt = carriageway.boundary.interpolate(carriageway.boundary.project(sp))
    dx = nearest_pt.x - point[0]
    dy = nearest_pt.y - point[1]
    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)
    return angle_deg % 360.0


# ---------------------------------------------------------------------------
# GeoJSON export (debug / visualisation)
# ---------------------------------------------------------------------------

def export_zones_geojson(context: PlacementContext, out_path: Path) -> Path:
    """Write placement zones and POI to a GeoJSON file for debugging."""
    from shapely.geometry import mapping, Point as ShapelyPoint

    features: List[Dict[str, Any]] = []

    # Carriageway
    if not context.carriageway.is_empty:
        features.append({
            "type": "Feature",
            "properties": {"layer": "carriageway"},
            "geometry": mapping(context.carriageway),
        })

    # Sidewalk zone
    if not context.sidewalk_zone.is_empty:
        features.append({
            "type": "Feature",
            "properties": {"layer": "sidewalk_zone"},
            "geometry": mapping(context.sidewalk_zone),
        })

    # POI points
    for poi_type, points in nonempty_poi_points(getattr(context, "poi_points_by_type", {})).items():
        for pt in points:
            features.append({
                "type": "Feature",
                "properties": {"layer": poi_type},
                "geometry": mapping(ShapelyPoint(pt)),
            })

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(geojson, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Exported placement zones GeoJSON to %s (%d features)", out_path, len(features))
    return out_path
