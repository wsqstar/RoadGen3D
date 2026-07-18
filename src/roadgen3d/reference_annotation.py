"""Reference-plan annotation parsing and graph conversion."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Tuple

from .street_priors import DEFAULT_CATEGORIES
from .street_band_semantics import detailed_strip_allowed_categories
from .semantic_design_layers import SKELETON_DESIGN_PROFILES, normalize_skeleton_design_profile
from .types import (
    RoadSegmentBand,
    RoadSegmentCrossSectionStrip,
    RoadSegmentEdge,
    RoadSegmentFurnitureInstance,
    RoadSegmentGraph,
    RoadSegmentJunction,
    RoadSegmentJunctionApproachSplit,
    RoadSegmentJunctionControlPoint,
    RoadSegmentJunctionFootPoint,
    RoadSegmentMetaUrbanAssetHint,
    RoadSegmentNode,
    StreetComposeConfig,
)

ANNOTATION_SCHEMA_VERSION = "roadgen3d_reference_annotation_v2"
DEFAULT_PIXELS_PER_METER = 1.5
DEFAULT_ROUNDABOUT_RADIUS_PX = 36.0
DEFAULT_SEGMENT_LENGTH_M = 12.0
DEFAULT_CROSSWALK_DEPTH_M = 3.0
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
CENTER_STRIP_KINDS = frozenset({"drive_lane", "bus_lane", "bike_lane", "parking_lane", "median", "grass_belt", "shared_street_surface", "colored_pavement"})
SIDE_STRIP_KINDS = frozenset(
    {
        "nearroad_buffer",
        "nearroad_furnishing",
        "clear_sidewalk",
        "farfromroad_buffer",
        "frontage_reserve",
        "colored_pavement",
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
        "tree",
        "kiosk",
        "sculpture",
    }
)
VALID_FUNCTIONAL_ZONE_KINDS = frozenset(
    {
        "plaza",
        "garden",
        "playground",
        "amphitheater",
        "outdoor_seating",
        "parking",
        "kiosk",
        "sculpture",
    }
)
VALID_REGION_ROLES = frozenset({"scene_region", "building_region", "functional_zone"})
VALID_SURFACE_ANNOTATION_KINDS = frozenset(
    {
        "bus_lane_widening",
        "safety_island",
        "colored_pavement",
        "shared_surface",
        "transit_pad",
        "paving_zone",
    }
)
VALID_SURFACE_ROLES = frozenset(
    {
        "carriageway",
        "bus_lane",
        "bike_lane",
        "parking_lane",
        "median",
        "median_green",
        "grass_belt",
        "safety_island",
        "shared_street_surface",
        "colored_pavement",
        "sidewalk",
        "furnishing",
        "context_ground",
        "transit_pad",
        "crossing",
    }
)
SURFACE_ROLE_BY_KIND: Dict[str, str] = {
    "bus_lane_widening": "bus_lane",
    "safety_island": "safety_island",
    "colored_pavement": "colored_pavement",
    "shared_surface": "shared_street_surface",
    "transit_pad": "transit_pad",
    "paving_zone": "colored_pavement",
}
SURFACE_MATERIAL_PRESET_BY_KIND: Dict[str, str] = {
    "bus_lane_widening": "bus_lane_green",
    "safety_island": "safety_island_concrete",
    "colored_pavement": "colored_pavement",
    "shared_surface": "shared_street_surface",
    "transit_pad": "transit_pad",
    "paving_zone": "paving_zone",
}
NOMINAL_STRIP_WIDTHS: Dict[str, float] = {
    "drive_lane": DEFAULT_DRIVE_LANE_WIDTH_M,
    "bus_lane": 3.5,
    "bike_lane": 1.8,
    "parking_lane": 2.5,
    "median": 0.3,
    "grass_belt": 2.0,
    "shared_street_surface": 4.0,
    "colored_pavement": 1.5,
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
    "grass_belt": "Central Green Belt",
    "shared_street_surface": "Shared Street Surface",
    "colored_pavement": "Colored Pavement",
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
    "grass_belt": "median",
    "shared_street_surface": "mixed_use",
    "colored_pavement": "decorative_surface",
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
    "grass_belt": ("Tree",),
    "shared_street_surface": (),
    "colored_pavement": (),
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
    "grass_belt": "Central grass or planted median strip.",
    "shared_street_surface": "Shared pedestrian/vehicle street surface.",
    "colored_pavement": "Decorative colored paving band.",
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


def _as_string_tuple(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raw_items = list(value)
    else:
        raw_items = []
    return tuple(dict.fromkeys(str(item).strip() for item in raw_items if str(item).strip()))


def _parse_skeleton_design_profile_fields(value: Mapping[str, Any], label: str) -> Tuple[str, str, float, Tuple[str, ...]]:
    raw_profile = value.get("skeleton_design_profile", value.get("semantic_profile_id", ""))
    profile = normalize_skeleton_design_profile(raw_profile)
    if raw_profile and not profile:
        raise ValueError(f"{label}.skeleton_design_profile must be one of {sorted(SKELETON_DESIGN_PROFILES)}.")
    source = _as_string(value.get("skeleton_design_profile_source"), "manual" if profile else "")
    confidence = _as_optional_float(value.get("skeleton_design_profile_confidence"), f"{label}.skeleton_design_profile_confidence")
    reasons = _as_string_tuple(value.get("skeleton_design_profile_reasons") or value.get("semantic_reasons") or ())
    return profile, source, 1.0 if profile and confidence is None else float(confidence or 0.0), reasons


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


@dataclass(frozen=True)
class AnnotatedZoneFurnitureInstance:
    instance_id: str
    kind: str
    x_px: float
    y_px: float
    yaw_deg: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "kind": self.kind,
            "x_px": float(self.x_px),
            "y_px": float(self.y_px),
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
    start_junction_id: str = ""
    end_junction_id: str = ""
    skeleton_design_profile: str = ""
    skeleton_design_profile_source: str = ""
    skeleton_design_profile_confidence: float = 0.0
    skeleton_design_profile_reasons: Tuple[str, ...] = ()

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
            "start_junction_id": self.start_junction_id,
            "end_junction_id": self.end_junction_id,
            "skeleton_design_profile": self.skeleton_design_profile,
            "skeleton_design_profile_source": self.skeleton_design_profile_source,
            "skeleton_design_profile_confidence": float(self.skeleton_design_profile_confidence),
            "skeleton_design_profile_reasons": list(self.skeleton_design_profile_reasons),
            "points": [point.to_dict() for point in self.points],
        }


@dataclass(frozen=True)
class AnnotatedJunction:
    feature_id: str
    label: str
    kind: str
    anchor_x: float
    anchor_y: float
    connected_centerline_ids: Tuple[str, ...] = ()
    crosswalk_depth_m: float = DEFAULT_CROSSWALK_DEPTH_M
    source_mode: str = "explicit"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.feature_id,
            "label": self.label,
            "kind": self.kind,
            "x": float(self.anchor_x),
            "y": float(self.anchor_y),
            "anchor": {"x": float(self.anchor_x), "y": float(self.anchor_y)},
            "connected_centerline_ids": [str(item) for item in self.connected_centerline_ids],
            "crosswalk_depth_m": float(self.crosswalk_depth_m),
            "source_mode": self.source_mode,
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
class AnnotatedBuildingRegion:
    feature_id: str
    label: str
    center_x_px: float
    center_y_px: float
    width_px: float
    height_px: float
    yaw_deg: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.feature_id,
            "label": self.label,
            "center_px": {
                "x": float(self.center_x_px),
                "y": float(self.center_y_px),
            },
            "width_px": float(self.width_px),
            "height_px": float(self.height_px),
            "yaw_deg": float(self.yaw_deg),
        }


@dataclass(frozen=True)
class AnnotatedFunctionalZone:
    feature_id: str
    label: str
    kind: str
    points: Tuple[AnnotationPoint, ...]
    furniture_instances: Tuple[AnnotatedZoneFurnitureInstance, ...] = ()
    skeleton_design_profile: str = ""
    skeleton_design_profile_source: str = ""
    skeleton_design_profile_confidence: float = 0.0
    skeleton_design_profile_reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.feature_id,
            "label": self.label,
            "kind": self.kind,
            "points": [point.to_dict() for point in self.points],
            "furniture_instances": [item.to_dict() for item in self.furniture_instances],
            "skeleton_design_profile": self.skeleton_design_profile,
            "skeleton_design_profile_source": self.skeleton_design_profile_source,
            "skeleton_design_profile_confidence": float(self.skeleton_design_profile_confidence),
            "skeleton_design_profile_reasons": list(self.skeleton_design_profile_reasons),
        }


@dataclass(frozen=True)
class AnnotatedRegion:
    feature_id: str
    label: str
    region_role: str
    points: Tuple[AnnotationPoint, ...]
    kind: str = ""
    land_use_type: str = ""
    source_region_id: str = ""
    derived: bool = False
    material: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.feature_id,
            "label": self.label,
            "region_role": self.region_role,
            "points": [point.to_dict() for point in self.points],
            "derived": bool(self.derived),
        }
        if self.kind:
            payload["kind"] = self.kind
        if self.land_use_type:
            payload["land_use_type"] = self.land_use_type
        if self.source_region_id:
            payload["source_region_id"] = self.source_region_id
        if self.material:
            payload["material"] = dict(self.material)
        return payload


@dataclass(frozen=True)
class AnnotatedSurfaceMaterial:
    preset: str = ""
    color_hex: str = ""
    texture_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"preset": self.preset}
        if self.color_hex:
            result["color_hex"] = self.color_hex
        if self.texture_key:
            result["texture_key"] = self.texture_key
        return result


@dataclass(frozen=True)
class AnnotatedSurfaceAnnotation:
    feature_id: str
    label: str
    kind: str
    surface_role: str
    centerline_id: str
    station_start_m: float
    station_end_m: float
    lateral_start_m: float
    lateral_end_m: float
    material: AnnotatedSurfaceMaterial = field(default_factory=AnnotatedSurfaceMaterial)
    skeleton_design_profile: str = ""
    skeleton_design_profile_source: str = ""
    skeleton_design_profile_confidence: float = 0.0
    skeleton_design_profile_reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.feature_id,
            "label": self.label,
            "kind": self.kind,
            "surface_role": self.surface_role,
            "centerline_id": self.centerline_id,
            "station_start_m": float(self.station_start_m),
            "station_end_m": float(self.station_end_m),
            "lateral_start_m": float(self.lateral_start_m),
            "lateral_end_m": float(self.lateral_end_m),
            "material": self.material.to_dict(),
            "skeleton_design_profile": self.skeleton_design_profile,
            "skeleton_design_profile_source": self.skeleton_design_profile_source,
            "skeleton_design_profile_confidence": float(self.skeleton_design_profile_confidence),
            "skeleton_design_profile_reasons": list(self.skeleton_design_profile_reasons),
        }


@dataclass(frozen=True)
class AnnotatedStationStripPatch:
    feature_id: str
    label: str
    centerline_id: str
    strip_id: str
    station_start_m: float
    station_end_m: float
    kind: str = ""
    width_m: float | None = None
    direction: str = ""

    def to_dict(self) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        if self.kind:
            updates["kind"] = self.kind
        if self.width_m is not None:
            updates["width_m"] = float(self.width_m)
        if self.direction:
            updates["direction"] = self.direction
        return {
            "id": self.feature_id,
            "label": self.label,
            "centerline_id": self.centerline_id,
            "strip_id": self.strip_id,
            "station_start_m": float(self.station_start_m),
            "station_end_m": float(self.station_end_m),
            "updates": updates,
        }


@dataclass(frozen=True)
class BezierCurve3:
    start: AnnotationPoint
    end: AnnotationPoint
    control1: AnnotationPoint
    control2: AnnotationPoint

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "control1": self.control1.to_dict(),
            "control2": self.control2.to_dict(),
        }


@dataclass(frozen=True)
class JunctionQuadrantBezierPatch:
    patch_id: str
    strip_kind: str
    inner_curve: BezierCurve3
    outer_curve: BezierCurve3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "strip_kind": self.strip_kind,
            "inner_curve": self.inner_curve.to_dict(),
            "outer_curve": self.outer_curve.to_dict(),
        }


@dataclass(frozen=True)
class JunctionQuadrantSkeletonLine:
    line_id: str
    strip_kind: str
    curve: BezierCurve3
    width_m: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "strip_kind": self.strip_kind,
            "curve": self.curve.to_dict(),
            "width_m": float(self.width_m),
        }


@dataclass(frozen=True)
class JunctionSurfaceNode:
    node_id: str
    kind: str
    point: AnnotationPoint

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "point": self.point.to_dict(),
        }


@dataclass(frozen=True)
class JunctionSurfaceEdge:
    edge_id: str
    start_node_id: str
    end_node_id: str
    kind: str
    curve: BezierCurve3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "start_node_id": self.start_node_id,
            "end_node_id": self.end_node_id,
            "kind": self.kind,
            "curve": self.curve.to_dict(),
        }


@dataclass(frozen=True)
class JunctionLaneSurface:
    surface_id: str
    lane_id: str
    arm_key: str
    flow: str
    lane_index: int
    lane_width_m: float
    skeleton_id: str
    provenance: str = "generated"
    nodes: Tuple[JunctionSurfaceNode, ...] = ()
    edges: Tuple[JunctionSurfaceEdge, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "lane_id": self.lane_id,
            "arm_key": self.arm_key,
            "flow": self.flow,
            "lane_index": int(self.lane_index),
            "lane_width_m": float(self.lane_width_m),
            "skeleton_id": self.skeleton_id,
            "provenance": self.provenance,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
        }


@dataclass(frozen=True)
class JunctionMergedSurface:
    surface_id: str
    merged_from_surface_ids: Tuple[str, ...] = ()
    merged_from_lane_ids: Tuple[str, ...] = ()
    provenance: str = "merged"
    nodes: Tuple[JunctionSurfaceNode, ...] = ()
    edges: Tuple[JunctionSurfaceEdge, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "merged_from_surface_ids": list(self.merged_from_surface_ids),
            "merged_from_lane_ids": list(self.merged_from_lane_ids),
            "provenance": self.provenance,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
        }


@dataclass(frozen=True)
class JunctionQuadrantComposition:
    quadrant_id: str
    arm_a_id: str
    arm_b_id: str
    patches: Tuple[JunctionQuadrantBezierPatch, ...] = ()
    skeleton_lines: Tuple[JunctionQuadrantSkeletonLine, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quadrant_id": self.quadrant_id,
            "arm_a_id": self.arm_a_id,
            "arm_b_id": self.arm_b_id,
            "patches": [item.to_dict() for item in self.patches],
            "skeleton_lines": [item.to_dict() for item in self.skeleton_lines],
        }


@dataclass(frozen=True)
class JunctionComposition:
    junction_id: str
    kind: str
    quadrants: Tuple[JunctionQuadrantComposition, ...] = ()
    lane_surfaces: Tuple[JunctionLaneSurface, ...] = ()
    merged_surfaces: Tuple[JunctionMergedSurface, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "junction_id": self.junction_id,
            "kind": self.kind,
            "quadrants": [item.to_dict() for item in self.quadrants],
            "lane_surfaces": [item.to_dict() for item in self.lane_surfaces],
            "merged_surfaces": [item.to_dict() for item in self.merged_surfaces],
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
    junctions: Tuple[AnnotatedJunction, ...]
    roundabouts: Tuple[AnnotatedRoundabout, ...]
    control_points: Tuple[AnnotatedMarker, ...]
    regions: Tuple[AnnotatedRegion, ...] = ()
    building_regions: Tuple[AnnotatedBuildingRegion, ...] = ()
    functional_zones: Tuple[AnnotatedFunctionalZone, ...] = ()
    surface_annotations: Tuple[AnnotatedSurfaceAnnotation, ...] = ()
    station_strip_patches: Tuple[AnnotatedStationStripPatch, ...] = ()
    junction_compositions: Tuple[JunctionComposition, ...] = ()

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
            "regions": [item.to_dict() for item in self.regions],
            "building_regions": [item.to_dict() for item in self.building_regions],
            "functional_zones": [item.to_dict() for item in self.functional_zones],
            "surface_annotations": [item.to_dict() for item in self.surface_annotations],
            "station_strip_patches": [item.to_dict() for item in self.station_strip_patches],
            "junction_compositions": [item.to_dict() for item in self.junction_compositions],
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


def _parse_zone_furniture_instance(
    value: Any,
    zone_index: int,
    furniture_index: int,
    fallback_prefix: str,
) -> AnnotatedZoneFurnitureInstance:
    if not _is_record(value):
        raise ValueError(
            f"functional_zones[{zone_index}].furniture_instances[{furniture_index}] must be an object."
        )
    kind = _safe_slug(
        _as_string(value.get("kind"), "bench"),
        "bench",
    )
    if kind not in VALID_FURNITURE_KINDS:
        raise ValueError(
            f"functional_zones[{zone_index}].furniture_instances[{furniture_index}].kind must be one of {sorted(VALID_FURNITURE_KINDS)}."
        )
    return AnnotatedZoneFurnitureInstance(
        instance_id=_as_string(
            value.get("instance_id") or value.get("id"),
            f"{fallback_prefix}_furniture_{furniture_index + 1:02d}",
        ),
        kind=kind,
        x_px=_as_float(
            value.get("x_px"),
            f"functional_zones[{zone_index}].furniture_instances[{furniture_index}].x_px",
            default=0.0,
        ),
        y_px=_as_float(
            value.get("y_px"),
            f"functional_zones[{zone_index}].furniture_instances[{furniture_index}].y_px",
            default=0.0,
        ),
        yaw_deg=_as_optional_float(
            value.get("yaw_deg"),
            f"functional_zones[{zone_index}].furniture_instances[{furniture_index}].yaw_deg",
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
    skeleton_profile, skeleton_source, skeleton_confidence, skeleton_reasons = _parse_skeleton_design_profile_fields(
        value,
        f"centerlines[{index}]",
    )
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
        start_junction_id=_as_string(value.get("start_junction_id"), ""),
        end_junction_id=_as_string(value.get("end_junction_id"), ""),
        skeleton_design_profile=skeleton_profile,
        skeleton_design_profile_source=skeleton_source,
        skeleton_design_profile_confidence=skeleton_confidence,
        skeleton_design_profile_reasons=skeleton_reasons,
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


def _parse_junction(value: Any, index: int) -> AnnotatedJunction:
    if not _is_record(value):
        raise ValueError(f"junctions[{index}] must be an object.")
    fallback_id = f"junction_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    raw_anchor = value.get("anchor")
    if _is_record(raw_anchor):
        anchor_x = _as_float(raw_anchor.get("x"), f"junctions[{index}].anchor.x")
        anchor_y = _as_float(raw_anchor.get("y"), f"junctions[{index}].anchor.y")
    else:
        anchor_x = _as_float(value.get("x"), f"junctions[{index}].x")
        anchor_y = _as_float(value.get("y"), f"junctions[{index}].y")
    raw_connected = value.get("connected_centerline_ids") or []
    if not isinstance(raw_connected, Sequence) or isinstance(raw_connected, (str, bytes)):
        raise ValueError(f"junctions[{index}].connected_centerline_ids must be an array when provided.")
    connected_centerline_ids = tuple(
        _as_string(item, "") for item in raw_connected if _as_string(item, "")
    )
    source_mode = _as_string(
        value.get("source_mode"),
        "explicit" if connected_centerline_ids or _is_record(raw_anchor) else "legacy_marker",
    )
    kind_default = "intersection" if source_mode == "legacy_marker" else "t_junction"
    kind = _safe_slug(_as_string(value.get("kind"), kind_default), kind_default)
    return AnnotatedJunction(
        feature_id=feature_id,
        label=label,
        kind=kind or kind_default,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        connected_centerline_ids=connected_centerline_ids,
        crosswalk_depth_m=max(
            0.5,
            _as_float(
                value.get("crosswalk_depth_m"),
                f"junctions[{index}].crosswalk_depth_m",
                default=DEFAULT_CROSSWALK_DEPTH_M,
            ),
        ),
        source_mode=source_mode,
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


def _parse_building_region(value: Any, index: int) -> AnnotatedBuildingRegion:
    if not _is_record(value):
        raise ValueError(f"building_regions[{index}] must be an object.")
    fallback_id = f"building_region_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    center_raw = value.get("center_px")
    if _is_record(center_raw):
        center = _parse_point(center_raw, f"building_regions[{index}].center_px")
        center_x_px = float(center.x)
        center_y_px = float(center.y)
    else:
        center_x_px = _as_float(value.get("x"), f"building_regions[{index}].x", default=0.0)
        center_y_px = _as_float(value.get("y"), f"building_regions[{index}].y", default=0.0)
    return AnnotatedBuildingRegion(
        feature_id=feature_id,
        label=label,
        center_x_px=float(center_x_px),
        center_y_px=float(center_y_px),
        width_px=max(1.0, _as_float(value.get("width_px"), f"building_regions[{index}].width_px", default=64.0)),
        height_px=max(1.0, _as_float(value.get("height_px"), f"building_regions[{index}].height_px", default=48.0)),
        yaw_deg=_normalize_angle_deg(
            _as_float(value.get("yaw_deg"), f"building_regions[{index}].yaw_deg", default=0.0)
        ),
    )


def _parse_functional_zone(value: Any, index: int) -> AnnotatedFunctionalZone:
    if not _is_record(value):
        raise ValueError(f"functional_zones[{index}] must be an object.")
    fallback_id = f"functional_zone_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    kind = _safe_slug(_as_string(value.get("kind"), "plaza"), "plaza")
    if kind not in VALID_FUNCTIONAL_ZONE_KINDS:
        raise ValueError(
            f"functional_zones[{index}].kind must be one of {sorted(VALID_FUNCTIONAL_ZONE_KINDS)}."
        )
    skeleton_profile, skeleton_source, skeleton_confidence, skeleton_reasons = _parse_skeleton_design_profile_fields(
        value,
        f"functional_zones[{index}]",
    )
    raw_points = value.get("points")
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        points: Tuple[AnnotationPoint, ...] = ()
    else:
        points = tuple(
            _parse_point(item, f"functional_zones[{index}].points[{point_idx}]")
            for point_idx, item in enumerate(raw_points)
        )
    raw_furniture = value.get("furniture_instances") or []
    if not isinstance(raw_furniture, Sequence) or isinstance(raw_furniture, (str, bytes)):
        furniture_instances: Tuple[AnnotatedZoneFurnitureInstance, ...] = ()
    else:
        furniture_instances = tuple(
            _parse_zone_furniture_instance(
                item,
                zone_index=index,
                furniture_index=furniture_index,
                fallback_prefix=feature_id,
            )
            for furniture_index, item in enumerate(raw_furniture)
        )
    return AnnotatedFunctionalZone(
        feature_id=feature_id,
        label=label,
        kind=kind,
        points=points,
        furniture_instances=furniture_instances,
        skeleton_design_profile=skeleton_profile,
        skeleton_design_profile_source=skeleton_source,
        skeleton_design_profile_confidence=skeleton_confidence,
        skeleton_design_profile_reasons=skeleton_reasons,
    )


def _parse_region(value: Any, index: int) -> AnnotatedRegion:
    if not _is_record(value):
        raise ValueError(f"regions[{index}] must be an object.")
    fallback_id = f"region_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    label = _as_string(value.get("label"), feature_id)
    region_role = _safe_slug(_as_string(value.get("region_role") or value.get("role"), "scene_region"), "scene_region")
    if region_role not in VALID_REGION_ROLES:
        raise ValueError(f"regions[{index}].region_role must be one of {sorted(VALID_REGION_ROLES)}.")
    raw_points = value.get("points")
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        raise ValueError(f"regions[{index}].points must be an array.")
    points = tuple(
        _parse_point(item, f"regions[{index}].points[{point_idx}]")
        for point_idx, item in enumerate(raw_points)
    )
    if len(points) < 3:
        raise ValueError(f"regions[{index}] must contain at least three points.")
    material_raw = value.get("material") or {}
    if material_raw and not _is_record(material_raw):
        raise ValueError(f"regions[{index}].material must be an object when provided.")
    return AnnotatedRegion(
        feature_id=feature_id,
        label=label,
        region_role=region_role,
        points=points,
        kind=_safe_slug(_as_string(value.get("kind"), ""), ""),
        land_use_type=_safe_slug(_as_string(value.get("land_use_type"), ""), ""),
        source_region_id=_as_string(value.get("source_region_id"), ""),
        derived=bool(value.get("derived", False)),
        material=dict(material_raw),
    )


def _parse_surface_material(value: Any, *, label: str, default_preset: str) -> AnnotatedSurfaceMaterial:
    if value is None:
        return AnnotatedSurfaceMaterial(preset=default_preset)
    if isinstance(value, str):
        return AnnotatedSurfaceMaterial(preset=_safe_slug(_as_string(value, default_preset), default_preset))
    if not _is_record(value):
        raise ValueError(f"{label} must be an object when provided.")
    color_hex = _as_string(value.get("color_hex"), "")
    if color_hex and not color_hex.startswith("#"):
        color_hex = f"#{color_hex}"
    return AnnotatedSurfaceMaterial(
        preset=_safe_slug(_as_string(value.get("preset"), default_preset), default_preset),
        color_hex=color_hex,
        texture_key=_safe_slug(_as_string(value.get("texture_key"), ""), ""),
    )


def _parse_surface_annotation(value: Any, index: int) -> AnnotatedSurfaceAnnotation:
    if not _is_record(value):
        raise ValueError(f"surface_annotations[{index}] must be an object.")
    fallback_id = f"surface_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    kind = _safe_slug(_as_string(value.get("kind"), "paving_zone"), "paving_zone")
    if kind not in VALID_SURFACE_ANNOTATION_KINDS:
        raise ValueError(
            f"surface_annotations[{index}].kind must be one of {sorted(VALID_SURFACE_ANNOTATION_KINDS)}."
        )
    role_default = SURFACE_ROLE_BY_KIND.get(kind, "colored_pavement")
    surface_role = _safe_slug(_as_string(value.get("surface_role"), role_default), role_default)
    if surface_role not in VALID_SURFACE_ROLES:
        raise ValueError(
            f"surface_annotations[{index}].surface_role must be one of {sorted(VALID_SURFACE_ROLES)}."
        )
    material = _parse_surface_material(
        value.get("material"),
        label=f"surface_annotations[{index}].material",
        default_preset=SURFACE_MATERIAL_PRESET_BY_KIND.get(kind, surface_role),
    )
    skeleton_profile, skeleton_source, skeleton_confidence, skeleton_reasons = _parse_skeleton_design_profile_fields(
        value,
        f"surface_annotations[{index}]",
    )
    return AnnotatedSurfaceAnnotation(
        feature_id=feature_id,
        label=_as_string(value.get("label"), feature_id),
        kind=kind,
        surface_role=surface_role,
        centerline_id=_as_string(value.get("centerline_id"), ""),
        station_start_m=_as_float(
            value.get("station_start_m"),
            f"surface_annotations[{index}].station_start_m",
        ),
        station_end_m=_as_float(
            value.get("station_end_m"),
            f"surface_annotations[{index}].station_end_m",
        ),
        lateral_start_m=_as_float(
            value.get("lateral_start_m"),
            f"surface_annotations[{index}].lateral_start_m",
        ),
        lateral_end_m=_as_float(
            value.get("lateral_end_m"),
            f"surface_annotations[{index}].lateral_end_m",
        ),
        material=material,
        skeleton_design_profile=skeleton_profile,
        skeleton_design_profile_source=skeleton_source,
        skeleton_design_profile_confidence=skeleton_confidence,
        skeleton_design_profile_reasons=skeleton_reasons,
    )


def _parse_station_strip_patch(value: Any, index: int) -> AnnotatedStationStripPatch:
    if not _is_record(value):
        raise ValueError(f"station_strip_patches[{index}] must be an object.")
    fallback_id = f"station_strip_patch_{index + 1:02d}"
    feature_id = _as_string(value.get("id") or value.get("feature_id"), fallback_id)
    updates_raw = value.get("updates") if _is_record(value.get("updates")) else {}
    kind = _safe_slug(
        _as_string(
            updates_raw.get("kind") if isinstance(updates_raw, Mapping) else value.get("kind"),
            "",
        ),
        "",
    )
    if kind and kind not in VALID_STRIP_KINDS:
        raise ValueError(
            f"station_strip_patches[{index}].updates.kind must be one of {sorted(VALID_STRIP_KINDS)}."
        )
    direction = _safe_slug(
        _as_string(
            updates_raw.get("direction") if isinstance(updates_raw, Mapping) else value.get("direction"),
            "",
        ),
        "",
    )
    if direction and direction not in VALID_STRIP_DIRECTIONS:
        raise ValueError(
            f"station_strip_patches[{index}].updates.direction must be one of {sorted(VALID_STRIP_DIRECTIONS)}."
        )
    width_value = (
        updates_raw.get("width_m")
        if isinstance(updates_raw, Mapping) and updates_raw.get("width_m") is not None
        else value.get("width_m")
    )
    width_m = (
        _as_float(width_value, f"station_strip_patches[{index}].updates.width_m")
        if width_value is not None
        else None
    )
    return AnnotatedStationStripPatch(
        feature_id=feature_id,
        label=_as_string(value.get("label"), feature_id),
        centerline_id=_as_string(value.get("centerline_id"), ""),
        strip_id=_as_string(value.get("strip_id"), ""),
        station_start_m=_as_float(
            value.get("station_start_m"),
            f"station_strip_patches[{index}].station_start_m",
        ),
        station_end_m=_as_float(
            value.get("station_end_m"),
            f"station_strip_patches[{index}].station_end_m",
        ),
        kind=kind,
        width_m=width_m,
        direction=direction,
    )


def functional_zone_to_local_coords(
    zone: AnnotatedFunctionalZone,
    annotation: ReferenceAnnotation,
) -> List[Tuple[float, float]]:
    """Convert functional zone pixel coordinates to local metres (x_east, z_north)."""
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    return [
        ((float(p.x) - center_x) / ppm, (center_y - float(p.y)) / ppm)
        for p in zone.points
    ]


def _annotation_point_xy(point: AnnotationPoint) -> Tuple[float, float]:
    return (float(point.x), float(point.y))


def _polyline_length(points: Sequence[Tuple[float, float]]) -> float:
    return float(sum(_distance(a, b) for a, b in zip(points[:-1], points[1:])))


def _centerline_length_m(centerline: AnnotatedCenterline, annotation: ReferenceAnnotation) -> float:
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    return _polyline_length([_annotation_point_xy(point) for point in centerline.points]) / ppm


def _junction_anchor_xy(junction: AnnotatedJunction) -> Tuple[float, float]:
    return (float(junction.anchor_x), float(junction.anchor_y))


def _point_on_segment_distance(
    point_xy: Tuple[float, float],
    start_xy: Tuple[float, float],
    end_xy: Tuple[float, float],
) -> float:
    dx = float(end_xy[0]) - float(start_xy[0])
    dy = float(end_xy[1]) - float(start_xy[1])
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-6:
        return _distance(point_xy, start_xy)
    ratio = max(
        0.0,
        min(
            ((float(point_xy[0]) - float(start_xy[0])) * dx + (float(point_xy[1]) - float(start_xy[1])) * dy) / length_sq,
            1.0,
        ),
    )
    projected = (
        float(start_xy[0]) + dx * ratio,
        float(start_xy[1]) + dy * ratio,
    )
    return _distance(point_xy, projected)


def _point_on_polyline_distance(
    point_xy: Tuple[float, float],
    polyline_xy: Sequence[Tuple[float, float]],
) -> float:
    if len(polyline_xy) < 2:
        return float("inf")
    best = float("inf")
    for start_xy, end_xy in zip(polyline_xy[:-1], polyline_xy[1:]):
        best = min(best, _point_on_segment_distance(point_xy, start_xy, end_xy))
    return best


def _parse_bezier_curve3(value: Any, label: str) -> BezierCurve3:
    if not _is_record(value):
        raise ValueError(f"{label} must be an object.")
    return BezierCurve3(
        start=_parse_point(value.get("start"), f"{label}.start"),
        end=_parse_point(value.get("end"), f"{label}.end"),
        control1=_parse_point(value.get("control1"), f"{label}.control1"),
        control2=_parse_point(value.get("control2"), f"{label}.control2"),
    )


def _parse_junction_quadrant_bezier_patch(value: Any, index: int) -> JunctionQuadrantBezierPatch:
    if not _is_record(value):
        raise ValueError(f"junction quadrant patch at index {index} must be an object.")
    return JunctionQuadrantBezierPatch(
        patch_id=_as_string(value.get("patch_id") or value.get("patchId"), f"patch_{index}"),
        strip_kind=_as_string(value.get("strip_kind") or value.get("stripKind"), "clear_sidewalk"),
        inner_curve=_parse_bezier_curve3(value.get("inner_curve") or value.get("innerCurve"), f"patch[{index}].inner_curve"),
        outer_curve=_parse_bezier_curve3(value.get("outer_curve") or value.get("outerCurve"), f"patch[{index}].outer_curve"),
    )


def _parse_junction_quadrant_skeleton_line(value: Any, index: int) -> JunctionQuadrantSkeletonLine:
    if not _is_record(value):
        raise ValueError(f"junction quadrant skeleton line at index {index} must be an object.")
    return JunctionQuadrantSkeletonLine(
        line_id=_as_string(value.get("line_id") or value.get("lineId"), f"line_{index}"),
        strip_kind=_as_string(value.get("strip_kind") or value.get("stripKind"), "clear_sidewalk"),
        curve=_parse_bezier_curve3(value.get("curve"), f"line[{index}].curve"),
        width_m=_as_float(value.get("width_m") or value.get("widthM"), "width_m", default=1.0),
    )


def _parse_junction_surface_node(value: Any, index: int) -> JunctionSurfaceNode:
    if not _is_record(value):
        raise ValueError(f"junction surface node at index {index} must be an object.")
    return JunctionSurfaceNode(
        node_id=_as_string(value.get("node_id") or value.get("nodeId"), f"node_{index}"),
        kind=_as_string(value.get("kind"), "custom"),
        point=_parse_point(value.get("point") or value.get("xy") or value.get("location"), f"surface node[{index}].point"),
    )


def _parse_junction_surface_edge(value: Any, index: int) -> JunctionSurfaceEdge:
    if not _is_record(value):
        raise ValueError(f"junction surface edge at index {index} must be an object.")
    return JunctionSurfaceEdge(
        edge_id=_as_string(value.get("edge_id") or value.get("edgeId"), f"edge_{index}"),
        start_node_id=_as_string(value.get("start_node_id") or value.get("startNodeId"), ""),
        end_node_id=_as_string(value.get("end_node_id") or value.get("endNodeId"), ""),
        kind=_as_string(value.get("kind"), "line"),
        curve=_parse_bezier_curve3(value.get("curve"), f"surface edge[{index}].curve"),
    )


def _parse_junction_lane_surface(value: Any, index: int) -> JunctionLaneSurface:
    if not _is_record(value):
        raise ValueError(f"junction lane surface at index {index} must be an object.")
    nodes_raw = value.get("nodes") or []
    edges_raw = value.get("edges") or []
    if not isinstance(nodes_raw, Sequence) or isinstance(nodes_raw, (str, bytes)):
        raise ValueError(f"lane_surfaces[{index}].nodes must be an array.")
    if not isinstance(edges_raw, Sequence) or isinstance(edges_raw, (str, bytes)):
        raise ValueError(f"lane_surfaces[{index}].edges must be an array.")
    return JunctionLaneSurface(
        surface_id=_as_string(value.get("surface_id") or value.get("surfaceId"), f"lane_surface_{index}"),
        lane_id=_as_string(value.get("lane_id") or value.get("laneId"), ""),
        arm_key=_as_string(value.get("arm_key") or value.get("armKey"), "north"),
        flow=_as_string(value.get("flow"), "inbound"),
        lane_index=max(0, _as_int(value.get("lane_index") or value.get("laneIndex"), "lane_index", default=0)),
        lane_width_m=max(0.01, _as_float(value.get("lane_width_m") or value.get("laneWidthM"), "lane_width_m", default=3.5)),
        skeleton_id=_as_string(value.get("skeleton_id") or value.get("skeletonId"), ""),
        provenance=_as_string(value.get("provenance"), "generated"),
        nodes=tuple(_parse_junction_surface_node(item, i) for i, item in enumerate(nodes_raw)),
        edges=tuple(_parse_junction_surface_edge(item, i) for i, item in enumerate(edges_raw)),
    )


def _parse_junction_merged_surface(value: Any, index: int) -> JunctionMergedSurface:
    if not _is_record(value):
        raise ValueError(f"junction merged surface at index {index} must be an object.")
    nodes_raw = value.get("nodes") or []
    edges_raw = value.get("edges") or []
    if not isinstance(nodes_raw, Sequence) or isinstance(nodes_raw, (str, bytes)):
        raise ValueError(f"merged_surfaces[{index}].nodes must be an array.")
    if not isinstance(edges_raw, Sequence) or isinstance(edges_raw, (str, bytes)):
        raise ValueError(f"merged_surfaces[{index}].edges must be an array.")
    merged_from_surface_ids_raw = value.get("merged_from_surface_ids") or value.get("mergedFromSurfaceIds") or []
    merged_from_lane_ids_raw = value.get("merged_from_lane_ids") or value.get("mergedFromLaneIds") or []
    if not isinstance(merged_from_surface_ids_raw, Sequence) or isinstance(merged_from_surface_ids_raw, (str, bytes)):
        raise ValueError(f"merged_surfaces[{index}].merged_from_surface_ids must be an array.")
    if not isinstance(merged_from_lane_ids_raw, Sequence) or isinstance(merged_from_lane_ids_raw, (str, bytes)):
        raise ValueError(f"merged_surfaces[{index}].merged_from_lane_ids must be an array.")
    return JunctionMergedSurface(
        surface_id=_as_string(value.get("surface_id") or value.get("surfaceId"), f"merged_surface_{index}"),
        merged_from_surface_ids=tuple(_as_string(item) for item in merged_from_surface_ids_raw if _as_string(item)),
        merged_from_lane_ids=tuple(_as_string(item) for item in merged_from_lane_ids_raw if _as_string(item)),
        provenance=_as_string(value.get("provenance"), "merged"),
        nodes=tuple(_parse_junction_surface_node(item, i) for i, item in enumerate(nodes_raw)),
        edges=tuple(_parse_junction_surface_edge(item, i) for i, item in enumerate(edges_raw)),
    )


def _parse_junction_quadrant_composition(value: Any, index: int) -> JunctionQuadrantComposition:
    if not _is_record(value):
        raise ValueError(f"junction quadrant composition at index {index} must be an object.")
    patches_raw = value.get("patches") or []
    skeleton_lines_raw = value.get("skeleton_lines") or value.get("skeletonLines") or []
    if not isinstance(patches_raw, Sequence) or isinstance(patches_raw, (str, bytes)):
        raise ValueError(f"quadrant[{index}].patches must be an array.")
    if not isinstance(skeleton_lines_raw, Sequence) or isinstance(skeleton_lines_raw, (str, bytes)):
        raise ValueError(f"quadrant[{index}].skeleton_lines must be an array.")
    return JunctionQuadrantComposition(
        quadrant_id=_as_string(value.get("quadrant_id") or value.get("quadrantId"), f"quadrant_{index}"),
        arm_a_id=_as_string(value.get("arm_a_id") or value.get("armAId"), ""),
        arm_b_id=_as_string(value.get("arm_b_id") or value.get("armBId"), ""),
        patches=tuple(_parse_junction_quadrant_bezier_patch(item, i) for i, item in enumerate(patches_raw)),
        skeleton_lines=tuple(
            _parse_junction_quadrant_skeleton_line(item, i) for i, item in enumerate(skeleton_lines_raw)
        ),
    )


def _parse_junction_composition(value: Any, index: int) -> JunctionComposition:
    if not _is_record(value):
        raise ValueError(f"junction composition at index {index} must be an object.")
    quadrants_raw = value.get("quadrants") or value.get("quadrants") or []
    lane_surfaces_raw = value.get("lane_surfaces") or value.get("laneSurfaces") or []
    merged_surfaces_raw = value.get("merged_surfaces") or value.get("mergedSurfaces") or []
    if not isinstance(quadrants_raw, Sequence) or isinstance(quadrants_raw, (str, bytes)):
        raise ValueError(f"junction_compositions[{index}].quadrants must be an array.")
    if not isinstance(lane_surfaces_raw, Sequence) or isinstance(lane_surfaces_raw, (str, bytes)):
        raise ValueError(f"junction_compositions[{index}].lane_surfaces must be an array.")
    if not isinstance(merged_surfaces_raw, Sequence) or isinstance(merged_surfaces_raw, (str, bytes)):
        raise ValueError(f"junction_compositions[{index}].merged_surfaces must be an array.")
    return JunctionComposition(
        junction_id=_as_string(value.get("junction_id") or value.get("junctionId"), f"composition_{index}"),
        kind=_as_string(value.get("kind"), "cross_junction"),
        quadrants=tuple(
            _parse_junction_quadrant_composition(item, i) for i, item in enumerate(quadrants_raw)
        ),
        lane_surfaces=tuple(_parse_junction_lane_surface(item, i) for i, item in enumerate(lane_surfaces_raw)),
        merged_surfaces=tuple(_parse_junction_merged_surface(item, i) for i, item in enumerate(merged_surfaces_raw)),
    )


def _validate_explicit_junction_model(annotation: ReferenceAnnotation) -> None:
    explicit_junctions = [junction for junction in annotation.junctions if str(junction.source_mode or "") == "explicit"]
    if not explicit_junctions:
        return
    endpoint_tolerance_px = max(float(annotation.pixels_per_meter) * 0.35, 4.0)
    centerlines_by_id = {str(centerline.feature_id): centerline for centerline in annotation.centerlines}

    for junction in explicit_junctions:
        junction_id = str(junction.feature_id)
        anchor_xy = _junction_anchor_xy(junction)
        connected_ids = {str(item) for item in junction.connected_centerline_ids if str(item)}

        for centerline_id in connected_ids:
            centerline = centerlines_by_id.get(centerline_id)
            if centerline is None:
                raise ValueError(
                    f"Explicit junction '{junction_id}' references missing centerline '{centerline_id}'."
                )
            point_xy = [_annotation_point_xy(point) for point in centerline.points]
            if len(point_xy) < 2:
                continue
            anchored_at_start = _distance(point_xy[0], anchor_xy) <= endpoint_tolerance_px
            anchored_at_end = _distance(point_xy[-1], anchor_xy) <= endpoint_tolerance_px
            if not anchored_at_start and not anchored_at_end:
                raise ValueError(
                    f"Centerline '{centerline.feature_id}' is connected to explicit junction '{junction_id}' "
                    "but does not terminate at that junction anchor."
                )
            if anchored_at_start and str(centerline.start_junction_id or "") != junction_id:
                raise ValueError(
                    f"Centerline '{centerline.feature_id}' starts at explicit junction '{junction_id}' "
                    "but is missing matching start_junction_id metadata."
                )
            if anchored_at_end and str(centerline.end_junction_id or "") != junction_id:
                raise ValueError(
                    f"Centerline '{centerline.feature_id}' ends at explicit junction '{junction_id}' "
                    "but is missing matching end_junction_id metadata."
                )

        for centerline in annotation.centerlines:
            point_xy = [_annotation_point_xy(point) for point in centerline.points]
            if len(point_xy) < 2:
                continue
            anchored_at_start = _distance(point_xy[0], anchor_xy) <= endpoint_tolerance_px
            anchored_at_end = _distance(point_xy[-1], anchor_xy) <= endpoint_tolerance_px
            endpoint_refs_junction = junction_id in {
                str(centerline.start_junction_id or ""),
                str(centerline.end_junction_id or ""),
            }
            if endpoint_refs_junction and str(centerline.feature_id) not in connected_ids:
                raise ValueError(
                    f"Centerline '{centerline.feature_id}' points to explicit junction '{junction_id}', "
                    "but the junction does not include it in connected_centerline_ids."
                )
            if anchored_at_start or anchored_at_end:
                continue
            if _point_on_polyline_distance(anchor_xy, point_xy) <= endpoint_tolerance_px:
                raise ValueError(
                    f"Centerline '{centerline.feature_id}' passes through explicit junction '{junction_id}'. "
                    "Reference Plan Annotator centerlines must terminate at explicit junctions instead of continuing through them."
                )


def _validate_surface_annotations(annotation: ReferenceAnnotation) -> None:
    if not annotation.surface_annotations:
        return
    centerlines_by_id = {str(centerline.feature_id): centerline for centerline in annotation.centerlines}
    for index, surface in enumerate(annotation.surface_annotations):
        centerline = centerlines_by_id.get(str(surface.centerline_id))
        if centerline is None:
            raise ValueError(
                f"surface_annotations[{index}].centerline_id references missing centerline '{surface.centerline_id}'."
            )
        if float(surface.station_start_m) < 0.0:
            raise ValueError(f"surface_annotations[{index}].station_start_m must be non-negative.")
        if float(surface.station_end_m) <= float(surface.station_start_m):
            raise ValueError(f"surface_annotations[{index}].station_end_m must be greater than station_start_m.")
        centerline_length_m = _centerline_length_m(centerline, annotation)
        if float(surface.station_end_m) > centerline_length_m + 1e-6:
            raise ValueError(
                f"surface_annotations[{index}].station_end_m exceeds centerline '{surface.centerline_id}' length "
                f"({centerline_length_m:.3f}m)."
            )
        if float(surface.lateral_end_m) <= float(surface.lateral_start_m):
            raise ValueError(
                f"surface_annotations[{index}].lateral_end_m must be greater than lateral_start_m."
            )
        if float(surface.lateral_end_m) - float(surface.lateral_start_m) < 0.05:
            raise ValueError(f"surface_annotations[{index}] lateral width must be at least 0.05m.")


def _validate_station_strip_patches(annotation: ReferenceAnnotation) -> None:
    if not annotation.station_strip_patches:
        return
    centerlines_by_id = {str(centerline.feature_id): centerline for centerline in annotation.centerlines}
    for index, patch in enumerate(annotation.station_strip_patches):
        centerline = centerlines_by_id.get(str(patch.centerline_id))
        if centerline is None:
            raise ValueError(
                f"station_strip_patches[{index}].centerline_id references missing centerline '{patch.centerline_id}'."
            )
        if not str(patch.strip_id):
            raise ValueError(f"station_strip_patches[{index}].strip_id is required.")
        target_strip = next((strip for strip in centerline.cross_section_strips if strip.strip_id == patch.strip_id), None)
        if target_strip is None:
            raise ValueError(
                f"station_strip_patches[{index}].strip_id references missing strip '{patch.strip_id}' "
                f"on centerline '{patch.centerline_id}'."
            )
        if not patch.kind and patch.width_m is None and not patch.direction:
            raise ValueError(f"station_strip_patches[{index}].updates must include kind, width_m, or direction.")
        if patch.kind:
            compatible_kinds = CENTER_STRIP_KINDS if target_strip.zone == "center" else SIDE_STRIP_KINDS
            if patch.kind not in compatible_kinds:
                raise ValueError(
                    f"station_strip_patches[{index}].updates.kind '{patch.kind}' is not valid for "
                    f"{target_strip.zone} strip '{patch.strip_id}'."
                )
        if patch.width_m is not None and float(patch.width_m) <= 0.0:
            raise ValueError(f"station_strip_patches[{index}].updates.width_m must be greater than 0.")
        if float(patch.station_start_m) < 0.0:
            raise ValueError(f"station_strip_patches[{index}].station_start_m must be non-negative.")
        if float(patch.station_end_m) <= float(patch.station_start_m):
            raise ValueError(f"station_strip_patches[{index}].station_end_m must be greater than station_start_m.")
        centerline_length_m = _centerline_length_m(centerline, annotation)
        if float(patch.station_end_m) > centerline_length_m + 1e-6:
            raise ValueError(
                f"station_strip_patches[{index}].station_end_m exceeds centerline '{patch.centerline_id}' length "
                f"({centerline_length_m:.3f}m)."
            )


def parse_reference_annotation(payload: Mapping[str, Any]) -> ReferenceAnnotation:
    if not _is_record(payload):
        raise ValueError("Annotation JSON must be an object.")
    version = _as_string(payload.get("version"), ANNOTATION_SCHEMA_VERSION)
    if version != ANNOTATION_SCHEMA_VERSION:
        raise ValueError(
            f"Annotation version must be '{ANNOTATION_SCHEMA_VERSION}', got {version or '<missing>'}."
        )

    centerlines_raw = payload.get("centerlines") or []
    junctions_raw = payload.get("junctions") or []
    roundabouts_raw = payload.get("roundabouts") or []
    control_points_raw = payload.get("control_points") or []
    regions_raw = payload.get("regions") or []
    building_regions_raw = payload.get("building_regions") or []
    functional_zones_raw = payload.get("functional_zones") or []
    surface_annotations_raw = payload.get("surface_annotations") or []
    station_strip_patches_raw = payload.get("station_strip_patches") or []
    junction_compositions_raw = payload.get("junction_compositions") or payload.get("compositions") or []
    if not isinstance(junction_compositions_raw, Sequence) or isinstance(junction_compositions_raw, (str, bytes)):
        raise ValueError("junction_compositions must be an array.")

    if not isinstance(centerlines_raw, Sequence) or isinstance(centerlines_raw, (str, bytes)):
        raise ValueError("centerlines must be an array.")
    if not isinstance(junctions_raw, Sequence) or isinstance(junctions_raw, (str, bytes)):
        raise ValueError("junctions must be an array.")
    if not isinstance(roundabouts_raw, Sequence) or isinstance(roundabouts_raw, (str, bytes)):
        raise ValueError("roundabouts must be an array.")
    if not isinstance(control_points_raw, Sequence) or isinstance(control_points_raw, (str, bytes)):
        raise ValueError("control_points must be an array.")
    if not isinstance(regions_raw, Sequence) or isinstance(regions_raw, (str, bytes)):
        raise ValueError("regions must be an array.")
    if not isinstance(building_regions_raw, Sequence) or isinstance(building_regions_raw, (str, bytes)):
        raise ValueError("building_regions must be an array.")
    if not isinstance(functional_zones_raw, Sequence) or isinstance(functional_zones_raw, (str, bytes)):
        raise ValueError("functional_zones must be an array.")
    if not isinstance(surface_annotations_raw, Sequence) or isinstance(surface_annotations_raw, (str, bytes)):
        raise ValueError("surface_annotations must be an array.")
    if not isinstance(station_strip_patches_raw, Sequence) or isinstance(station_strip_patches_raw, (str, bytes)):
        raise ValueError("station_strip_patches must be an array.")

    centerlines = tuple(_parse_centerline(item, index) for index, item in enumerate(centerlines_raw))
    if not centerlines:
        raise ValueError("At least one centerline is required.")

    annotation = ReferenceAnnotation(
        version=version,
        plan_id=_as_string(payload.get("plan_id"), "custom_annotation"),
        image_path=_as_string(payload.get("image_path"), ""),
        image_width_px=_as_int(payload.get("image_width_px"), "image_width_px", default=0),
        image_height_px=_as_int(payload.get("image_height_px"), "image_height_px", default=0),
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
            _parse_junction(item, index)
            for index, item in enumerate(junctions_raw)
        ),
        roundabouts=tuple(_parse_roundabout(item, index) for index, item in enumerate(roundabouts_raw)),
        control_points=tuple(
            _parse_marker(item, index, collection="control_points", default_kind="control_point")
            for index, item in enumerate(control_points_raw)
        ),
        regions=tuple(
            _parse_region(item, index)
            for index, item in enumerate(regions_raw)
        ),
        building_regions=tuple(
            _parse_building_region(item, index)
            for index, item in enumerate(building_regions_raw)
        ),
        functional_zones=tuple(
            _parse_functional_zone(item, index)
            for index, item in enumerate(functional_zones_raw)
        ),
        surface_annotations=tuple(
            _parse_surface_annotation(item, index)
            for index, item in enumerate(surface_annotations_raw)
        ),
        station_strip_patches=tuple(
            _parse_station_strip_patch(item, index)
            for index, item in enumerate(station_strip_patches_raw)
        ),
        junction_compositions=tuple(
            _parse_junction_composition(item, index)
            for index, item in enumerate(junction_compositions_raw)
        ),
    )
    if annotation.image_width_px <= 0 or annotation.image_height_px <= 0:
        raise ValueError("Annotation image_width_px and image_height_px must be positive.")
    _validate_surface_annotations(annotation)
    _validate_station_strip_patches(annotation)
    _validate_explicit_junction_model(annotation)
    return annotation


def build_reference_annotation_compose_config(overrides: Mapping[str, Any] | None = None) -> StreetComposeConfig:
    payload: MutableMapping[str, Any] = dict(overrides or {})
    corner_mode = _as_string(payload.get("junction_corner_radius_mode"), "auto").strip().lower()
    if corner_mode not in {"auto", "fixed"}:
        raise ValueError("junction_corner_radius_mode must be 'auto' or 'fixed'.")
    fixed_corner_radius = payload.get("junction_corner_radius_m")
    fixed_corner_radius_m = (
        max(0.25, _as_float(fixed_corner_radius, "junction_corner_radius_m"))
        if fixed_corner_radius is not None
        else None
    )
    min_corner_radius_m = max(
        0.25,
        _as_float(payload.get("junction_corner_min_radius_m"), "junction_corner_min_radius_m", default=3.0),
    )
    max_corner_radius_m = max(
        min_corner_radius_m,
        _as_float(payload.get("junction_corner_max_radius_m"), "junction_corner_max_radius_m", default=8.0),
    )
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
        junction_corner_radius_mode=corner_mode,
        junction_corner_radius_m=fixed_corner_radius_m,
        junction_corner_min_radius_m=min_corner_radius_m,
        junction_corner_max_radius_m=max_corner_radius_m,
        junction_precision_grid_m=max(
            0.0001,
            _as_float(payload.get("junction_precision_grid_m"), "junction_precision_grid_m", default=0.001),
        ),
        junction_seam_extension_m=max(
            0.0,
            _as_float(payload.get("junction_seam_extension_m"), "junction_seam_extension_m", default=0.02),
        ),
        curb_width_m=max(0.05, _as_float(payload.get("curb_width_m"), "curb_width_m", default=0.12)),
        curb_reveal_m=max(0.05, _as_float(payload.get("curb_reveal_m"), "curb_reveal_m", default=0.15)),
        curb_top_mode="flush_with_sidewalk",
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


def _build_surface_annotation_records(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    return [surface.to_dict() for surface in annotation.surface_annotations]


def _build_station_strip_patch_records(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    return [patch.to_dict() for patch in annotation.station_strip_patches]


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


def _junction_anchor_local(annotation: ReferenceAnnotation, junction: AnnotatedJunction) -> Tuple[float, float]:
    return _pixel_to_local(annotation, x=junction.anchor_x, y=junction.anchor_y)


def _build_explicit_graph_junctions(
    annotation: ReferenceAnnotation,
    local_centerlines: Sequence[Tuple[int, AnnotatedCenterline, Sequence[Tuple[float, float]]]],
) -> List[RoadSegmentJunction]:
    centerline_lookup = {
        str(centerline.feature_id): (int(road_id), centerline, tuple((float(x), float(y)) for x, y in points))
        for road_id, centerline, points in local_centerlines
    }
    result: List[RoadSegmentJunction] = []
    for junction in annotation.junctions:
        if not junction.connected_centerline_ids:
            continue
        paired_connections: List[Tuple[int, str]] = []
        for centerline_id in junction.connected_centerline_ids:
            match = centerline_lookup.get(str(centerline_id))
            if match is None:
                continue
            pair = (int(match[0]), str(centerline_id))
            if pair not in paired_connections:
                paired_connections.append(pair)
        connected_road_ids = [int(item[0]) for item in paired_connections]
        connected_centerline_ids = [str(item[1]) for item in paired_connections]
        if len(set(connected_road_ids)) < 2:
            continue
        result.append(
            RoadSegmentJunction(
                junction_id=str(junction.feature_id),
                kind=str(junction.kind),
                anchor_xy=_junction_anchor_local(annotation, junction),
                connected_road_ids=tuple(connected_road_ids),
                connected_centerline_ids=tuple(connected_centerline_ids),
                crosswalk_depth_m=float(junction.crosswalk_depth_m),
                source_mode=str(junction.source_mode),
            )
        )
    return result


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

    return _segment_bands_for_strips(
        strips=centerline.cross_section_strips,
        segment_id=segment_id,
        config=config,
        poi_types=poi_types,
    )


def _segment_bands_for_strips(
    *,
    strips: Sequence[AnnotatedCrossSectionStrip],
    segment_id: str,
    config: StreetComposeConfig,
    poi_types: Sequence[str],
) -> Tuple[RoadSegmentBand, ...]:
    if not strips:
        return _default_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types)
    bands = [
        RoadSegmentBand(
            band_id=f"{segment_id}_{strip.strip_id}",
            segment_id=segment_id,
            side="left" if strip.zone == "left" else "right",
            kind=strip.kind,
            width_m=float(strip.width_m),
            allowed_categories=(
                detailed_strip_allowed_categories(strip.kind)
                if strip.kind in SIDE_STRIP_KINDS
                else tuple(DEFAULT_CATEGORIES)
            ),
            nearest_poi_types=tuple(poi_types),
        )
        for strip in strips
        if strip.zone in {"left", "right"}
    ]
    return tuple(bands) if bands else _default_segment_bands(segment_id=segment_id, config=config, poi_types=poi_types)


def _segment_cross_section_strips(centerline: AnnotatedCenterline) -> Tuple[RoadSegmentCrossSectionStrip, ...]:
    return _segment_cross_section_strips_from_strips(centerline.cross_section_strips)


def _segment_cross_section_strips_from_strips(
    strips: Sequence[AnnotatedCrossSectionStrip],
) -> Tuple[RoadSegmentCrossSectionStrip, ...]:
    return tuple(
        RoadSegmentCrossSectionStrip(
            strip_id=strip.strip_id,
            zone=strip.zone,
            kind=strip.kind,
            width_m=float(strip.width_m),
            direction=strip.direction,
            order_index=int(strip.order_index),
        )
        for strip in strips
    )


def _cross_section_strips_for_station(
    centerline: AnnotatedCenterline,
    *,
    station_center_m: float,
    station_strip_patches: Sequence[AnnotatedStationStripPatch],
) -> Tuple[AnnotatedCrossSectionStrip, ...]:
    if not centerline.cross_section_strips:
        return ()
    strips = list(centerline.cross_section_strips)
    for patch in station_strip_patches:
        if str(patch.centerline_id) != str(centerline.feature_id):
            continue
        if not (float(patch.station_start_m) <= float(station_center_m) < float(patch.station_end_m)):
            continue
        for strip_index, strip in enumerate(strips):
            if strip.strip_id != patch.strip_id:
                continue
            strips[strip_index] = AnnotatedCrossSectionStrip(
                strip_id=strip.strip_id,
                zone=strip.zone,
                kind=patch.kind or strip.kind,
                width_m=float(patch.width_m) if patch.width_m is not None else float(strip.width_m),
                direction=patch.direction or strip.direction,
                order_index=int(strip.order_index),
            )
            break
    return tuple(strips)


def _segment_breakpoints_for_station_patches(
    *,
    segment_start_station_m: float,
    segment_length_m: float,
    segment_length_target_m: float,
    station_strip_patches: Sequence[AnnotatedStationStripPatch],
) -> Tuple[float, ...]:
    length = max(float(segment_length_m), 0.0)
    if length <= 1e-6:
        return (0.0, length)
    breakpoints = {0.0, length}
    subdivisions = max(1, int(math.ceil(length / max(float(segment_length_target_m), 1e-6))))
    for step in range(1, subdivisions):
        breakpoints.add(length * float(step) / float(subdivisions))
    segment_end_station_m = float(segment_start_station_m) + length
    for patch in station_strip_patches:
        for station in (float(patch.station_start_m), float(patch.station_end_m)):
            if float(segment_start_station_m) + 1e-6 < station < segment_end_station_m - 1e-6:
                breakpoints.add(station - float(segment_start_station_m))
    return tuple(sorted(breakpoints))


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
    station_strip_patches: Sequence[AnnotatedStationStripPatch] = (),
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
    metaurban_asset_hints = _segment_metaurban_asset_hints(centerline)

    for coord_idx in range(len(polyline_m) - 1):
        start = tuple(polyline_m[coord_idx])
        end = tuple(polyline_m[coord_idx + 1])
        length = _distance(start, end)
        if length <= 1e-6:
            continue
        coord_station_start_m = float(station_m)
        breakpoints = _segment_breakpoints_for_station_patches(
            segment_start_station_m=coord_station_start_m,
            segment_length_m=length,
            segment_length_target_m=segment_length_target,
            station_strip_patches=station_strip_patches,
        )
        for part_idx, (local_start_m, local_end_m) in enumerate(zip(breakpoints[:-1], breakpoints[1:])):
            if float(local_end_m) <= float(local_start_m) + 1e-6:
                continue
            a = _interpolate(start, end, float(local_start_m) / float(length))
            b = _interpolate(start, end, float(local_end_m) / float(length))
            center = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
            segment_id = f"annot_seg_{segment_counter:04d}"
            segment_counter += 1
            length_m = _distance(a, b)
            station_start_m = coord_station_start_m + float(local_start_m)
            station_end_m = coord_station_start_m + float(local_end_m)
            station_center_m = float((station_start_m + station_end_m) * 0.5)
            segment_strips = _cross_section_strips_for_station(
                centerline,
                station_center_m=station_center_m,
                station_strip_patches=station_strip_patches,
            )
            if segment_strips:
                lane_profile = _lane_profile_from_strips(segment_strips)
                cross_section_width_m = float(sum(max(float(strip.width_m), 0.0) for strip in segment_strips))
                carriageway_width_m = float(
                    sum(max(float(strip.width_m), 0.0) for strip in segment_strips if strip.zone == "center")
                )
                cross_section_strips = _segment_cross_section_strips_from_strips(segment_strips)
                bands = _segment_bands_for_strips(
                    strips=segment_strips,
                    segment_id=segment_id,
                    config=config,
                    poi_types=(),
                )
            else:
                lane_profile = centerline.lane_profile()
                cross_section_width_m = float(centerline.cross_section_width_m())
                carriageway_width_m = float(centerline.carriageway_width_m())
                cross_section_strips = _segment_cross_section_strips(centerline)
                bands = _segment_bands_for_centerline(
                    centerline=centerline,
                    segment_id=segment_id,
                    config=config,
                    poi_types=(),
                )
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
                (bool(centerline.start_junction_id) and coord_idx == 0 and part_idx == 0)
                or (
                    bool(centerline.end_junction_id)
                    and coord_idx == len(polyline_m) - 2
                    and part_idx == len(breakpoints) - 2
                )
                or part_idx == 0
                or part_idx == len(breakpoints) - 2
                or any(_distance(center, anchor) <= junction_tolerance_m for anchor in junction_anchors)
                or any(_distance(center, anchor) <= roundabout_tolerance_m for anchor in roundabout_anchors)
            )
            include_end = coord_idx == len(polyline_m) - 2 and part_idx == len(breakpoints) - 2
            if poi_types:
                bands = tuple(
                    RoadSegmentBand(
                        band_id=band.band_id,
                        segment_id=band.segment_id,
                        side=band.side,
                        kind=band.kind,
                        width_m=band.width_m,
                        allowed_categories=band.allowed_categories,
                        nearest_poi_types=tuple(poi_types),
                    )
                    for band in bands
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
                    bands=bands,
                    station_start_m=float(station_start_m),
                    station_end_m=float(station_end_m),
                    station_center_m=station_center_m,
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
                    start_junction_id=(
                        str(centerline.start_junction_id)
                        if coord_idx == 0 and part_idx == 0 and centerline.start_junction_id
                        else ""
                    ),
                    end_junction_id=(
                        str(centerline.end_junction_id)
                        if coord_idx == len(polyline_m) - 2 and part_idx == len(breakpoints) - 2 and centerline.end_junction_id
                        else ""
                    ),
                    skeleton_design_profile=str(centerline.skeleton_design_profile),
                    skeleton_design_profile_source=str(centerline.skeleton_design_profile_source or ("manual" if centerline.skeleton_design_profile else "")),
                    skeleton_design_profile_confidence=float(centerline.skeleton_design_profile_confidence),
                    skeleton_design_profile_reasons=tuple(centerline.skeleton_design_profile_reasons),
                )
            )
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
        station_m = coord_station_start_m + length

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

    explicit_graph_junctions = _build_explicit_graph_junctions(annotation, local_centerlines)
    explicit_junctions = [tuple(float(value) for value in junction.anchor_xy) for junction in explicit_graph_junctions]
    legacy_marker_junctions = [
        _junction_anchor_local(annotation, item)
        for item in annotation.junctions
        if not item.connected_centerline_ids
    ]
    roundabout_centers = [_pixel_to_local(annotation, x=item.x, y=item.y) for item in annotation.roundabouts]
    control_points = [(item, _pixel_to_local(annotation, x=item.x, y=item.y)) for item in annotation.control_points]
    junction_tolerance_m = max(float(resolved_config.segment_length_m) * 0.5, 4.0)
    if explicit_graph_junctions:
        junction_anchors = list(explicit_junctions)
    else:
        auto_junctions = _collect_auto_junction_anchors(
            [points for _, _, points in local_centerlines],
            tolerance_m=junction_tolerance_m,
        )
        junction_anchors = _merge_anchor_points(
            [*legacy_marker_junctions, *auto_junctions],
            tolerance_m=junction_tolerance_m,
        )

    nodes: List[RoadSegmentNode] = []
    edges: List[RoadSegmentEdge] = []
    segment_counter = 0
    edge_counter = 0
    centerline_terminals: Dict[str, Dict[str, Any]] = {}
    default_anchor_width_m = max(
        [float(centerline.cross_section_width_m()) for _, centerline, _ in local_centerlines] + [float(resolved_config.road_width_m)],
    )

    for road_id, centerline, points in local_centerlines:
        centerline_station_strip_patches = tuple(
            patch
            for patch in annotation.station_strip_patches
            if str(patch.centerline_id) == str(centerline.feature_id)
        )
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
            station_strip_patches=centerline_station_strip_patches,
        )
        nodes.extend(centerline_nodes)
        edges.extend(centerline_edges)
        if centerline_nodes:
            centerline_terminals[str(centerline.feature_id)] = {
                "road_id": int(road_id),
                "start_segment_id": str(centerline_nodes[0].segment_id),
                "end_segment_id": str(centerline_nodes[-1].segment_id),
                "start_xy": tuple(points[0]),
                "end_xy": tuple(points[-1]),
                "start_junction_id": str(centerline.start_junction_id or ""),
                "end_junction_id": str(centerline.end_junction_id or ""),
            }
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
    graph_junctions: List[RoadSegmentJunction] = list(explicit_graph_junctions)

    if explicit_graph_junctions:
        for junction in explicit_graph_junctions:
            touching_segment_ids: List[str] = []
            for centerline_id in junction.connected_centerline_ids:
                terminal = centerline_terminals.get(str(centerline_id))
                if terminal is None:
                    continue
                matched = False
                if terminal["start_junction_id"] == junction.junction_id:
                    touching_segment_ids.append(str(terminal["start_segment_id"]))
                    matched = True
                if terminal["end_junction_id"] == junction.junction_id:
                    touching_segment_ids.append(str(terminal["end_segment_id"]))
                    matched = True
                if matched:
                    continue
                anchor = tuple(float(value) for value in junction.anchor_xy)
                start_distance = _distance(tuple(terminal["start_xy"]), anchor)
                end_distance = _distance(tuple(terminal["end_xy"]), anchor)
                if start_distance <= max(junction_tolerance_m, 0.5):
                    touching_segment_ids.append(str(terminal["start_segment_id"]))
                if end_distance <= max(junction_tolerance_m, 0.5):
                    touching_segment_ids.append(str(terminal["end_segment_id"]))
            unique_touching = list(dict.fromkeys(touching_segment_ids))
            for from_idx, from_segment_id in enumerate(unique_touching):
                for to_segment_id in unique_touching[from_idx + 1:]:
                    for pair in ((from_segment_id, to_segment_id), (to_segment_id, from_segment_id)):
                        if pair[0] == pair[1] or pair in edge_pairs:
                            continue
                        edge_pairs.add(pair)
                        edges.append(
                            RoadSegmentEdge(
                                edge_id=f"annot_edge_{edge_counter:04d}",
                                from_segment_id=pair[0],
                                to_segment_id=pair[1],
                                weight=1.0,
                            )
                        )
                        edge_counter += 1
    else:
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
        graph_junctions = [
            RoadSegmentJunction(
                junction_id=str(item["junction_id"]),
                kind=str(item["kind"]),
                anchor_xy=(float(item["anchor"][0]), float(item["anchor"][1])),
                connected_road_ids=tuple(int(value) for value in item.get("connected_road_ids", []) or ()),
                connected_centerline_ids=tuple(str(value) for value in item.get("connected_centerline_ids", []) or ()),
                crosswalk_depth_m=DEFAULT_CROSSWALK_DEPTH_M,
                source_mode="derived",
            )
            for item in _derive_topology_junctions(local_centerlines, tolerance_m=junction_tolerance_m)
        ]

    return RoadSegmentGraph(nodes=tuple(nodes), edges=tuple(edges), junctions=tuple(graph_junctions), mode="annotation")


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
    explicit_junctions = [_junction_anchor_local(annotation, marker) for marker in annotation.junctions]
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
        points.append(_junction_anchor_local(annotation, marker))
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
    for region in annotation.building_regions:
        center_xy = _pixel_to_local(annotation, x=region.center_x_px, y=region.center_y_px)
        half_width_m = float(region.width_px) / max(float(annotation.pixels_per_meter), 1.0) * 0.5
        half_height_m = float(region.height_px) / max(float(annotation.pixels_per_meter), 1.0) * 0.5
        points.extend(
            [
                (center_xy[0] - half_width_m, center_xy[1] - half_height_m),
                (center_xy[0] + half_width_m, center_xy[1] + half_height_m),
            ]
        )
    for region in annotation.regions:
        points.extend(_pixel_to_local(annotation, x=point.x, y=point.y) for point in region.points)
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
    surface_roles: Dict[str, int] = {}
    for surface in annotation.surface_annotations:
        surface_roles[surface.surface_role] = surface_roles.get(surface.surface_role, 0) + 1
    region_role_counts: Dict[str, int] = {}
    for region in annotation.regions:
        region_role_counts[region.region_role] = region_role_counts.get(region.region_role, 0) + 1
    return {
        "plan_id": annotation.plan_id,
        "image_path": annotation.image_path,
        "image_width_px": int(annotation.image_width_px),
        "image_height_px": int(annotation.image_height_px),
        "pixels_per_meter": float(annotation.pixels_per_meter),
        "annotation_road_count": len(road_profiles),
        "centerline_count": len(annotation.centerlines),
        "explicit_junction_count": sum(1 for item in annotation.junctions if item.source_mode == "explicit"),
        "legacy_junction_count": sum(1 for item in annotation.junctions if item.source_mode != "explicit"),
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
        "region_count": len(annotation.regions),
        "scene_region_count": region_role_counts.get("scene_region", 0),
        "region_role_counts": region_role_counts,
        "derived_region_count": sum(1 for item in annotation.regions if item.derived),
        "building_region_count": len(annotation.building_regions),
        "region_building_region_count": region_role_counts.get("building_region", 0),
        "surface_annotation_count": len(annotation.surface_annotations),
        "surface_annotation_role_counts": surface_roles,
        "station_strip_patch_count": len(annotation.station_strip_patches),
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
    surface_annotations = _build_surface_annotation_records(annotation)
    station_strip_patches = _build_station_strip_patch_records(annotation)
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
    summary["surface_annotation_count"] = len(surface_annotations)
    summary["station_strip_patch_count"] = len(station_strip_patches)
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
        "surface_annotations": surface_annotations,
        "station_strip_patches": station_strip_patches,
        "metaurban_asset_hints": metaurban_asset_hints,
        "metaurban_asset_guide": metaurban_asset_guide,
        "derived_junctions": derived_junctions,
        "summary": summary,
    }


__all__ = [
    "ANNOTATION_SCHEMA_VERSION",
    "AnnotatedCenterline",
    "AnnotatedBuildingRegion",
    "AnnotatedCrossSectionStrip",
    "AnnotatedJunction",
    "AnnotatedMarker",
    "AnnotatedRegion",
    "AnnotatedRoundabout",
    "AnnotatedStationStripPatch",
    "AnnotatedSurfaceAnnotation",
    "AnnotatedSurfaceMaterial",
    "AnnotatedStreetFurnitureInstance",
    "AnnotationPoint",
    "DEFAULT_PIXELS_PER_METER",
    "DEFAULT_ROUNDABOUT_RADIUS_PX",
    "JunctionComposition",
    "JunctionLaneSurface",
    "JunctionMergedSurface",
    "JunctionQuadrantBezierPatch",
    "JunctionQuadrantComposition",
    "JunctionQuadrantSkeletonLine",
    "JunctionSurfaceEdge",
    "JunctionSurfaceNode",
    "ReferenceAnnotation",
    "VALID_REGION_ROLES",
    "build_reference_annotation_compose_config",
    "build_reference_annotation_graph_payload",
    "build_segment_graph_from_annotation",
    "parse_reference_annotation",
    "summarize_reference_annotation",
]
