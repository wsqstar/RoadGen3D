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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


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


@dataclass(frozen=True)
class JunctionArmSideStrip:
    """A signed side-strip band on one junction arm.

    The signed offsets follow RoadPen's q-axis convention: positive values are
    on the left side of the outward branch direction, negative values are on
    the right side. Keeping this sign is the important RoadPen detail; averaging
    left/right widths collapses both sidewalks into ambiguous triangles.
    """

    strip_id: str
    strip_kind: str
    zone: str
    width_m: float
    inner_offset_m: float
    outer_offset_m: float


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
    side_strips: List[JunctionArmSideStrip]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JunctionArm":
        """Create JunctionArm from a dictionary (e.g., from existing arm dict)."""
        angle_deg = float(data.get("angle_deg", 0.0))
        tangent = _unit_vector_from_angle(angle_deg)
        normal = (-float(tangent[1]), float(tangent[0]))  # 90° CCW rotation

        carriageway_width = float(data.get("carriageway_width_m", 8.0))
        strip_widths: Dict[str, float] = {}
        side_strips: List[JunctionArmSideStrip] = []

        # Extract strip widths from side_strip_layouts if available
        side_strip_layouts = data.get("side_strip_layouts", {}) or {}
        for side, strips in side_strip_layouts.items():
            if not isinstance(strips, (list, tuple)):
                continue
            for strip in strips:
                kind = str(strip.get("kind", ""))
                width = float(strip.get("width_m", 0.0))
                if kind and width > 0:
                    strip_widths[kind] = max(float(strip_widths.get(kind, 0.0)), width)
                    inner_offset = strip.get("inner_offset_m")
                    outer_offset = strip.get("outer_offset_m")
                    if inner_offset is None or outer_offset is None:
                        continue
                    side_strips.append(
                        JunctionArmSideStrip(
                            strip_id=str(strip.get("strip_id", "") or f"{side}_{kind}"),
                            strip_kind=kind,
                            zone=str(strip.get("zone", "") or side),
                            width_m=width,
                            inner_offset_m=float(inner_offset),
                            outer_offset_m=float(outer_offset),
                        )
                    )

        # Fallback to individual width fields
        for kind in NON_CARRIAGEWAY_STRIP_KINDS:
            if kind not in strip_widths:
                key = f"{kind}_width_m"
                if key in data:
                    strip_widths[kind] = float(data[key])
            if not any(strip.strip_kind == kind for strip in side_strips):
                width = float(strip_widths.get(kind, 0.0) or 0.0)
                if width <= 0.0:
                    continue
                inner_abs = carriageway_width * 0.5
                for previous_kind in ("nearroad_buffer", "nearroad_furnishing", "clear_sidewalk", "farfromroad_buffer", "frontage_reserve"):
                    if previous_kind == kind:
                        break
                    inner_abs += float(strip_widths.get(previous_kind, 0.0) or 0.0)
                outer_abs = inner_abs + width
                side_strips.extend(
                    [
                        JunctionArmSideStrip(
                            strip_id=f"left_{kind}",
                            strip_kind=kind,
                            zone="left",
                            width_m=width,
                            inner_offset_m=inner_abs,
                            outer_offset_m=outer_abs,
                        ),
                        JunctionArmSideStrip(
                            strip_id=f"right_{kind}",
                            strip_kind=kind,
                            zone="right",
                            width_m=width,
                            inner_offset_m=-inner_abs,
                            outer_offset_m=-outer_abs,
                        ),
                    ]
                )

        return cls(
            road_id=int(data.get("road_id", 0)),
            centerline_id=str(data.get("centerline_id", "")),
            angle_deg=float(data.get("angle_deg", 0.0)),
            tangent=tangent,
            normal=normal,
            carriageway_half_width_m=carriageway_width * 0.5,
            strip_widths_by_kind=strip_widths,
            side_strips=side_strips,
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

    def side_strip(self, strip_kind: str, zone: str) -> JunctionArmSideStrip | None:
        """Return the outermost side strip matching kind/zone."""

        candidates = [
            strip
            for strip in self.side_strips
            if strip.strip_kind == strip_kind and strip.zone == zone
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda strip: max(abs(strip.inner_offset_m), abs(strip.outer_offset_m)))

    def outer_reference_strip(self, zone: str) -> JunctionArmSideStrip | None:
        """Return the farthest available strip on a side for turn-radius hints."""

        candidates = [strip for strip in self.side_strips if strip.zone == zone]
        if not candidates:
            return None
        return max(candidates, key=lambda strip: max(abs(strip.inner_offset_m), abs(strip.outer_offset_m)))


@dataclass
class JunctionCorner:
    """A corner region between two adjacent junction arms."""
    corner_index: int  # Index in [0, num_arms-1]
    arm_a: JunctionArm  # First arm
    arm_b: JunctionArm  # Second arm (adjacent CCW)
    corner_center: Tuple[float, float]  # Center point of the corner
    bisector: Tuple[float, float]  # Internal angle bisector direction
    outer_corner_point: Tuple[float, float]  # Outer point of the corner region


def _carriageway_surface_from_arm_throats(
    anchor_xy: Tuple[float, float],
    arms: Sequence[JunctionArm],
    *,
    crosswalk_depth_m: float,
) -> Any:
    """Build a RoadPen-style carriageway surface from straight arm throats.

    RoadPen's useful bit here is not a convex mouth envelope. The road strips
    extend into the junction and then visually merge, so each arm should keep a
    cut perpendicular to its lane direction. A convex hull turns a normal cross
    into an octagon with diagonal cuts at the approaches, which is exactly the
    artifact we are avoiding.
    """
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union

    ax, ay = float(anchor_xy[0]), float(anchor_xy[1])
    throat_polygons: List[Any] = []
    for arm in arms:
        half_width = max(float(arm.carriageway_half_width_m), 0.5)
        profile_offset = half_width + sum(max(float(value), 0.0) for value in arm.strip_widths_by_kind.values())
        depth = max(
            float(crosswalk_depth_m) + half_width,
            half_width * 2.4,
            profile_offset * 1.35,
            4.0,
        )
        centerline = LineString(
            [
                (ax, ay),
                (
                    ax + float(arm.tangent[0]) * depth,
                    ay + float(arm.tangent[1]) * depth,
                ),
            ]
        )
        throat = centerline.buffer(half_width, cap_style="flat")
        if not getattr(throat, "is_empty", True):
            throat_polygons.append(throat)

    if not throat_polygons:
        return Polygon()
    merged = unary_union(throat_polygons)
    if not getattr(merged, "is_valid", True):
        merged = merged.buffer(0)
    return merged


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
    fused_corner_patch_records: List[Dict[str, Any]]
    debug_info: Dict[str, Any]  # Debugging information


def _add_points(a: Tuple[float, float], b: Tuple[float, float]) -> Tuple[float, float]:
    return (float(a[0]) + float(b[0]), float(a[1]) + float(b[1]))


def _sub_points(a: Tuple[float, float], b: Tuple[float, float]) -> Tuple[float, float]:
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _scale_point(point: Tuple[float, float], value: float) -> Tuple[float, float]:
    return (float(point[0]) * float(value), float(point[1]) * float(value))


def _dot(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1])


