"""Build discrete road-segment graphs from projected OSM features."""

from __future__ import annotations

import math
from typing import List, Mapping, Sequence, Tuple

from .osm_semantics import nearest_semantic_block, semantic_profile_for_segment
from .poi_taxonomy import extract_poi_points_by_type
from .street_priors import DEFAULT_CATEGORIES
from .types import RoadSegmentBand, RoadSegmentEdge, RoadSegmentGraph, RoadSegmentNode, StreetComposeConfig


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _interpolate(a: Tuple[float, float], b: Tuple[float, float], ratio: float) -> Tuple[float, float]:
    ratio = max(0.0, min(float(ratio), 1.0))
    return (
        float(a[0]) + (float(b[0]) - float(a[0])) * ratio,
        float(a[1]) + (float(b[1]) - float(a[1])) * ratio,
    )


def _nearest_poi_types(
    point: Tuple[float, float],
    *,
    poi_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
    threshold_m: float = 18.0,
) -> Tuple[str, ...]:
    poi_types: List[str] = []
    for poi_type, points in poi_points_by_type.items():
        if points and min(_distance(point, item) for item in points) <= threshold_m:
            poi_types.append(str(poi_type))
    return tuple(sorted(set(poi_types)))


def _segment_bands(
    *,
    segment_id: str,
    config: StreetComposeConfig,
    poi_types: Sequence[str],
) -> Tuple[RoadSegmentBand, ...]:
    edge_kind = "right_transit_edge" if str(config.design_rule_profile).strip().lower() == "transit_priority_v1" else "right_furnishing"
    common_allowed = tuple(DEFAULT_CATEGORIES)
    return (
        RoadSegmentBand(
            band_id=f"{segment_id}_left",
            segment_id=segment_id,
            side="left",
            kind="left_furnishing",
            width_m=float(config.sidewalk_width_m),
            allowed_categories=common_allowed,
            nearest_poi_types=tuple(poi_types),
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_right",
            segment_id=segment_id,
            side="right",
            kind=edge_kind,
            width_m=float(config.sidewalk_width_m),
            allowed_categories=common_allowed,
            nearest_poi_types=tuple(poi_types),
        ),
    )


def build_segment_graph(
    projected_features: object,
    config: StreetComposeConfig,
) -> RoadSegmentGraph:
    """Convert projected OSM roads into a segment graph for discrete layout solving."""

    roads = list(getattr(projected_features, "roads", []))
    poi_points_by_type = {
        poi_type: tuple(points)
        for poi_type, points in extract_poi_points_by_type(projected_features).items()
    }
    semantic_blocks = tuple(getattr(projected_features, "semantic_blocks", ()) or ())
    semantic_enabled = str(getattr(config, "layout_mode", "") or "").strip().lower() == "osm_multiblock"
    segment_length = max(float(getattr(config, "segment_length_m", 12.0)), 4.0)

    node_specs: List[dict] = []
    edges: List[RoadSegmentEdge] = []
    last_segment_by_road: dict[int, str] = {}
    cumulative_station_m = 0.0
    segment_counter = 0
    edge_counter = 0

    for road in roads:
        coords = list(getattr(road, "coords", []))
        if len(coords) < 2:
            continue
        road_id = int(getattr(road, "osm_id", segment_counter))
        for coord_idx in range(len(coords) - 1):
            start = tuple(coords[coord_idx])
            end = tuple(coords[coord_idx + 1])
            length = _distance(start, end)
            if length <= 1e-6:
                continue
            subdivisions = max(1, int(math.ceil(length / segment_length)))
            for part_idx in range(subdivisions):
                a = _interpolate(start, end, float(part_idx) / float(subdivisions))
                b = _interpolate(start, end, float(part_idx + 1) / float(subdivisions))
                center = ((float(a[0]) + float(b[0])) / 2.0, (float(a[1]) + float(b[1])) / 2.0)
                poi_types = _nearest_poi_types(
                    center,
                    poi_points_by_type=poi_points_by_type,
                )
                semantic_block = nearest_semantic_block(center, semantic_blocks) if semantic_enabled else None
                if semantic_enabled:
                    semantic_profile_id, semantic_reasons, semantic_confidence, semantic_block_id = semantic_profile_for_segment(
                        highway_type=str(getattr(road, "highway_type", "")),
                        poi_types=poi_types,
                        semantic_block=semantic_block,
                    )
                else:
                    semantic_profile_id, semantic_reasons, semantic_confidence, semantic_block_id = "", (), 0.0, ""
                segment_id = f"seg_{segment_counter:04d}"
                segment_counter += 1
                segment_length_m = float(_distance(a, b))
                station_start_m = float(cumulative_station_m)
                station_end_m = float(cumulative_station_m + segment_length_m)
                node_specs.append(
                    {
                        "segment_id": segment_id,
                        "road_id": road_id,
                        "start_xy": (float(a[0]), float(a[1])),
                        "end_xy": (float(b[0]), float(b[1])),
                        "center_xy": center,
                        "length_m": segment_length_m,
                        "is_junction": (coord_idx == 0 or coord_idx == len(coords) - 2 or part_idx == 0 or part_idx == subdivisions - 1),
                        "is_accessible": True,
                        "highway_type": str(getattr(road, "highway_type", "")),
                        "poi_types": poi_types,
                        "semantic_profile_id": semantic_profile_id,
                        "semantic_reasons": semantic_reasons,
                        "semantic_confidence": semantic_confidence,
                        "semantic_block_id": semantic_block_id,
                        "bands": _segment_bands(segment_id=segment_id, config=config, poi_types=poi_types),
                        "station_start_m": station_start_m,
                        "station_end_m": station_end_m,
                        "station_center_m": (station_start_m + station_end_m) / 2.0,
                    }
                )
                cumulative_station_m += segment_length_m
                previous_segment = last_segment_by_road.get(road_id)
                if previous_segment is not None:
                    edges.append(
                        RoadSegmentEdge(
                            edge_id=f"edge_{edge_counter:04d}",
                            from_segment_id=previous_segment,
                            to_segment_id=segment_id,
                            weight=1.0,
                        )
                    )
                    edge_counter += 1
                last_segment_by_road[road_id] = segment_id

    half_length = float(cumulative_station_m) / 2.0
    nodes: List[RoadSegmentNode] = [
        RoadSegmentNode(
            segment_id=str(spec["segment_id"]),
            road_id=int(spec["road_id"]),
            start_xy=tuple(spec["start_xy"]),
            end_xy=tuple(spec["end_xy"]),
            center_xy=tuple(spec["center_xy"]),
            length_m=float(spec["length_m"]),
            is_junction=bool(spec["is_junction"]),
            is_accessible=bool(spec["is_accessible"]),
            highway_type=str(spec["highway_type"]),
            poi_types=tuple(spec["poi_types"]),
            semantic_profile_id=str(spec["semantic_profile_id"]),
            semantic_reasons=tuple(spec["semantic_reasons"]),
            semantic_confidence=float(spec["semantic_confidence"]),
            semantic_block_id=str(spec["semantic_block_id"]),
            bands=tuple(spec["bands"]),
            station_start_m=float(spec["station_start_m"]) - half_length,
            station_end_m=float(spec["station_end_m"]) - half_length,
            station_center_m=float(spec["station_center_m"]) - half_length,
        )
        for spec in node_specs
    ]
    return RoadSegmentGraph(nodes=tuple(nodes), edges=tuple(edges), mode=str(getattr(config, "layout_mode", "osm") or "osm"))
