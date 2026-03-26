"""RoadGen3D-native port of MetaUrban's procedural road block grammar.

The original MetaUrban generator builds maps through BIG -> PGBlock ->
NodeRoadNetwork. That pipeline is tightly coupled to Panda3D/runtime objects,
so this module ports the useful block-sequence layer into a pure Python graph
generator that emits RoadGen3D ``RoadSegmentGraph`` payloads.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from .types import RoadSegmentBand, RoadSegmentEdge, RoadSegmentGraph, RoadSegmentNode

METAURBAN_V2_BLOCK_WEIGHTS: Dict[str, float] = {
    "C": 0.40,  # Curve
    "S": 0.20,  # Straight
    "X": 0.15,  # StdInterSection
    "T": 0.15,  # StdTInterSection
}
SUPPORTED_METAURBAN_BLOCKS: Tuple[str, ...] = tuple(METAURBAN_V2_BLOCK_WEIGHTS.keys())
DEFAULT_ALLOWED_CATEGORIES: Tuple[str, ...] = (
    "bench",
    "lamp",
    "trash",
    "tree",
    "bus_stop",
    "mailbox",
    "hydrant",
    "bollard",
)


@dataclass(frozen=True)
class MetaUrbanProceduralConfig:
    """Configuration for the RoadGen3D-side MetaUrban grammar port."""

    seed: int = 42
    block_count: int = 6
    block_sequence: str = ""
    lane_count: int = 2
    lane_width_m: float = 3.5
    sidewalk_width_m: float = 2.5
    segment_length_m: float = 12.0
    entrance_length_m: float = 10.0
    straight_length_m: float = 28.0
    curve_radius_m: float = 16.0
    curve_angle_deg: float = 60.0
    intersection_span_m: float = 18.0
    branch_length_m: float = 24.0
    start_heading_deg: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _normalize_block_sequence(value: str) -> str:
    tokens = "".join(ch for ch in str(value or "").upper() if ch.isalpha())
    if tokens.startswith("I"):
        tokens = tokens[1:]
    invalid = sorted({token for token in tokens if token not in SUPPORTED_METAURBAN_BLOCKS})
    if invalid:
        raise ValueError(
            "Unsupported MetaUrban block tokens: "
            f"{', '.join(invalid)}. Supported tokens: {', '.join(SUPPORTED_METAURBAN_BLOCKS)}."
        )
    return tokens


def sample_metaurban_block_sequence(
    block_count: int,
    *,
    rng: random.Random | None = None,
) -> str:
    """Sample a MetaUrban-style block sequence from the v2 prior."""

    count = max(int(block_count), 1)
    sampler = rng or random.Random(42)
    tokens = list(SUPPORTED_METAURBAN_BLOCKS)
    weights = [float(METAURBAN_V2_BLOCK_WEIGHTS[token]) for token in tokens]
    return "".join(sampler.choices(tokens, weights=weights, k=count))


def resolve_metaurban_block_sequence(
    config: MetaUrbanProceduralConfig,
    *,
    rng: random.Random | None = None,
) -> str:
    """Return the explicit or sampled block sequence for a config."""

    normalized = _normalize_block_sequence(config.block_sequence)
    if normalized:
        return normalized
    return sample_metaurban_block_sequence(config.block_count, rng=rng)


def _advance(point: Tuple[float, float], heading_rad: float, length_m: float) -> Tuple[float, float]:
    return (
        float(point[0]) + float(length_m) * math.cos(float(heading_rad)),
        float(point[1]) + float(length_m) * math.sin(float(heading_rad)),
    )


def _heading_left(heading_rad: float) -> float:
    return float(heading_rad) + math.pi / 2.0


def _heading_right(heading_rad: float) -> float:
    return float(heading_rad) - math.pi / 2.0


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def _subdivide_line(
    start_xy: Tuple[float, float],
    end_xy: Tuple[float, float],
    *,
    max_segment_length_m: float,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    total_length = _distance(start_xy, end_xy)
    if total_length <= 1e-6:
        return []
    subdivisions = max(1, int(math.ceil(total_length / max(float(max_segment_length_m), 1.0))))
    segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for index in range(subdivisions):
        ratio_a = float(index) / float(subdivisions)
        ratio_b = float(index + 1) / float(subdivisions)
        point_a = (
            float(start_xy[0]) + (float(end_xy[0]) - float(start_xy[0])) * ratio_a,
            float(start_xy[1]) + (float(end_xy[1]) - float(start_xy[1])) * ratio_a,
        )
        point_b = (
            float(start_xy[0]) + (float(end_xy[0]) - float(start_xy[0])) * ratio_b,
            float(start_xy[1]) + (float(end_xy[1]) - float(start_xy[1])) * ratio_b,
        )
        segments.append((point_a, point_b))
    return segments


def _arc_polyline(
    start_xy: Tuple[float, float],
    heading_rad: float,
    *,
    radius_m: float,
    sweep_deg: float,
    turn_left: bool,
    max_segment_length_m: float,
) -> Tuple[List[Tuple[float, float]], Tuple[float, float], float]:
    radius = max(float(radius_m), 1.0)
    sweep_rad = math.radians(max(float(sweep_deg), 1.0))
    if not turn_left:
        sweep_rad = -sweep_rad
    center_heading = _heading_left(heading_rad) if turn_left else _heading_right(heading_rad)
    center_xy = _advance(start_xy, center_heading, radius)
    start_angle = math.atan2(float(start_xy[1]) - float(center_xy[1]), float(start_xy[0]) - float(center_xy[0]))
    arc_length = abs(sweep_rad) * radius
    segments = max(1, int(math.ceil(arc_length / max(float(max_segment_length_m), 1.0))))
    points: List[Tuple[float, float]] = [start_xy]
    for index in range(1, segments + 1):
        ratio = float(index) / float(segments)
        angle = float(start_angle) + float(sweep_rad) * ratio
        points.append(
            (
                float(center_xy[0]) + radius * math.cos(angle),
                float(center_xy[1]) + radius * math.sin(angle),
            )
        )
    end_xy = points[-1]
    end_heading = float(heading_rad) + float(sweep_rad)
    return points, end_xy, end_heading


def _segment_bands(
    *,
    segment_id: str,
    config: MetaUrbanProceduralConfig,
    junction: bool,
    poi_types: Sequence[str],
) -> Tuple[RoadSegmentBand, ...]:
    right_kind = "right_transit_edge" if "bus_stop" in set(poi_types) else "right_furnishing"
    nearest = tuple(str(value) for value in poi_types)
    left_allowed = DEFAULT_ALLOWED_CATEGORIES
    right_allowed = DEFAULT_ALLOWED_CATEGORIES if not junction else DEFAULT_ALLOWED_CATEGORIES + ("bus_stop",)
    sidewalk_width = float(max(config.sidewalk_width_m, 1.2))
    return (
        RoadSegmentBand(
            band_id=f"{segment_id}_left",
            segment_id=segment_id,
            side="left",
            kind="left_furnishing",
            width_m=sidewalk_width,
            allowed_categories=left_allowed,
            nearest_poi_types=nearest,
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_right",
            segment_id=segment_id,
            side="right",
            kind=right_kind,
            width_m=sidewalk_width,
            allowed_categories=right_allowed,
            nearest_poi_types=nearest,
        ),
    )


class _GraphBuilder:
    def __init__(self, config: MetaUrbanProceduralConfig):
        self._config = config
        self._segment_index = 0
        self._edge_index = 0
        self._station_cursor_m = 0.0
        self._nodes: List[RoadSegmentNode] = []
        self._edges: List[RoadSegmentEdge] = []

    def add_polyline(
        self,
        points: Sequence[Tuple[float, float]],
        *,
        road_id: int,
        connect_from: Sequence[str] = (),
        is_junction: bool = False,
        poi_types: Sequence[str] = (),
        highway_type: str = "metaurban_procedural",
    ) -> Tuple[Tuple[str, ...], Tuple[float, float]]:
        segment_ids: List[str] = []
        previous_segment_id = ""
        connect_from_ids = tuple(str(item) for item in connect_from if str(item))
        for point_a, point_b in zip(points[:-1], points[1:]):
            for start_xy, end_xy in _subdivide_line(
                point_a,
                point_b,
                max_segment_length_m=float(self._config.segment_length_m),
            ):
                segment_id = f"seg_{self._segment_index:04d}"
                self._segment_index += 1
                length_m = float(_distance(start_xy, end_xy))
                station_start = float(self._station_cursor_m)
                station_end = float(self._station_cursor_m + length_m)
                center_xy = (
                    (float(start_xy[0]) + float(end_xy[0])) / 2.0,
                    (float(start_xy[1]) + float(end_xy[1])) / 2.0,
                )
                self._nodes.append(
                    RoadSegmentNode(
                        segment_id=segment_id,
                        road_id=int(road_id),
                        start_xy=(float(start_xy[0]), float(start_xy[1])),
                        end_xy=(float(end_xy[0]), float(end_xy[1])),
                        center_xy=(float(center_xy[0]), float(center_xy[1])),
                        length_m=length_m,
                        is_junction=bool(is_junction),
                        is_accessible=True,
                        highway_type=str(highway_type),
                        poi_types=tuple(str(value) for value in poi_types),
                        bands=_segment_bands(
                            segment_id=segment_id,
                            config=self._config,
                            junction=bool(is_junction),
                            poi_types=tuple(str(value) for value in poi_types),
                        ),
                        station_start_m=station_start,
                        station_end_m=station_end,
                        station_center_m=(station_start + station_end) / 2.0,
                    )
                )
                if previous_segment_id:
                    self._append_edge(previous_segment_id, segment_id)
                elif connect_from_ids:
                    for anchor_segment_id in connect_from_ids:
                        self._append_edge(anchor_segment_id, segment_id)
                previous_segment_id = segment_id
                segment_ids.append(segment_id)
                self._station_cursor_m = station_end
        if len(points) < 2:
            raise ValueError("points must contain at least two positions")
        return tuple(segment_ids), (float(points[-1][0]), float(points[-1][1]))

    def _append_edge(self, from_segment_id: str, to_segment_id: str) -> None:
        self._edges.append(
            RoadSegmentEdge(
                edge_id=f"edge_{self._edge_index:04d}",
                from_segment_id=str(from_segment_id),
                to_segment_id=str(to_segment_id),
                weight=1.0,
            )
        )
        self._edge_index += 1

    def build(self) -> RoadSegmentGraph:
        total_length = float(sum(float(node.length_m) for node in self._nodes))
        half_length = total_length / 2.0
        nodes = tuple(
            RoadSegmentNode(
                segment_id=node.segment_id,
                road_id=node.road_id,
                start_xy=node.start_xy,
                end_xy=node.end_xy,
                center_xy=node.center_xy,
                length_m=node.length_m,
                is_junction=node.is_junction,
                is_accessible=node.is_accessible,
                highway_type=node.highway_type,
                poi_types=node.poi_types,
                bands=node.bands,
                station_start_m=float(node.station_start_m) - half_length,
                station_end_m=float(node.station_end_m) - half_length,
                station_center_m=float(node.station_center_m) - half_length,
            )
            for node in self._nodes
        )
        return RoadSegmentGraph(
            nodes=nodes,
            edges=tuple(self._edges),
            mode="metaurban_procedural",
        )


def build_metaurban_segment_graph(config: MetaUrbanProceduralConfig) -> RoadSegmentGraph:
    """Build a RoadSegmentGraph from a MetaUrban-style block sequence."""

    rng = random.Random(int(config.seed))
    sequence = resolve_metaurban_block_sequence(config, rng=rng)
    builder = _GraphBuilder(config)
    cursor_xy = (0.0, 0.0)
    heading_rad = math.radians(float(config.start_heading_deg))
    road_id = 1

    entrance_end = _advance(cursor_xy, heading_rad, float(max(config.entrance_length_m, 1.0)))
    current_segments, cursor_xy = builder.add_polyline(
        [cursor_xy, entrance_end],
        road_id=road_id,
        highway_type="metaurban_entry",
    )
    current_anchor = current_segments[-1]
    road_id += 1

    for token in sequence:
        if token == "S":
            end_xy = _advance(cursor_xy, heading_rad, float(max(config.straight_length_m, 1.0)))
            segment_ids, cursor_xy = builder.add_polyline(
                [cursor_xy, end_xy],
                road_id=road_id,
                connect_from=(current_anchor,),
            )
            current_anchor = segment_ids[-1]
            road_id += 1
            continue

        if token == "C":
            turn_left = bool(rng.randint(0, 1))
            arc_points, cursor_xy, heading_rad = _arc_polyline(
                cursor_xy,
                heading_rad,
                radius_m=float(config.curve_radius_m),
                sweep_deg=float(config.curve_angle_deg),
                turn_left=turn_left,
                max_segment_length_m=float(config.segment_length_m),
            )
            segment_ids, cursor_xy = builder.add_polyline(
                arc_points,
                road_id=road_id,
                connect_from=(current_anchor,),
            )
            current_anchor = segment_ids[-1]
            road_id += 1

            exit_xy = _advance(cursor_xy, heading_rad, float(max(config.straight_length_m * 0.65, 1.0)))
            segment_ids, cursor_xy = builder.add_polyline(
                [cursor_xy, exit_xy],
                road_id=road_id,
                connect_from=(current_anchor,),
            )
            current_anchor = segment_ids[-1]
            road_id += 1
            continue

        if token == "X":
            hub_xy = _advance(cursor_xy, heading_rad, float(max(config.intersection_span_m * 0.4, 2.0)))
            hub_ids, hub_xy = builder.add_polyline(
                [cursor_xy, hub_xy],
                road_id=road_id,
                connect_from=(current_anchor,),
                is_junction=True,
                poi_types=("crossing",),
                highway_type="metaurban_intersection",
            )
            hub_anchor = hub_ids[-1]
            road_id += 1

            forward_xy = _advance(hub_xy, heading_rad, float(max(config.intersection_span_m, 2.0)))
            segment_ids, cursor_xy = builder.add_polyline(
                [hub_xy, forward_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("crossing",),
                highway_type="metaurban_intersection",
            )
            current_anchor = segment_ids[-1]
            road_id += 1

            left_xy = _advance(hub_xy, _heading_left(heading_rad), float(max(config.branch_length_m, 2.0)))
            builder.add_polyline(
                [hub_xy, left_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("crossing",),
                highway_type="metaurban_intersection_branch",
            )
            road_id += 1

            right_xy = _advance(hub_xy, _heading_right(heading_rad), float(max(config.branch_length_m, 2.0)))
            builder.add_polyline(
                [hub_xy, right_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("crossing",),
                highway_type="metaurban_intersection_branch",
            )
            road_id += 1
            continue

        if token == "T":
            hub_xy = _advance(cursor_xy, heading_rad, float(max(config.intersection_span_m * 0.4, 2.0)))
            hub_ids, hub_xy = builder.add_polyline(
                [cursor_xy, hub_xy],
                road_id=road_id,
                connect_from=(current_anchor,),
                is_junction=True,
                poi_types=("crossing",),
                highway_type="metaurban_t_intersection",
            )
            hub_anchor = hub_ids[-1]
            road_id += 1

            turn_left = bool(rng.randint(0, 1))
            continuation_heading = _heading_left(heading_rad) if turn_left else _heading_right(heading_rad)
            branch_heading = _heading_right(heading_rad) if turn_left else _heading_left(heading_rad)
            continuation_xy = _advance(hub_xy, continuation_heading, float(max(config.intersection_span_m, 2.0)))
            segment_ids, cursor_xy = builder.add_polyline(
                [hub_xy, continuation_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("crossing",),
                highway_type="metaurban_t_intersection",
            )
            current_anchor = segment_ids[-1]
            heading_rad = continuation_heading
            road_id += 1

            branch_xy = _advance(hub_xy, branch_heading, float(max(config.branch_length_m, 2.0)))
            builder.add_polyline(
                [hub_xy, branch_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("crossing",),
                highway_type="metaurban_t_intersection_branch",
            )
            road_id += 1
            continue

        raise ValueError(f"Unsupported MetaUrban block token: {token}")

    return builder.build()


def build_metaurban_layout_payload(config: MetaUrbanProceduralConfig) -> Dict[str, object]:
    """Build a JSON-serializable payload for the procedural graph."""

    graph = build_metaurban_segment_graph(config)
    sequence = resolve_metaurban_block_sequence(config, rng=random.Random(int(config.seed)))
    return {
        "generator": "metaurban_procedural_v1",
        "reference": {
            "source_repo": "metaurban",
            "source_modules": [
                "metaurban/component/algorithm/BIG.py",
                "metaurban/component/algorithm/blocks_prob_dist.py",
                "metaurban/component/pgblock/straight.py",
                "metaurban/component/pgblock/curve.py",
            ],
            "notes": (
                "RoadGen3D-native graph port of MetaUrban's block-sequence generator. "
                "Current port supports Straight(S), Curve(C), StdInterSection(X), and StdTInterSection(T)."
            ),
        },
        "config": config.to_dict(),
        "summary": {
            **graph.summary(),
            "block_sequence": sequence,
            "supported_block_types": list(SUPPORTED_METAURBAN_BLOCKS),
        },
        "graph": graph.to_dict(),
    }


def write_metaurban_layout_payload(
    output_path: Path,
    config: MetaUrbanProceduralConfig,
) -> Dict[str, object]:
    """Generate and write a procedural-layout payload to disk."""

    payload = build_metaurban_layout_payload(config)
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return payload


__all__ = [
    "DEFAULT_ALLOWED_CATEGORIES",
    "METAURBAN_V2_BLOCK_WEIGHTS",
    "MetaUrbanProceduralConfig",
    "SUPPORTED_METAURBAN_BLOCKS",
    "build_metaurban_layout_payload",
    "build_metaurban_segment_graph",
    "resolve_metaurban_block_sequence",
    "sample_metaurban_block_sequence",
    "write_metaurban_layout_payload",
]