def _cross_vec(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), float(value)))


def _ccw_gap_radians(arm_a: JunctionArm, arm_b: JunctionArm) -> float:
    raw = math.atan2(arm_b.tangent[1], arm_b.tangent[0]) - math.atan2(arm_a.tangent[1], arm_a.tangent[0])
    return raw if raw >= 0.0 else raw + math.pi * 2.0


def _can_build_collision_corner(arm_a: JunctionArm, arm_b: JunctionArm) -> bool:
    gap = _ccw_gap_radians(arm_a, arm_b)
    if gap <= math.pi / 12.0 or gap >= math.pi * 0.93:
        return False
    gap_dot = _dot(arm_a.tangent, arm_b.tangent)
    return gap_dot > -0.96 and gap_dot < math.cos(math.pi / 12.0)


def _branch_pair_key(arm_a: JunctionArm, arm_b: JunctionArm) -> str:
    return "::".join(sorted([arm_a.centerline_id, arm_b.centerline_id]))


def _pass_through_pairs(arms: Sequence[JunctionArm]) -> set[str]:
    candidates: List[Tuple[JunctionArm, JunctionArm, float]] = []
    for i, arm_a in enumerate(arms):
        for arm_b in arms[i + 1:]:
            value = _dot(arm_a.tangent, arm_b.tangent)
            if value <= -0.82:
                candidates.append((arm_a, arm_b, value))
    candidates.sort(key=lambda item: item[2])
    if len(arms) == 3 and len(candidates) >= 2 and candidates[1][2] - candidates[0][2] < 0.08:
        return set()
    used: set[str] = set()
    pairs: set[str] = set()
    for arm_a, arm_b, _value in candidates:
        if arm_a.centerline_id in used or arm_b.centerline_id in used:
            continue
        used.add(arm_a.centerline_id)
        used.add(arm_b.centerline_id)
        pairs.add(_branch_pair_key(arm_a, arm_b))
    return pairs


