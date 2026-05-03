"""Bridge reference annotations into the corridor scene/export pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .osm_ingest import OsmRoad, ProjectedFeatures
from .placement_zones import PlacementContext, build_placement_context, build_sidewalk_zones_from_roads
from .junction_surface_normalization import normalize_junction_surface_geometries
from .reference_annotation import (
    BezierCurve3,
    JunctionComposition,
    JunctionLaneSurface,
    JunctionMergedSurface,
    ReferenceAnnotation,
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
    build_segment_graph_from_annotation,
    functional_zone_to_local_coords,
    parse_reference_annotation,
)
from .types import RoadSegmentGraph, StreetComposeConfig

ANNOTATION_SCENE_BBOX_PADDING_M = 36.0


@dataclass(frozen=True)
class ReferenceAnnotationSceneBridgeResult:
    annotation: ReferenceAnnotation
    road_segment_graph: RoadSegmentGraph
    projected_features: ProjectedFeatures
    placement_context: PlacementContext
    summary_metadata: Dict[str, Any]


def _collect_local_centerlines(annotation: ReferenceAnnotation) -> List[Tuple[int, Any, List[Tuple[float, float]]]]:
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    items: List[Tuple[int, Any, List[Tuple[float, float]]]] = []
    road_id = 1
    for centerline in annotation.centerlines:
        points: List[Tuple[float, float]] = []
        for point in centerline.points:
            xy = (
                (float(point.x) - center_x) / ppm,
                (center_y - float(point.y)) / ppm,
            )
            if not points or points[-1] != xy:
                points.append(xy)
        if len(points) >= 2:
            items.append((road_id, centerline, points))
            road_id += 1
    return items


def _graph_bbox(
    local_centerlines: Sequence[Tuple[int, Any, Sequence[Tuple[float, float]]]],
    *,
    padding_m: float,
) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []
    for _, _, points in local_centerlines:
        for point in points:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    if not xs or not ys:
        pad = max(float(padding_m), 10.0)
        return (-pad, -pad, pad, pad)
    return (
        float(min(xs) - padding_m),
        float(min(ys) - padding_m),
        float(max(xs) + padding_m),
        float(max(ys) + padding_m),
    )


def _annotation_building_region_records(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    regions: List[Dict[str, Any]] = []
    for order_index, region in enumerate(annotation.building_regions):
        center_x, center_y = (
            (float(region.center_x_px) - float(annotation.image_width_px) * 0.5) / ppm,
            (float(annotation.image_height_px) * 0.5 - float(region.center_y_px)) / ppm,
        )
        half_width_m = float(region.width_px) / ppm * 0.5
        half_height_m = float(region.height_px) / ppm * 0.5
        yaw_rad = math.radians(float(region.yaw_deg))
        axis_x = (math.cos(yaw_rad), math.sin(yaw_rad))
        axis_y = (-math.sin(yaw_rad), math.cos(yaw_rad))

        def _offset(local_x: float, local_y: float) -> Tuple[float, float]:
            return (
                float(center_x + axis_x[0] * local_x + axis_y[0] * local_y),
                float(center_y + axis_x[1] * local_x + axis_y[1] * local_y),
            )

        polygon_xz = (
            _offset(-half_width_m, -half_height_m),
            _offset(half_width_m, -half_height_m),
            _offset(half_width_m, half_height_m),
            _offset(-half_width_m, half_height_m),
            _offset(-half_width_m, -half_height_m),
        )
        regions.append(
            {
                "region_id": str(region.feature_id),
                "label": str(region.label),
                "order_index": int(order_index),
                "center_xz": (float(center_x), float(center_y)),
                "width_m": float(half_width_m * 2.0),
                "height_m": float(half_height_m * 2.0),
                "yaw_deg": float(region.yaw_deg),
                "polygon_xz": tuple((float(x), float(z)) for x, z in polygon_xz),
            }
        )
    return regions


def build_reference_annotation_scene_bridge(
    annotation_input: ReferenceAnnotation | Mapping[str, Any],
    *,
    compose_config: StreetComposeConfig | Mapping[str, Any] | None = None,
) -> ReferenceAnnotationSceneBridgeResult:
    annotation = (
        annotation_input
        if isinstance(annotation_input, ReferenceAnnotation)
        else parse_reference_annotation(annotation_input)
    )
    resolved_config = (
        compose_config
        if isinstance(compose_config, StreetComposeConfig)
        else build_reference_annotation_compose_config(compose_config or {})
    )
    road_segment_graph = build_segment_graph_from_annotation(annotation, config=resolved_config)
    local_centerlines = _collect_local_centerlines(annotation)
    synthetic_roads: List[OsmRoad] = []
    for road_id, centerline, points in local_centerlines:
        synthetic_roads.append(
            OsmRoad(
                osm_id=int(road_id),
                highway_type=str(getattr(centerline, "highway_type", "") or "annotated_centerline"),
                coords=list(points),
                width_m=float(centerline.carriageway_width_m()),
            )
        )
    projected_features = ProjectedFeatures(
        roads=synthetic_roads,
        buildings=[],
        entrances=[],
        bus_stops=[],
        fire_points=[],
        poi_points_by_type={},
        bbox_m=_graph_bbox(local_centerlines, padding_m=float(ANNOTATION_SCENE_BBOX_PADDING_M)),
        origin_utm=(0.0, 0.0),
        utm_epsg=0,
    )
    placement_context = build_placement_context(
        projected_features,
        resolved_config,
        road_segment_graph=road_segment_graph,
    )
    placement_context.junction_geometries = _apply_manual_junction_compositions(
        annotation,
        list(getattr(placement_context, "junction_geometries", []) or []),
        road_segment_graph=road_segment_graph,
        roads=synthetic_roads,
    )
    _refresh_road_surfaces_for_junction_geometries(
        placement_context,
        synthetic_roads,
        list(getattr(placement_context, "junction_geometries", []) or []),
    )
    placement_context.junction_geometries = normalize_junction_surface_geometries(
        list(getattr(placement_context, "junction_geometries", []) or [])
    )
    placement_context.building_regions = _annotation_building_region_records(annotation)
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    placement_context.functional_zones = [
        {
            "id": zone.feature_id,
            "label": zone.label,
            "kind": zone.kind,
            "points": functional_zone_to_local_coords(zone, annotation),
            "furniture_instances": [
                {
                    "instance_id": inst.instance_id,
                    "kind": inst.kind,
                    "x": (inst.x_px - center_x) / ppm,
                    "y": (center_y - inst.y_px) / ppm,
                    "yaw_deg": inst.yaw_deg,
                }
                for inst in zone.furniture_instances
            ],
        }
        for zone in annotation.functional_zones
    ]
    payload = build_reference_annotation_graph_payload(annotation, config=resolved_config)
    summary_metadata = {
        **dict(payload.get("summary", {}) or {}),
        "layout_mode": "annotation",
        "generator": "reference_annotation_bridge_v1",
        "synthetic_road_count": int(len(projected_features.roads)),
        "junction_geometry_count": int(len(getattr(placement_context, "junction_geometries", []) or [])),
    }
    return ReferenceAnnotationSceneBridgeResult(
        annotation=annotation,
        road_segment_graph=road_segment_graph,
        projected_features=projected_features,
        placement_context=placement_context,
        summary_metadata=summary_metadata,
    )


def _refresh_road_surfaces_for_junction_geometries(
    placement_context: PlacementContext,
    roads: Sequence[OsmRoad],
    junction_geometries: Sequence[Mapping[str, Any]],
) -> None:
    """Re-trim road arm surfaces after derived junction geometry is replaced.

    ``build_placement_context`` initially trims roads against its own junction
    geometry. The reference bridge may then replace cross junctions with the
    RoadPen-style fusion geometry, so the rendered road arms must be clipped
    again against the final carriageway core. Otherwise 3D still shows the old
    diagonal cuts from the pre-fusion geometry.
    """

    if not junction_geometries:
        return
    try:
        from shapely.geometry import LineString, MultiPolygon
        from shapely.ops import unary_union
    except ImportError:
        return

    def clean(geometry: Any) -> Any:
        if geometry is None or getattr(geometry, "is_empty", True):
            return MultiPolygon()
        if not getattr(geometry, "is_valid", True):
            try:
                geometry = geometry.buffer(0)
            except Exception:
                return MultiPolygon()
        return geometry

    aoi_polygon = getattr(placement_context, "aoi_polygon", None)
    road_polygons: List[Any] = []
    for road in roads:
        coords = list(getattr(road, "coords", ()) or ())
        if len(coords) < 2:
            continue
        line = LineString(coords)
        if getattr(line, "is_empty", True):
            continue
        half_width_m = max(float(getattr(road, "width_m", 0.0) or 0.0) * 0.5, 0.5)
        polygon = clean(line.buffer(half_width_m, cap_style="flat"))
        if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
            polygon = clean(polygon.intersection(aoi_polygon))
        if not getattr(polygon, "is_empty", True):
            road_polygons.append(polygon)

    if not road_polygons:
        return

    trim_sources = [
        junction.get("carriageway_core") or junction.get("junction_core_rect")
        for junction in junction_geometries
    ]
    trim_sources = [clean(item) for item in trim_sources if item is not None]
    trim_geometry = clean(unary_union(trim_sources)) if trim_sources else MultiPolygon()

    trimmed_road_polygons: List[Any] = []
    for polygon in road_polygons:
        trimmed = polygon
        if not getattr(trim_geometry, "is_empty", True):
            trimmed = clean(trimmed.difference(trim_geometry))
        if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
            trimmed = clean(trimmed.intersection(aoi_polygon))
        if not getattr(trimmed, "is_empty", True):
            trimmed_road_polygons.append(trimmed)

    carriageway = clean(unary_union(trimmed_road_polygons)) if trimmed_road_polygons else MultiPolygon()
    placement_context.carriageway = carriageway
    placement_context.carriageway_polygon = carriageway
    placement_context.road_arm_geometries = trimmed_road_polygons

    carriageway_width_m = max(float(getattr(placement_context, "carriageway_width_m", 0.0) or 0.0), 1.0)
    left_sidewalk_width_m = max(
        float(getattr(placement_context, "required_left_width_m", 0.0) or 0.0),
        float(getattr(placement_context, "left_clear_path_width_m", 0.0) or 0.0)
        + float(getattr(placement_context, "left_furnishing_width_m", 0.0) or 0.0),
    )
    right_sidewalk_width_m = max(
        float(getattr(placement_context, "required_right_width_m", 0.0) or 0.0),
        float(getattr(placement_context, "right_clear_path_width_m", 0.0) or 0.0)
        + float(getattr(placement_context, "right_furnishing_width_m", 0.0) or 0.0),
    )
    if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
        left_sidewalk, right_sidewalk, sidewalk = build_sidewalk_zones_from_roads(
            list(roads),
            carriageway_width_m=carriageway_width_m,
            left_sidewalk_width_m=left_sidewalk_width_m,
            right_sidewalk_width_m=right_sidewalk_width_m,
            aoi_polygon=aoi_polygon,
        )
        sidewalk_trim_sources = [
            junction.get("sidewalk_trim_zone")
            for junction in junction_geometries
            if junction.get("sidewalk_trim_zone") is not None
        ]
        sidewalk_trim = clean(unary_union([clean(item) for item in sidewalk_trim_sources])) if sidewalk_trim_sources else MultiPolygon()
        if not getattr(sidewalk_trim, "is_empty", True):
            left_sidewalk = clean(left_sidewalk.difference(sidewalk_trim))
            right_sidewalk = clean(right_sidewalk.difference(sidewalk_trim))
            sidewalk = clean(sidewalk.difference(sidewalk_trim))
        placement_context.left_sidewalk_zone = left_sidewalk
        placement_context.right_sidewalk_zone = right_sidewalk
        placement_context.sidewalk_zone = sidewalk


def _sample_bezier_points(curve: BezierCurve3, steps: int = 16) -> Sequence[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    n = max(2, steps)
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        u2 = u * u
        u3 = u2 * u
        t2 = t * t
        t3 = t2 * t
        x = u3 * curve.start.x + 3 * u2 * t * curve.control1.x + 3 * u * t2 * curve.control2.x + t3 * curve.end.x
        y = u3 * curve.start.y + 3 * u2 * t * curve.control1.y + 3 * u * t2 * curve.control2.y + t3 * curve.end.y
        points.append((float(x), float(y)))
    return points


def _surface_boundary_points(surface: JunctionLaneSurface | JunctionMergedSurface, steps: int = 12) -> List[Tuple[float, float]]:
    sampled_points: List[Tuple[float, float]] = []
    for edge in getattr(surface, "edges", ()) or ():
        edge_points = _sample_bezier_points(edge.curve, steps=steps)
        if not sampled_points:
            sampled_points.extend(edge_points)
        else:
            sampled_points.extend(edge_points[1:])
    if not sampled_points:
        for node in getattr(surface, "nodes", ()) or ():
            sampled_points.append((float(node.point.x), float(node.point.y)))
    if sampled_points and sampled_points[0] != sampled_points[-1]:
        sampled_points.append(sampled_points[0])
    return sampled_points


def _surface_polygon_record(surface: JunctionLaneSurface | JunctionMergedSurface) -> Dict[str, Any] | None:
    from shapely.geometry import Polygon

    points = _surface_boundary_points(surface, steps=10)
    if len(points) < 4:
        return None
    polygon = Polygon(points)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if getattr(polygon, "is_empty", True):
        return None
    record: Dict[str, Any] = {
        "surface_id": str(surface.surface_id),
        "provenance": str(getattr(surface, "provenance", "generated") or "generated"),
        "geometry": polygon,
        "node_count": len(getattr(surface, "nodes", ()) or ()),
        "edge_count": len(getattr(surface, "edges", ()) or ()),
    }
    if isinstance(surface, JunctionLaneSurface):
        record.update(
            {
                "surface_kind": "lane",
                "lane_id": str(surface.lane_id),
                "arm_key": str(surface.arm_key),
                "flow": str(surface.flow),
                "lane_index": int(surface.lane_index),
                "skeleton_id": str(surface.skeleton_id),
            }
        )
    else:
        record.update(
            {
                "surface_kind": "merged",
                "merged_from_surface_ids": list(surface.merged_from_surface_ids),
                "merged_from_lane_ids": list(surface.merged_from_lane_ids),
            }
        )
    return record


def _build_cross_fusion_junction_geometry(
    junction: Any,
    arms: List[Dict[str, Any]],
    *,
    crosswalk_depth_m: float = 3.0,
) -> Dict[str, Any]:
    """Build junction geometry using cross strip fusion for cross_junction.

    This uses the new angle-bisector approach where:
    - Vehicle lanes go straight through as carriageway core
    - Non-vehicle strips (nearroad_furnishing, clear_sidewalk, frontage_reserve)
      bend along angle bisectors and are merged into continuous surfaces
    """
    from roadgen3d.cross_strip_fusion import build_cross_strip_fusion, cross_strip_fusion_to_junction_geometry

    try:
        fusion_result = build_cross_strip_fusion(
            junction_id=str(junction.feature_id),
            anchor_xy=(float(junction.anchor_x), float(junction.anchor_y)),
            arms=arms,
            crosswalk_depth_m=crosswalk_depth_m,
        )
        geometry = cross_strip_fusion_to_junction_geometry(fusion_result)
        # Mark as auto-generated
        geometry["generation_mode"] = "cross_strip_fusion_auto"
        return geometry
    except Exception:
        # Fallback to None, will be handled by existing logic
        return None


def _build_manual_junction_geometry(
    composition: JunctionComposition,
) -> Dict[str, Any]:
    from shapely.geometry import Polygon

    sidewalk_corner_patches: List[Dict[str, Any]] = []
    nearroad_corner_patches: List[Dict[str, Any]] = []
    frontage_corner_patches: List[Dict[str, Any]] = []
    lane_surface_patches: List[Dict[str, Any]] = []
    merged_surface_patches: List[Dict[str, Any]] = []
    sidewalk_corner_polylines: List[Dict[str, Any]] = []
    nearroad_corner_polylines: List[Dict[str, Any]] = []
    frontage_corner_polylines: List[Dict[str, Any]] = []
    quadrant_corner_kernels: List[Dict[str, Any]] = []

    strip_to_patch_bucket: Dict[str, List[List[Dict[str, Any]]]] = {
        "clear_sidewalk": sidewalk_corner_patches,
        "nearroad_furnishing": nearroad_corner_patches,
        "frontage_reserve": frontage_corner_patches,
    }
    strip_to_polyline_bucket: Dict[str, List[List[Dict[str, Any]]]] = {
        "clear_sidewalk": sidewalk_corner_polylines,
        "nearroad_furnishing": nearroad_corner_polylines,
        "frontage_reserve": frontage_corner_polylines,
    }

    for lane_surface in composition.lane_surfaces:
        record = _surface_polygon_record(lane_surface)
        if record is not None:
            lane_surface_patches.append(record)

    for merged_surface in composition.merged_surfaces:
        record = _surface_polygon_record(merged_surface)
        if record is not None:
            merged_surface_patches.append(record)

    for quadrant in composition.quadrants:
        # Build patches from bezier curves
        for patch in quadrant.patches:
            inner_pts = _sample_bezier_points(patch.inner_curve, steps=12)
            outer_pts = _sample_bezier_points(patch.outer_curve, steps=12)
            # Closed ring: inner from start->end, then outer from end->start
            ring = list(inner_pts) + list(reversed(outer_pts))
            if len(ring) >= 3:
                poly = Polygon(ring)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not getattr(poly, "is_empty", True):
                    bucket = strip_to_patch_bucket.get(patch.strip_kind)
                    if bucket is not None:
                        bucket.append({"patch_id": patch.patch_id, "geometry": poly})

        # Build polylines from skeleton lines
        for skeleton in quadrant.skeleton_lines:
            pts = _sample_bezier_points(skeleton.curve, steps=12)
            bucket = strip_to_polyline_bucket.get(skeleton.strip_kind)
            if bucket is not None:
                bucket.append({
                    "polyline_id": skeleton.line_id,
                    "quadrant_id": quadrant.quadrant_id,
                    "kernel_id": f"{quadrant.quadrant_id}_kernel",
                    "points_xy": [[float(x), float(y)] for x, y in pts],
                    "width_m": round(float(skeleton.width_m), 3),
                })

        # Build a simplified quadrant_corner_kernel from the first patch (sidewalk preferred)
        canonical_patch = next(
            (p for p in quadrant.patches if p.strip_kind == "clear_sidewalk"),
            quadrant.patches[0] if quadrant.patches else None,
        )
        if canonical_patch is not None:
            inner_pts = _sample_bezier_points(canonical_patch.inner_curve, steps=8)
            quadrant_corner_kernels.append({
                "kernel_id": f"{quadrant.quadrant_id}_kernel",
                "quadrant_id": quadrant.quadrant_id,
                "road_a_id": 0,
                "road_b_id": 0,
                "centerline_a_id": quadrant.arm_a_id,
                "centerline_b_id": quadrant.arm_b_id,
                "kernel_kind": "polyline_fallback",
                "center_xy": [0.0, 0.0],
                "radius_m": 0.0,
                "start_xy": [float(canonical_patch.inner_curve.start.x), float(canonical_patch.inner_curve.start.y)],
                "end_xy": [float(canonical_patch.inner_curve.end.x), float(canonical_patch.inner_curve.end.y)],
                "start_heading_deg": 0.0,
                "end_heading_deg": 0.0,
                "clockwise": None,
                "sampled_points_xy": [[float(x), float(y)] for x, y in inner_pts],
            })

    geometry: Dict[str, Any] = {
        "junction_id": composition.junction_id,
        "kind": composition.kind,
        "quadrant_corner_kernels": quadrant_corner_kernels,
        "sidewalk_corner_polylines": sidewalk_corner_polylines,
        "nearroad_corner_polylines": nearroad_corner_polylines,
        "frontage_corner_polylines": frontage_corner_polylines,
        "sidewalk_corner_patches": sidewalk_corner_patches,
        "nearroad_corner_patches": nearroad_corner_patches,
        "frontage_corner_patches": frontage_corner_patches,
        "lane_surface_patches": lane_surface_patches,
        "merged_surface_patches": merged_surface_patches,
    }
    return geometry


def _try_build_cross_fusion_for_junction(
    annotation: ReferenceAnnotation,
    junction_geom: Dict[str, Any],
    road_segment_graph: Any | None,
    roads: Sequence[OsmRoad] = (),
) -> Dict[str, Any] | None:
    """Try to build cross fusion geometry for a junction.

    Returns None if fusion fails, in which case the caller should use the original geometry.
    """
    from roadgen3d.cross_strip_fusion import build_cross_strip_fusion, cross_strip_fusion_to_junction_geometry

    junction_id = str(junction_geom.get("junction_id", "") or "")
    anchor_xy = junction_geom.get("anchor_xy", [0.0, 0.0])
    if len(anchor_xy) < 2:
        return None

    # Try to get arms data from road_segment_graph
    arms = _extract_junction_arms_from_graph(road_segment_graph, junction_id, anchor_xy, roads=roads)
    if arms is None or len(arms) < 4:
        return None

    try:
        fusion_result = build_cross_strip_fusion(
            junction_id=junction_id,
            anchor_xy=(float(anchor_xy[0]), float(anchor_xy[1])),
            arms=arms,
            crosswalk_depth_m=3.0,
        )
        geometry = cross_strip_fusion_to_junction_geometry(fusion_result)
        # Preserve the original junction_id and kind
        geometry["junction_id"] = junction_id
        geometry["kind"] = "cross_junction"
        geometry["generation_mode"] = "cross_strip_fusion_auto"
        # Preserve arm count from original
        geometry["arm_count"] = len(arms)
        return geometry
    except Exception:
        return None


def _extract_junction_arms_from_graph(
    road_segment_graph: Any | None,
    junction_id: str,
    anchor_xy: List[float],
    *,
    roads: Sequence[OsmRoad] = (),
) -> List[Dict[str, Any]] | None:
    """Extract arms data from road_segment_graph for a specific junction."""
    if road_segment_graph is None:
        return None

    from roadgen3d.placement_zones import _road_profile_widths_from_graph

    # Find the junction in the graph
    junctions = list(getattr(road_segment_graph, "junctions", ()) or ())
    junction_obj = None
    for j in junctions:
        if str(getattr(j, "junction_id", "") or "") == junction_id:
            junction_obj = j
            break
    if junction_obj is None:
        anchor = (float(anchor_xy[0]), float(anchor_xy[1]))
        candidates = [
            j
            for j in junctions
            if str(getattr(j, "kind", "") or "") == "cross_junction"
            and math.hypot(
                anchor[0] - float(tuple(getattr(j, "anchor_xy", (0.0, 0.0)) or (0.0, 0.0))[0]),
                anchor[1] - float(tuple(getattr(j, "anchor_xy", (0.0, 0.0)) or (0.0, 0.0))[1]),
            ) <= 0.75
        ]
        if candidates:
            junction_obj = candidates[0]

    if junction_obj is None:
        return None

    # Get road profiles
    road_profiles = _road_profile_widths_from_graph(road_segment_graph)

    # Build arms data similar to _build_explicit_graph_junction_geometries
    roads_by_id = {}
    for road in roads or getattr(road_segment_graph, "roads", ()) or ():
        road_id = int(getattr(road, "osm_id", 0) or 0)
        if road_id > 0:
            roads_by_id[road_id] = road

    anchor = (float(anchor_xy[0]), float(anchor_xy[1]))
    def point_distance(point: Tuple[float, float], other: Tuple[float, float]) -> float:
        return math.hypot(float(point[0]) - float(other[0]), float(point[1]) - float(other[1]))

    def angle_deg(point: Tuple[float, float], other: Tuple[float, float]) -> float:
        value = math.degrees(math.atan2(float(other[1]) - float(point[1]), float(other[0]) - float(point[0])))
        return value + 360.0 if value < 0.0 else value

    arms: List[Dict[str, Any]] = []
    seen_road_ids: set[int] = set()

    connected_road_ids = tuple(getattr(junction_obj, "connected_road_ids", ()) or ())
    connected_centerline_ids = tuple(getattr(junction_obj, "connected_centerline_ids", ()) or ())

    for road_id, centerline_id in zip(connected_road_ids, connected_centerline_ids):
        road_id = int(road_id)
        if road_id <= 0 or road_id in seen_road_ids:
            continue
        road = roads_by_id.get(road_id)
        if road is None:
            continue
        points = list(getattr(road, "coords", ()) or ())
        if len(points) < 2:
            continue

        # Find the neighbor point closest to anchor
        if point_distance(anchor, points[0]) <= point_distance(anchor, points[-1]):
            neighbor = points[1] if len(points) > 1 else points[0]
        else:
            neighbor = points[-2] if len(points) > 1 else points[0]

        length_m = math.hypot(float(neighbor[0]) - anchor[0], float(neighbor[1]) - anchor[1])
        if length_m <= 1e-6:
            continue

        tangent = (
            (float(neighbor[0]) - anchor[0]) / length_m,
            (float(neighbor[1]) - anchor[1]) / length_m,
        )
        profile = road_profiles.get(road_id, {})

        arms.append({
            "road_id": road_id,
            "centerline_id": str(centerline_id),
            "angle_deg": angle_deg(anchor, neighbor),
            "tangent": tangent,
            "normal": (float(tangent[1]), float(-tangent[0])),
            "carriageway_width_m": max(float(profile.get("carriageway_width_m", 8.0) or 8.0), 1.0),
            "nearroad_furnishing_width_m": float(profile.get("nearroad_furnishing_width_m", 0.0) or 0.0),
            "clear_sidewalk_width_m": float(profile.get("clear_sidewalk_width_m", 0.0) or 0.0),
            "frontage_reserve_width_m": float(profile.get("frontage_reserve_width_m", 0.0) or 0.0),
            "side_strip_layouts": dict(profile.get("side_strip_layouts", {}) or {}),
            "center_strip_layouts": list(profile.get("center_strip_layouts", []) or ()),
        })
        seen_road_ids.add(road_id)

    return arms if len(arms) >= 4 else None


def _apply_manual_junction_compositions(
    annotation: ReferenceAnnotation,
    junction_geometries: List[Dict[str, Any]],
    *,
    road_segment_graph: Any | None = None,
    roads: Sequence[OsmRoad] = (),
) -> List[Dict[str, Any]]:
    manual_by_junction_id = {comp.junction_id: comp for comp in annotation.junction_compositions} if annotation.junction_compositions else {}

    # Pre-compute arms data for cross junctions without manual compositions
    arms_by_junction_id: Dict[str, List[Dict[str, Any]]] = {}
    if road_segment_graph is not None and not manual_by_junction_id:
        # Only pre-compute if there are manual compositions to apply,
        # because we only need fusion for cross_junctions without manual compositions
        pass  # Lazy computation below

    result: List[Dict[str, Any]] = []
    seen_junction_ids: set[str] = set()
    for geom in junction_geometries:
        junction_id = str(geom.get("junction_id", "") or "")
        kind = str(geom.get("kind", "") or "")

        if junction_id in manual_by_junction_id:
            manual_geom = _build_manual_junction_geometry(manual_by_junction_id[junction_id])
            # Preserve fields that manual geom doesn't provide (core rect, crosswalks, etc.)
            merged = {**geom, **manual_geom}
            result.append(merged)
            seen_junction_ids.add(junction_id)
        elif kind == "cross_junction" and junction_id not in manual_by_junction_id:
            # Try to generate geometry using cross strip fusion
            fusion_geom = _try_build_cross_fusion_for_junction(
                annotation, geom, road_segment_graph, roads
            )
            if fusion_geom is not None:
                result.append({**geom, **fusion_geom})
            else:
                result.append(geom)
        else:
            result.append(geom)

    for junction_id, comp in manual_by_junction_id.items():
        if junction_id in seen_junction_ids:
            continue
        result.append(_build_manual_junction_geometry(comp))
    return result


__all__ = [
    "ANNOTATION_SCENE_BBOX_PADDING_M",
    "ReferenceAnnotationSceneBridgeResult",
    "build_reference_annotation_scene_bridge",
]
