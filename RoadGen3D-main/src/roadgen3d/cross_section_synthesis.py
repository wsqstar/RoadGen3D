"""POI-driven cross-section synthesis for OSM street composition."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .poi_taxonomy import nonempty_poi_points, normalize_poi_points_by_type, poi_breakdown_string

_SIDEWALK_ONLY_POI_TYPES = {"entrance", "parking_entrance", "subway_entrance"}
_FURNISHING_POI_TYPES = {"bus_stop", "post_box", "waste_basket", "bollard", "fire_hydrant"}
_EDGE_POI_TYPES = {"crossing", "traffic_signals"}

_SIDEWALK_MARGIN_M = 0.35
_FURNISHING_MARGIN_M = 0.30
_EDGE_MARGIN_M = 0.20


@dataclass(frozen=True)
class PoiDrivenCrossSection:
    """Synthesized cross-section widths for a selected OSM road."""

    carriageway_width_m: float
    left_clear_path_width_m: float
    right_clear_path_width_m: float
    left_furnishing_width_m: float
    right_furnishing_width_m: float
    required_left_width_m: float
    required_right_width_m: float
    row_width_m: float
    width_expanded: bool
    width_reallocation_reason: str
    poi_fit_feasible: bool
    poi_fit_report: Dict[str, Any] = field(default_factory=dict)
    candidate_poi_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)

    @property
    def left_sidewalk_width_m(self) -> float:
        return float(self.left_clear_path_width_m + self.left_furnishing_width_m)

    @property
    def right_sidewalk_width_m(self) -> float:
        return float(self.right_clear_path_width_m + self.right_furnishing_width_m)


def _safe_round(value: float) -> float:
    return round(float(value), 4)


def _road_reference(roads: Sequence[Any]) -> Any | None:
    if not roads:
        return None
    if len(roads) == 1:
        return roads[0]

    def _length(road: Any) -> float:
        coords = list(getattr(road, "coords", []) or [])
        if len(coords) < 2:
            return 0.0
        total = 0.0
        for start, end in zip(coords, coords[1:]):
            total += math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
        return total

    return max(roads, key=_length)


def _distance_to_segment(
    point: Tuple[float, float],
    start: Tuple[float, float],
    end: Tuple[float, float],
) -> Tuple[float, float]:
    px, py = float(point[0]), float(point[1])
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    dx = ex - sx
    dy = ey - sy
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 1e-9:
        return math.hypot(px - sx, py - sy), 0.0
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / seg_len_sq))
    nx = sx + t * dx
    ny = sy + t * dy
    dist = math.hypot(px - nx, py - ny)
    cross = dx * (py - sy) - dy * (px - sx)
    return dist, cross


def classify_point_relative_to_road(
    point: Tuple[float, float],
    road: Any | None,
) -> Tuple[str, float]:
    """Return the side ('left'|'right') and lateral distance to the road centerline."""

    coords = list(getattr(road, "coords", []) or [])
    if len(coords) < 2:
        return ("right", abs(float(point[1])))

    best_distance = float("inf")
    best_cross = 0.0
    for start, end in zip(coords, coords[1:]):
        distance, cross = _distance_to_segment(point, start, end)
        if distance < best_distance:
            best_distance = distance
            best_cross = cross
    side = "left" if best_cross >= 0.0 else "right"
    return side, float(best_distance)


def _collect_candidate_pois(
    road: Any | None,
    poi_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
    search_radius_m: float,
) -> Tuple[Dict[str, List[Tuple[float, float]]], Dict[str, int], Dict[str, int]]:
    normalized = normalize_poi_points_by_type(poi_points_by_type)
    if road is None:
        return normalized, {"left": 0, "right": 0}, {}

    filtered: Dict[str, List[Tuple[float, float]]] = {poi_type: [] for poi_type in normalized}
    side_counts = {"left": 0, "right": 0}
    per_type_counts: Dict[str, int] = {}
    for poi_type, points in nonempty_poi_points(normalized).items():
        for point in points:
            side, distance = classify_point_relative_to_road(point, road)
            if distance > float(search_radius_m):
                continue
            filtered[poi_type].append((float(point[0]), float(point[1])))
            side_counts[side] += 1
        if filtered[poi_type]:
            per_type_counts[poi_type] = len(filtered[poi_type])
    return filtered, side_counts, per_type_counts


def _base_side_widths(
    *,
    sidewalk_seed_width_m: float,
    min_clear_path_width_m: float,
    left_furnishing_min_width_m: float,
    right_furnishing_min_width_m: float,
) -> Tuple[float, float, float, float]:
    left_total = max(float(sidewalk_seed_width_m), float(min_clear_path_width_m + left_furnishing_min_width_m))
    right_total = max(float(sidewalk_seed_width_m), float(min_clear_path_width_m + right_furnishing_min_width_m))
    left_furnishing = float(left_furnishing_min_width_m)
    right_furnishing = float(right_furnishing_min_width_m)
    left_clear = max(float(min_clear_path_width_m), float(left_total - left_furnishing))
    right_clear = max(float(min_clear_path_width_m), float(right_total - right_furnishing))
    return left_clear, right_clear, left_furnishing, right_furnishing


def synthesize_poi_driven_cross_section(
    *,
    roads: Sequence[Any],
    poi_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
    road_width_m: float,
    lane_count: int,
    sidewalk_seed_width_m: float,
    base_lane_width_m: float | None,
    min_clear_path_width_m: float,
    left_furnishing_min_width_m: float,
    right_furnishing_min_width_m: float,
) -> PoiDrivenCrossSection:
    """Synthesize left/right pedestrian widths so target POIs remain on-street."""

    reference_road = _road_reference(roads)
    lane_count_value = max(1, int(lane_count))
    seed_carriageway_width = max(float(road_width_m), 0.5)
    inferred_lane_width = (
        float(base_lane_width_m)
        if base_lane_width_m is not None and float(base_lane_width_m) > 0.0
        else float(seed_carriageway_width / lane_count_value)
    )
    carriageway_width = max(float(inferred_lane_width * lane_count_value), 0.5)

    left_clear, right_clear, left_furnishing, right_furnishing = _base_side_widths(
        sidewalk_seed_width_m=float(sidewalk_seed_width_m),
        min_clear_path_width_m=float(min_clear_path_width_m),
        left_furnishing_min_width_m=float(left_furnishing_min_width_m),
        right_furnishing_min_width_m=float(right_furnishing_min_width_m),
    )
    base_left_total = float(left_clear + left_furnishing)
    base_right_total = float(right_clear + right_furnishing)
    base_row_width = float(seed_carriageway_width + base_left_total + base_right_total)
    search_radius_m = max(float(seed_carriageway_width * 0.5 + sidewalk_seed_width_m + 8.0), 15.0)

    candidate_pois, side_counts, candidate_counts = _collect_candidate_pois(
        reference_road,
        poi_points_by_type,
        search_radius_m=float(search_radius_m),
    )

    carriageway_half = float(carriageway_width / 2.0)
    required_left_total = float(base_left_total)
    required_right_total = float(base_right_total)
    required_left_furnishing = float(left_furnishing)
    required_right_furnishing = float(right_furnishing)
    carriageway_aligned_points: List[Dict[str, Any]] = []

    for poi_type, points in nonempty_poi_points(candidate_pois).items():
        for point in points:
            side, lateral_distance = classify_point_relative_to_road(point, reference_road)
            side_total_required = max(0.0, float(lateral_distance - carriageway_half))
            if poi_type in _SIDEWALK_ONLY_POI_TYPES:
                if lateral_distance <= carriageway_half + 1e-6:
                    carriageway_aligned_points.append(
                        {
                            "poi_type": poi_type,
                            "point_xz": [_safe_round(point[0]), _safe_round(point[1])],
                            "reason": "poi_within_existing_carriageway",
                        }
                    )
                    continue
                required_value = side_total_required + _SIDEWALK_MARGIN_M
                if side == "left":
                    required_left_total = max(required_left_total, float(required_value))
                else:
                    required_right_total = max(required_right_total, float(required_value))
            elif poi_type in _FURNISHING_POI_TYPES:
                if lateral_distance <= carriageway_half + 1e-6:
                    carriageway_aligned_points.append(
                        {
                            "poi_type": poi_type,
                            "point_xz": [_safe_round(point[0]), _safe_round(point[1])],
                            "reason": "poi_within_existing_carriageway",
                        }
                    )
                    continue
                required_value = side_total_required + _FURNISHING_MARGIN_M
                if side == "left":
                    required_left_furnishing = max(required_left_furnishing, float(required_value))
                else:
                    required_right_furnishing = max(required_right_furnishing, float(required_value))
            elif poi_type in _EDGE_POI_TYPES:
                required_value = max(0.0, side_total_required) + _EDGE_MARGIN_M
                if side == "left":
                    required_left_total = max(required_left_total, float(required_value))
                else:
                    required_right_total = max(required_right_total, float(required_value))

    left_furnishing = max(float(left_furnishing), float(required_left_furnishing))
    right_furnishing = max(float(right_furnishing), float(required_right_furnishing))
    required_left_total = max(float(required_left_total), float(left_furnishing + min_clear_path_width_m))
    required_right_total = max(float(required_right_total), float(right_furnishing + min_clear_path_width_m))
    left_clear = max(float(min_clear_path_width_m), float(required_left_total - left_furnishing))
    right_clear = max(float(min_clear_path_width_m), float(required_right_total - right_furnishing))
    final_left_total = float(left_clear + left_furnishing)
    final_right_total = float(right_clear + right_furnishing)
    row_width = float(carriageway_width + final_left_total + final_right_total)

    containment_failures: List[Dict[str, Any]] = []
    for poi_type, points in nonempty_poi_points(candidate_pois).items():
        for point in points:
            side, lateral_distance = classify_point_relative_to_road(point, reference_road)
            side_total = final_left_total if side == "left" else final_right_total
            furnishing_width = left_furnishing if side == "left" else right_furnishing
            if poi_type in _SIDEWALK_ONLY_POI_TYPES:
                contained = lateral_distance <= carriageway_half + side_total + 1e-6
            elif poi_type in _FURNISHING_POI_TYPES:
                contained = lateral_distance <= carriageway_half + max(side_total, furnishing_width) + 1e-6
            else:
                contained = lateral_distance <= carriageway_half + side_total + 1e-6
            if not contained:
                containment_failures.append(
                    {
                        "poi_type": poi_type,
                        "point_xz": [_safe_round(point[0]), _safe_round(point[1])],
                        "side": side,
                        "lateral_distance_m": _safe_round(lateral_distance),
                    }
                )

    width_expanded = bool(row_width > base_row_width + 1e-6)
    reasons: List[str] = []
    released_width = max(float(seed_carriageway_width - carriageway_width), 0.0)
    if released_width > 1e-6:
        reasons.append(f"reallocated {_safe_round(released_width)}m from carriageway to pedestrian bands")
    if width_expanded:
        reasons.append(f"expanded total row width by {_safe_round(row_width - base_row_width)}m")
    if final_left_total > base_left_total + 1e-6 and final_right_total <= base_right_total + 1e-6:
        reasons.append("left-side POIs widened the left sidewalk corridor")
    elif final_right_total > base_right_total + 1e-6 and final_left_total <= base_left_total + 1e-6:
        reasons.append("right-side POIs widened the right sidewalk corridor")
    elif final_left_total > base_left_total + 1e-6 and final_right_total > base_right_total + 1e-6:
        reasons.append("POIs on both sides widened both sidewalk corridors")
    if not reasons:
        reasons.append("base pedestrian corridor widths already satisfied POI containment")

    report = {
        "candidate_poi_count": int(sum(len(points) for points in candidate_pois.values())),
        "candidate_poi_breakdown": {
            poi_type: len(points)
            for poi_type, points in nonempty_poi_points(candidate_pois).items()
        },
        "candidate_poi_breakdown_str": poi_breakdown_string(candidate_counts),
        "candidate_poi_side_counts": {side: int(count) for side, count in side_counts.items()},
        "required_left_width_m": _safe_round(required_left_total),
        "required_right_width_m": _safe_round(required_right_total),
        "final_left_width_m": _safe_round(final_left_total),
        "final_right_width_m": _safe_round(final_right_total),
        "carriageway_width_m": _safe_round(carriageway_width),
        "row_width_m": _safe_round(row_width),
        "released_width_m": _safe_round(released_width),
        "width_expanded": bool(width_expanded),
        "carriageway_aligned_points": carriageway_aligned_points,
        "containment_failures": containment_failures,
    }
    feasible = not containment_failures

    return PoiDrivenCrossSection(
        carriageway_width_m=float(carriageway_width),
        left_clear_path_width_m=float(left_clear),
        right_clear_path_width_m=float(right_clear),
        left_furnishing_width_m=float(left_furnishing),
        right_furnishing_width_m=float(right_furnishing),
        required_left_width_m=float(required_left_total),
        required_right_width_m=float(required_right_total),
        row_width_m=float(row_width),
        width_expanded=bool(width_expanded),
        width_reallocation_reason="; ".join(reasons),
        poi_fit_feasible=bool(feasible),
        poi_fit_report=report,
        candidate_poi_points_by_type=candidate_pois,
    )