def _virtual_lane_boundary_q(left_q: float, right_q: float) -> float:
    return (-float(left_q) + float(right_q)) * 0.5


def _signed_band_bounds(strip: JunctionArmSideStrip) -> Tuple[float, float]:
    values = (float(strip.inner_offset_m), float(strip.outer_offset_m))
    return (min(values), max(values))


def _max_strip_offset(strip: JunctionArmSideStrip) -> float:
    return max(abs(float(strip.inner_offset_m)), abs(float(strip.outer_offset_m)))


def _connector_depth(strip_a: JunctionArmSideStrip, strip_b: JunctionArmSideStrip) -> float:
    max_offset = max(_max_strip_offset(strip_a), _max_strip_offset(strip_b))
    return _clamp(max_offset * 1.5, 18.0, 42.0)


def _wide_connector_depth(arm_a: JunctionArm, arm_b: JunctionArm) -> float:
    max_offset = max(
        max((_max_strip_offset(strip) for strip in arm_a.side_strips), default=arm_a.carriageway_half_width_m),
        max((_max_strip_offset(strip) for strip in arm_b.side_strips), default=arm_b.carriageway_half_width_m),
    )
    return _clamp(max_offset * 1.65, 36.0, 92.0)


def _compact_connector_ell(
    arm_a: JunctionArm,
    arm_b: JunctionArm,
    reference_a: JunctionArmSideStrip,
    reference_b: JunctionArmSideStrip,
) -> float:
    u = _scale_point(arm_a.tangent, -1.0)
    v = arm_b.tangent
    delta = math.acos(_clamp(_dot(u, v), -1.0, 1.0))
    max_offset = max(_max_strip_offset(reference_a), _max_strip_offset(reference_b))
    min_radius = max_offset + 10.0 * 0.5
    desired_radius = max(max_offset * 2.0 * 2.2, 10.0)
    desired_ell = desired_radius * math.tan(delta * 0.5)
    mouth_limit = _connector_depth(reference_a, reference_b)
    min_ell = min_radius * math.tan(delta * 0.5)
    return _clamp(desired_ell, max(10.0 * 0.6, min_ell), max(mouth_limit, min_ell))


