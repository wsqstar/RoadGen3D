"""Cross Strip Fusion Generator - Unified junction geometry for cross_junction.

Core rules:
- Vehicle lanes (drive_lane, bus_lane, bike_lane, parking_lane) go straight through
  the intersection as a single carriageway core polygon.
- Non-vehicle strips (nearroad_furnishing, clear_sidewalk, frontage_reserve) bend
  along angle bisectors at each corner, then same-type strips are merged into
  continuous surfaces.

This module provides a shared geometry generator that can be used by:
- Reference Plan Annotator (frontend overlay)
- Junction Editor (default seed generation)
- Backend bridge (Python pipeline)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Strip kinds that go straight through (vehicle lanes)
CARRIAGEWAY_STRIP_KINDS: frozenset = frozenset({
    "drive_lane",
    "bus_lane",
    "bike_lane",
    "parking_lane",
})

# Strip kinds that bend along angle bisectors
CORNER_FUSION_STRIP_KINDS: frozenset = frozenset({
    "nearroad_furnishing",
    "clear_sidewalk",
    "frontage_reserve",
})

# All non-center strip kinds that need corner processing
NON_CARRIAGEWAY_STRIP_KINDS: frozenset = CORNER_FUSION_STRIP_KINDS


def _require_shapely():
    """Import and return shapely module, raising if unavailable."""
    try:
        import shapely
    except ImportError as exc:
        raise RuntimeError(
            "`shapely` is required for cross strip fusion. Install with: pip install shapely"
        ) from exc
    return shapely


def _normalize_angle_deg(value: float) -> float:
    """Normalize angle to [0, 360) range."""
    normalized = math.fmod(float(value), 360.0)
    if normalized < 0.0:
        normalized += 360.0
    return normalized


def _angle_deg(from_point: Tuple[float, float], to_point: Tuple[float, float]) -> float:
    """Compute angle in degrees from one point to another."""
    return _normalize_angle_deg(
        math.degrees(float(math.atan2(float(to_point[1]) - float(from_point[1]), float(to_point[0]) - float(from_point[0]))))
    )


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Compute Euclidean distance between two points."""
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _normalize_vector(vector: Tuple[float, float]) -> Tuple[float, float] | None:
    """Normalize a 2D vector to unit length."""
    length = math.hypot(float(vector[0]), float(vector[1]))
    if length <= 1e-9:
        return None
    return (float(vector[0]) / length, float(vector[1]) / length)


def _unit_vector_from_angle(angle_deg: float) -> Tuple[float, float]:
    """Create unit vector from angle in degrees."""
    angle_rad = math.radians(float(angle_deg))
    return (math.cos(angle_rad), math.sin(angle_rad))


def _angle_bisector(
    tangent_a: Tuple[float, float],
    tangent_b: Tuple[float, float],
) -> Tuple[float, float]:
    """Compute the internal angle bisector direction between two tangent vectors.

    Returns a normalized direction vector pointing into the corner (internal bisector).
    """
    # Sum the two normalized tangent vectors
    bisector = (float(tangent_a[0]) + float(tangent_b[0]), float(tangent_a[1]) + float(tangent_b[1]))
    normalized = _normalize_vector(bisector)
    if normalized is not None:
        return normalized

    # If tangents are opposite (180°), use perpendicular
    perp_a = (-float(tangent_a[1]), float(tangent_a[0]))
    return perp_a


def _point_along_line(
    point: Tuple[float, float],
    direction: Tuple[float, float],
    distance: float,
) -> Tuple[float, float]:
    """Compute a point along a line from origin point in direction."""
    return (
        float(point[0]) + float(direction[0]) * float(distance),
        float(point[1]) + float(direction[1]) * float(distance),
    )


def _line_intersection(
    point_a: Tuple[float, float],
    direction_a: Tuple[float, float],
    point_b: Tuple[float, float],
    direction_b: Tuple[float, float],
) -> Tuple[float, float] | None:
    """Find intersection of two lines defined by point and direction."""
    ax, ay = float(point_a[0]), float(point_a[1])
    adx, ady = float(direction_a[0]), float(direction_a[1])
    bx, by = float(point_b[0]), float(point_b[1])
    bdx, bdy = float(direction_b[0]), float(direction_b[1])
    determinant = adx * bdy - ady * bdx
    if abs(determinant) <= 1e-9:
        return None
    delta_x = bx - ax
    delta_y = by - ay
    t_value = (delta_x * bdy - delta_y * bdx) / determinant
    return (ax + adx * t_value, ay + ady * t_value)


