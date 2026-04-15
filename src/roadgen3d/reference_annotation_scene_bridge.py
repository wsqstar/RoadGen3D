"""Bridge reference annotations into the corridor scene/export pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .osm_ingest import OsmRoad, ProjectedFeatures
from .placement_zones import PlacementContext, build_placement_context
from .reference_annotation import (
    BezierCurve3,
    JunctionComposition,
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


def _build_manual_junction_geometry(
    composition: JunctionComposition,
) -> Dict[str, Any]:
    from shapely.geometry import Polygon

    sidewalk_corner_patches: List[Dict[str, Any]] = []
    nearroad_corner_patches: List[Dict[str, Any]] = []
    frontage_corner_patches: List[Dict[str, Any]] = []
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
    }
    return geometry


def _apply_manual_junction_compositions(
    annotation: ReferenceAnnotation,
    junction_geometries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not annotation.junction_compositions:
        return junction_geometries

    manual_by_junction_id = {comp.junction_id: comp for comp in annotation.junction_compositions}
    result: List[Dict[str, Any]] = []
    for geom in junction_geometries:
        junction_id = str(geom.get("junction_id", "") or "")
        if junction_id in manual_by_junction_id:
            manual_geom = _build_manual_junction_geometry(manual_by_junction_id[junction_id])
            # Preserve fields that manual geom doesn't provide (core rect, crosswalks, etc.)
            merged = {**geom, **manual_geom}
            result.append(merged)
        else:
            result.append(geom)
    return result


__all__ = [
    "ANNOTATION_SCENE_BBOX_PADDING_M",
    "ReferenceAnnotationSceneBridgeResult",
    "build_reference_annotation_scene_bridge",
]