def _build_virtual_lane_turn(
    anchor_xy: Tuple[float, float],
    arm_a: JunctionArm,
    arm_b: JunctionArm,
    reference_a: JunctionArmSideStrip,
    reference_b: JunctionArmSideStrip,
    ell_hint: float,
) -> Dict[str, Any] | None:
    u = _scale_point(arm_a.tangent, -1.0)
    v = arm_b.tangent
    delta = math.acos(_clamp(_dot(u, v), -1.0, 1.0))
    cr = _cross_vec(u, v)
    if delta <= math.pi / 36.0 or abs(cr) <= 1e-9:
        return None
    max_offset = max(_max_strip_offset(reference_a), _max_strip_offset(reference_b))
    min_radius = max_offset + 10.0 * 0.5
    min_ell = min_radius * math.tan(delta * 0.5)
    ell = max(float(ell_hint), 10.0 * 0.6, min_ell)
    radius = max(ell / max(math.tan(delta * 0.5), 1e-9), 10.0)
    return {
        "u": u,
        "v": v,
        "a": _add_points(anchor_xy, _scale_point(arm_a.tangent, ell)),
        "b": _add_points(anchor_xy, _scale_point(arm_b.tangent, ell)),
        "delta": delta,
        "sigma": 1.0 if cr >= 0.0 else -1.0,
        "radius": radius,
    }


def _sample_offset_turn_curve(turn: Mapping[str, Any], q: float, samples: int = 18) -> List[Tuple[float, float]]:
    sample_count = max(4, int(samples))
    radius_q = float(turn["radius"]) - float(turn["sigma"]) * float(q)
    if radius_q <= 1e-6:
        return []
    u = tuple(float(value) for value in turn["u"])
    v = tuple(float(value) for value in turn["v"])
    a = tuple(float(value) for value in turn["a"])
    b = tuple(float(value) for value in turn["b"])
    normal_in = (-u[1], u[0])
    normal_out = (-v[1], v[0])
    p0 = _add_points(a, _scale_point(normal_in, q))
    p3 = _add_points(b, _scale_point(normal_out, q))
    handle = (4.0 / 3.0) * radius_q * math.tan(float(turn["delta"]) / 4.0)
    p1 = _add_points(p0, _scale_point(u, handle))
    p2 = _add_points(p3, _scale_point(v, -handle))
    points: List[Tuple[float, float]] = []
    for index in range(sample_count):
        t = 0.0 if sample_count <= 1 else index / float(sample_count - 1)
        mt = 1.0 - t
        points.append(
            (
                mt * mt * mt * p0[0] + 3.0 * mt * mt * t * p1[0] + 3.0 * mt * t * t * p2[0] + t * t * t * p3[0],
                mt * mt * mt * p0[1] + 3.0 * mt * mt * t * p1[1] + 3.0 * mt * t * t * p2[1] + t * t * t * p3[1],
            )
        )
    return points


def _build_lane_connector_polygon(
    anchor_xy: Tuple[float, float],
    arm_a: JunctionArm,
    strip_a: JunctionArmSideStrip,
    arm_b: JunctionArm,
    strip_b: JunctionArmSideStrip,
    reference_a: JunctionArmSideStrip,
    reference_b: JunctionArmSideStrip,
    ell_hint: float,
) -> Tuple[List[Tuple[float, float]], Tuple[float, float], Tuple[float, float]] | None:
    turn = _build_virtual_lane_turn(anchor_xy, arm_a, arm_b, reference_a, reference_b, ell_hint)
    if turn is None:
        return None
    a_inner, a_outer = _signed_band_bounds(strip_a)
    b_inner, b_outer = _signed_band_bounds(strip_b)
    outer_q = _virtual_lane_boundary_q(a_outer, b_inner)
    inner_q = _virtual_lane_boundary_q(a_inner, b_outer)
    outer_curve = _sample_offset_turn_curve(turn, outer_q, 18)
    inner_curve = _sample_offset_turn_curve(turn, inner_q, 18)
    if len(outer_curve) < 4 or len(inner_curve) < 4:
        return None
    polygon = [*outer_curve, *reversed(inner_curve)]
    polygon.append(polygon[0])
    return polygon, tuple(float(value) for value in turn["a"]), tuple(float(value) for value in turn["b"])