@dataclass
class JunctionArm:
    """A single arm (road approach) of a junction."""
    road_id: int
    centerline_id: str
    angle_deg: float  # Heading direction from junction center to arm
    tangent: Tuple[float, float]  # Unit vector in heading direction
    normal: Tuple[float, float]  # Perpendicular to tangent (90° CCW)
    carriageway_half_width_m: float  # Half width of carriageway
    strip_widths_by_kind: Dict[str, float]  # Width of each strip kind on this arm

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JunctionArm":
        """Create JunctionArm from a dictionary (e.g., from existing arm dict)."""
        angle_deg = float(data.get("angle_deg", 0.0))
        tangent = _unit_vector_from_angle(angle_deg)
        normal = (-float(tangent[1]), float(tangent[0]))  # 90° CCW rotation

        carriageway_width = float(data.get("carriageway_width_m", 8.0))
        strip_widths: Dict[str, float] = {}

        # Extract strip widths from side_strip_layouts if available
        side_strip_layouts = data.get("side_strip_layouts", {}) or {}
        for side, strips in side_strip_layouts.items():
            if not isinstance(strips, (list, tuple)):
                continue
            for strip in strips:
                kind = str(strip.get("kind", ""))
                width = float(strip.get("width_m", 0.0))
                if kind and width > 0:
                    strip_widths[kind] = width

        # Fallback to individual width fields
        for kind in NON_CARRIAGEWAY_STRIP_KINDS:
            if kind not in strip_widths:
                key = f"{kind}_width_m"
                if key in data:
                    strip_widths[kind] = float(data[key])

        return cls(
            road_id=int(data.get("road_id", 0)),
            centerline_id=str(data.get("centerline_id", "")),
            angle_deg=float(data.get("angle_deg", 0.0)),
            tangent=tangent,
            normal=normal,
            carriageway_half_width_m=carriageway_width * 0.5,
            strip_widths_by_kind=strip_widths,
        )

    def outer_edge_offset_m(self, strip_kind: str) -> float | None:
        """Get the outer edge offset from carriageway center for a strip kind.

        Returns the distance from the carriageway centerline to the outer edge
        of the strip (positive outward from road center).
        """
        # Sum all strip widths from carriageway edge outward
        sorted_kinds = ["nearroad_buffer", "nearroad_furnishing", "clear_sidewalk",
                        "farfromroad_buffer", "frontage_reserve"]
        offset = self.carriageway_half_width_m

        for kind in sorted_kinds:
            if kind == strip_kind:
                # Add this strip's width to get to outer edge
                width = self.strip_widths_by_kind.get(kind, 0.0)
                return offset + width
            # Add strip width to running total
            offset += self.strip_widths_by_kind.get(kind, 0.0)

        return None


@dataclass
class JunctionCorner:
    """A corner region between two adjacent junction arms."""
    corner_index: int  # Index in [0, num_arms-1]
    arm_a: JunctionArm  # First arm
    arm_b: JunctionArm  # Second arm (adjacent CCW)
    corner_center: Tuple[float, float]  # Center point of the corner
    bisector: Tuple[float, float]  # Internal angle bisector direction
    outer_corner_point: Tuple[float, float]  # Outer point of the corner region


@dataclass
class CornerStripSegment:
    """A single segment of a strip kind at a corner."""
    strip_kind: str
    corner_index: int
    centerline_points: List[Tuple[float, float]]
    width_m: float
    inner_edge_points: List[Tuple[float, float]]
    outer_edge_points: List[Tuple[float, float]]


@dataclass
class CrossStripFusionResult:
    """Result of cross strip fusion geometry generation."""
    junction_id: str
    kind: str  # "cross_junction", "t_junction", etc.
    anchor_xy: Tuple[float, float]
    arms: List[JunctionArm]
    corners: List[JunctionCorner]
    carriageway_core_polygon: Any  # Shapely Polygon
    fused_corner_strips: Dict[str, Any]  # {strip_kind: shapely Polygon}
    debug_info: Dict[str, Any]  # Debugging information


