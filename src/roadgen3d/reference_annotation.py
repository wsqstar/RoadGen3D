"""Reference-plan annotation parsing and graph conversion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from .street_priors import DEFAULT_CATEGORIES
from .types import RoadSegmentBand, RoadSegmentEdge, RoadSegmentGraph, RoadSegmentNode, StreetComposeConfig

ANNOTATION_SCHEMA_VERSION = "roadgen3d_reference_annotation_v1"
DEFAULT_PIXELS_PER_METER = 8.0
DEFAULT_ROUNDABOUT_RADIUS_PX = 36.0
DEFAULT_ROAD_WIDTH_M = 12.0
DEFAULT_FORWARD_DRIVE_LANE_COUNT = 1
DEFAULT_REVERSE_DRIVE_LANE_COUNT = 1
DEFAULT_BIKE_LANE_COUNT = 0
DEFAULT_BUS_LANE_COUNT = 0
DEFAULT_PARKING_LANE_COUNT = 0


def _is_record(value: Any) -> bool:
    return isinstance(value, Mapping)


def _as_string(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else default
    if value is None:
        return default
    return str(value)


def _as_float(value: Any, label: str, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError(f"{label} must be a finite number.")
        return float(default)
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be a finite number.")
    return parsed


def _as_optional_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be a finite number.")
    return parsed


def _as_int(value: Any, label: str, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError(f"{label} must be a finite integer.")
        return int(default)
    parsed = int(value)
    return parsed


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _interpolate(a: Tuple[float, float], b: Tuple[float, float], ratio: float) -> Tuple[float, float]:
    clamped = max(0.0, min(float(ratio), 1.0))
    return (
        float(a[0]) + (float(b[0]) - float(a[0])) * clamped,
        float(a[1]) + (float(b[1]) - float(a[1])) * clamped,
    )


def _dedupe_adjacent_points(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    deduped: List[Tuple[float, float]] = []
    for point in points:
        xy = (float(point[0]), float(point[1]))
        if not deduped or _distance(deduped[-1], xy) > 1e-6:
            deduped.append(xy)
    return deduped


def _safe_slug(label: str, fallback: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    collapsed = "_".join(part for part in normalized.split("_") if part)
    return collapsed or fallback


def _segment_bands(
    *,
    segment_id: str,
    config: StreetComposeConfig,
    poi_types: Sequence[str],
) -> Tuple[RoadSegmentBand, ...]:
    return (
        RoadSegmentBand(
            band_id=f"{segment_id}_left",
            segment_id=segment_id,
            side="left",
            kind="left_furnishing",
            width_m=float(config.sidewalk_width_m),
            allowed_categories=tuple(DEFAULT_CATEGORIES),
            nearest_poi_types=tuple(poi_types),
        ),
        RoadSegmentBand(
            band_id=f"{segment_id}_right",
            segment_id=segment_id,
            side="right",
            kind="right_furnishing",
            width_m=float(config.sidewalk_width_m),
            allowed_categories=tuple(DEFAULT_CATEGORIES),
            nearest_poi_types=tuple(poi_types),
        ),
    )


def _lane_profile_dict(
    *,
    forward_drive_lane_count: int,
    reverse_drive_lane_count: int,
    bike_lane_count: int,
    bus_lane_count: int,
    parking_lane_count: int,
) -> Dict[str, int]:
    return {
        "forward_drive_lane_count": int(max(forward_drive_lane_count, 0)),
        "reverse_drive_lane_count": int(max(reverse_drive_lane_count, 0)),
        "bike_lane_count": int(max(bike_lane_count, 0)),
        "bus_lane_count": int(max(bus_lane_count, 0)),
        "parking_lane_count": int(max(parking_lane_count, 0)),
        "total_drive_lane_count": int(max(forward_drive_lane_count, 0) + max(reverse_drive_lane_count, 0)),
        "total_lane_count": int(
            max(forward_drive_lane_count, 0)
            + max(reverse_drive_lane_count, 0)
            + max(bike_lane_count, 0)
            + max(bus_lane_count, 0)
            + max(parking_lane_count, 0)
        ),
    }


@dataclass(frozen=True)
class AnnotationPoint:
    x: float
    y: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": float(self.x), "y": float(self.y)}


@dataclass(frozen=True)
class AnnotatedCenterline:
    feature_id: str
    label: str
    points: Tuple[AnnotationPoint, ...]
    road_width_m: float = DEFAULT_ROAD_WIDTH_M
    reference_width_px: float | None = None
    forward_drive_lane_count: int = DEFAULT_FORWARD_DRIVE_LANE_COUNT
    reverse_drive_lane_count: int = DEFAULT_REVERSE_DRIVE_LANE_COUNT
    bike_lane_count: int = DEFAULT_BIKE_LANE_COUNT
    bus_lane_count: int = DEFAULT_BUS_LANE_COUNT
    parking_lane_count: int = DEFAULT_PARKING_LANE_COUNT
    highway_type: str = "annotated_centerline"

    def lane_profile(self) -> Dict[str, int]:
        return _lane_profile_dict(
            forward_drive_lane_count=self.forward_drive_lane_count,
            reverse_drive_lane_count=self.reverse_drive_lane_count,
            bike_lane_count=self.bike_lane_count,
            bus_lane_count=self.bus_lane_count,
            parking_lane_count=self.parking_lane_count,
        )

    def to_dict(self) -> Dict[str, Any]:
        lane_profile = self.lane_profile()
        return {
            "id": self.feature_id,
            "label": self.label,
            "road_width_m": float(self.road_width_m),
            "reference_width_px": (
                float(self.reference_width_px)
                if self.reference_width_px is not None
                else None
            ),
            "forward_drive_lane_count": int(self.forward_drive_lane_count),
            "reverse_drive_lane_count": int(self.reverse_drive_lane_count),
            "bike_lane_count": int(self.bike_lane_count),
            "bus_lane_count": int(self.bus_lane_count),
            "parking_lane_count": int(self.parking_lane_count),
            "lane_count": int(lane_profile["total_drive_lane_count"]),
            "lane_profile": lane_profile,
            "highway_type": self.highway_type,
            "points": [point.to_dict() for point in self.points],
        }


@dataclass(frozen=True)
class AnnotatedMarker:
    feature_id: str
    label: str
    x: float
    y: float
    kind: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.feature_id,
            "label": self.label,
            "kind": self.kind,
            "x": float(self.x),
            "y": float(self.y),
        }


@dataclass(frozen=True)
class AnnotatedRoundabout:
    feature_id: str
    label: str
    x: float
    y: float
    radius_px: float = DEFAULT_ROUNDABOUT_RADIUS_PX

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.feature_id,
            "label": self.label,
            "x": float(self.x),
            "y": float(self.y),
            "radius_px": float(self.radius_px),
        }


@dataclass(frozen=True)
class ReferenceAnnotation:
    version: str
    plan_id: str
    image_path: str
    image_width_px: int
    image_height_px: int
    pixels_per_meter: float
    centerlines: Tuple[AnnotatedCenterline, ...]
    junctions: Tuple[AnnotatedMarker, ...]
    roundabouts: Tuple[AnnotatedRoundabout, ...]
    control_points: Tuple[AnnotatedMarker, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "plan_id": self.plan_id,
            "image_path": self.image_path,
            "image_width_px": int(self.image_width_px),
            "image_height_px": int(self.image_height_px),
            "pixels_per_meter": float(self.pixels_per_meter),
            "centerlines": [item.to_dict() for item in self.centerlines],
            "junctions": [item.to_dict() for item in self.junctions],
            "roundabouts": [item.to_dict() for item in self.roundabouts],
            "control_points": [item.to_dict() for item in self.control_points],
        }


def _parse_point(value: Any, label: str) -> AnnotationPoint:
    if not _is_record(value):
        raise ValueError(f"{label} must be an object with x/y coordinates.")
    return AnnotationPoint(
        x=_as_float(value.get("x"), f"{label}.x"),
        y=_as_float(value.get("y"), f"{label}.y"),
    )


def _resolve_drive_lane_defaults(value: Mapping[str, Any], index: int) -> Tuple[int, int]:
    legacy_lane_count = max(
        1,
        _as_int(value.get("lane_count"), f"centerlines[{index}].lane_count", default=2),
    )
    forward_default = max(1, int(math.ceil(float(legacy_lane_count) / 2.0)))
    reverse_default = max(0, int(legacy_lane_count - forward_default))
    forward_drive_lane_count = max(
        0,
        _as_int(
            value.get("forward_drive_lane_count"),
            f"centerlines[{index}].forward_drive_lane_count",
            default=forward_default,
        ),
    )
    reverse_drive_lane_count = max(
        0,
        _as_int(
            value.get("reverse_drive_lane_count"),
            f"centerlines[{index}].reverse_drive_lane_count",
            default=reverse_default,
        ),
    )
    if forward_drive_lane_count <= 0 and reverse_drive_lane_count <= 0:
        return DEFAULT_FORWARD_DRIVE_LANE_COUNT, DEFAULT_REVERSE_DRIVE_LANE_COUNT
    return forward_drive_lane_count, reverse_drive_lane_count


def _parse_centerline(value: Any, index: int) -> AnnotatedCenterline:
    if not _is_record(value):
        raise ValueError(f"centerlines[{index}] must be an object.")
    raw_points = value.get("points")
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        raise ValueError(f"centerlines[{index}].points must be an array.")
    points = tuple(_parse_point(item, f"centerlines[{index}].points[{point_idx}]") for point_idx, item in enumerate(raw_points))
    if len(points) < 2:
        raise ValueError(f"centerlines[{index}] must contain at least two points.")
    fallback_id = f"centerline_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    forward_drive_lane_count, reverse_drive_lane_count = _resolve_drive_lane_defaults(value, index)
    reference_width_px = _as_optional_float(
        value.get("reference_width_px"),
        f"centerlines[{index}].reference_width_px",
    )
    return AnnotatedCenterline(
        feature_id=feature_id,
        label=label,
        points=points,
        road_width_m=max(
            1.0,
            _as_float(
                value.get("road_width_m"),
                f"centerlines[{index}].road_width_m",
                default=DEFAULT_ROAD_WIDTH_M,
            ),
        ),
        reference_width_px=max(1.0, reference_width_px) if reference_width_px is not None else None,
        forward_drive_lane_count=forward_drive_lane_count,
        reverse_drive_lane_count=reverse_drive_lane_count,
        bike_lane_count=max(
            0,
            _as_int(
                value.get("bike_lane_count"),
                f"centerlines[{index}].bike_lane_count",
                default=DEFAULT_BIKE_LANE_COUNT,
            ),
        ),
        bus_lane_count=max(
            0,
            _as_int(
                value.get("bus_lane_count"),
                f"centerlines[{index}].bus_lane_count",
                default=DEFAULT_BUS_LANE_COUNT,
            ),
        ),
        parking_lane_count=max(
            0,
            _as_int(
                value.get("parking_lane_count"),
                f"centerlines[{index}].parking_lane_count",
                default=DEFAULT_PARKING_LANE_COUNT,
            ),
        ),
        highway_type=_as_string(value.get("highway_type"), "annotated_centerline"),
    )


def _parse_marker(value: Any, index: int, *, collection: str, default_kind: str) -> AnnotatedMarker:
    if not _is_record(value):
        raise ValueError(f"{collection}[{index}] must be an object.")
    fallback_id = f"{default_kind}_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    kind = _safe_slug(_as_string(value.get("kind"), default_kind), default_kind)
    return AnnotatedMarker(
        feature_id=feature_id,
        label=label,
        x=_as_float(value.get("x"), f"{collection}[{index}].x"),
        y=_as_float(value.get("y"), f"{collection}[{index}].y"),
        kind=kind or default_kind,
    )


def _parse_roundabout(value: Any, index: int) -> AnnotatedRoundabout:
    if not _is_record(value):
        raise ValueError(f"roundabouts[{index}] must be an object.")
    fallback_id = f"roundabout_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    return AnnotatedRoundabout(
        feature_id=feature_id,
        label=label,
        x=_as_float(value.get("x"), f"roundabouts[{index}].x"),
        y=_as_float(value.get("y"), f"roundabouts[{index}].y"),
        radius_px=max(6.0, _as_float(value.get("radius_px"), f"roundabouts[{index}].radius_px", default=DEFAULT_ROUNDABOUT_RADIUS_PX)),
    )


def parse_reference_annotation(payload: Mapping[str, Any]) -> ReferenceAnnotation:
    if not _is_record(payload):
        raise ValueError("Annotation JSON must be an object.")

    centerlines_raw = payload.get("centerlines") or []
    junctions_raw = payload.get("junctions") or []
    roundabouts_raw = payload.get("roundabouts") or []
    control_points_raw = payload.get("control_points") or []

    if not isinstance(centerlines_raw, Sequence) or isinstance(centerlines_raw, (str, bytes)):
        raise ValueError("centerlines must be an array.")
    if not isinstance(junctions_raw, Sequence) or isinstance(junctions_raw, (str, bytes)):
        raise ValueError("junctions must be an array.")
    if not isinstance(roundabouts_raw, Sequence) or isinstance(roundabouts_raw, (str, bytes)):
        raise ValueError("roundabouts must be an array.")
    if not isinstance(control_points_raw, Sequence) or isinstance(control_points_raw, (str, bytes)):
        raise ValueError("control_points must be an array.")

    centerlines = tuple(_parse_centerline(item, index) for index, item in enumerate(centerlines_raw))
    if not centerlines:
        raise ValueError("At least one centerline is required.")

    return ReferenceAnnotation(
        version=_as_string(payload.get("version"), ANNOTATION_SCHEMA_VERSION),
        plan_id=_as_string(payload.get("plan_id"), "custom_annotation"),
        image_path=_as_string(payload.get("image_path"), ""),
        image_width_px=max(0, _as_int(payload.get("image_width_px"), "image_width_px", default=0)),
        image_height_px=max(0, _as_int(payload.get("image_height_px"), "image_height_px", default=0)),
        pixels_per_meter=max(0.1, _as_float(payload.get("pixels_per_meter"), "pixels_per_meter", default=DEFAULT_PIXELS_PER_METER)),
        centerlines=centerlines,
        junctions=tuple(_parse_marker(item, index, collection="junctions", default_kind="intersection") for index, item in enumerate(junctions_raw)),
        roundabouts=tuple(_parse_roundabout(item, index) for index, item in enumerate(roundabouts_raw)),
        control_points=tuple(_parse_marker(item, index, collection="control_points", default_kind="control_point") for index, item in enumerate(control_points_raw)),
    )


def build_reference_annotation_compose_config(overrides: Mapping[str, Any] | None = None) -> StreetComposeConfig:
    payload: MutableMapping[str, Any] = dict(overrides or {})
    return StreetComposeConfig(
        query=_as_string(payload.get("query"), "reference annotation graph"),
        length_m=max(24.0, _as_float(payload.get("length_m"), "length_m", default=120.0)),
        road_width_m=max(4.0, _as_float(payload.get("road_width_m"), "road_width_m", default=12.0)),
        sidewalk_width_m=max(1.0, _as_float(payload.get("sidewalk_width_m"), "sidewalk_width_m", default=3.0)),
        lane_count=max(1, _as_int(payload.get("lane_count"), "lane_count", default=2)),
        density=max(0.1, _as_float(payload.get("density"), "density", default=1.0)),
        seed=_as_int(payload.get("seed"), "seed", default=42),
        topk_per_category=max(1, _as_int(payload.get("topk_per_category"), "topk_per_category", default=20)),
        max_trials_per_slot=max(1, _as_int(payload.get("max_trials_per_slot"), "max_trials_per_slot", default=30)),
        segment_length_m=max(4.0, _as_float(payload.get("segment_length_m"), "segment_length_m", default=12.0)),
        layout_mode=_as_string(payload.get("layout_mode"), "annotation"),
    )


def _pixel_to_local(
    annotation: ReferenceAnnotation,
    *,
    x: float,
    y: float,
) -> Tuple[float, float]:
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    return ((float(x) - center_x) / ppm, (center_y - float(y)) / ppm)


def _build_annotation_road_profiles(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    road_profiles: List[Dict[str, Any]] = []
    for road_id, centerline in enumerate(annotation.centerlines, start=1):
        lane_profile = centerline.lane_profile()
        reference_width_m = (
            float(centerline.reference_width_px) / max(float(annotation.pixels_per_meter), 1e-6)
            if centerline.reference_width_px is not None
            else None
        )
        road_profiles.append(
            {
                "road_id": int(road_id),
                "annotation_id": centerline.feature_id,
                "label": centerline.label,
                "road_width_m": float(centerline.road_width_m),
                "reference_width_px": (
                    float(centerline.reference_width_px)
                    if centerline.reference_width_px is not None
                    else None
                ),
                "reference_width_m": reference_width_m,
                "forward_drive_lane_count": int(centerline.forward_drive_lane_count),
                "reverse_drive_lane_count": int(centerline.reverse_drive_lane_count),
                "bike_lane_count": int(centerline.bike_lane_count),
                "bus_lane_count": int(centerline.bus_lane_count),
                "parking_lane_count": int(centerline.parking_lane_count),
                "lane_profile": lane_profile,
                "highway_type": centerline.highway_type,
            }
        )
    return road_profiles


def _collect_auto_junction_anchors(
    polylines: Sequence[Sequence[Tuple[float, float]]],
    *,
    tolerance_m: float,
) -> List[Tuple[float, float]]:
    clusters: List[Dict[str, Any]] = []
    for polyline in polylines:
        for point in polyline:
            matched = None
            for cluster in clusters:
                if _distance(cluster["point"], point) <= tolerance_m:
                    matched = cluster
                    break
            if matched is None:
                clusters.append({"point": point, "count": 1})
            else:
                count = int(matched["count"]) + 1
                anchor = (
                    (matched["point"][0] * matched["count"] + point[0]) / count,
                    (matched["point"][1] * matched["count"] + point[1]) / count,
                )
                matched["point"] = anchor
                matched["count"] = count
    return [tuple(cluster["point"]) for cluster in clusters if int(cluster["count"]) >= 2]


def _build_poi_types(
    point: Tuple[float, float],
    *,
    junction_anchors: Sequence[Tuple[float, float]],
    roundabout_anchors: Sequence[Tuple[float, float]],
    control_points: Sequence[Tuple[AnnotatedMarker, Tuple[float, float]]],
    junction_tolerance_m: float,
    roundabout_tolerance_m: float,
    control_tolerance_m: float,
) -> Tuple[str, ...]:
    poi_types: List[str] = []
    if any(_distance(point, anchor) <= junction_tolerance_m for anchor in junction_anchors):
        poi_types.append("junction")
    if any(_distance(point, anchor) <= roundabout_tolerance_m for anchor in roundabout_anchors):
        poi_types.append("roundabout")
    for marker, marker_xy in control_points:
        if _distance(point, marker_xy) <= control_tolerance_m:
            poi_types.append(marker.kind)
    return tuple(sorted(set(poi_types)))


def _build_centerline_nodes(
    centerline: AnnotatedCenterline,
    *,
    road_id: int,
    polyline_m: Sequence[Tuple[float, float]],
    config: StreetComposeConfig,
    segment_counter_start: int,
    edge_counter_start: int,
    junction_anchors: Sequence[Tuple[float, float]],
    roundabout_anchors: Sequence[Tuple[float, float]],
    control_points: Sequence[Tuple[AnnotatedMarker, Tuple[float, float]]],
) -> Tuple[List[RoadSegmentNode], List[RoadSegmentEdge], int, int]:
    nodes: List[RoadSegmentNode] = []
    edges: List[RoadSegmentEdge] = []
    segment_counter = int(segment_counter_start)
    edge_counter = int(edge_counter_start)
    last_segment_id: str | None = None
    station_m = 0.0
    segment_length_target = max(float(config.segment_length_m), 4.0)
    junction_tolerance_m = max(float(centerline.road_width_m) * 0.85, segment_length_target * 0.6, 3.0)
    roundabout_tolerance_m = max(float(centerline.road_width_m), segment_length_target, 5.0)
    control_tolerance_m = max(float(centerline.road_width_m), 6.0)

    for coord_idx in range(len(polyline_m) - 1):
        start = tuple(polyline_m[coord_idx])
        end = tuple(polyline_m[coord_idx + 1])
        length = _distance(start, end)
        if length <= 1e-6:
            continue
        subdivisions = max(1, int(math.ceil(length / segment_length_target)))
        for part_idx in range(subdivisions):
            a = _interpolate(start, end, float(part_idx) / float(subdivisions))
            b = _interpolate(start, end, float(part_idx + 1) / float(subdivisions))
            center = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
            segment_id = f"annot_seg_{segment_counter:04d}"
            segment_counter += 1
            length_m = _distance(a, b)
            station_start_m = station_m
            station_end_m = station_m + length_m
            poi_types = _build_poi_types(
                center,
                junction_anchors=junction_anchors,
                roundabout_anchors=roundabout_anchors,
                control_points=control_points,
                junction_tolerance_m=junction_tolerance_m,
                roundabout_tolerance_m=roundabout_tolerance_m,
                control_tolerance_m=control_tolerance_m,
            )
            is_junction = (
                part_idx == 0
                or part_idx == subdivisions - 1
                or any(_distance(center, anchor) <= junction_tolerance_m for anchor in junction_anchors)
                or any(_distance(center, anchor) <= roundabout_tolerance_m for anchor in roundabout_anchors)
            )
            nodes.append(
                RoadSegmentNode(
                    segment_id=segment_id,
                    road_id=int(road_id),
                    start_xy=(float(a[0]), float(a[1])),
                    end_xy=(float(b[0]), float(b[1])),
                    center_xy=(float(center[0]), float(center[1])),
                    length_m=float(length_m),
                    is_junction=bool(is_junction),
                    is_accessible=True,
                    highway_type=centerline.highway_type,
                    poi_types=tuple(poi_types),
                    bands=_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types),
                    station_start_m=float(station_start_m),
                    station_end_m=float(station_end_m),
                    station_center_m=float((station_start_m + station_end_m) * 0.5),
                    road_width_m=float(centerline.road_width_m),
                    lane_profile=centerline.lane_profile(),
                )
            )
            station_m = station_end_m
            if last_segment_id is not None:
                edges.append(
                    RoadSegmentEdge(
                        edge_id=f"annot_edge_{edge_counter:04d}",
                        from_segment_id=last_segment_id,
                        to_segment_id=segment_id,
                        weight=1.0,
                    )
                )
                edge_counter += 1
            last_segment_id = segment_id

    return nodes, edges, segment_counter, edge_counter


def _build_roundabout_nodes(
    roundabout: AnnotatedRoundabout,
    *,
    road_id: int,
    config: StreetComposeConfig,
    segment_counter_start: int,
    edge_counter_start: int,
    center_xy: Tuple[float, float],
    radius_m: float,
) -> Tuple[List[RoadSegmentNode], List[RoadSegmentEdge], int, int]:
    nodes: List[RoadSegmentNode] = []
    edges: List[RoadSegmentEdge] = []
    segment_counter = int(segment_counter_start)
    edge_counter = int(edge_counter_start)
    radius_m = max(float(radius_m), 4.0)
    circumference = max(2.0 * math.pi * radius_m, 24.0)
    segment_count = max(8, int(math.ceil(circumference / max(float(config.segment_length_m), 4.0))))
    points: List[Tuple[float, float]] = []
    for idx in range(segment_count):
        angle = (2.0 * math.pi * float(idx)) / float(segment_count)
        points.append((center_xy[0] + math.cos(angle) * radius_m, center_xy[1] + math.sin(angle) * radius_m))
    points.append(points[0])

    station_m = 0.0
    segment_ids: List[str] = []
    for idx in range(segment_count):
        a = points[idx]
        b = points[idx + 1]
        length_m = _distance(a, b)
        segment_id = f"annot_seg_{segment_counter:04d}"
        segment_counter += 1
        segment_ids.append(segment_id)
        station_start_m = station_m
        station_end_m = station_m + length_m
        center = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
        poi_types = ("roundabout",)
        nodes.append(
            RoadSegmentNode(
                segment_id=segment_id,
                road_id=int(road_id),
                start_xy=(float(a[0]), float(a[1])),
                end_xy=(float(b[0]), float(b[1])),
                center_xy=(float(center[0]), float(center[1])),
                length_m=float(length_m),
                is_junction=True,
                is_accessible=True,
                highway_type="annotated_roundabout",
                poi_types=poi_types,
                bands=_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types),
                station_start_m=float(station_start_m),
                station_end_m=float(station_end_m),
                station_center_m=float((station_start_m + station_end_m) * 0.5),
                road_width_m=float(config.road_width_m),
                lane_profile=_lane_profile_dict(
                    forward_drive_lane_count=0,
                    reverse_drive_lane_count=0,
                    bike_lane_count=0,
                    bus_lane_count=0,
                    parking_lane_count=0,
                ),
            )
        )
        station_m = station_end_m

    for idx in range(len(segment_ids)):
        edges.append(
            RoadSegmentEdge(
                edge_id=f"annot_edge_{edge_counter:04d}",
                from_segment_id=segment_ids[idx],
                to_segment_id=segment_ids[(idx + 1) % len(segment_ids)],
                weight=1.0,
            )
        )
        edge_counter += 1
    return nodes, edges, segment_counter, edge_counter


def build_segment_graph_from_annotation(
    annotation_input: ReferenceAnnotation | Mapping[str, Any],
    *,
    config: StreetComposeConfig | None = None,
) -> RoadSegmentGraph:
    annotation = annotation_input if isinstance(annotation_input, ReferenceAnnotation) else parse_reference_annotation(annotation_input)
    resolved_config = config or build_reference_annotation_compose_config()

    local_centerlines: List[Tuple[AnnotatedCenterline, List[Tuple[float, float]]]] = []
    for centerline in annotation.centerlines:
        points = _dedupe_adjacent_points(
            [_pixel_to_local(annotation, x=point.x, y=point.y) for point in centerline.points]
        )
        if len(points) >= 2:
            local_centerlines.append((centerline, points))
    if not local_centerlines:
        raise ValueError("Annotation contains no usable centerlines.")

    explicit_junctions = [
        _pixel_to_local(annotation, x=item.x, y=item.y)
        for item in annotation.junctions
    ]
    roundabout_centers = [
        _pixel_to_local(annotation, x=item.x, y=item.y)
        for item in annotation.roundabouts
    ]
    control_points = [
        (item, _pixel_to_local(annotation, x=item.x, y=item.y))
        for item in annotation.control_points
    ]
    auto_junctions = _collect_auto_junction_anchors(
        [points for _, points in local_centerlines],
        tolerance_m=max(float(resolved_config.segment_length_m) * 0.5, 4.0),
    )
    junction_anchors = explicit_junctions + [anchor for anchor in auto_junctions if anchor not in explicit_junctions]

    nodes: List[RoadSegmentNode] = []
    edges: List[RoadSegmentEdge] = []
    segment_counter = 0
    edge_counter = 0
    road_id = 1
    default_anchor_width_m = max(
        [float(centerline.road_width_m) for centerline, _ in local_centerlines] + [float(resolved_config.road_width_m)],
    )

    for centerline, points in local_centerlines:
        centerline_nodes, centerline_edges, segment_counter, edge_counter = _build_centerline_nodes(
            centerline,
            road_id=road_id,
            polyline_m=points,
            config=resolved_config,
            segment_counter_start=segment_counter,
            edge_counter_start=edge_counter,
            junction_anchors=junction_anchors,
            roundabout_anchors=roundabout_centers,
            control_points=control_points,
        )
        nodes.extend(centerline_nodes)
        edges.extend(centerline_edges)
        road_id += 1

    for roundabout, center_xy in zip(annotation.roundabouts, roundabout_centers):
        radius_m = float(roundabout.radius_px) / max(float(annotation.pixels_per_meter), 1.0)
        roundabout_nodes, roundabout_edges, segment_counter, edge_counter = _build_roundabout_nodes(
            roundabout,
            road_id=road_id,
            config=resolved_config,
            segment_counter_start=segment_counter,
            edge_counter_start=edge_counter,
            center_xy=center_xy,
            radius_m=radius_m,
        )
        nodes.extend(roundabout_nodes)
        edges.extend(roundabout_edges)
        road_id += 1

    edge_pairs = {(edge.from_segment_id, edge.to_segment_id) for edge in edges}

    anchor_groups: List[Tuple[str, Tuple[float, float], float]] = []
    for anchor in junction_anchors:
        anchor_groups.append(("junction", anchor, max(default_anchor_width_m, 8.0)))
    for roundabout, center_xy in zip(annotation.roundabouts, roundabout_centers):
        radius_m = max(float(roundabout.radius_px) / max(float(annotation.pixels_per_meter), 1.0), 4.0)
        anchor_groups.append(("roundabout", center_xy, radius_m + default_anchor_width_m))

    for kind, anchor, threshold_m in anchor_groups:
        touching_nodes = [
            node
            for node in nodes
            if (
                _distance(node.center_xy, anchor) <= threshold_m
                or _distance(node.start_xy, anchor) <= threshold_m
                or _distance(node.end_xy, anchor) <= threshold_m
            )
        ]
        if len(touching_nodes) < 2:
            continue
        for from_idx, from_node in enumerate(touching_nodes):
            for to_node in touching_nodes[from_idx + 1:]:
                pairs = (
                    (from_node.segment_id, to_node.segment_id),
                    (to_node.segment_id, from_node.segment_id),
                )
                for from_segment_id, to_segment_id in pairs:
                    if from_segment_id == to_segment_id or (from_segment_id, to_segment_id) in edge_pairs:
                        continue
                    edge_pairs.add((from_segment_id, to_segment_id))
                    edges.append(
                        RoadSegmentEdge(
                            edge_id=f"annot_edge_{edge_counter:04d}",
                            from_segment_id=from_segment_id,
                            to_segment_id=to_segment_id,
                            weight=1.0 if kind == "junction" else 0.9,
                        )
                    )
                    edge_counter += 1

    return RoadSegmentGraph(nodes=tuple(nodes), edges=tuple(edges), mode="annotation")


def summarize_reference_annotation(annotation_input: ReferenceAnnotation | Mapping[str, Any]) -> Dict[str, Any]:
    annotation = annotation_input if isinstance(annotation_input, ReferenceAnnotation) else parse_reference_annotation(annotation_input)
    road_profiles = _build_annotation_road_profiles(annotation)
    points: List[Tuple[float, float]] = []
    for centerline in annotation.centerlines:
        for point in centerline.points:
            points.append(_pixel_to_local(annotation, x=point.x, y=point.y))
    for marker in annotation.junctions:
        points.append(_pixel_to_local(annotation, x=marker.x, y=marker.y))
    for marker in annotation.control_points:
        points.append(_pixel_to_local(annotation, x=marker.x, y=marker.y))
    for roundabout in annotation.roundabouts:
        center_xy = _pixel_to_local(annotation, x=roundabout.x, y=roundabout.y)
        radius_m = float(roundabout.radius_px) / max(float(annotation.pixels_per_meter), 1.0)
        points.extend(
            [
                (center_xy[0] - radius_m, center_xy[1] - radius_m),
                (center_xy[0] + radius_m, center_xy[1] + radius_m),
            ]
        )
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        bounds = {
            "min_x_m": float(min(xs)),
            "max_x_m": float(max(xs)),
            "min_y_m": float(min(ys)),
            "max_y_m": float(max(ys)),
            "width_m": float(max(xs) - min(xs)),
            "height_m": float(max(ys) - min(ys)),
        }
    else:
        bounds = {
            "min_x_m": 0.0,
            "max_x_m": 0.0,
            "min_y_m": 0.0,
            "max_y_m": 0.0,
            "width_m": 0.0,
            "height_m": 0.0,
        }
    road_widths = [float(item["road_width_m"]) for item in road_profiles]
    reference_widths_px = [
        float(item["reference_width_px"])
        for item in road_profiles
        if item.get("reference_width_px") is not None
    ]
    return {
        "plan_id": annotation.plan_id,
        "image_path": annotation.image_path,
        "image_width_px": int(annotation.image_width_px),
        "image_height_px": int(annotation.image_height_px),
        "pixels_per_meter": float(annotation.pixels_per_meter),
        "annotation_road_count": len(road_profiles),
        "centerline_count": len(annotation.centerlines),
        "junction_count": len(annotation.junctions),
        "roundabout_count": len(annotation.roundabouts),
        "control_point_count": len(annotation.control_points),
        "control_point_kinds": sorted({item.kind for item in annotation.control_points}),
        "total_drive_lane_count": sum(int(item["lane_profile"]["total_drive_lane_count"]) for item in road_profiles),
        "bike_lane_count": sum(int(item["bike_lane_count"]) for item in road_profiles),
        "bus_lane_count": sum(int(item["bus_lane_count"]) for item in road_profiles),
        "parking_lane_count": sum(int(item["parking_lane_count"]) for item in road_profiles),
        "min_reference_width_px": min(reference_widths_px) if reference_widths_px else 0.0,
        "max_reference_width_px": max(reference_widths_px) if reference_widths_px else 0.0,
        "avg_reference_width_px": (
            sum(reference_widths_px) / len(reference_widths_px)
            if reference_widths_px
            else 0.0
        ),
        "min_annotation_road_width_m": min(road_widths) if road_widths else 0.0,
        "max_annotation_road_width_m": max(road_widths) if road_widths else 0.0,
        "avg_annotation_road_width_m": (
            sum(road_widths) / len(road_widths)
            if road_widths
            else 0.0
        ),
        **bounds,
    }


def build_reference_annotation_graph_payload(
    annotation_input: ReferenceAnnotation | Mapping[str, Any],
    *,
    config: StreetComposeConfig | None = None,
) -> Dict[str, Any]:
    annotation = annotation_input if isinstance(annotation_input, ReferenceAnnotation) else parse_reference_annotation(annotation_input)
    resolved_config = config or build_reference_annotation_compose_config()
    graph = build_segment_graph_from_annotation(annotation, config=resolved_config)
    road_profiles = _build_annotation_road_profiles(annotation)
    summary = summarize_reference_annotation(annotation)
    summary.update(graph.summary())
    summary["road_profile_count"] = len(road_profiles)
    summary["segment_length_target_m"] = float(resolved_config.segment_length_m)
    summary["compose_fallback_road_width_m"] = float(resolved_config.road_width_m)
    summary["compose_fallback_lane_count"] = int(resolved_config.lane_count)
    summary["sidewalk_width_m"] = float(resolved_config.sidewalk_width_m)
    return {
        "annotation": annotation.to_dict(),
        "graph": graph.to_dict(),
        "road_profiles": road_profiles,
        "summary": summary,
    }


__all__ = [
    "ANNOTATION_SCHEMA_VERSION",
    "AnnotatedCenterline",
    "AnnotatedMarker",
    "AnnotatedRoundabout",
    "AnnotationPoint",
    "DEFAULT_PIXELS_PER_METER",
    "DEFAULT_ROUNDABOUT_RADIUS_PX",
    "ReferenceAnnotation",
    "build_reference_annotation_compose_config",
    "build_reference_annotation_graph_payload",
    "build_segment_graph_from_annotation",
    "parse_reference_annotation",
    "summarize_reference_annotation",
]