def _polygon_from_points(points: Sequence[Tuple[float, float]]) -> Any | None:
    from shapely.geometry import Polygon

    if len(points) < 4:
        return None
    polygon = Polygon(points)
    if not getattr(polygon, "is_valid", True):
        try:
            polygon = polygon.buffer(0)
        except Exception:
            return None
    if getattr(polygon, "is_empty", True) or float(getattr(polygon, "area", 0.0) or 0.0) <= 1e-8:
        return None
    return polygon


def _build_corner_connector_patch_records(
    junction_id: str,
    anchor_xy: Tuple[float, float],
    arms: Sequence[JunctionArm],
    strip_kinds: Sequence[str],
) -> List[Dict[str, Any]]:
    """Build RoadPen-style side-strip connector patches between adjacent arms."""

    patch_records: List[Dict[str, Any]] = []
    pass_through = _pass_through_pairs(arms)
    for arm_index, arm in enumerate(arms):
        next_arm = arms[(arm_index + 1) % len(arms)]
        if pass_through and _branch_pair_key(arm, next_arm) in pass_through:
            continue
        if not _can_build_collision_corner(arm, next_arm):
            continue
        reference_a = arm.outer_reference_strip("left")
        reference_b = next_arm.outer_reference_strip("right")
        if reference_a is None or reference_b is None:
            continue
        gap_radians = _ccw_gap_radians(arm, next_arm)
        ell_hint = (
            _compact_connector_ell(arm, next_arm, reference_a, reference_b)
            if gap_radians <= math.pi * 0.5
            else _wide_connector_depth(arm, next_arm)
        )
        quadrant_id = f"{junction_id}_quadrant_{arm_index:02d}"
        for strip_kind in strip_kinds:
            strip_a = arm.side_strip(strip_kind, "left")
            strip_b = next_arm.side_strip(strip_kind, "right")
            if strip_a is None or strip_b is None:
                continue
            connector = _build_lane_connector_polygon(
                anchor_xy,
                arm,
                strip_a,
                next_arm,
                strip_b,
                reference_a,
                reference_b,
                ell_hint,
            )
            if connector is None:
                continue
            polygon_points, from_stop, to_stop = connector
            polygon = _polygon_from_points(polygon_points)
            if polygon is None:
                continue
            patch_records.append(
                {
                    "patch_id": f"{junction_id}_{strip_kind}_{arm_index:02d}_connector",
                    "strip_kind": strip_kind,
                    "geometry": polygon,
                    "is_fused": True,
                    "generation_mode": "roadpen_style_lane_connector",
                    "quadrant_id": quadrant_id,
                    "from_road_id": int(arm.road_id),
                    "from_centerline_id": arm.centerline_id,
                    "from_strip_id": strip_a.strip_id,
                    "from_strip_zone": strip_a.zone,
                    "from_stop_xy": [round(float(from_stop[0]), 3), round(float(from_stop[1]), 3)],
                    "to_road_id": int(next_arm.road_id),
                    "to_centerline_id": next_arm.centerline_id,
                    "to_strip_id": strip_b.strip_id,
                    "to_strip_zone": strip_b.zone,
                    "to_stop_xy": [round(float(to_stop[0]), 3), round(float(to_stop[1]), 3)],
                    "gap_radians": round(float(gap_radians), 6),
                }
            )
    return patch_records


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

    # Build carriageway core from straight approach throats. This follows the
    # RoadPen model more closely than adding quadrant turn sectors or a convex
    # mouth hull: each road strip extends into the junction and visually merges
    # with perpendicular lane cuts.
    carriageway_core = _carriageway_surface_from_arm_throats(
        anchor_xy,
        junction_arms,
        crosswalk_depth_m=crosswalk_depth_m,
    )
    if getattr(carriageway_core, "is_empty", True):
        corner_points = [c.corner_center for c in corners]
        points_geom = shapely.geometry.MultiPoint(corner_points)
        carriageway_core = points_geom.convex_hull

    if not carriageway_core.is_valid:
        carriageway_core = carriageway_core.buffer(0)

    # Build fused corner strips for each strip kind. This deliberately follows
    # RoadPen's connector model: adjacent arms connect left-side outer bands to
    # the next arm's right-side bands with virtual turn curves. The old
    # angle-bisector wedges started at the junction anchor and produced visible
    # triangular slivers in the exported layout.
    fused_corner_strips: Dict[str, Any] = {}
    fused_corner_patch_records = _build_corner_connector_patch_records(
        junction_id,
        anchor_xy,
        junction_arms,
        strip_kinds,
    )
    if fused_corner_patch_records:
        from shapely.ops import unary_union

        for strip_kind in strip_kinds:
            corner_polygons = [
                patch["geometry"]
                for patch in fused_corner_patch_records
                if patch.get("strip_kind") == strip_kind
                and patch.get("geometry") is not None
                and not getattr(patch.get("geometry"), "is_empty", True)
            ]
            if not corner_polygons:
                continue
            merged = unary_union(corner_polygons)
            if not getattr(carriageway_core, "is_empty", True):
                merged = merged.difference(carriageway_core)
            if not getattr(merged, "is_valid", True):
                merged = merged.buffer(0)
            if not getattr(merged, "is_empty", True):
                fused_corner_strips[strip_kind] = merged

    # Build debug info
    debug_info = {
        "arm_count": len(junction_arms),
        "corner_count": len(corners),
        "strip_kinds_generated": list(fused_corner_strips.keys()),
        "corner_connector_patch_count": len(fused_corner_patch_records),
        "carriageway_core_area_m2": float(carriageway_core.area),
        "generation_mode": "roadpen_style_junction_fusion_v1",
    }

    return CrossStripFusionResult(
        junction_id=junction_id,
        kind="cross_junction" if len(junction_arms) == 4 else "t_junction",
        anchor_xy=anchor_xy,
        arms=junction_arms,
        corners=corners,
        carriageway_core_polygon=carriageway_core,
        fused_corner_strips=fused_corner_strips,
        fused_corner_patch_records=fused_corner_patch_records,
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
        "canonical_surface_patches": [
            {
                "surface_id": f"{fusion_result.junction_id}_canonical_carriageway",
                "surface_role": "carriageway",
                "surface_kind": "canonical",
                "geometry": fusion_result.carriageway_core_polygon,
                "source_kind": "roadpen_style_mouth_union",
            }
        ],
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

    for patch in fusion_result.fused_corner_patch_records:
        strip_kind = str(patch.get("strip_kind", "") or "")
        bucket_name = strip_to_bucket.get(strip_kind)
        polygon = patch.get("geometry")
        if not bucket_name or polygon is None or getattr(polygon, "is_empty", True):
            continue
        record = dict(patch)
        record["surface_role"] = {
            "clear_sidewalk": "sidewalk",
            "nearroad_furnishing": "furnishing",
            "frontage_reserve": "context_ground",
        }.get(strip_kind, "sidewalk")
        result[bucket_name].append(record)
        result["canonical_surface_patches"].append(
            {
                "surface_id": str(record.get("patch_id", f"{fusion_result.junction_id}_{strip_kind}")),
                "surface_role": record["surface_role"],
                "surface_kind": "canonical",
                "strip_kind": strip_kind,
                "geometry": polygon,
                "source_kind": "roadpen_style_lane_connector",
            }
        )

    # Defensive fallback for callers/tests that only inspect the merged buckets.
    if not fusion_result.fused_corner_patch_records:
        for strip_kind, polygon in fusion_result.fused_corner_strips.items():
            bucket_name = strip_to_bucket.get(strip_kind)
            if bucket_name and polygon is not None:
                try:
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