def build_cross_strip_fusion(
    junction_id: str,
    anchor_xy: Tuple[float, float],
    arms: Sequence[Dict[str, Any]],
    *,
    crosswalk_depth_m: float = 3.0,
    min_corner_radius_m: float = 0.5,
    strip_kinds: Tuple[str, ...] = ("nearroad_furnishing", "clear_sidewalk", "frontage_reserve"),
) -> CrossStripFusionResult:
    """Generate unified cross junction geometry with angle bisector corner fusion.

    Args:
        junction_id: Unique identifier for this junction
        anchor_xy: Center point of the junction (x, y)
        arms: List of arm dictionaries with tangent, carriageway_width_m, etc.
        crosswalk_depth_m: Depth of crosswalk areas
        min_corner_radius_m: Minimum radius for corner curves
        strip_kinds: Tuple of strip kinds to generate corner fusion for

    Returns:
        CrossStripFusionResult with carriageway core and fused corner strips
    """
    shapely = _require_shapely()
    from shapely.geometry import Polygon

    # Convert arms to JunctionArm objects
    junction_arms: List[JunctionArm] = [
        JunctionArm.from_dict(arm) for arm in arms
    ]

    if len(junction_arms) < 3:
        raise ValueError(f"Cross junction requires at least 3 arms, got {len(junction_arms)}")

    # Sort arms by angle
    junction_arms = sorted(junction_arms, key=lambda a: a.angle_deg)

    # Build corners between adjacent arms
    corners: List[JunctionCorner] = []
    for i, arm_a in enumerate(junction_arms):
        arm_b = junction_arms[(i + 1) % len(junction_arms)]

        # Compute angle between arms
        angle_diff = _normalize_angle_deg(arm_b.angle_deg - arm_a.angle_deg)
        if angle_diff > 180:
            angle_diff -= 360

        # Compute internal angle bisector
        bisector = _angle_bisector(arm_a.tangent, arm_b.tangent)

        # Compute corner center: intersection of arm boundaries
        arm_a_boundary = arm_a.carriageway_half_width_m
        arm_b_boundary = arm_b.carriageway_half_width_m

        # Find intersection of arm normal rays from anchor
        arm_a_ray_origin = _point_along_line(anchor_xy, arm_a.normal, arm_a_boundary)
        arm_b_ray_origin = _point_along_line(anchor_xy, arm_b.normal, arm_b_boundary)

        corner_center = _line_intersection(
            arm_a_ray_origin, arm_b.tangent,
            arm_b_ray_origin, arm_a.tangent,
        )
        if corner_center is None:
            # Fallback: simple midpoint
            corner_center = (
                (arm_a_ray_origin[0] + arm_b_ray_origin[0]) * 0.5,
                (arm_a_ray_origin[1] + arm_b_ray_origin[1]) * 0.5,
            )

        # Compute outer corner point along bisector
        outer_offset = arm_a_boundary + arm_b_boundary + crosswalk_depth_m
        outer_corner_point = _point_along_line(corner_center, bisector, outer_offset)

        corners.append(JunctionCorner(
            corner_index=i,
            arm_a=arm_a,
            arm_b=arm_b,
            corner_center=corner_center,
            bisector=bisector,
            outer_corner_point=outer_corner_point,
        ))

    # Build carriageway core polygon
    # For cross junction: simple rectangle formed by arm boundaries
    if len(junction_arms) == 4:
        # Standard cross: use the corner centers as rectangle vertices
        rect_half_u = junction_arms[0].carriageway_half_width_m
        rect_half_v = junction_arms[1].carriageway_half_width_m

        # Determine principal axes from arms
        axis_u = junction_arms[0].tangent
        axis_v = junction_arms[1].tangent

        # Create rectangle
        ax, ay = anchor_xy
        ux, uy = axis_u
        vx, vy = axis_v

        rect_points = [
            (ax - ux * rect_half_u - vx * rect_half_v, ay - uy * rect_half_u - vy * rect_half_v),
            (ax + ux * rect_half_u - vx * rect_half_v, ay + uy * rect_half_u - vy * rect_half_v),
            (ax + ux * rect_half_u + vx * rect_half_v, ay + uy * rect_half_u + vy * rect_half_v),
            (ax - ux * rect_half_u + vx * rect_half_v, ay - uy * rect_half_u + vy * rect_half_v),
        ]
        carriageway_core = Polygon(rect_points)
    else:
        # Non-standard: use convex hull of corner centers
        corner_points = [c.corner_center for c in corners]
        from shapely.ops import unary_union
        points_geom = shapely.geometry.MultiPoint(corner_points)
        carriageway_core = points_geom.convex_hull

    if not carriageway_core.is_valid:
        carriageway_core = carriageway_core.buffer(0)

    # Build fused corner strips for each strip kind
    fused_corner_strips: Dict[str, Any] = {}

    for strip_kind in strip_kinds:
        corner_polygons: List[Any] = []

        for corner in corners:
            arm_a = corner.arm_a
            arm_b = corner.arm_b

            # Get strip widths for this kind
            width_a = arm_a.strip_widths_by_kind.get(strip_kind, 0.0)
            width_b = arm_b.strip_widths_by_kind.get(strip_kind, 0.0)

            if width_a <= 0 and width_b <= 0:
                continue

            # Use average width for this corner
            avg_width = (width_a + width_b) * 0.5
            if avg_width <= 0:
                continue

            # Build corner polygon using angle bisector approach
            # Points: arm_a inner edge, arm_a outer edge, bisector outer, arm_b outer, arm_b inner

            # Inner edge points (at carriageway boundary)
            inner_a = _point_along_line(anchor_xy, arm_a.normal, arm_a.carriageway_half_width_m)
            inner_b = _point_along_line(anchor_xy, arm_b.normal, arm_b.carriageway_half_width_m)

            # Outer edge points (at strip outer boundary)
            outer_a_offset = arm_a.carriageway_half_width_m + width_a
            outer_b_offset = arm_b.carriageway_half_width_m + width_b

            outer_a = _point_along_line(anchor_xy, arm_a.normal, outer_a_offset)
            outer_b = _point_along_line(anchor_xy, arm_b.normal, outer_b_offset)

            # Bisector outer point
            bisector_outer = corner.outer_corner_point

            # Create corner polygon
            corner_poly_points = [
                inner_a,
                outer_a,
                bisector_outer,
                outer_b,
                inner_b,
            ]

            # Ensure we have a valid polygon
            if len(corner_poly_points) >= 3:
                try:
                    corner_poly = Polygon(corner_poly_points)
                    if corner_poly.is_valid:
                        corner_polygons.append(corner_poly)
                    else:
                        corner_polygons.append(corner_poly.buffer(0))
                except Exception:
                    pass

        # Merge all corner polygons for this strip kind
        if corner_polygons:
            from shapely.ops import unary_union
            merged = unary_union(corner_polygons)
            fused_corner_strips[strip_kind] = merged

    # Build debug info
    debug_info = {
        "arm_count": len(junction_arms),
        "corner_count": len(corners),
        "strip_kinds_generated": list(fused_corner_strips.keys()),
        "carriageway_core_area_m2": float(carriageway_core.area),
    }

    return CrossStripFusionResult(
        junction_id=junction_id,
        kind="cross_junction" if len(junction_arms) == 4 else "t_junction",
        anchor_xy=anchor_xy,
        arms=junction_arms,
        corners=corners,
        carriageway_core_polygon=carriageway_core,
        fused_corner_strips=fused_corner_strips,
        debug_info=debug_info,
    )


