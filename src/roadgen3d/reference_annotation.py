"""Reference-plan annotation parsing and graph conversion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Tuple

from .street_priors import DEFAULT_CATEGORIES
from .types import (
    RoadSegmentBand,
    RoadSegmentCrossSectionStrip,
    RoadSegmentEdge,
    RoadSegmentFurnitureInstance,
    RoadSegmentGraph,
    RoadSegmentMetaUrbanAssetHint,
    RoadSegmentNode,
    StreetComposeConfig,
)

ANNOTATION_SCHEMA_VERSION = "roadgen3d_reference_annotation_v2"
DEFAULT_PIXELS_PER_METER = 8.0
DEFAULT_ROUNDABOUT_RADIUS_PX = 36.0
DEFAULT_SEGMENT_LENGTH_M = 12.0
DEFAULT_FORWARD_DRIVE_LANE_COUNT = 2
DEFAULT_REVERSE_DRIVE_LANE_COUNT = 2
DEFAULT_BIKE_LANE_COUNT = 0
DEFAULT_BUS_LANE_COUNT = 0
DEFAULT_PARKING_LANE_COUNT = 0
DEFAULT_DRIVE_LANE_WIDTH_M = 3.3

CROSS_SECTION_MODE_COARSE = "coarse"
CROSS_SECTION_MODE_DETAILED = "detailed"
VALID_CROSS_SECTION_MODES = frozenset({CROSS_SECTION_MODE_COARSE, CROSS_SECTION_MODE_DETAILED})
VALID_CROSS_SECTION_ZONES = frozenset({"left", "center", "right"})
VALID_STRIP_DIRECTIONS = frozenset({"forward", "reverse", "bidirectional", "none"})
LANE_STRIP_KINDS = frozenset({"drive_lane", "bus_lane", "bike_lane", "parking_lane"})
CENTER_STRIP_KINDS = frozenset({"drive_lane", "bus_lane", "bike_lane", "parking_lane", "median"})
SIDE_STRIP_KINDS = frozenset(
    {
        "nearroad_buffer",
        "nearroad_furnishing",
        "clear_sidewalk",
        "farfromroad_buffer",
        "frontage_reserve",
    }
)
VALID_STRIP_KINDS = frozenset(CENTER_STRIP_KINDS | SIDE_STRIP_KINDS)
FURNITURE_COMPATIBLE_STRIP_KINDS = frozenset({"nearroad_furnishing", "frontage_reserve"})
VALID_FURNITURE_KINDS = frozenset(
    {
        "bench",
        "lamp",
        "trash",
        "mailbox",
        "bollard",
        "sign",
        "hydrant",
        "bus_stop",
    }
)
NOMINAL_STRIP_WIDTHS: Dict[str, float] = {
    "drive_lane": DEFAULT_DRIVE_LANE_WIDTH_M,
    "bus_lane": 3.5,
    "bike_lane": 1.8,
    "parking_lane": 2.5,
    "median": 0.3,
    "nearroad_buffer": 0.5,
    "nearroad_furnishing": 1.5,
    "clear_sidewalk": 2.5,
    "farfromroad_buffer": 0.5,
    "frontage_reserve": 2.0,
}
DEFAULT_ROAD_WIDTH_M = (
    (DEFAULT_FORWARD_DRIVE_LANE_COUNT + DEFAULT_REVERSE_DRIVE_LANE_COUNT) * DEFAULT_DRIVE_LANE_WIDTH_M
    + 2
    * (
        NOMINAL_STRIP_WIDTHS["nearroad_furnishing"]
        + NOMINAL_STRIP_WIDTHS["clear_sidewalk"]
        + NOMINAL_STRIP_WIDTHS["frontage_reserve"]
    )
    + NOMINAL_STRIP_WIDTHS["median"]
)
ROOT = Path(__file__).resolve().parents[2]
METAURBAN_ROOT = (ROOT / "metaurban").resolve()
METAURBAN_ASSETS_DIR = (METAURBAN_ROOT / "assets").resolve()
METAURBAN_PEDESTRIAN_ASSETS_DIR = (METAURBAN_ROOT / "assets_pedestrian").resolve()
METAURBAN_STRIP_DISPLAY_LABELS: Dict[str, str] = {
    "drive_lane": "Drive Lane",
    "bus_lane": "Bus Lane",
    "bike_lane": "Bike Lane",
    "parking_lane": "Parking Lane",
    "median": "Median",
    "nearroad_buffer": "Near-road Buffer",
    "nearroad_furnishing": "Near-road Furnishing",
    "clear_sidewalk": "Main Sidewalk",
    "farfromroad_buffer": "Outer Buffer",
    "frontage_reserve": "Valid Region",
}
METAURBAN_STRIP_ZONE_HINTS: Dict[str, str] = {
    "drive_lane": "carriageway",
    "bus_lane": "carriageway",
    "bike_lane": "carriageway_edge",
    "parking_lane": "carriageway_edge",
    "median": "median",
    "nearroad_buffer": "nearroad_buffer_sidewalk",
    "nearroad_furnishing": "nearroad_sidewalk",
    "clear_sidewalk": "main_sidewalk",
    "farfromroad_buffer": "farfromroad_sidewalk",
    "frontage_reserve": "valid_region",
}
METAURBAN_STRIP_ASSET_HINTS: Dict[str, Tuple[str, ...]] = {
    "drive_lane": (),
    "bus_lane": (),
    "bike_lane": (),
    "parking_lane": (),
    "median": (),
    "nearroad_buffer": ("Tree", "Traffic_sign", "Bollard"),
    "nearroad_furnishing": ("Lamp_post", "TrashCan", "FireHydrant"),
    "clear_sidewalk": ("Pedestrian", "Wheelchair", "Mailbox"),
    "farfromroad_buffer": ("Bench",),
    "frontage_reserve": ("Building",),
}
METAURBAN_STRIP_PLACEMENT_HINTS: Dict[str, str] = {
    "drive_lane": "Roadway travel space.",
    "bus_lane": "Transit-priority roadway space.",
    "bike_lane": "Protected bike movement space.",
    "parking_lane": "Road-edge parking or loading space.",
    "median": "Median separator or refuge space.",
    "nearroad_buffer": "MetaUrban nearroad_buffer_sidewalk objects typically sit here.",
    "nearroad_furnishing": "MetaUrban nearroad_sidewalk furniture and utilities typically sit here.",
    "clear_sidewalk": "MetaUrban main_sidewalk pedestrian flows and mailbox-scale objects typically sit here.",
    "farfromroad_buffer": "MetaUrban farfromroad_sidewalk furniture or planting can extend here.",
    "frontage_reserve": "MetaUrban valid_region buildings and frontage reserve typically start here.",
}
METAURBAN_ASSET_DOWNLOAD_COMMAND = "python metaurban/pull_asset.py --update"


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
    return int(value)


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


def _normalize_angle_deg(value: float) -> float:
    normalized = math.fmod(float(value), 360.0)
    if normalized < 0.0:
        normalized += 360.0
    return normalized


def _angle_deg(from_point: Tuple[float, float], to_point: Tuple[float, float]) -> float:
    return _normalize_angle_deg(
        math.degrees(float(math.atan2(float(to_point[1]) - float(from_point[1]), float(to_point[0]) - float(from_point[0]))))
    )


def _circular_angle_diffs_deg(angles_deg: Sequence[float]) -> List[float]:
    if not angles_deg:
        return []
    ordered = sorted(_normalize_angle_deg(value) for value in angles_deg)
    diffs: List[float] = []
    for index, value in enumerate(ordered):
        next_value = ordered[(index + 1) % len(ordered)]
        raw_diff = next_value - value
        if index == len(ordered) - 1:
            raw_diff += 360.0
        diffs.append(float(raw_diff))
    return diffs


def _classify_topology_junction_kind(angles_deg: Sequence[float]) -> str:
    arm_count = len(tuple(angles_deg))
    diffs = _circular_angle_diffs_deg(angles_deg)
    if arm_count == 4 and diffs and max(abs(diff - 90.0) for diff in diffs) <= 35.0:
        return "cross_junction"
    if arm_count == 3 and diffs and any(diff >= 145.0 for diff in diffs):
        return "t_junction"
    return "complex_junction"


def _safe_slug(label: str, fallback: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    collapsed = "_".join(part for part in normalized.split("_") if part)
    return collapsed or fallback


def _lane_profile_dict(
    *,
    forward_drive_lane_count: int,
    reverse_drive_lane_count: int,
    bike_lane_count: int,
    bus_lane_count: int,
    parking_lane_count: int,
    bidirectional_drive_lane_count: int = 0,
    bidirectional_lane_count: int = 0,
) -> Dict[str, int]:
    forward_drive_lane_count = int(max(forward_drive_lane_count, 0))
    reverse_drive_lane_count = int(max(reverse_drive_lane_count, 0))
    bike_lane_count = int(max(bike_lane_count, 0))
    bus_lane_count = int(max(bus_lane_count, 0))
    parking_lane_count = int(max(parking_lane_count, 0))
    bidirectional_drive_lane_count = int(max(bidirectional_drive_lane_count, 0))
    bidirectional_lane_count = int(max(bidirectional_lane_count, 0))
    return {
        "forward_drive_lane_count": forward_drive_lane_count,
        "reverse_drive_lane_count": reverse_drive_lane_count,
        "bike_lane_count": bike_lane_count,
        "bus_lane_count": bus_lane_count,
        "parking_lane_count": parking_lane_count,
        "bidirectional_drive_lane_count": bidirectional_drive_lane_count,
        "bidirectional_lane_count": bidirectional_lane_count,
        "total_drive_lane_count": forward_drive_lane_count + reverse_drive_lane_count + bidirectional_drive_lane_count,
        "total_lane_count": (
            forward_drive_lane_count
            + reverse_drive_lane_count
            + bike_lane_count
            + bus_lane_count
            + parking_lane_count
            + bidirectional_drive_lane_count
        ),
    }


@dataclass(frozen=True)
class AnnotationPoint:
    x: float
    y: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": float(self.x), "y": float(self.y)}


@dataclass(frozen=True)
class AnnotatedCrossSectionStrip:
    strip_id: str
    zone: str
    kind: str
    width_m: float
    direction: str = "none"
    order_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strip_id": self.strip_id,
            "zone": self.zone,
            "kind": self.kind,
            "width_m": float(self.width_m),
            "direction": self.direction,
            "order_index": int(self.order_index),
        }


@dataclass(frozen=True)
class AnnotatedStreetFurnitureInstance:
    instance_id: str
    centerline_id: str
    strip_id: str
    kind: str
    station_m: float
    lateral_offset_m: float
    yaw_deg: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "centerline_id": self.centerline_id,
            "strip_id": self.strip_id,
            "kind": self.kind,
            "station_m": float(self.station_m),
            "lateral_offset_m": float(self.lateral_offset_m),
            "yaw_deg": float(self.yaw_deg) if self.yaw_deg is not None else None,
        }


def _lane_profile_from_strips(strips: Sequence[AnnotatedCrossSectionStrip]) -> Dict[str, int]:
    forward_drive_lane_count = 0
    reverse_drive_lane_count = 0
    bike_lane_count = 0
    bus_lane_count = 0
    parking_lane_count = 0
    bidirectional_drive_lane_count = 0
    bidirectional_lane_count = 0

    for strip in strips:
        if strip.zone != "center":
            continue
        if strip.kind == "drive_lane":
            if strip.direction == "forward":
                forward_drive_lane_count += 1
            elif strip.direction == "reverse":
                reverse_drive_lane_count += 1
            elif strip.direction == "bidirectional":
                bidirectional_drive_lane_count += 1
                bidirectional_lane_count += 1
        elif strip.kind == "bike_lane":
            bike_lane_count += 1
            if strip.direction == "bidirectional":
                bidirectional_lane_count += 1
        elif strip.kind == "bus_lane":
            bus_lane_count += 1
            if strip.direction == "bidirectional":
                bidirectional_lane_count += 1
        elif strip.kind == "parking_lane":
            parking_lane_count += 1

    return _lane_profile_dict(
        forward_drive_lane_count=forward_drive_lane_count,
        reverse_drive_lane_count=reverse_drive_lane_count,
        bike_lane_count=bike_lane_count,
        bus_lane_count=bus_lane_count,
        parking_lane_count=parking_lane_count,
        bidirectional_drive_lane_count=bidirectional_drive_lane_count,
        bidirectional_lane_count=bidirectional_lane_count,
    )


def _split_auxiliary_count_across_directions(
    total: int,
    forward_drive_lane_count: int,
    reverse_drive_lane_count: int,
) -> Tuple[int, int]:
    total = int(max(total, 0))
    if forward_drive_lane_count > 0 and reverse_drive_lane_count > 0:
        return int(math.ceil(float(total) / 2.0)), int(math.floor(float(total) / 2.0))
    if reverse_drive_lane_count > 0:
        return total, 0
    return 0, total


def _nominal_seed_cross_section_width(
    forward_drive_lane_count: int,
    reverse_drive_lane_count: int,
    bike_lane_count: int,
    bus_lane_count: int,
    parking_lane_count: int,
) -> float:
    left_parking, right_parking = _split_auxiliary_count_across_directions(
        parking_lane_count,
        forward_drive_lane_count,
        reverse_drive_lane_count,
    )
    left_bike, right_bike = _split_auxiliary_count_across_directions(
        bike_lane_count,
        forward_drive_lane_count,
        reverse_drive_lane_count,
    )
    left_bus, right_bus = _split_auxiliary_count_across_directions(
        bus_lane_count,
        forward_drive_lane_count,
        reverse_drive_lane_count,
    )
    side_width = 2.0 * (
        float(NOMINAL_STRIP_WIDTHS["nearroad_furnishing"])
        + float(NOMINAL_STRIP_WIDTHS["clear_sidewalk"])
        + float(NOMINAL_STRIP_WIDTHS["frontage_reserve"])
    )
    center_width = (
        (max(int(forward_drive_lane_count), 0) + max(int(reverse_drive_lane_count), 0))
        * float(NOMINAL_STRIP_WIDTHS["drive_lane"])
        + (left_parking + right_parking) * float(NOMINAL_STRIP_WIDTHS["parking_lane"])
        + (left_bike + right_bike) * float(NOMINAL_STRIP_WIDTHS["bike_lane"])
        + (left_bus + right_bus) * float(NOMINAL_STRIP_WIDTHS["bus_lane"])
        + (
            float(NOMINAL_STRIP_WIDTHS["median"])
            if forward_drive_lane_count > 0 and reverse_drive_lane_count > 0
            else 0.0
        )
    )
    return round(side_width + center_width, 3)


def _next_seed_strip_id(strips: Sequence[AnnotatedCrossSectionStrip], zone: str) -> str:
    next_index = sum(1 for strip in strips if strip.zone == zone) + 1
    return f"{zone}_{next_index:02d}"


def _seed_detailed_cross_section(centerline: "AnnotatedCenterline") -> Tuple[AnnotatedCrossSectionStrip, ...]:
    left_parking, right_parking = _split_auxiliary_count_across_directions(
        centerline.parking_lane_count,
        centerline.forward_drive_lane_count,
        centerline.reverse_drive_lane_count,
    )
    left_bike, right_bike = _split_auxiliary_count_across_directions(
        centerline.bike_lane_count,
        centerline.forward_drive_lane_count,
        centerline.reverse_drive_lane_count,
    )
    left_bus, right_bus = _split_auxiliary_count_across_directions(
        centerline.bus_lane_count,
        centerline.forward_drive_lane_count,
        centerline.reverse_drive_lane_count,
    )
    strips: List[AnnotatedCrossSectionStrip] = []

    def _push(zone: str, kind: str, direction: str) -> None:
        strips.append(
            AnnotatedCrossSectionStrip(
                strip_id=_next_seed_strip_id(strips, zone),
                zone=zone,
                kind=kind,
                width_m=float(NOMINAL_STRIP_WIDTHS[kind]),
                direction=direction,
                order_index=sum(1 for strip in strips if strip.zone == zone),
            )
        )

    _push("left", "nearroad_furnishing", "none")
    _push("left", "clear_sidewalk", "none")
    _push("left", "frontage_reserve", "none")
    _push("right", "nearroad_furnishing", "none")
    _push("right", "clear_sidewalk", "none")
    _push("right", "frontage_reserve", "none")

    for _ in range(left_parking):
        _push("center", "parking_lane", "reverse")
    for _ in range(left_bike):
        _push("center", "bike_lane", "reverse")
    for _ in range(left_bus):
        _push("center", "bus_lane", "reverse")
    for _ in range(max(int(centerline.reverse_drive_lane_count), 0)):
        _push("center", "drive_lane", "reverse")
    if centerline.forward_drive_lane_count > 0 and centerline.reverse_drive_lane_count > 0:
        _push("center", "median", "none")
    for _ in range(max(int(centerline.forward_drive_lane_count), 0)):
        _push("center", "drive_lane", "forward")
    for _ in range(right_bus):
        _push("center", "bus_lane", "forward")
    for _ in range(right_bike):
        _push("center", "bike_lane", "forward")
    for _ in range(right_parking):
        _push("center", "parking_lane", "forward")

    nominal_total_width = sum(float(strip.width_m) for strip in strips)
    target_width = max(float(centerline.road_width_m), 1.0)
    scale = target_width / nominal_total_width if nominal_total_width > 0.0 else 1.0
    return tuple(
        AnnotatedCrossSectionStrip(
            strip_id=strip.strip_id,
            zone=strip.zone,
            kind=strip.kind,
            width_m=round(float(strip.width_m) * scale, 3),
            direction=strip.direction,
            order_index=strip.order_index,
        )
        for strip in strips
    )


def _preview_strips_for_centerline(centerline: "AnnotatedCenterline") -> Tuple[str, Tuple[AnnotatedCrossSectionStrip, ...]]:
    if centerline.resolved_cross_section_mode() == CROSS_SECTION_MODE_DETAILED and centerline.cross_section_strips:
        return "detailed", centerline.cross_section_strips
    return "seed", _seed_detailed_cross_section(centerline)


def _metaurban_asset_directory_flags() -> Dict[str, bool]:
    return {
        "assets_dir_present": bool(METAURBAN_ASSETS_DIR.exists()),
        "assets_pedestrian_dir_present": bool(METAURBAN_PEDESTRIAN_ASSETS_DIR.exists()),
    }


def _metaurban_asset_directory_status_for_assets(suggested_assets: Sequence[str]) -> str:
    if not suggested_assets:
        return "not_applicable"
    flags = _metaurban_asset_directory_flags()
    requires_assets = any(asset not in {"Pedestrian", "Wheelchair"} for asset in suggested_assets)
    requires_pedestrians = any(asset in {"Pedestrian", "Wheelchair"} for asset in suggested_assets)
    assets_ready = (not requires_assets) or flags["assets_dir_present"]
    pedestrians_ready = (not requires_pedestrians) or flags["assets_pedestrian_dir_present"]
    return "available" if assets_ready and pedestrians_ready else "hook_only"


def _build_metaurban_asset_hint_records(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    for centerline in annotation.centerlines:
        source_mode, strips = _preview_strips_for_centerline(centerline)
        furniture_by_strip: Dict[str, List[str]] = {}
        for instance in centerline.street_furniture_instances:
            furniture_by_strip.setdefault(instance.strip_id, []).append(instance.kind)
        for strip in strips:
            suggested_assets = METAURBAN_STRIP_ASSET_HINTS.get(strip.kind, ())
            hints.append(
                {
                    "annotation_id": centerline.feature_id,
                    "label": centerline.label,
                    "source_mode": source_mode,
                    "strip_id": strip.strip_id,
                    "zone": strip.zone,
                    "strip_kind": strip.kind,
                    "direction": strip.direction,
                    "width_m": float(strip.width_m),
                    "metaurban_zone": METAURBAN_STRIP_ZONE_HINTS.get(strip.kind, strip.kind),
                    "display_label": METAURBAN_STRIP_DISPLAY_LABELS.get(strip.kind, strip.kind.replace("_", " ").title()),
                    "suggested_assets": list(suggested_assets),
                    "explicit_furniture_kinds": sorted(set(furniture_by_strip.get(strip.strip_id, []))),
                    "placement_hint": METAURBAN_STRIP_PLACEMENT_HINTS.get(strip.kind, ""),
                    "asset_source": "metaurban_asset_config",
                    "asset_directory_status": _metaurban_asset_directory_status_for_assets(suggested_assets),
                    **_metaurban_asset_directory_flags(),
                }
            )
    return hints


def _build_metaurban_asset_guide() -> Dict[str, Any]:
    return {
        "download_command": METAURBAN_ASSET_DOWNLOAD_COMMAND,
        "assets_dir": str(METAURBAN_ASSETS_DIR),
        "assets_pedestrian_dir": str(METAURBAN_PEDESTRIAN_ASSETS_DIR),
        **_metaurban_asset_directory_flags(),
    }


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
    cross_section_mode: str = CROSS_SECTION_MODE_COARSE
    cross_section_strips: Tuple[AnnotatedCrossSectionStrip, ...] = ()
    street_furniture_instances: Tuple[AnnotatedStreetFurnitureInstance, ...] = ()

    def resolved_cross_section_mode(self) -> str:
        if self.cross_section_strips:
            return CROSS_SECTION_MODE_DETAILED
        if self.cross_section_mode in VALID_CROSS_SECTION_MODES:
            return self.cross_section_mode
        return CROSS_SECTION_MODE_COARSE

    def lane_profile(self) -> Dict[str, int]:
        if self.resolved_cross_section_mode() == CROSS_SECTION_MODE_DETAILED and self.cross_section_strips:
            return _lane_profile_from_strips(self.cross_section_strips)
        return _lane_profile_dict(
            forward_drive_lane_count=self.forward_drive_lane_count,
            reverse_drive_lane_count=self.reverse_drive_lane_count,
            bike_lane_count=self.bike_lane_count,
            bus_lane_count=self.bus_lane_count,
            parking_lane_count=self.parking_lane_count,
        )

    def cross_section_width_m(self) -> float:
        if self.resolved_cross_section_mode() == CROSS_SECTION_MODE_DETAILED and self.cross_section_strips:
            return float(sum(max(float(strip.width_m), 0.0) for strip in self.cross_section_strips))
        return float(self.road_width_m)

    def carriageway_width_m(self) -> float:
        if self.resolved_cross_section_mode() == CROSS_SECTION_MODE_DETAILED and self.cross_section_strips:
            width_m = sum(
                max(float(strip.width_m), 0.0)
                for strip in self.cross_section_strips
                if strip.zone == "center"
            )
            if width_m > 0.0:
                return float(width_m)
        return float(self.road_width_m)

    def to_dict(self) -> Dict[str, Any]:
        lane_profile = self.lane_profile()
        return {
            "id": self.feature_id,
            "label": self.label,
            "road_width_m": float(self.cross_section_width_m()),
            "carriageway_width_m": float(self.carriageway_width_m()),
            "reference_width_px": (
                float(self.reference_width_px)
                if self.reference_width_px is not None
                else None
            ),
            "forward_drive_lane_count": int(lane_profile["forward_drive_lane_count"]),
            "reverse_drive_lane_count": int(lane_profile["reverse_drive_lane_count"]),
            "bike_lane_count": int(lane_profile["bike_lane_count"]),
            "bus_lane_count": int(lane_profile["bus_lane_count"]),
            "parking_lane_count": int(lane_profile["parking_lane_count"]),
            "lane_count": int(lane_profile["total_drive_lane_count"]),
            "lane_profile": lane_profile,
            "highway_type": self.highway_type,
            "cross_section_mode": self.resolved_cross_section_mode(),
            "cross_section_strips": [strip.to_dict() for strip in self.cross_section_strips],
            "street_furniture_instances": [item.to_dict() for item in self.street_furniture_instances],
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
        _as_int(
            value.get("lane_count"),
            f"centerlines[{index}].lane_count",
            default=DEFAULT_FORWARD_DRIVE_LANE_COUNT + DEFAULT_REVERSE_DRIVE_LANE_COUNT,
        ),
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


def _parse_cross_section_strip(
    value: Any,
    *,
    centerline_index: int,
    strip_index: int,
    fallback_prefix: str,
) -> AnnotatedCrossSectionStrip:
    if not _is_record(value):
        raise ValueError(f"centerlines[{centerline_index}].cross_section_strips[{strip_index}] must be an object.")
    strip_id = _as_string(
        value.get("strip_id"),
        f"{fallback_prefix}_strip_{strip_index + 1:02d}",
    )
    zone = _safe_slug(
        _as_string(value.get("zone"), "center"),
        "center",
    )
    if zone not in VALID_CROSS_SECTION_ZONES:
        raise ValueError(
            f"centerlines[{centerline_index}].cross_section_strips[{strip_index}].zone must be one of {sorted(VALID_CROSS_SECTION_ZONES)}."
        )
    kind = _safe_slug(
        _as_string(value.get("kind"), "drive_lane"),
        "drive_lane",
    )
    if kind not in VALID_STRIP_KINDS:
        raise ValueError(
            f"centerlines[{centerline_index}].cross_section_strips[{strip_index}].kind must be one of {sorted(VALID_STRIP_KINDS)}."
        )
    direction = _safe_slug(
        _as_string(value.get("direction"), "none"),
        "none",
    )
    if direction not in VALID_STRIP_DIRECTIONS:
        raise ValueError(
            f"centerlines[{centerline_index}].cross_section_strips[{strip_index}].direction must be one of {sorted(VALID_STRIP_DIRECTIONS)}."
        )
    if zone in {"left", "right"} and kind not in SIDE_STRIP_KINDS:
        raise ValueError(
            f"centerlines[{centerline_index}].cross_section_strips[{strip_index}] uses side zone '{zone}' but kind '{kind}' is not a side strip."
        )
    if zone == "center" and kind not in CENTER_STRIP_KINDS:
        raise ValueError(
            f"centerlines[{centerline_index}].cross_section_strips[{strip_index}] uses center zone but kind '{kind}' is not a center strip."
        )
    if kind in SIDE_STRIP_KINDS or kind == "median":
        direction = "none"
    return AnnotatedCrossSectionStrip(
        strip_id=strip_id,
        zone=zone,
        kind=kind,
        width_m=max(
            0.1,
            _as_float(
                value.get("width_m"),
                f"centerlines[{centerline_index}].cross_section_strips[{strip_index}].width_m",
                default=1.0,
            ),
        ),
        direction=direction,
        order_index=max(
            0,
            _as_int(
                value.get("order_index"),
                f"centerlines[{centerline_index}].cross_section_strips[{strip_index}].order_index",
                default=strip_index,
            ),
        ),
    )


def _parse_street_furniture_instance(
    value: Any,
    *,
    centerline_index: int,
    furniture_index: int,
    fallback_prefix: str,
    fallback_centerline_id: str,
) -> AnnotatedStreetFurnitureInstance:
    if not _is_record(value):
        raise ValueError(
            f"centerlines[{centerline_index}].street_furniture_instances[{furniture_index}] must be an object."
        )
    kind = _safe_slug(
        _as_string(value.get("kind"), "bench"),
        "bench",
    )
    if kind not in VALID_FURNITURE_KINDS:
        raise ValueError(
            f"centerlines[{centerline_index}].street_furniture_instances[{furniture_index}].kind must be one of {sorted(VALID_FURNITURE_KINDS)}."
        )
    return AnnotatedStreetFurnitureInstance(
        instance_id=_as_string(
            value.get("instance_id") or value.get("id"),
            f"{fallback_prefix}_furniture_{furniture_index + 1:02d}",
        ),
        centerline_id=_as_string(
            value.get("centerline_id"),
            fallback_centerline_id,
        ),
        strip_id=_as_string(
            value.get("strip_id"),
            "",
        ),
        kind=kind,
        station_m=max(
            0.0,
            _as_float(
                value.get("station_m"),
                f"centerlines[{centerline_index}].street_furniture_instances[{furniture_index}].station_m",
                default=0.0,
            ),
        ),
        lateral_offset_m=_as_float(
            value.get("lateral_offset_m"),
            f"centerlines[{centerline_index}].street_furniture_instances[{furniture_index}].lateral_offset_m",
            default=0.0,
        ),
        yaw_deg=_as_optional_float(
            value.get("yaw_deg"),
            f"centerlines[{centerline_index}].street_furniture_instances[{furniture_index}].yaw_deg",
        ),
    )


def _sorted_cross_section_strips(
    strips: Sequence[AnnotatedCrossSectionStrip],
) -> Tuple[AnnotatedCrossSectionStrip, ...]:
    zone_rank = {"left": 0, "center": 1, "right": 2}
    return tuple(
        sorted(
            strips,
            key=lambda item: (zone_rank.get(item.zone, 99), int(item.order_index), item.strip_id),
        )
    )


def _parse_centerline(value: Any, index: int) -> AnnotatedCenterline:
    if not _is_record(value):
        raise ValueError(f"centerlines[{index}] must be an object.")
    raw_points = value.get("points")
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        raise ValueError(f"centerlines[{index}].points must be an array.")
    points = tuple(
        _parse_point(item, f"centerlines[{index}].points[{point_idx}]")
        for point_idx, item in enumerate(raw_points)
    )
    if len(points) < 2:
        raise ValueError(f"centerlines[{index}] must contain at least two points.")
    fallback_id = f"centerline_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    forward_drive_lane_count, reverse_drive_lane_count = _resolve_drive_lane_defaults(value, index)
    bike_lane_count = max(
        0,
        _as_int(
            value.get("bike_lane_count"),
            f"centerlines[{index}].bike_lane_count",
            default=DEFAULT_BIKE_LANE_COUNT,
        ),
    )
    bus_lane_count = max(
        0,
        _as_int(
            value.get("bus_lane_count"),
            f"centerlines[{index}].bus_lane_count",
            default=DEFAULT_BUS_LANE_COUNT,
        ),
    )
    parking_lane_count = max(
        0,
        _as_int(
            value.get("parking_lane_count"),
            f"centerlines[{index}].parking_lane_count",
            default=DEFAULT_PARKING_LANE_COUNT,
        ),
    )
    reference_width_px = _as_optional_float(
        value.get("reference_width_px"),
        f"centerlines[{index}].reference_width_px",
    )

    raw_strips = value.get("cross_section_strips") or []
    if not isinstance(raw_strips, Sequence) or isinstance(raw_strips, (str, bytes)):
        raise ValueError(f"centerlines[{index}].cross_section_strips must be an array.")
    cross_section_strips = _sorted_cross_section_strips(
        [
            _parse_cross_section_strip(
                item,
                centerline_index=index,
                strip_index=strip_index,
                fallback_prefix=feature_id,
            )
            for strip_index, item in enumerate(raw_strips)
        ]
    )
    raw_mode = _safe_slug(
        _as_string(
            value.get("cross_section_mode"),
            CROSS_SECTION_MODE_DETAILED if cross_section_strips else CROSS_SECTION_MODE_COARSE,
        ),
        CROSS_SECTION_MODE_COARSE,
    )
    cross_section_mode = raw_mode if raw_mode in VALID_CROSS_SECTION_MODES else CROSS_SECTION_MODE_COARSE
    if cross_section_mode == CROSS_SECTION_MODE_DETAILED and not cross_section_strips:
        cross_section_mode = CROSS_SECTION_MODE_COARSE

    raw_furniture = value.get("street_furniture_instances") or []
    if not isinstance(raw_furniture, Sequence) or isinstance(raw_furniture, (str, bytes)):
        raise ValueError(f"centerlines[{index}].street_furniture_instances must be an array.")
    street_furniture_instances = tuple(
        _parse_street_furniture_instance(
            item,
            centerline_index=index,
            furniture_index=furniture_index,
            fallback_prefix=feature_id,
            fallback_centerline_id=feature_id,
        )
        for furniture_index, item in enumerate(raw_furniture)
    )
    strip_by_id = {strip.strip_id: strip for strip in cross_section_strips}
    for furniture_index, instance in enumerate(street_furniture_instances):
        if instance.centerline_id != feature_id:
            raise ValueError(
                f"centerlines[{index}].street_furniture_instances[{furniture_index}].centerline_id must match {feature_id}."
            )
        if instance.strip_id not in strip_by_id:
            raise ValueError(
                f"centerlines[{index}].street_furniture_instances[{furniture_index}].strip_id must reference an existing cross-section strip."
            )
        if strip_by_id[instance.strip_id].kind not in FURNITURE_COMPATIBLE_STRIP_KINDS:
            raise ValueError(
                f"centerlines[{index}].street_furniture_instances[{furniture_index}] must target a furniture-compatible strip."
            )

    centerline = AnnotatedCenterline(
        feature_id=feature_id,
        label=label,
        points=points,
        road_width_m=max(
            1.0,
            _as_float(
                value.get("road_width_m"),
                f"centerlines[{index}].road_width_m",
                default=_nominal_seed_cross_section_width(
                    forward_drive_lane_count,
                    reverse_drive_lane_count,
                    bike_lane_count,
                    bus_lane_count,
                    parking_lane_count,
                ),
            ),
        ),
        reference_width_px=max(1.0, reference_width_px) if reference_width_px is not None else None,
        forward_drive_lane_count=forward_drive_lane_count,
        reverse_drive_lane_count=reverse_drive_lane_count,
        bike_lane_count=bike_lane_count,
        bus_lane_count=bus_lane_count,
        parking_lane_count=parking_lane_count,
        highway_type=_as_string(value.get("highway_type"), "annotated_centerline"),
        cross_section_mode=cross_section_mode,
        cross_section_strips=cross_section_strips,
        street_furniture_instances=street_furniture_instances,
    )
    if centerline.resolved_cross_section_mode() == CROSS_SECTION_MODE_DETAILED and centerline.carriageway_width_m() <= 0.0:
        raise ValueError(f"centerlines[{index}] detailed cross section must include at least one center strip.")
    return centerline


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
        radius_px=max(
            6.0,
            _as_float(
                value.get("radius_px"),
                f"roundabouts[{index}].radius_px",
                default=DEFAULT_ROUNDABOUT_RADIUS_PX,
            ),
        ),
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
        pixels_per_meter=max(
            0.1,
            _as_float(
                payload.get("pixels_per_meter"),
                "pixels_per_meter",
                default=DEFAULT_PIXELS_PER_METER,
            ),
        ),
        centerlines=centerlines,
        junctions=tuple(
            _parse_marker(item, index, collection="junctions", default_kind="intersection")
            for index, item in enumerate(junctions_raw)
        ),
        roundabouts=tuple(_parse_roundabout(item, index) for index, item in enumerate(roundabouts_raw)),
        control_points=tuple(
            _parse_marker(item, index, collection="control_points", default_kind="control_point")
            for index, item in enumerate(control_points_raw)
        ),
    )


def build_reference_annotation_compose_config(overrides: Mapping[str, Any] | None = None) -> StreetComposeConfig:
    payload: MutableMapping[str, Any] = dict(overrides or {})
    return StreetComposeConfig(
        query=_as_string(payload.get("query"), "reference annotation graph"),
        length_m=max(24.0, _as_float(payload.get("length_m"), "length_m", default=120.0)),
        road_width_m=max(4.0, _as_float(payload.get("road_width_m"), "road_width_m", default=DEFAULT_ROAD_WIDTH_M)),
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
                "road_width_m": float(centerline.cross_section_width_m()),
                "carriageway_width_m": float(centerline.carriageway_width_m()),
                "cross_section_width_m": float(centerline.cross_section_width_m()),
                "cross_section_mode": centerline.resolved_cross_section_mode(),
                "reference_width_px": (
                    float(centerline.reference_width_px)
                    if centerline.reference_width_px is not None
                    else None
                ),
                "reference_width_m": reference_width_m,
                "forward_drive_lane_count": int(lane_profile["forward_drive_lane_count"]),
                "reverse_drive_lane_count": int(lane_profile["reverse_drive_lane_count"]),
                "bike_lane_count": int(lane_profile["bike_lane_count"]),
                "bus_lane_count": int(lane_profile["bus_lane_count"]),
                "parking_lane_count": int(lane_profile["parking_lane_count"]),
                "lane_profile": lane_profile,
                "highway_type": centerline.highway_type,
                "cross_section_strip_count": len(centerline.cross_section_strips),
                "street_furniture_instance_count": len(centerline.street_furniture_instances),
            }
        )
    return road_profiles


def _build_cross_section_profiles(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for centerline in annotation.centerlines:
        profiles.append(
            {
                "annotation_id": centerline.feature_id,
                "label": centerline.label,
                "cross_section_mode": centerline.resolved_cross_section_mode(),
                "carriageway_width_m": float(centerline.carriageway_width_m()),
                "cross_section_width_m": float(centerline.cross_section_width_m()),
                "strip_count": len(centerline.cross_section_strips),
                "strips": [strip.to_dict() for strip in centerline.cross_section_strips],
                "street_furniture_instance_count": len(centerline.street_furniture_instances),
            }
        )
    return profiles


def _build_street_furniture_instances(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for centerline in annotation.centerlines:
        for instance in centerline.street_furniture_instances:
            items.append(instance.to_dict())
    return items


def _collect_local_centerlines(
    annotation: ReferenceAnnotation,
) -> List[Tuple[int, AnnotatedCenterline, List[Tuple[float, float]]]]:
    local_centerlines: List[Tuple[int, AnnotatedCenterline, List[Tuple[float, float]]]] = []
    road_id = 1
    for centerline in annotation.centerlines:
        points = _dedupe_adjacent_points(
            [_pixel_to_local(annotation, x=point.x, y=point.y) for point in centerline.points]
        )
        if len(points) >= 2:
            local_centerlines.append((road_id, centerline, points))
            road_id += 1
    return local_centerlines


def _derive_topology_junctions(
    local_centerlines: Sequence[Tuple[int, AnnotatedCenterline, Sequence[Tuple[float, float]]]],
    *,
    tolerance_m: float,
) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for road_id, centerline, points in local_centerlines:
        for vertex_index, point in enumerate(points):
            matched = None
            for cluster in clusters:
                if _distance(cluster["point"], point) <= tolerance_m:
                    matched = cluster
                    break
            if matched is None:
                matched = {
                    "point": tuple(point),
                    "count": 0,
                    "members": [],
                }
                clusters.append(matched)
            count = int(matched["count"]) + 1
            anchor = (
                (float(matched["point"][0]) * float(matched["count"]) + float(point[0])) / float(count),
                (float(matched["point"][1]) * float(matched["count"]) + float(point[1])) / float(count),
            )
            matched["point"] = anchor
            matched["count"] = count
            matched["members"].append(
                {
                    "road_id": int(road_id),
                    "centerline_id": str(centerline.feature_id),
                    "vertex_index": int(vertex_index),
                    "points": tuple((float(item[0]), float(item[1])) for item in points),
                }
            )

    derived: List[Dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        members = list(cluster.get("members", []))
        connected_road_ids = sorted({int(member["road_id"]) for member in members})
        if len(connected_road_ids) < 2:
            continue
        anchor = (float(cluster["point"][0]), float(cluster["point"][1]))
        arm_records: List[Dict[str, Any]] = []
        seen_arm_keys: set[Tuple[int, int, int]] = set()
        for member in members:
            points = tuple(member["points"])
            vertex_index = int(member["vertex_index"])
            for neighbor_index in (vertex_index - 1, vertex_index + 1):
                if neighbor_index < 0 or neighbor_index >= len(points):
                    continue
                neighbor = points[neighbor_index]
                if _distance(anchor, neighbor) <= max(float(tolerance_m) * 0.25, 0.05):
                    continue
                arm_key = (
                    int(member["road_id"]),
                    int(round(float(neighbor[0]) * 1000.0)),
                    int(round(float(neighbor[1]) * 1000.0)),
                )
                if arm_key in seen_arm_keys:
                    continue
                seen_arm_keys.add(arm_key)
                arm_records.append(
                    {
                        "road_id": int(member["road_id"]),
                        "centerline_id": str(member["centerline_id"]),
                        "angle_deg": float(_angle_deg(anchor, neighbor)),
                    }
                )
        arm_angles = [float(item["angle_deg"]) for item in arm_records]
        arm_count = len(arm_angles)
        if arm_count < 3:
            continue
        kind = _classify_topology_junction_kind(arm_angles)
        derived.append(
            {
                "junction_id": f"derived_junction_{index:02d}",
                "kind": kind,
                "anchor": [round(anchor[0], 3), round(anchor[1], 3)],
                "arm_count": int(arm_count),
                "connected_road_ids": connected_road_ids,
                "connected_centerline_ids": sorted({str(member["centerline_id"]) for member in members}),
                "arm_angles_deg": [round(value, 2) for value in sorted(arm_angles)],
            }
        )
    return derived


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


def _merge_anchor_points(
    anchors: Sequence[Tuple[float, float]],
    *,
    tolerance_m: float,
) -> List[Tuple[float, float]]:
    clusters: List[Dict[str, Any]] = []
    for anchor in anchors:
        matched = None
        for cluster in clusters:
            if _distance(cluster["point"], anchor) <= tolerance_m:
                matched = cluster
                break
        if matched is None:
            clusters.append({"point": anchor, "count": 1})
        else:
            count = int(matched["count"]) + 1
            merged = (
                (matched["point"][0] * matched["count"] + anchor[0]) / count,
                (matched["point"][1] * matched["count"] + anchor[1]) / count,
            )
            matched["point"] = merged
            matched["count"] = count
    return [tuple(cluster["point"]) for cluster in clusters]


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


def _default_segment_bands(
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


def _segment_bands_for_centerline(
    *,
    centerline: AnnotatedCenterline,
    segment_id: str,
    config: StreetComposeConfig,
    poi_types: Sequence[str],
) -> Tuple[RoadSegmentBand, ...]:
    if centerline.resolved_cross_section_mode() != CROSS_SECTION_MODE_DETAILED or not centerline.cross_section_strips:
        return _default_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types)

    bands = [
        RoadSegmentBand(
            band_id=f"{segment_id}_{strip.strip_id}",
            segment_id=segment_id,
            side="left" if strip.zone == "left" else "right",
            kind=strip.kind,
            width_m=float(strip.width_m),
            allowed_categories=tuple(DEFAULT_CATEGORIES),
            nearest_poi_types=tuple(poi_types),
        )
        for strip in centerline.cross_section_strips
        if strip.zone in {"left", "right"}
    ]
    return tuple(bands) if bands else _default_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types)


def _segment_cross_section_strips(centerline: AnnotatedCenterline) -> Tuple[RoadSegmentCrossSectionStrip, ...]:
    return tuple(
        RoadSegmentCrossSectionStrip(
            strip_id=strip.strip_id,
            zone=strip.zone,
            kind=strip.kind,
            width_m=float(strip.width_m),
            direction=strip.direction,
            order_index=int(strip.order_index),
        )
        for strip in centerline.cross_section_strips
    )


def _segment_furniture_instances(
    centerline: AnnotatedCenterline,
    *,
    station_start_m: float,
    station_end_m: float,
    include_end: bool,
) -> Tuple[RoadSegmentFurnitureInstance, ...]:
    matches: List[RoadSegmentFurnitureInstance] = []
    epsilon = 1e-6
    for instance in centerline.street_furniture_instances:
        station_m = float(instance.station_m)
        within = station_start_m - epsilon <= station_m <= station_end_m + epsilon if include_end else (
            station_start_m - epsilon <= station_m < station_end_m + epsilon
        )
        if not within:
            continue
        matches.append(
            RoadSegmentFurnitureInstance(
                instance_id=instance.instance_id,
                centerline_id=instance.centerline_id,
                strip_id=instance.strip_id,
                kind=instance.kind,
                station_m=station_m,
                lateral_offset_m=float(instance.lateral_offset_m),
                yaw_deg=instance.yaw_deg,
            )
        )
    return tuple(matches)


def _segment_metaurban_asset_hints(
    centerline: AnnotatedCenterline,
) -> Tuple[RoadSegmentMetaUrbanAssetHint, ...]:
    hint_records = _build_metaurban_asset_hint_records(
        ReferenceAnnotation(
            version=ANNOTATION_SCHEMA_VERSION,
            plan_id="",
            image_path="",
            image_width_px=0,
            image_height_px=0,
            pixels_per_meter=DEFAULT_PIXELS_PER_METER,
            centerlines=(centerline,),
            junctions=(),
            roundabouts=(),
            control_points=(),
        )
    )
    return tuple(
        RoadSegmentMetaUrbanAssetHint(
            strip_id=str(record["strip_id"]),
            zone=str(record["zone"]),
            strip_kind=str(record["strip_kind"]),
            metaurban_zone=str(record["metaurban_zone"]),
            display_label=str(record["display_label"]),
            suggested_assets=tuple(str(item) for item in record.get("suggested_assets", []) or ()),
            placement_hint=str(record.get("placement_hint", "") or ""),
            asset_source=str(record.get("asset_source", "metaurban_asset_config") or "metaurban_asset_config"),
            asset_directory_status=str(record.get("asset_directory_status", "hook_only") or "hook_only"),
        )
        for record in hint_records
    )


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
    anchor_width_m = max(float(centerline.cross_section_width_m()), 1.0)
    junction_tolerance_m = max(anchor_width_m * 0.85, segment_length_target * 0.6, 3.0)
    roundabout_tolerance_m = max(anchor_width_m, segment_length_target, 5.0)
    control_tolerance_m = max(anchor_width_m, 6.0)
    lane_profile = centerline.lane_profile()
    cross_section_width_m = float(centerline.cross_section_width_m())
    carriageway_width_m = float(centerline.carriageway_width_m())
    cross_section_strips = _segment_cross_section_strips(centerline)
    metaurban_asset_hints = _segment_metaurban_asset_hints(centerline)

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
            include_end = coord_idx == len(polyline_m) - 2 and part_idx == subdivisions - 1
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
                    bands=_segment_bands_for_centerline(
                        centerline=centerline,
                        segment_id=segment_id,
                        config=config,
                        poi_types=poi_types,
                    ),
                    station_start_m=float(station_start_m),
                    station_end_m=float(station_end_m),
                    station_center_m=float((station_start_m + station_end_m) * 0.5),
                    road_width_m=carriageway_width_m,
                    lane_profile=lane_profile,
                    cross_section_strips=cross_section_strips,
                    cross_section_width_m=cross_section_width_m,
                    street_furniture_instances=_segment_furniture_instances(
                        centerline,
                        station_start_m=station_start_m,
                        station_end_m=station_end_m,
                        include_end=include_end,
                    ),
                    metaurban_asset_hints=metaurban_asset_hints,
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
                bands=_default_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types),
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
                cross_section_width_m=float(config.road_width_m),
                metaurban_asset_hints=(),
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
    local_centerlines = _collect_local_centerlines(annotation)
    if not local_centerlines:
        raise ValueError("Annotation contains no usable centerlines.")

    explicit_junctions = [_pixel_to_local(annotation, x=item.x, y=item.y) for item in annotation.junctions]
    roundabout_centers = [_pixel_to_local(annotation, x=item.x, y=item.y) for item in annotation.roundabouts]
    control_points = [(item, _pixel_to_local(annotation, x=item.x, y=item.y)) for item in annotation.control_points]
    junction_tolerance_m = max(float(resolved_config.segment_length_m) * 0.5, 4.0)
    auto_junctions = _collect_auto_junction_anchors(
        [points for _, _, points in local_centerlines],
        tolerance_m=junction_tolerance_m,
    )
    junction_anchors = _merge_anchor_points(
        [*explicit_junctions, *auto_junctions],
        tolerance_m=junction_tolerance_m,
    )

    nodes: List[RoadSegmentNode] = []
    edges: List[RoadSegmentEdge] = []
    segment_counter = 0
    edge_counter = 0
    default_anchor_width_m = max(
        [float(centerline.cross_section_width_m()) for _, centerline, _ in local_centerlines] + [float(resolved_config.road_width_m)],
    )

    for road_id, centerline, points in local_centerlines:
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
    road_id = len(local_centerlines) + 1

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
    cross_section_profiles = _build_cross_section_profiles(annotation)
    furniture_instances = _build_street_furniture_instances(annotation)
    local_centerlines_with_ids = _collect_local_centerlines(annotation)
    local_centerlines: List[List[Tuple[float, float]]] = []
    points: List[Tuple[float, float]] = []
    for _, centerline, local_points in local_centerlines_with_ids:
        local_centerlines.append(list(local_points))
        for point in centerline.points:
            points.append(_pixel_to_local(annotation, x=point.x, y=point.y))
    explicit_junctions = [_pixel_to_local(annotation, x=marker.x, y=marker.y) for marker in annotation.junctions]
    junction_tolerance_m = max(DEFAULT_SEGMENT_LENGTH_M * 0.5, 4.0)
    topology_derived_junctions = _derive_topology_junctions(
        local_centerlines_with_ids,
        tolerance_m=junction_tolerance_m,
    )
    derived_junctions = [tuple(float(value) for value in item["anchor"]) for item in topology_derived_junctions]
    topology_junctions = _merge_anchor_points(
        [*explicit_junctions, *derived_junctions],
        tolerance_m=junction_tolerance_m,
    )
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
    carriageway_widths = [float(item["carriageway_width_m"]) for item in road_profiles]
    reference_widths_px = [
        float(item["reference_width_px"])
        for item in road_profiles
        if item.get("reference_width_px") is not None
    ]
    strip_count = sum(int(item["strip_count"]) for item in cross_section_profiles)
    return {
        "plan_id": annotation.plan_id,
        "image_path": annotation.image_path,
        "image_width_px": int(annotation.image_width_px),
        "image_height_px": int(annotation.image_height_px),
        "pixels_per_meter": float(annotation.pixels_per_meter),
        "annotation_road_count": len(road_profiles),
        "centerline_count": len(annotation.centerlines),
        "detailed_centerline_count": sum(
            1
            for centerline in annotation.centerlines
            if centerline.resolved_cross_section_mode() == CROSS_SECTION_MODE_DETAILED
        ),
        "junction_count": len(annotation.junctions),
        "derived_junction_count": len(topology_derived_junctions),
        "topology_junction_count": len(topology_junctions),
        "t_junction_count": sum(1 for item in topology_derived_junctions if str(item.get("kind", "")) == "t_junction"),
        "cross_junction_count": sum(
            1 for item in topology_derived_junctions if str(item.get("kind", "")) == "cross_junction"
        ),
        "roundabout_count": len(annotation.roundabouts),
        "control_point_count": len(annotation.control_points),
        "control_point_kinds": sorted({item.kind for item in annotation.control_points}),
        "cross_section_strip_count": strip_count,
        "street_furniture_instance_count": len(furniture_instances),
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
        "min_carriageway_width_m": min(carriageway_widths) if carriageway_widths else 0.0,
        "max_carriageway_width_m": max(carriageway_widths) if carriageway_widths else 0.0,
        "avg_carriageway_width_m": (
            sum(carriageway_widths) / len(carriageway_widths)
            if carriageway_widths
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
    cross_section_profiles = _build_cross_section_profiles(annotation)
    street_furniture_instances = _build_street_furniture_instances(annotation)
    metaurban_asset_hints = _build_metaurban_asset_hint_records(annotation)
    metaurban_asset_guide = _build_metaurban_asset_guide()
    derived_junctions = _derive_topology_junctions(
        _collect_local_centerlines(annotation),
        tolerance_m=max(float(resolved_config.segment_length_m) * 0.5, 4.0),
    )
    summary = summarize_reference_annotation(annotation)
    summary.update(graph.summary())
    summary["road_profile_count"] = len(road_profiles)
    summary["cross_section_profile_count"] = len(cross_section_profiles)
    summary["street_furniture_instance_count"] = len(street_furniture_instances)
    summary["metaurban_asset_hint_count"] = len(metaurban_asset_hints)
    summary["metaurban_assets_dir_present"] = bool(metaurban_asset_guide["assets_dir_present"])
    summary["metaurban_pedestrian_assets_dir_present"] = bool(metaurban_asset_guide["assets_pedestrian_dir_present"])
    summary["segment_length_target_m"] = float(resolved_config.segment_length_m)
    summary["compose_fallback_road_width_m"] = float(resolved_config.road_width_m)
    summary["compose_fallback_lane_count"] = int(resolved_config.lane_count)
    summary["sidewalk_width_m"] = float(resolved_config.sidewalk_width_m)
    return {
        "annotation": annotation.to_dict(),
        "graph": graph.to_dict(),
        "road_profiles": road_profiles,
        "cross_section_profiles": cross_section_profiles,
        "street_furniture_instances": street_furniture_instances,
        "metaurban_asset_hints": metaurban_asset_hints,
        "metaurban_asset_guide": metaurban_asset_guide,
        "derived_junctions": derived_junctions,
        "summary": summary,
    }


__all__ = [
    "ANNOTATION_SCHEMA_VERSION",
    "AnnotatedCenterline",
    "AnnotatedCrossSectionStrip",
    "AnnotatedMarker",
    "AnnotatedRoundabout",
    "AnnotatedStreetFurnitureInstance",
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
