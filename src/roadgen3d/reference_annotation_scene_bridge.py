"""Bridge reference annotations into the corridor scene/export pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .osm_ingest import OsmRoad, ProjectedFeatures
from .placement_zones import PlacementContext, build_placement_context
from .reference_annotation import (
    ReferenceAnnotation,
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
    build_segment_graph_from_annotation,
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


__all__ = [
    "ANNOTATION_SCENE_BBOX_PADDING_M",
    "ReferenceAnnotationSceneBridgeResult",
    "build_reference_annotation_scene_bridge",
]