def cross_strip_fusion_to_junction_geometry(
    fusion_result: CrossStripFusionResult,
) -> Dict[str, Any]:
    """Convert CrossStripFusionResult to junction geometry dict format.

    This output format is compatible with the existing junction_geometries
    structure used by the placement context.
    """
    result: Dict[str, Any] = {
        "junction_id": fusion_result.junction_id,
        "kind": fusion_result.kind,
        "anchor_xy": [float(fusion_result.anchor_xy[0]), float(fusion_result.anchor_xy[1])],
        "arm_count": len(fusion_result.arms),
        "carriageway_core": fusion_result.carriageway_core_polygon,
        # Map fused_corner_strips to existing bucket names
        "sidewalk_corner_patches": [],
        "nearroad_corner_patches": [],
        "frontage_corner_patches": [],
        "debug_info": fusion_result.debug_info,
    }

    # Map strip kinds to bucket names
    strip_to_bucket = {
        "clear_sidewalk": "sidewalk_corner_patches",
        "nearroad_furnishing": "nearroad_corner_patches",
        "frontage_reserve": "frontage_corner_patches",
    }

    for strip_kind, polygon in fusion_result.fused_corner_strips.items():
        bucket_name = strip_to_bucket.get(strip_kind)
        if bucket_name and polygon is not None:
            try:
                import shapely
                if polygon.geom_type == "Polygon":
                    result[bucket_name].append({
                        "patch_id": f"{fusion_result.junction_id}_{strip_kind}_fused",
                        "strip_kind": strip_kind,
                        "geometry": polygon,
                        "is_fused": True,
                    })
                elif polygon.geom_type == "MultiPolygon":
                    for i, poly in enumerate(polygon.geoms):
                        result[bucket_name].append({
                            "patch_id": f"{fusion_result.junction_id}_{strip_kind}_fused_{i}",
                            "strip_kind": strip_kind,
                            "geometry": poly,
                            "is_fused": True,
                        })
            except Exception:
                pass

    return result


# Alias for backward compatibility
CrossJunctionGeometryResult = CrossStripFusionResult
generate_cross_junction_geometry = build_cross_strip_fusion
