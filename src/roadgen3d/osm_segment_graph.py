"""Build discrete road-segment graphs from projected OSM features."""

from __future__ import annotations

import math
from typing import List, Mapping, Sequence, Tuple

from .osm_semantics import nearest_semantic_block, road_length_m, semantic_profile_for_segment
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


def _point_at_station(coords: Sequence[Tuple[float, float]], station_m: float) -> Tuple[float, float]:
    if not coords:
        return (0.0, 0.0)
    if len(coords) == 1:
        return (float(coords[0][0]), float(coords[0][1]))
    target = max(0.0, float(station_m))
    travelled = 0.0
    for idx in range(len(coords) - 1):
        start = (float(coords[idx][0]), float(coords[idx][1]))
        end = (float(coords[idx + 1][0]), float(coords[idx + 1][1]))
        span = _distance(start, end)
        if span <= 1e-9:
            continue
        if travelled + span >= target:
            return _interpolate(start, end, (target - travelled) / span)
        travelled += span
    last = coords[-1]
    return (float(last[0]), float(last[1]))


def _resampled_polyline_segments(
    coords: Sequence[Tuple[float, float]],
    *,
    segment_length_m: float,
) -> Tuple[Tuple[Tuple[float, float], Tuple[float, float], int, int], ...]:
    total_length = sum(_distance(coords[idx], coords[idx + 1]) for idx in range(max(0, len(coords) - 1)))
    if total_length <= 1e-6:
        return tuple()
    segment_count = max(1, int(math.ceil(total_length / max(float(segment_length_m), 1.0))))
    return tuple(
        (
            _point_at_station(coords, total_length * float(idx) / float(segment_count)),
            _point_at_station(coords, total_length * float(idx + 1) / float(segment_count)),
            idx,
            segment_count,
        )
        for idx in range(segment_count)
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
    short_road_policy = str(getattr(config, "osm_short_road_policy", "semantic") or "semantic").strip().lower()
    short_road_min_length_m = max(float(getattr(config, "osm_short_road_min_length_m", 0.0) or 0.0), 0.0)

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
        road_total_length_m = road_length_m(road)
        short_default_style = (
            semantic_enabled
            and short_road_policy == "default_style"
            and short_road_min_length_m > 0.0
            and road_total_length_m < short_road_min_length_m
        )
        if semantic_enabled:
            segment_specs = _resampled_polyline_segments(coords, segment_length_m=segment_length)
        else:
            legacy_specs: List[Tuple[Tuple[float, float], Tuple[float, float], int, int]] = []
            for coord_idx in range(len(coords) - 1):
                start = tuple(coords[coord_idx])
                end = tuple(coords[coord_idx + 1])
                length = _distance(start, end)
                if length <= 1e-6:
                    continue
                subdivisions = max(1, int(math.ceil(length / segment_length)))
                legacy_specs.extend(
                    (
                        _interpolate(start, end, float(part_idx) / float(subdivisions)),
                        _interpolate(start, end, float(part_idx + 1) / float(subdivisions)),
                        part_idx,
                        subdivisions,
                    )
                    for part_idx in range(subdivisions)
                )
            segment_specs = tuple(legacy_specs)

        for a, b, part_idx, subdivisions in segment_specs:
            center = ((float(a[0]) + float(b[0])) / 2.0, (float(a[1]) + float(b[1])) / 2.0)
            poi_types = () if short_default_style else _nearest_poi_types(
                center,
                poi_points_by_type=poi_points_by_type,
            )
            semantic_block = None if short_default_style else (nearest_semantic_block(center, semantic_blocks) if semantic_enabled else None)
            if short_default_style:
                semantic_profile_id, semantic_reasons, semantic_confidence, semantic_block_id = (
                    "",
                    ("short road rendered with default style",),
                    0.0,
                    "",
                )
            elif semantic_enabled:
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
                    "is_junction": (part_idx == 0 or part_idx == subdivisions - 1),
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
                    "road_width_m": float(getattr(road, "width_m", 0.0) or getattr(config, "road_width_m", 0.0) or 0.0),
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
            road_width_m=float(spec["road_width_m"]),
            bands=tuple(spec["bands"]),
            station_start_m=float(spec["station_start_m"]) - half_length,
            station_end_m=float(spec["station_end_m"]) - half_length,
            station_center_m=float(spec["station_center_m"]) - half_length,
        )
        for spec in node_specs
    ]
    return RoadSegmentGraph(nodes=tuple(nodes), edges=tuple(edges), mode=str(getattr(config, "layout_mode", "osm") or "osm"))
