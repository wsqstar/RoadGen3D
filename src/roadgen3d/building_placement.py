"""Geometry helpers for surrounding-building placement.

The main composer owns retrieval, mesh caching, and scene export.  This module
keeps the placement-safety geometry small and testable: building mesh origins
are aligned to target centers, then final footprints are checked against
road-occupied surfaces before they are admitted to the scene.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence, Tuple


VEHICLE_SURFACE_ROLES = frozenset(
    {
        "carriageway",
        "drive_lane",
        "bike_lane",
        "bus_lane",
        "parking_lane",
        "center_shared_street_surface",
    }
)

BUILDING_FORBIDDEN_SURFACE_ROLES = VEHICLE_SURFACE_ROLES | frozenset(
    {
        "clear_path",
        "clear_sidewalk",
        "colored_pavement",
        "context_ground",
        "crossing",
        "crosswalk",
        "curb",
        "furnishing",
        "furnishing_zone",
        "grass_belt",
        "median",
        "median_green",
        "nearroad",
        "nearroad_buffer",
        "planting_soil",
        "safety_island",
        "shared_street_surface",
        "sidewalk",
        "transit_pad",
    }
)

BUILDING_FORBIDDEN_STRIP_TOKENS = BUILDING_FORBIDDEN_SURFACE_ROLES | frozenset(
    {
        "center",
        "frontage",
        "parking",
        "road",
        "strip",
    }
)

BUILDING_ALLOWED_STRIP_TOKENS = frozenset(
    {
        "building_buffer",
        "building_region",
    }
)

BUILDING_FORBIDDEN_JUNCTION_PATCH_KEYS = frozenset(
    {
        "canonical_surface_patches",
        "crosswalk_patches",
        "frontage_corner_patches",
        "lane_surface_patches",
        "merged_surface_patches",
        "nearroad_corner_patches",
        "normalized_surface_patches",
        "sidewalk_corner_patches",
        "turn_lane_patches",
    }
)


@dataclass(frozen=True)
class BuildingPoseResolution:
    """Resolved transform position and bounds for one building target."""

    placement_xz: Tuple[float, float]
    visual_center_xz: Tuple[float, float]
    bbox_xz: Tuple[float, float, float, float]
    adjusted: bool = False
    rejected: bool = False
    reject_reason: str = ""
    push_distance_m: float = 0.0
    checked_vehicle_lanes: bool = False


def _scale_xz(scale: float | Sequence[float]) -> Tuple[float, float]:
    if isinstance(scale, (list, tuple)):
        scale_x = float(scale[0]) if len(scale) >= 1 else 1.0
        scale_z = float(scale[2]) if len(scale) >= 3 else (float(scale[-1]) if scale else scale_x)
    else:
        scale_x = float(scale)
        scale_z = float(scale)
    return scale_x, scale_z


def _rotate_local_xz(local_x: float, local_z: float, yaw_deg: float) -> Tuple[float, float]:
    yaw_rad = math.radians(float(yaw_deg))
    cos_y = math.cos(yaw_rad)
    sin_y = math.sin(yaw_rad)
    return (
        float(local_x * cos_y + local_z * sin_y),
        float(-local_x * sin_y + local_z * cos_y),
    )


def _bbox_from_points(points: Sequence[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [float(point[0]) for point in points]
    zs = [float(point[1]) for point in points]
    return min(xs), max(xs), min(zs), max(zs)


def _bbox_intersects(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])


def placement_xz_for_visual_center(
    *,
    visual_center_xz: Tuple[float, float],
    yaw_deg: float,
    center_x: float,
    center_z: float,
    scale: float | Sequence[float],
) -> Tuple[float, float]:
    """Return transform translation that places the mesh bbox center at target."""

    scale_x, scale_z = _scale_xz(scale)
    offset_x, offset_z = _rotate_local_xz(
        float(center_x) * scale_x,
        float(center_z) * scale_z,
        yaw_deg,
    )
    return (
        float(visual_center_xz[0]) - offset_x,
        float(visual_center_xz[1]) - offset_z,
    )


def building_footprint_points(
    *,
    placement_xz: Tuple[float, float],
    yaw_deg: float,
    half_x: float,
    half_z: float,
    center_x: float,
    center_z: float,
    scale: float | Sequence[float],
    clearance_m: float = 0.0,
) -> Tuple[Tuple[float, float], ...]:
    """Compute the world XZ corners of a transformed building bbox footprint."""

    scale_x, scale_z = _scale_xz(scale)
    half_x_m = max(float(half_x) * scale_x + float(clearance_m), 0.0)
    half_z_m = max(float(half_z) * scale_z + float(clearance_m), 0.0)
    center_x_m = float(center_x) * scale_x
    center_z_m = float(center_z) * scale_z
    local_corners = (
        (center_x_m - half_x_m, center_z_m - half_z_m),
        (center_x_m + half_x_m, center_z_m - half_z_m),
        (center_x_m + half_x_m, center_z_m + half_z_m),
        (center_x_m - half_x_m, center_z_m + half_z_m),
    )
    points = []
    for local_x, local_z in local_corners:
        dx, dz = _rotate_local_xz(local_x, local_z, yaw_deg)
        points.append((float(placement_xz[0]) + dx, float(placement_xz[1]) + dz))
    return tuple(points)


def building_bbox_xz(
    *,
    placement_xz: Tuple[float, float],
    yaw_deg: float,
    half_x: float,
    half_z: float,
    center_x: float,
    center_z: float,
    scale: float | Sequence[float],
    clearance_m: float = 0.0,
) -> Tuple[float, float, float, float]:
    return _bbox_from_points(
        building_footprint_points(
            placement_xz=placement_xz,
            yaw_deg=yaw_deg,
            half_x=half_x,
            half_z=half_z,
            center_x=center_x,
            center_z=center_z,
            scale=scale,
            clearance_m=clearance_m,
        )
    )


def _candidate_geometries(values: Iterable[Any]) -> Iterable[Any]:
    for value in values:
        if value is None or getattr(value, "is_empty", True):
            continue
        yield value


def _union_geometries(geometries: Sequence[Any]) -> Any:
    if not geometries:
        return None
    try:
        from shapely.ops import unary_union
    except Exception:
        return None
    try:
        return unary_union(tuple(geometries))
    except Exception:
        return None


def _surface_role_from_patch(patch: Any) -> str:
    if not isinstance(patch, dict):
        return ""
    for key in ("surface_role", "role", "strip_kind", "kind"):
        value = str(patch.get(key, "") or "").strip().lower()
        if value:
            return value
    return ""


def _is_building_allowed_strip_key(key_lc: str) -> bool:
    return any(token in key_lc for token in BUILDING_ALLOWED_STRIP_TOKENS)


def _is_building_forbidden_strip_key(key_lc: str) -> bool:
    if not key_lc or _is_building_allowed_strip_key(key_lc):
        return False
    return any(token in key_lc for token in BUILDING_FORBIDDEN_STRIP_TOKENS)


def _patch_geometry(patch: Any) -> Any:
    if not isinstance(patch, dict):
        return None
    return patch.get("geometry")


def _polygonal_record_geometry(record: Any) -> Any:
    if not isinstance(record, dict):
        return None
    geometry = record.get("geometry")
    if geometry is not None:
        return geometry
    points = record.get("points") or record.get("polygon_xz") or record.get("polygon")
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        return None
    try:
        from shapely.geometry import Polygon as ShapelyPolygon

        return ShapelyPolygon([(float(point[0]), float(point[1])) for point in points])
    except Exception:
        return None


def _road_occupied_junction_geometries(junction: Any) -> Iterable[Any]:
    if not isinstance(junction, dict):
        return ()

    geometries = []
    geometries.extend(
        _candidate_geometries(
            (
                junction.get("carriageway_core"),
                junction.get("junction_core_rect"),
            )
        )
    )
    for patch_key in BUILDING_FORBIDDEN_JUNCTION_PATCH_KEYS:
        for patch in (junction.get(patch_key, ()) or ()):
            role = _surface_role_from_patch(patch)
            if patch_key != "normalized_surface_patches" or not role or role in BUILDING_FORBIDDEN_SURFACE_ROLES:
                geometries.extend(_candidate_geometries((_patch_geometry(patch),)))
    return tuple(geometries)


def vehicle_lane_forbidden_geometry(placement_ctx: object | None) -> Any:
    """Union carriageway and explicit vehicle lane surfaces, when available."""

    if placement_ctx is None:
        return None
    geometries = []
    carriageway = getattr(placement_ctx, "carriageway_polygon", None)
    if carriageway is None:
        carriageway = getattr(placement_ctx, "carriageway", None)
    geometries.extend(_candidate_geometries((carriageway,)))

    strip_zones = getattr(placement_ctx, "strip_zones", {}) or {}
    if isinstance(strip_zones, dict):
        for key, geometry in strip_zones.items():
            key_lc = str(key).strip().lower()
            if any(role in key_lc for role in VEHICLE_SURFACE_ROLES):
                geometries.extend(_candidate_geometries((geometry,)))

    for junction in getattr(placement_ctx, "junction_geometries", ()) or ():
        for patch in (junction.get("normalized_surface_patches", ()) if isinstance(junction, dict) else ()) or ():
            role = str(patch.get("surface_role", "") or "").strip().lower()
            if role in VEHICLE_SURFACE_ROLES:
                geometries.extend(_candidate_geometries((patch.get("geometry"),)))

    return _union_geometries(geometries)


def building_forbidden_geometry(placement_ctx: object | None) -> Any:
    """Union road-occupied surfaces that surrounding buildings must not enter."""

    if placement_ctx is None:
        return None
    geometries = []
    geometries.extend(
        _candidate_geometries(
            (
                getattr(placement_ctx, "carriageway_polygon", None),
                getattr(placement_ctx, "carriageway", None),
                getattr(placement_ctx, "sidewalk_zone", None),
                getattr(placement_ctx, "left_sidewalk_zone", None),
                getattr(placement_ctx, "right_sidewalk_zone", None),
            )
        )
    )
    for geometry in getattr(placement_ctx, "road_arm_geometries", ()) or ():
        geometries.extend(_candidate_geometries((geometry,)))

    strip_zones = getattr(placement_ctx, "strip_zones", {}) or {}
    if isinstance(strip_zones, dict):
        for key, geometry in strip_zones.items():
            if _is_building_forbidden_strip_key(str(key).strip().lower()):
                geometries.extend(_candidate_geometries((geometry,)))

    segment_strip_zones = getattr(placement_ctx, "segment_strip_zones", {}) or {}
    if isinstance(segment_strip_zones, dict):
        for strip_map in segment_strip_zones.values():
            if not isinstance(strip_map, dict):
                continue
            for key, geometry in strip_map.items():
                if _is_building_forbidden_strip_key(str(key).strip().lower()):
                    geometries.extend(_candidate_geometries((geometry,)))

    for junction in getattr(placement_ctx, "junction_geometries", ()) or ():
        geometries.extend(_road_occupied_junction_geometries(junction))

    for surface in getattr(placement_ctx, "surface_annotations", ()) or ():
        geometries.extend(_candidate_geometries((_polygonal_record_geometry(surface),)))

    for zone in getattr(placement_ctx, "functional_zones", ()) or ():
        geometries.extend(_candidate_geometries((_polygonal_record_geometry(zone),)))

    return _union_geometries(geometries)


def _outward_vector(
    *,
    visual_center_xz: Tuple[float, float],
    street_edge_xz: Tuple[float, float] | None,
    yaw_deg: float,
    side: str,
) -> Tuple[float, float]:
    if street_edge_xz is not None:
        dx = float(visual_center_xz[0]) - float(street_edge_xz[0])
        dz = float(visual_center_xz[1]) - float(street_edge_xz[1])
        length = math.hypot(dx, dz)
        if length > 1e-6:
            return dx / length, dz / length
    yaw_rad = math.radians(float(yaw_deg))
    sign = 1.0 if str(side).strip().lower() == "left" else -1.0
    return -math.sin(yaw_rad) * sign, math.cos(yaw_rad) * sign


def _fallback_vehicle_bbox(config: object | None) -> Tuple[float, float, float, float] | None:
    if config is None:
        return None
    length_m = float(getattr(config, "length_m", 0.0) or 0.0)
    road_width_m = float(getattr(config, "road_width_m", 0.0) or 0.0)
    if length_m <= 0.0 or road_width_m <= 0.0:
        return None
    return (-length_m / 2.0, length_m / 2.0, -road_width_m / 2.0, road_width_m / 2.0)


def _footprint_intrudes(
    *,
    placement_xz: Tuple[float, float],
    yaw_deg: float,
    half_x: float,
    half_z: float,
    center_x: float,
    center_z: float,
    scale: float | Sequence[float],
    forbidden_geometry: Any,
    fallback_bbox: Tuple[float, float, float, float] | None,
    vehicle_clearance_m: float,
) -> bool:
    points = building_footprint_points(
        placement_xz=placement_xz,
        yaw_deg=yaw_deg,
        half_x=half_x,
        half_z=half_z,
        center_x=center_x,
        center_z=center_z,
        scale=scale,
        clearance_m=0.0,
    )
    if forbidden_geometry is not None and not getattr(forbidden_geometry, "is_empty", True):
        try:
            from shapely.geometry import Polygon as ShapelyPolygon

            footprint = ShapelyPolygon(points)
            forbidden = forbidden_geometry
            if float(vehicle_clearance_m) > 0.0:
                forbidden = forbidden.buffer(float(vehicle_clearance_m))
            return bool(float(footprint.intersection(forbidden).area) > 1e-6)
        except Exception:
            pass
    if fallback_bbox is None:
        return False
    return _bbox_intersects(_bbox_from_points(points), fallback_bbox)


def resolve_building_pose(
    *,
    target_center_xz: Tuple[float, float],
    street_edge_xz: Tuple[float, float] | None,
    side: str,
    yaw_deg: float,
    half_x: float,
    half_z: float,
    center_x: float,
    center_z: float,
    scale: float | Sequence[float],
    placement_ctx: object | None = None,
    forbidden_geometry: Any = None,
    config: object | None = None,
    bbox_clearance_m: float = 0.15,
    vehicle_clearance_m: float = 0.10,
    max_push_m: float | None = None,
) -> BuildingPoseResolution:
    """Align a building asset to its target center and keep it out of roads."""

    forbidden = forbidden_geometry if forbidden_geometry is not None else building_forbidden_geometry(placement_ctx)
    fallback_bbox = _fallback_vehicle_bbox(config)
    checked = bool(forbidden is not None or fallback_bbox is not None)
    direction = _outward_vector(
        visual_center_xz=target_center_xz,
        street_edge_xz=street_edge_xz,
        yaw_deg=yaw_deg,
        side=side,
    )
    if max_push_m is None:
        scale_x, scale_z = _scale_xz(scale)
        footprint_span = max(float(half_x) * scale_x, float(half_z) * scale_z) * 2.0
        road_width = float(getattr(config, "road_width_m", 0.0) or 0.0) if config is not None else 0.0
        max_push_m = max(12.0, footprint_span + road_width + 2.0)

    def pose_for_push(push_m: float) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        visual_center = (
            float(target_center_xz[0]) + float(direction[0]) * float(push_m),
            float(target_center_xz[1]) + float(direction[1]) * float(push_m),
        )
        placement = placement_xz_for_visual_center(
            visual_center_xz=visual_center,
            yaw_deg=yaw_deg,
            center_x=center_x,
            center_z=center_z,
            scale=scale,
        )
        return visual_center, placement

    def build_result(push_m: float, *, adjusted: bool) -> BuildingPoseResolution:
        visual_center, placement = pose_for_push(push_m)
        bbox = building_bbox_xz(
            placement_xz=placement,
            yaw_deg=yaw_deg,
            half_x=half_x,
            half_z=half_z,
            center_x=center_x,
            center_z=center_z,
            scale=scale,
            clearance_m=bbox_clearance_m,
        )
        return BuildingPoseResolution(
            placement_xz=placement,
            visual_center_xz=visual_center,
            bbox_xz=bbox,
            adjusted=adjusted,
            push_distance_m=float(push_m),
            checked_vehicle_lanes=checked,
        )

    _visual_center, initial_placement = pose_for_push(0.0)
    if not _footprint_intrudes(
        placement_xz=initial_placement,
        yaw_deg=yaw_deg,
        half_x=half_x,
        half_z=half_z,
        center_x=center_x,
        center_z=center_z,
        scale=scale,
        forbidden_geometry=forbidden,
        fallback_bbox=fallback_bbox,
        vehicle_clearance_m=vehicle_clearance_m,
    ):
        return build_result(0.0, adjusted=False)

    step_m = 0.5
    push_m = step_m
    first_clear_push = None
    while push_m <= float(max_push_m) + 1e-9:
        _visual_center, placement = pose_for_push(push_m)
        if not _footprint_intrudes(
            placement_xz=placement,
            yaw_deg=yaw_deg,
            half_x=half_x,
            half_z=half_z,
            center_x=center_x,
            center_z=center_z,
            scale=scale,
            forbidden_geometry=forbidden,
            fallback_bbox=fallback_bbox,
            vehicle_clearance_m=vehicle_clearance_m,
        ):
            first_clear_push = float(push_m)
            break
        push_m += step_m

    if first_clear_push is None:
        return BuildingPoseResolution(
            placement_xz=initial_placement,
            visual_center_xz=target_center_xz,
            bbox_xz=building_bbox_xz(
                placement_xz=initial_placement,
                yaw_deg=yaw_deg,
                half_x=half_x,
                half_z=half_z,
                center_x=center_x,
                center_z=center_z,
                scale=scale,
                clearance_m=bbox_clearance_m,
            ),
            rejected=True,
            reject_reason="building_intrudes_vehicle_lane",
            checked_vehicle_lanes=checked,
        )

    low = max(0.0, first_clear_push - step_m)
    high = float(first_clear_push)
    for _ in range(10):
        mid = (low + high) * 0.5
        _visual_center, placement = pose_for_push(mid)
        if _footprint_intrudes(
            placement_xz=placement,
            yaw_deg=yaw_deg,
            half_x=half_x,
            half_z=half_z,
            center_x=center_x,
            center_z=center_z,
            scale=scale,
            forbidden_geometry=forbidden,
            fallback_bbox=fallback_bbox,
            vehicle_clearance_m=vehicle_clearance_m,
        ):
            low = mid
        else:
            high = mid
    return build_result(high, adjusted=True)
