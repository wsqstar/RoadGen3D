"""Bridge MetaUrban procedural graphs into the corridor scene/export pipeline."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .metaurban_procedural import (
    SUPPORTED_METAURBAN_BLOCKS,
    MetaUrbanProceduralConfig,
    build_metaurban_reference_config,
    build_metaurban_segment_graph,
    compute_metaurban_plan_metrics,
    get_metaurban_reference_plan,
    resolve_metaurban_block_sequence,
)
from .osm_ingest import OsmRoad, ProjectedFeatures
from .placement_zones import PlacementContext, build_placement_context
from .types import RoadSegmentGraph, RoadSegmentNode, StreetComposeConfig

METAURBAN_SCENE_BBOX_PADDING_M = 42.0


@dataclass(frozen=True)
class MetaUrbanSceneBridgeResult:
    """Synthetic corridor context derived from a MetaUrban procedural graph."""

    procedural_config: MetaUrbanProceduralConfig
    road_segment_graph: RoadSegmentGraph
    projected_features: ProjectedFeatures
    placement_context: PlacementContext
    reference_plan: Any
    evaluation: Dict[str, float]
    summary_metadata: Dict[str, Any]


def _dedupe_adjacent_points(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    deduped: List[Tuple[float, float]] = []
    for point in points:
        xy = (float(point[0]), float(point[1]))
        if not deduped or deduped[-1] != xy:
            deduped.append(xy)
    return deduped


def _polyline_is_closed(points: Sequence[Tuple[float, float]]) -> bool:
    if len(points) < 3:
        return False
    first = points[0]
    last = points[-1]
    return abs(float(first[0]) - float(last[0])) <= 1e-6 and abs(float(first[1]) - float(last[1])) <= 1e-6


def _grouped_polyline(nodes: Sequence[RoadSegmentNode]) -> List[Tuple[float, float]]:
    if not nodes:
        return []
    ordered = sorted(
        nodes,
        key=lambda node: (
            float(getattr(node, "station_start_m", 0.0)),
            str(getattr(node, "segment_id", "")),
        ),
    )
    points: List[Tuple[float, float]] = [tuple(float(v) for v in ordered[0].start_xy)]
    points.extend(tuple(float(v) for v in node.end_xy) for node in ordered)
    return _dedupe_adjacent_points(points)


def _nodes_to_segment_roads(
    nodes: Sequence[RoadSegmentNode],
    *,
    width_m: float,
    next_osm_id: int,
) -> Tuple[List[OsmRoad], int]:
    roads: List[OsmRoad] = []
    counter = int(next_osm_id)
    for node in nodes:
        coords = _dedupe_adjacent_points(
            [
                tuple(float(v) for v in node.start_xy),
                tuple(float(v) for v in node.end_xy),
            ]
        )
        if len(coords) < 2:
            continue
        roads.append(
            OsmRoad(
                osm_id=counter,
                highway_type=str(getattr(node, "highway_type", "") or "metaurban_segment"),
                coords=coords,
                width_m=float(width_m),
            )
        )
        counter += 1
    return roads, counter


def _graph_to_synthetic_roads(
    graph: RoadSegmentGraph,
    *,
    road_width_m: float,
) -> List[OsmRoad]:
    grouped: Dict[int, List[RoadSegmentNode]] = defaultdict(list)
    for node in graph.nodes:
        grouped[int(getattr(node, "road_id", 0) or 0)].append(node)

    roads: List[OsmRoad] = []
    synthetic_osm_id = 100000
    for road_id in sorted(grouped):
        nodes = grouped[road_id]
        coords = _grouped_polyline(nodes)
        if len(coords) >= 2 and not _polyline_is_closed(coords):
            ordered_nodes = sorted(
                nodes,
                key=lambda node: (
                    float(getattr(node, "station_start_m", 0.0)),
                    str(getattr(node, "segment_id", "")),
                ),
            )
            roads.append(
                OsmRoad(
                    osm_id=int(road_id) if road_id else synthetic_osm_id,
                    highway_type=str(getattr(ordered_nodes[0], "highway_type", "") or "metaurban_road"),
                    coords=list(coords),
                    width_m=float(road_width_m),
                )
            )
            if not road_id:
                synthetic_osm_id += 1
            continue
        fallback_roads, synthetic_osm_id = _nodes_to_segment_roads(
            nodes,
            width_m=float(road_width_m),
            next_osm_id=synthetic_osm_id,
        )
        roads.extend(fallback_roads)
    return roads


def _graph_bbox(
    graph: RoadSegmentGraph,
    *,
    padding_m: float,
) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []
    for node in graph.nodes:
        xs.extend([float(node.start_xy[0]), float(node.end_xy[0])])
        ys.extend([float(node.start_xy[1]), float(node.end_xy[1])])
    if not xs or not ys:
        pad = float(max(padding_m, 10.0))
        return (-pad, -pad, pad, pad)
    return (
        float(min(xs) - padding_m),
        float(min(ys) - padding_m),
        float(max(xs) + padding_m),
        float(max(ys) + padding_m),
    )


def build_metaurban_scene_bridge(
    compose_config: StreetComposeConfig,
    *,
    plan_id: str,
    procedural_config: MetaUrbanProceduralConfig | None = None,
) -> MetaUrbanSceneBridgeResult:
    """Build synthetic corridor geometry/context for a MetaUrban reference plan."""

    reference_plan = get_metaurban_reference_plan(plan_id)
    resolved_procedural_config = procedural_config or build_metaurban_reference_config(
        reference_plan.plan_id,
        lane_count=max(int(compose_config.lane_count), 1),
        sidewalk_width_m=float(compose_config.sidewalk_width_m),
        lane_width_m=float(max(float(compose_config.road_width_m) / max(int(compose_config.lane_count), 1), 2.8)),
        segment_length_m=float(max(4.0, getattr(compose_config, "segment_length_m", 12.0))),
    )
    road_segment_graph = build_metaurban_segment_graph(resolved_procedural_config)
    synthetic_roads = _graph_to_synthetic_roads(
        road_segment_graph,
        road_width_m=float(compose_config.road_width_m),
    )
    padding_m = max(
        float(METAURBAN_SCENE_BBOX_PADDING_M),
        float(compose_config.road_width_m) * 0.5 + float(compose_config.sidewalk_width_m) * 2.0 + 35.0,
    )
    projected_features = ProjectedFeatures(
        roads=list(synthetic_roads),
        buildings=[],
        entrances=[],
        bus_stops=[],
        fire_points=[],
        poi_points_by_type={},
        bbox_m=_graph_bbox(road_segment_graph, padding_m=float(padding_m)),
        origin_utm=(0.0, 0.0),
        utm_epsg=0,
    )
    placement_context = build_placement_context(
        projected_features,
        compose_config,
        road_segment_graph=road_segment_graph,
    )
    evaluation = compute_metaurban_plan_metrics(road_segment_graph)
    block_sequence = resolve_metaurban_block_sequence(
        resolved_procedural_config,
        rng=random.Random(int(resolved_procedural_config.seed)),
    )
    summary_metadata: Dict[str, Any] = {
        **road_segment_graph.summary(),
        **evaluation,
        "generator": "metaurban_procedural_v1",
        "generation_stage": "scene_export",
        "layout_mode": "metaurban",
        "reference_plan_id": reference_plan.plan_id,
        "reference_plan_label": reference_plan.label,
        "reference_plan_description": reference_plan.description,
        "reference_plan_image_path": str(reference_plan.image_path),
        "block_sequence": str(block_sequence),
        "supported_block_types": list(SUPPORTED_METAURBAN_BLOCKS),
        "synthetic_road_count": int(len(projected_features.roads)),
        "road_selection_requested": "metaurban_reference_plan",
        "road_selection_used": "metaurban_reference_plan",
        "road_selection_fallback_reason": "",
    }
    return MetaUrbanSceneBridgeResult(
        procedural_config=resolved_procedural_config,
        road_segment_graph=road_segment_graph,
        projected_features=projected_features,
        placement_context=placement_context,
        reference_plan=reference_plan,
        evaluation=evaluation,
        summary_metadata=summary_metadata,
    )


__all__ = [
    "METAURBAN_SCENE_BBOX_PADDING_M",
    "MetaUrbanSceneBridgeResult",
    "build_metaurban_scene_bridge",
]
