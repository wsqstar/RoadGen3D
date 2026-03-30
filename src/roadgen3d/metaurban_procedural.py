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

from .reference_annotation import (
    METAURBAN_STRIP_ASSET_HINTS,
    METAURBAN_STRIP_DISPLAY_LABELS,
    METAURBAN_STRIP_PLACEMENT_HINTS,
    METAURBAN_STRIP_ZONE_HINTS,
)
from .street_band_semantics import detailed_strip_allowed_categories
from .types import (
    RoadSegmentBand,
    RoadSegmentCrossSectionStrip,
    RoadSegmentEdge,
    RoadSegmentGraph,
    RoadSegmentMetaUrbanAssetHint,
    RoadSegmentNode,
)

ROOT = Path(__file__).resolve().parents[2]
METAURBAN_V2_BLOCK_WEIGHTS: Dict[str, float] = {
    "C": 0.40,  # Curve
    "S": 0.20,  # Straight
    "X": 0.15,  # StdInterSection
    "T": 0.15,  # StdTInterSection
    "O": 0.10,  # Roundabout
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

DEFAULT_NEARROAD_FURNISHING_WIDTH_M = 1.5
DEFAULT_MAIN_SIDEWALK_WIDTH_M = 2.5
DEFAULT_VALID_REGION_WIDTH_M = 2.0


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


@dataclass(frozen=True)
class MetaUrbanReferencePlan:
    """One site-specific reference plan backed by a local image and preset config."""

    plan_id: str
    label: str
    description: str
    image_path: Path
    block_sequence: str
    seed: int = 42
    straight_length_m: float = 28.0
    intersection_span_m: float = 18.0
    branch_length_m: float = 24.0
    curve_radius_m: float = 16.0
    curve_angle_deg: float = 60.0

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["image_path"] = str(self.image_path)
        return payload


_REFERENCE_PLANS: Dict[str, MetaUrbanReferencePlan] = {
    "hkust_gz_gate": MetaUrbanReferencePlan(
        plan_id="hkust_gz_gate",
        label="HKUST-GZ Gate",
        description=(
            "Approximate the HKUST(GZ) gate frontage: west approach, signalized crossroad, "
            "tree-lined boulevard, center roundabout, and east campus-edge intersection."
        ),
        image_path=(ROOT / "assets" / "hkust-gz" / "image.png").resolve(),
        block_sequence="SXSOXS",
        seed=17,
        straight_length_m=34.0,
        intersection_span_m=22.0,
        branch_length_m=26.0,
        curve_radius_m=18.0,
        curve_angle_deg=60.0,
    ),
}


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


def list_metaurban_reference_plans() -> Tuple[MetaUrbanReferencePlan, ...]:
    """Return all built-in reference plans."""

    return tuple(_REFERENCE_PLANS.values())


def get_metaurban_reference_plan(plan_id: str) -> MetaUrbanReferencePlan:
    """Return one built-in reference plan by id."""

    key = str(plan_id or "").strip().lower()
    if key not in _REFERENCE_PLANS:
        raise KeyError(f"Unknown MetaUrban reference plan: {plan_id}")
    return _REFERENCE_PLANS[key]


def build_metaurban_reference_config(
    plan_id: str,
    *,
    lane_count: int = 2,
    sidewalk_width_m: float = 2.5,
    lane_width_m: float = 3.5,
    segment_length_m: float = 12.0,
    start_heading_deg: float = 0.0,
) -> MetaUrbanProceduralConfig:
    """Build a procedural config from a site-specific preset."""

    plan = get_metaurban_reference_plan(plan_id)
    return MetaUrbanProceduralConfig(
        seed=int(plan.seed),
        block_count=len(plan.block_sequence),
        block_sequence=str(plan.block_sequence),
        lane_count=max(int(lane_count), 1),
        lane_width_m=float(max(lane_width_m, 2.8)),
        sidewalk_width_m=float(max(sidewalk_width_m, 1.8)),
        segment_length_m=float(max(segment_length_m, 4.0)),
        straight_length_m=float(plan.straight_length_m),
        intersection_span_m=float(plan.intersection_span_m),
        branch_length_m=float(plan.branch_length_m),
        curve_radius_m=float(plan.curve_radius_m),
        curve_angle_deg=float(plan.curve_angle_deg),
        start_heading_deg=float(start_heading_deg),
    )


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
    nearest = tuple(str(value) for value in poi_types)
    nearroad_allowed = detailed_strip_allowed_categories("nearroad_furnishing")
    clear_allowed = detailed_strip_allowed_categories("clear_sidewalk")
    frontage_allowed = detailed_strip_allowed_categories("frontage_reserve")
    return (
        RoadSegmentBand(
            band_id=f"{segment_id}_left_nearroad",
            segment_id=segment_id,
            side="left",
            kind="nearroad_furnishing",
            width_m=float(DEFAULT_NEARROAD_FURNISHING_WIDTH_M),
            allowed_categories=nearroad_allowed,
            nearest_poi_types=nearest,
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_left_clear",
            segment_id=segment_id,
            side="left",
            kind="clear_sidewalk",
            width_m=float(DEFAULT_MAIN_SIDEWALK_WIDTH_M),
            allowed_categories=clear_allowed,
            nearest_poi_types=nearest,
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_left_frontage",
            segment_id=segment_id,
            side="left",
            kind="frontage_reserve",
            width_m=float(DEFAULT_VALID_REGION_WIDTH_M),
            allowed_categories=frontage_allowed,
            nearest_poi_types=nearest,
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_right_nearroad",
            segment_id=segment_id,
            side="right",
            kind="nearroad_furnishing",
            width_m=float(DEFAULT_NEARROAD_FURNISHING_WIDTH_M),
            allowed_categories=nearroad_allowed if not junction else tuple(dict.fromkeys(nearroad_allowed + ("bus_stop",))),
            nearest_poi_types=nearest,
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_right_clear",
            segment_id=segment_id,
            side="right",
            kind="clear_sidewalk",
            width_m=float(DEFAULT_MAIN_SIDEWALK_WIDTH_M),
            allowed_categories=clear_allowed,
            nearest_poi_types=nearest,
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_right_frontage",
            segment_id=segment_id,
            side="right",
            kind="frontage_reserve",
            width_m=float(DEFAULT_VALID_REGION_WIDTH_M),
            allowed_categories=frontage_allowed,
            nearest_poi_types=nearest,
        ),
    )


def _segment_cross_section_strips(config: MetaUrbanProceduralConfig) -> Tuple[RoadSegmentCrossSectionStrip, ...]:
    lane_width_m = float(max(config.lane_width_m, 2.8))
    center_lane_count = max(int(config.lane_count), 1)
    strips: List[RoadSegmentCrossSectionStrip] = []

    def _push(strip_id: str, zone: str, kind: str, width_m: float, order_index: int, direction: str = "none") -> None:
        strips.append(
            RoadSegmentCrossSectionStrip(
                strip_id=str(strip_id),
                zone=str(zone),
                kind=str(kind),
                width_m=float(width_m),
                direction=str(direction),
                order_index=int(order_index),
            )
        )

    _push("left_nearroad", "left", "nearroad_furnishing", DEFAULT_NEARROAD_FURNISHING_WIDTH_M, 0)
    _push("left_clear", "left", "clear_sidewalk", DEFAULT_MAIN_SIDEWALK_WIDTH_M, 1)
    _push("left_frontage", "left", "frontage_reserve", DEFAULT_VALID_REGION_WIDTH_M, 2)

    order_index = 0
    for lane_index in range(center_lane_count):
        direction = "reverse" if lane_index < center_lane_count / 2.0 else "forward"
        _push(f"drive_lane_{lane_index:02d}", "center", "drive_lane", lane_width_m, order_index, direction)
        order_index += 1

    _push("right_nearroad", "right", "nearroad_furnishing", DEFAULT_NEARROAD_FURNISHING_WIDTH_M, 0)
    _push("right_clear", "right", "clear_sidewalk", DEFAULT_MAIN_SIDEWALK_WIDTH_M, 1)
    _push("right_frontage", "right", "frontage_reserve", DEFAULT_VALID_REGION_WIDTH_M, 2)
    return tuple(strips)


def _segment_metaurban_asset_hints(strips: Sequence[RoadSegmentCrossSectionStrip]) -> Tuple[RoadSegmentMetaUrbanAssetHint, ...]:
    hints: List[RoadSegmentMetaUrbanAssetHint] = []
    for strip in strips:
        strip_kind = str(strip.kind)
        hints.append(
            RoadSegmentMetaUrbanAssetHint(
                strip_id=str(strip.strip_id),
                zone=str(strip.zone),
                strip_kind=strip_kind,
                metaurban_zone=str(METAURBAN_STRIP_ZONE_HINTS.get(strip_kind, "")),
                display_label=str(METAURBAN_STRIP_DISPLAY_LABELS.get(strip_kind, strip_kind)),
                suggested_assets=tuple(METAURBAN_STRIP_ASSET_HINTS.get(strip_kind, ())),
                placement_hint=str(METAURBAN_STRIP_PLACEMENT_HINTS.get(strip_kind, "")),
                asset_source="metaurban_asset_config",
                asset_directory_status="hook_only",
            )
        )
    return tuple(hints)


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
                cross_section_strips = _segment_cross_section_strips(self._config)
                cross_section_width_m = float(sum(float(strip.width_m) for strip in cross_section_strips))
                road_width_m = float(
                    sum(
                        float(strip.width_m)
                        for strip in cross_section_strips
                        if str(strip.zone).strip().lower() == "center"
                    )
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
                        road_width_m=float(road_width_m),
                        lane_profile={
                            "forward_drive_lane_count": int(max(int(math.ceil(self._config.lane_count / 2.0)), 1)),
                            "reverse_drive_lane_count": int(max(int(self._config.lane_count // 2), 0)),
                            "bike_lane_count": 0,
                            "bus_lane_count": 0,
                            "parking_lane_count": 0,
                        },
                        cross_section_strips=cross_section_strips,
                        cross_section_width_m=float(cross_section_width_m),
                        metaurban_asset_hints=_segment_metaurban_asset_hints(cross_section_strips),
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
                road_width_m=float(node.road_width_m),
                lane_profile=dict(node.lane_profile),
                cross_section_strips=tuple(node.cross_section_strips),
                cross_section_width_m=float(node.cross_section_width_m),
                metaurban_asset_hints=tuple(node.metaurban_asset_hints),
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

        if token == "O":
            hub_xy = _advance(cursor_xy, heading_rad, float(max(config.intersection_span_m * 0.35, 2.0)))
            hub_ids, hub_xy = builder.add_polyline(
                [cursor_xy, hub_xy],
                road_id=road_id,
                connect_from=(current_anchor,),
                is_junction=True,
                poi_types=("roundabout",),
                highway_type="metaurban_roundabout",
            )
            hub_anchor = hub_ids[-1]
            road_id += 1

            ring_radius = float(max(config.curve_radius_m * 0.75, config.lane_width_m * config.lane_count * 1.3, 8.0))
            ring_points = [
                _advance(hub_xy, heading_rad, ring_radius),
                _advance(hub_xy, _heading_left(heading_rad), ring_radius),
                _advance(hub_xy, heading_rad + math.pi, ring_radius),
                _advance(hub_xy, _heading_right(heading_rad), ring_radius),
                _advance(hub_xy, heading_rad, ring_radius),
            ]
            builder.add_polyline(
                ring_points,
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("roundabout",),
                highway_type="metaurban_roundabout_ring",
            )
            road_id += 1

            forward_xy = _advance(hub_xy, heading_rad, float(max(config.intersection_span_m * 1.1, 2.0)))
            segment_ids, cursor_xy = builder.add_polyline(
                [hub_xy, forward_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("roundabout",),
                highway_type="metaurban_roundabout_exit",
            )
            current_anchor = segment_ids[-1]
            road_id += 1

            left_xy = _advance(hub_xy, _heading_left(heading_rad), float(max(config.branch_length_m, 2.0)))
            builder.add_polyline(
                [hub_xy, left_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("roundabout",),
                highway_type="metaurban_roundabout_branch",
            )
            road_id += 1

            right_xy = _advance(hub_xy, _heading_right(heading_rad), float(max(config.branch_length_m, 2.0)))
            builder.add_polyline(
                [hub_xy, right_xy],
                road_id=road_id,
                connect_from=(hub_anchor,),
                is_junction=True,
                poi_types=("roundabout",),
                highway_type="metaurban_roundabout_branch",
            )
            road_id += 1
            continue

        raise ValueError(f"Unsupported MetaUrban block token: {token}")

    return builder.build()


def compute_metaurban_plan_metrics(graph: RoadSegmentGraph) -> Dict[str, float]:
    """Compute frontend-facing plan metrics for procedural layouts."""

    nodes = tuple(graph.nodes)
    edges = tuple(graph.edges)
    if not nodes:
        return {
            "total_network_length_m": 0.0,
            "junction_density_per_100m": 0.0,
            "connectivity_ratio": 0.0,
            "network_width_m": 0.0,
            "network_height_m": 0.0,
            "branching_factor": 0.0,
        }
    total_length = float(sum(float(node.length_m) for node in nodes))
    xs = [float(point) for node in nodes for point in (node.start_xy[0], node.end_xy[0])]
    ys = [float(point) for node in nodes for point in (node.start_xy[1], node.end_xy[1])]
    junction_count = sum(1 for node in nodes if bool(node.is_junction))
    outgoing_counts: Dict[str, int] = {}
    incoming_counts: Dict[str, int] = {}
    for edge in edges:
        outgoing_counts[edge.from_segment_id] = outgoing_counts.get(edge.from_segment_id, 0) + 1
        incoming_counts[edge.to_segment_id] = incoming_counts.get(edge.to_segment_id, 0) + 1
    branching_nodes = sum(1 for segment_id, count in outgoing_counts.items() if int(count) > 1 and segment_id)
    reachable_edge_budget = max(len(nodes) - 1, 1)
    return {
        "total_network_length_m": round(total_length, 2),
        "junction_density_per_100m": round((junction_count / max(total_length, 1.0)) * 100.0, 3),
        "connectivity_ratio": round(len(edges) / float(reachable_edge_budget), 3),
        "network_width_m": round(max(xs) - min(xs), 2) if xs else 0.0,
        "network_height_m": round(max(ys) - min(ys), 2) if ys else 0.0,
        "branching_factor": round(branching_nodes / float(max(len(nodes), 1)), 3),
    }


def build_metaurban_layout_payload(config: MetaUrbanProceduralConfig) -> Dict[str, object]:
    """Build a JSON-serializable payload for the procedural graph."""

    graph = build_metaurban_segment_graph(config)
    sequence = resolve_metaurban_block_sequence(config, rng=random.Random(int(config.seed)))
    evaluation = compute_metaurban_plan_metrics(graph)
    return {
        "generator": "metaurban_procedural_v1",
        "reference": {
            "source_repo": "metaurban",
            "source_modules": [
                "metaurban/component/algorithm/BIG.py",
                "metaurban/component/algorithm/blocks_prob_dist.py",
                "metaurban/component/pgblock/straight.py",
                "metaurban/component/pgblock/curve.py",
                "metaurban/component/pgblock/roundabout.py",
            ],
            "notes": (
                "RoadGen3D-native graph port of MetaUrban's block-sequence generator. "
                "Current port supports Straight(S), Curve(C), StdInterSection(X), "
                "StdTInterSection(T), and a graph-level Roundabout(O)."
            ),
        },
        "config": config.to_dict(),
        "summary": {
            **graph.summary(),
            **evaluation,
            "block_sequence": sequence,
            "supported_block_types": list(SUPPORTED_METAURBAN_BLOCKS),
        },
        "evaluation": evaluation,
        "graph": graph.to_dict(),
    }


def build_metaurban_reference_layout_payload(
    plan_id: str,
    *,
    lane_count: int = 2,
    sidewalk_width_m: float = 2.5,
    lane_width_m: float = 3.5,
    segment_length_m: float = 12.0,
) -> Dict[str, object]:
    """Build a procedural layout payload from a named reference plan preset."""

    plan = get_metaurban_reference_plan(plan_id)
    config = build_metaurban_reference_config(
        plan_id,
        lane_count=lane_count,
        sidewalk_width_m=sidewalk_width_m,
        lane_width_m=lane_width_m,
        segment_length_m=segment_length_m,
    )
    payload = build_metaurban_layout_payload(config)
    payload["reference_plan"] = {
        "plan_id": plan.plan_id,
        "label": plan.label,
        "description": plan.description,
        "image_path": str(plan.image_path),
    }
    payload["summary"] = {
        **dict(payload.get("summary", {}) or {}),
        "reference_plan_id": plan.plan_id,
        "reference_plan_label": plan.label,
    }
    return payload


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
    "MetaUrbanReferencePlan",
    "SUPPORTED_METAURBAN_BLOCKS",
    "build_metaurban_reference_config",
    "build_metaurban_reference_layout_payload",
    "build_metaurban_layout_payload",
    "build_metaurban_segment_graph",
    "compute_metaurban_plan_metrics",
    "get_metaurban_reference_plan",
    "list_metaurban_reference_plans",
    "resolve_metaurban_block_sequence",
    "sample_metaurban_block_sequence",
    "write_metaurban_layout_payload",
]
