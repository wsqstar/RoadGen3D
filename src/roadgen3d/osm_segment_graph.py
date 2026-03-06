"""Build discrete road-segment graphs from projected OSM features."""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

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
    entrances: Sequence[Tuple[float, float]],
    bus_stops: Sequence[Tuple[float, float]],
    fire_points: Sequence[Tuple[float, float]],
    threshold_m: float = 18.0,
) -> Tuple[str, ...]:
    poi_types: List[str] = []
    if entrances and min(_distance(point, item) for item in entrances) <= threshold_m:
        poi_types.append("entrance")
    if bus_stops and min(_distance(point, item) for item in bus_stops) <= threshold_m:
        poi_types.append("bus_stop")
    if fire_points and min(_distance(point, item) for item in fire_points) <= threshold_m:
        poi_types.append("fire")
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
    entrances = tuple(getattr(projected_features, "entrances", []))
    bus_stops = tuple(getattr(projected_features, "bus_stops", []))
    fire_points = tuple(getattr(projected_features, "fire_points", []))
    segment_length = max(float(getattr(config, "segment_length_m", 12.0)), 4.0)

    nodes: List[RoadSegmentNode] = []
    edges: List[RoadSegmentEdge] = []
    last_segment_by_road: dict[int, str] = {}
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
                    entrances=entrances,
                    bus_stops=bus_stops,
                    fire_points=fire_points,
                )
                segment_id = f"seg_{segment_counter:04d}"
                segment_counter += 1
                node = RoadSegmentNode(
                    segment_id=segment_id,
                    road_id=road_id,
                    start_xy=(float(a[0]), float(a[1])),
                    end_xy=(float(b[0]), float(b[1])),
                    center_xy=center,
                    length_m=float(_distance(a, b)),
                    is_junction=(coord_idx == 0 or coord_idx == len(coords) - 2 or part_idx == 0 or part_idx == subdivisions - 1),
                    is_accessible=True,
                    poi_types=poi_types,
                    bands=_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types),
                )
                nodes.append(node)
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

    return RoadSegmentGraph(nodes=tuple(nodes), edges=tuple(edges), mode="osm")
