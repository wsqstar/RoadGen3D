"""Entrance openness and noise shielding analysis for street scenes.

Street Design Guidelines — Entrance & Noise Rules
===================================================

Entrance Openness Standard
--------------------------
- Within a 4 m radius of each entrance, at least 60 % of the angular
  field must remain unobstructed by placed street furniture.
- No single asset should block more than 30 deg of the entrance's
  angular field at its given distance.

Noise Shielding Standard
------------------------
- From each entrance, 7 detection rays are cast in a ±30 deg fan toward
  the nearest carriageway edge.
- At least 30 % of the rays should be intercepted by effective shielding
  assets (trees, bollards, benches, lamps).
- Asset shielding effectiveness:
    tree   1.00  (dense canopy, largest profile)
    bollard 0.50  (solid post)
    bench   0.35  (partial, low height)
    lamp    0.15  (slender pole)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .types import EntranceAssessment, PlacedAsset, SceneEntranceReport

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHIELDING_EFFECTIVENESS: Dict[str, float] = {
    "tree": 1.0,
    "bollard": 0.5,
    "bench": 0.35,
    "lamp": 0.15,
}

_DEFAULT_OPENNESS_RADIUS_M = 4.0
_DEFAULT_OPENNESS_THRESHOLD = 0.6
_DEFAULT_OPENNESS_WEIGHT = 0.8
_DEFAULT_FAN_HALF_ANGLE_DEG = 30.0
_DEFAULT_RAY_COUNT = 7
_DEFAULT_SHIELDING_WEIGHT = 0.3
_ANGLE_RESOLUTION = 360  # 1-degree buckets


# ---------------------------------------------------------------------------
# PlacedAssetRegistry
# ---------------------------------------------------------------------------


class PlacedAssetRegistry:
    """Mutable, append-only registry of placed assets for scene-aware rules."""

    def __init__(self) -> None:
        self._assets: List[PlacedAsset] = []

    @property
    def assets(self) -> Tuple[PlacedAsset, ...]:
        return tuple(self._assets)

    def add(
        self,
        position_xz: Tuple[float, float],
        category: str,
        bbox_xz: Tuple[float, float, float, float],
    ) -> None:
        half_x = (float(bbox_xz[1]) - float(bbox_xz[0])) / 2.0
        half_z = (float(bbox_xz[3]) - float(bbox_xz[2])) / 2.0
        bbox_radius = max(abs(half_x), abs(half_z), 0.01)
        self._assets.append(
            PlacedAsset(
                position_xz=(float(position_xz[0]), float(position_xz[1])),
                category=str(category),
                bbox_xz=(float(bbox_xz[0]), float(bbox_xz[1]), float(bbox_xz[2]), float(bbox_xz[3])),
                bbox_radius=float(bbox_radius),
            )
        )

    def assets_within(self, center_xz: Tuple[float, float], radius_m: float) -> List[PlacedAsset]:
        cx, cz = float(center_xz[0]), float(center_xz[1])
        r2 = float(radius_m) ** 2
        result: List[PlacedAsset] = []
        for asset in self._assets:
            dx = asset.position_xz[0] - cx
            dz = asset.position_xz[1] - cz
            if dx * dx + dz * dz <= r2:
                result.append(asset)
        return result


# ---------------------------------------------------------------------------
# CarriagewayBoundary
# ---------------------------------------------------------------------------


class CarriagewayBoundary:
    """Unified interface to query the nearest carriageway edge point."""

    def __init__(
        self,
        edge_segments: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]],
    ) -> None:
        self._segments = list(edge_segments)

    @classmethod
    def from_template(cls, road_width_m: float, length_m: float) -> "CarriagewayBoundary":
        """Build boundary for a straight template road centered at z=0."""
        half_w = float(road_width_m) / 2.0
        length = float(length_m)
        segments = [
            ((0.0, half_w), (length, half_w)),    # left edge
            ((0.0, -half_w), (length, -half_w)),  # right edge
        ]
        return cls(edge_segments=segments)

    @classmethod
    def from_polygon(cls, carriageway_polygon: object) -> "CarriagewayBoundary":
        """Build boundary from a shapely Polygon/MultiPolygon."""
        segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        try:
            if hasattr(carriageway_polygon, "geoms"):
                polys = list(carriageway_polygon.geoms)
            else:
                polys = [carriageway_polygon]
            for poly in polys:
                coords = list(poly.exterior.coords)
                for i in range(len(coords) - 1):
                    segments.append(
                        ((float(coords[i][0]), float(coords[i][1])),
                         (float(coords[i + 1][0]), float(coords[i + 1][1])))
                    )
        except Exception:
            pass
        if not segments:
            return cls.from_template(road_width_m=8.0, length_m=80.0)
        return cls(edge_segments=segments)

    def nearest_edge_point(self, query_xz: Tuple[float, float]) -> Tuple[float, float]:
        """Return the closest point on the carriageway boundary."""
        qx, qz = float(query_xz[0]), float(query_xz[1])
        best_pt: Optional[Tuple[float, float]] = None
        best_d2 = float("inf")
        for (ax, az), (bx, bz) in self._segments:
            pt = _closest_point_on_segment(qx, qz, ax, az, bx, bz)
            d2 = (pt[0] - qx) ** 2 + (pt[1] - qz) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_pt = pt
        if best_pt is None:
            return (qx, 0.0)
        return best_pt


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _closest_point_on_segment(
    px: float, pz: float, ax: float, az: float, bx: float, bz: float,
) -> Tuple[float, float]:
    abx, abz = bx - ax, bz - az
    len2 = abx * abx + abz * abz
    if len2 < 1e-12:
        return (ax, az)
    t = max(0.0, min(1.0, ((px - ax) * abx + (pz - az) * abz) / len2))
    return (ax + t * abx, az + t * abz)


def _ray_intersects_aabb(
    ox: float, oz: float, dx: float, dz: float, ray_len: float,
    x_min: float, x_max: float, z_min: float, z_max: float,
) -> bool:
    """Test if a 2D ray from (ox,oz) in direction (dx,dz) of length ray_len
    intersects the axis-aligned bounding box [x_min,x_max] x [z_min,z_max]."""
    t_near = 0.0
    t_far = ray_len

    for origin, direction, lo, hi in [
        (ox, dx, x_min, x_max),
        (oz, dz, z_min, z_max),
    ]:
        if abs(direction) < 1e-12:
            if origin < lo or origin > hi:
                return False
        else:
            t1 = (lo - origin) / direction
            t2 = (hi - origin) / direction
            if t1 > t2:
                t1, t2 = t2, t1
            t_near = max(t_near, t1)
            t_far = min(t_far, t2)
            if t_near > t_far:
                return False
    return True


# ---------------------------------------------------------------------------
# Entrance openness computation
# ---------------------------------------------------------------------------


def compute_entrance_openness(
    entrance_xz: Tuple[float, float],
    registry: PlacedAssetRegistry,
    radius_m: float = _DEFAULT_OPENNESS_RADIUS_M,
) -> float:
    """Compute angular openness [0, 1] for a single entrance point.

    Uses a 1-degree-resolution bitmask to merge blocked angular intervals.
    """
    nearby = registry.assets_within(entrance_xz, radius_m)
    if not nearby:
        return 1.0

    blocked = [False] * _ANGLE_RESOLUTION
    ex, ez = float(entrance_xz[0]), float(entrance_xz[1])

    for asset in nearby:
        ax, az = asset.position_xz
        dx, dz = ax - ex, az - ez
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 0.01:
            continue
        angle_deg = math.degrees(math.atan2(dz, dx)) % 360.0
        half_span = math.degrees(math.atan2(asset.bbox_radius, dist))
        half_span = min(half_span, 45.0)  # cap at 45 deg per asset

        lo = int(math.floor(angle_deg - half_span)) % _ANGLE_RESOLUTION
        hi = int(math.ceil(angle_deg + half_span)) % _ANGLE_RESOLUTION

        if lo <= hi:
            for i in range(lo, hi + 1):
                blocked[i % _ANGLE_RESOLUTION] = True
        else:
            for i in range(lo, _ANGLE_RESOLUTION):
                blocked[i] = True
            for i in range(0, hi + 1):
                blocked[i] = True

    blocked_count = sum(blocked)
    return 1.0 - (float(blocked_count) / float(_ANGLE_RESOLUTION))


# ---------------------------------------------------------------------------
# Noise shielding computation
# ---------------------------------------------------------------------------


def compute_noise_shielding(
    entrance_xz: Tuple[float, float],
    carriageway_boundary: CarriagewayBoundary,
    registry: PlacedAssetRegistry,
    fan_half_angle_deg: float = _DEFAULT_FAN_HALF_ANGLE_DEG,
    ray_count: int = _DEFAULT_RAY_COUNT,
) -> float:
    """Compute noise shielding score [0, 1] for a single entrance point.

    Casts *ray_count* rays in a fan toward the carriageway and checks for
    asset interceptions.
    """
    ex, ez = float(entrance_xz[0]), float(entrance_xz[1])
    edge_pt = carriageway_boundary.nearest_edge_point(entrance_xz)
    dx, dz = edge_pt[0] - ex, edge_pt[1] - ez
    ray_len = math.sqrt(dx * dx + dz * dz)
    if ray_len < 0.1:
        return 0.0

    center_angle = math.atan2(dz, dx)
    half_fan = math.radians(float(fan_half_angle_deg))
    ray_count = max(int(ray_count), 1)
    if ray_count == 1:
        angles = [center_angle]
    else:
        angles = [
            center_angle - half_fan + 2.0 * half_fan * i / (ray_count - 1)
            for i in range(ray_count)
        ]

    all_assets = registry.assets
    total_shielding = 0.0

    for angle in angles:
        rdx = math.cos(angle)
        rdz = math.sin(angle)
        best_effectiveness = 0.0

        for asset in all_assets:
            eff = SHIELDING_EFFECTIVENESS.get(asset.category, 0.0)
            if eff <= best_effectiveness:
                continue
            if _ray_intersects_aabb(
                ex, ez, rdx, rdz, ray_len,
                asset.bbox_xz[0], asset.bbox_xz[1],
                asset.bbox_xz[2], asset.bbox_xz[3],
            ):
                best_effectiveness = max(best_effectiveness, eff)

        total_shielding += best_effectiveness

    return total_shielding / float(ray_count)


# ---------------------------------------------------------------------------
# Incremental evaluation for the placement loop
# ---------------------------------------------------------------------------


def score_entrance_impact(
    candidate_xz: Tuple[float, float],
    candidate_category: str,
    candidate_bbox_xz: Tuple[float, float, float, float],
    entrance_points_xz: Sequence[Tuple[float, float]],
    registry: PlacedAssetRegistry,
    carriageway_boundary: CarriagewayBoundary,
    openness_radius_m: float = _DEFAULT_OPENNESS_RADIUS_M,
    openness_threshold: float = _DEFAULT_OPENNESS_THRESHOLD,
) -> Tuple[float, float, Tuple[str, ...]]:
    """Evaluate the impact of placing a candidate asset near entrances.

    Returns ``(penalty, shielding_bonus, violated_rule_names)``.

    * **penalty** >= 0 : openness degradation cost.
    * **shielding_bonus** >= 0 : noise-shielding reward from the candidate.
    * **violated_rule_names** : tuple of rule names that would be violated.
    """
    if not entrance_points_xz:
        return 0.0, 0.0, ()

    cx, cz = float(candidate_xz[0]), float(candidate_xz[1])
    penalty = 0.0
    shielding_bonus = 0.0
    violated: List[str] = []

    half_x = (float(candidate_bbox_xz[1]) - float(candidate_bbox_xz[0])) / 2.0
    half_z = (float(candidate_bbox_xz[3]) - float(candidate_bbox_xz[2])) / 2.0
    candidate_radius = max(abs(half_x), abs(half_z), 0.01)

    eff = SHIELDING_EFFECTIVENESS.get(candidate_category, 0.0)

    for entrance in entrance_points_xz:
        ex, ez = float(entrance[0]), float(entrance[1])
        dx, dz = cx - ex, cz - ez
        dist = math.sqrt(dx * dx + dz * dz)

        # --- openness impact ---
        if dist < openness_radius_m and dist > 0.01:
            # Compute the angle this candidate would block
            half_span_deg = math.degrees(math.atan2(candidate_radius, dist))
            half_span_deg = min(half_span_deg, 45.0)
            added_blocked_frac = (2.0 * half_span_deg) / 360.0

            # Check if adding this pushes below threshold
            current_openness = compute_entrance_openness(entrance, registry, openness_radius_m)
            projected = current_openness - added_blocked_frac
            if projected < openness_threshold:
                gap = max(0.0, openness_threshold - projected)
                penalty += gap * _DEFAULT_OPENNESS_WEIGHT
                if "entrance_openness" not in violated:
                    violated.append("entrance_openness")

        # --- shielding impact ---
        if eff > 0.0:
            edge_pt = carriageway_boundary.nearest_edge_point(entrance)
            edge_dx, edge_dz = edge_pt[0] - ex, edge_pt[1] - ez
            ray_len = math.sqrt(edge_dx * edge_dx + edge_dz * edge_dz)
            if ray_len > 0.1:
                center_angle = math.atan2(edge_dz, edge_dx)
                cand_angle = math.atan2(dz, dx)
                angle_diff = abs(math.atan2(
                    math.sin(cand_angle - center_angle),
                    math.cos(cand_angle - center_angle),
                ))
                if angle_diff <= math.radians(_DEFAULT_FAN_HALF_ANGLE_DEG):
                    if _ray_intersects_aabb(
                        ex, ez,
                        math.cos(cand_angle), math.sin(cand_angle),
                        ray_len,
                        candidate_bbox_xz[0], candidate_bbox_xz[1],
                        candidate_bbox_xz[2], candidate_bbox_xz[3],
                    ):
                        shielding_bonus += eff * _DEFAULT_SHIELDING_WEIGHT

    return penalty, shielding_bonus, tuple(violated)


# ---------------------------------------------------------------------------
# Post-placement full evaluation
# ---------------------------------------------------------------------------


def evaluate_all_entrances(
    entrance_points_xz: Sequence[Tuple[float, float]],
    registry: PlacedAssetRegistry,
    carriageway_boundary: CarriagewayBoundary,
    openness_radius_m: float = _DEFAULT_OPENNESS_RADIUS_M,
    openness_threshold: float = _DEFAULT_OPENNESS_THRESHOLD,
    ray_count: int = _DEFAULT_RAY_COUNT,
    fan_half_angle_deg: float = _DEFAULT_FAN_HALF_ANGLE_DEG,
) -> SceneEntranceReport:
    """Evaluate openness and shielding for every entrance in the scene."""
    if not entrance_points_xz:
        return SceneEntranceReport(
            assessments=(),
            mean_openness=1.0,
            mean_shielding=0.0,
            min_openness=1.0,
            entrances_below_openness_threshold=0,
        )

    assessments: List[EntranceAssessment] = []
    for entrance in entrance_points_xz:
        openness = compute_entrance_openness(entrance, registry, openness_radius_m)
        shielding = compute_noise_shielding(
            entrance, carriageway_boundary, registry, fan_half_angle_deg, ray_count,
        )
        blocked_deg = (1.0 - openness) * 360.0
        ray_hits = int(round(shielding * ray_count))
        assessments.append(
            EntranceAssessment(
                entrance_xz=(float(entrance[0]), float(entrance[1])),
                openness_score=float(openness),
                shielding_score=float(shielding),
                blocked_angle_deg=float(blocked_deg),
                shielding_ray_hits=ray_hits,
                shielding_ray_total=int(ray_count),
            )
        )

    openness_scores = [a.openness_score for a in assessments]
    shielding_scores = [a.shielding_score for a in assessments]
    below_threshold = sum(1 for o in openness_scores if o < openness_threshold)

    return SceneEntranceReport(
        assessments=tuple(assessments),
        mean_openness=float(sum(openness_scores) / len(openness_scores)),
        mean_shielding=float(sum(shielding_scores) / len(shielding_scores)),
        min_openness=float(min(openness_scores)),
        entrances_below_openness_threshold=int(below_threshold),
    )
