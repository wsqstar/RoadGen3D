"""Walkability metrics computation (11底层指标).

This module is fully decoupled and uses EvalConfig for all parameters.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..core.config import WalkabilityConfig
from ..core.types import WalkabilityIndicators
from ..utils.bbox_utils import (
    compute_canopy_area,
    compute_clear_width_from_placements,
    compute_footprint_area,
)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, value)))


def _mean(values: Sequence[float]) -> float:
    items = [float(v) for v in values if v is not None]
    return float(sum(items) / len(items)) if items else 0.0


def _spacing_cv(values: Sequence[float]) -> float:
    """Compute coefficient of variation for spacing uniformity."""
    if len(values) < 2:
        return 0.0
    xs = sorted(values)
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    mean_gap = _mean(gaps)
    if mean_gap <= 1e-6:
        return 0.0
    variance = _mean([(gap - mean_gap) ** 2 for gap in gaps])
    return math.sqrt(max(variance, 0.0)) / mean_gap


def _lamp_uniformity(placements: Sequence[Mapping[str, Any]]) -> float:
    """Compute lighting uniformity from lamp positions."""
    lamp_xs = [
        float((placement.get("position_xyz") or [0.0])[0])
        for placement in placements
        if str(placement.get("category", "")).strip().lower() == "lamp"
        and isinstance(placement.get("position_xyz"), (list, tuple))
        and len(placement["position_xyz"]) >= 1
    ]
    if len(lamp_xs) < 2:
        return 1.0
    return _clamp(1.0 - _spacing_cv(lamp_xs))


def _transit_proximity(
    bus_stop_points: List[List[float]],
    road_width: float,
    sidewalk_width: float,
    decay_m: float,
) -> float:
    """Compute transit proximity score based on bus stop distance."""
    if not bus_stop_points:
        return 0.0

    road_half = road_width / 2.0
    walkway_z = [road_half + sidewalk_width / 2.0, -(road_half + sidewalk_width / 2.0)]
    min_dist = math.inf

    for point in bus_stop_points:
        if len(point) < 2:
            continue
        x, z = float(point[0]), float(point[1])
        for center_z in walkway_z:
            dist = math.hypot(x, z - center_z)
            min_dist = min(min_dist, dist)

    if not math.isfinite(min_dist):
        return 0.0
    return _clamp(math.exp(-min_dist / decay_m))


def _crossing_provision(
    crossing_points: List[List[float]],
    length_m: float,
    crossing_spacing_m: float,
) -> float:
    """Compute crossing provision score."""
    crossings = len(crossing_points)
    target = max(length_m / crossing_spacing_m, 1e-3)
    return _clamp(crossings / target)


def _entrance_density(entrance_count: int, length_m: float, density_ideal: float) -> float:
    """Compute entrance density score."""
    per_m = entrance_count / max(length_m, 1e-6)
    return _clamp(per_m / density_ideal)


def _poi_mix(land_use_summary: Dict[str, float], poi_points: Dict[str, List[List[float]]]) -> float:
    """Compute POI mix diversity using Shannon entropy."""
    poi_counts: Dict[str, float] = {}

    # From land use summary
    for key, value in land_use_summary.items():
        try:
            poi_counts[str(key)] = poi_counts.get(str(key), 0.0) + float(value or 0.0)
        except Exception:
            continue

    # From POI points
    for poi_type, points in poi_points.items():
        poi_counts[poi_type] = poi_counts.get(poi_type, 0.0) + len(points or [])

    values = [max(float(v), 0.0) for v in poi_counts.values() if v]
    total = sum(values)
    if total <= 0:
        return 0.0

    entropy = -sum((v / total) * math.log(max(v / total, 1e-9)) for v in values)
    max_entropy = math.log(len(values)) if values else 1.0
    return _clamp(entropy / max(max_entropy, 1e-9))


def _micro_env(tree_shade: float, noise: float, openness: float, config: WalkabilityConfig) -> float:
    """Compute micro-environment comfort."""
    return _clamp(
        config.micro_env_tree_weight * tree_shade
        + config.micro_env_noise_weight * noise
        + config.micro_env_openness_weight * openness
    )


def compute_walkability(
    placements: Sequence[Mapping[str, Any]],
    length_m: float,
    road_width_m: float,
    sidewalk_width_m: float,
    left_clear_path_width_m: float | None = None,
    right_clear_path_width_m: float | None = None,
    left_furnishing_width_m: float = 0.0,
    right_furnishing_width_m: float = 0.0,
    entrance_count: int = 0,
    mean_entrance_openness: float = 1.0,
    mean_noise_shielding: float = 0.0,
    bus_stop_points_xz: List[List[float]] | None = None,
    poi_points_by_type_xz: Dict[str, List[List[float]]] | None = None,
    land_use_summary: Dict[str, float] | None = None,
    config: WalkabilityConfig | None = None,
) -> WalkabilityIndicators:
    """Compute complete walkability indicators.

    Args:
        placements: List of placed assets
        length_m: Street segment length
        road_width_m: Total road width
        sidewalk_width_m: Single sidewalk width
        left_clear_path_width_m: Left clear width (auto-computed if None)
        right_clear_path_width_m: Right clear width (auto-computed if None)
        left_furnishing_width_m: Left furnishing zone width
        right_furnishing_width_m: Right furnishing zone width
        entrance_count: Number of building entrances
        mean_entrance_openness: Mean entrance openness
        mean_noise_shielding: Mean noise shielding
        bus_stop_points_xz: Bus stop coordinates
        poi_points_by_type_xz: POI points by type
        land_use_summary: Land use counts
        config: Walkability parameters (uses defaults if None)

    Returns:
        WalkabilityIndicators with all 11 metrics and pillar scores
    """
    cfg = config or WalkabilityConfig()

    # Auto-compute clear width from bbox if not provided
    if left_clear_path_width_m is None or right_clear_path_width_m is None:
        bbox_left, bbox_right = compute_clear_width_from_placements(
            placements, sidewalk_width_m, road_width_m
        )
        left_clear = left_clear_path_width_m if left_clear_path_width_m is not None else bbox_left
        right_clear = right_clear_path_width_m if right_clear_path_width_m is not None else bbox_right
    else:
        left_clear = left_clear_path_width_m
        right_clear = right_clear_path_width_m

    # 1. SID_CLR - Clear width
    clear_width = _mean([left_clear, right_clear])
    sid_clr = _clamp((clear_width - cfg.clear_width_min) / (cfg.clear_width_ideal - cfg.clear_width_min))

    # 2. CLEAR_CONT - Clear continuity
    clear_area = max(length_m * (left_clear + right_clear), 0.0)
    sidewalk_area = max(length_m * sidewalk_width_m * 2.0, 1e-3)
    clear_cont = _clamp(clear_area / sidewalk_area)

    # 3. FURN_D - Furniture density (using actual footprint area)
    total_area = sum(
        compute_footprint_area(p)
        for p in placements
        if str(p.get("category", "")).strip().lower() in _AMENITY_CATEGORIES
    )
    density = total_area / max(length_m, 1e-6)
    furn_d = _clamp(density / cfg.amenity_density_ideal)

    # 4. LIGHT_UNI - Light uniformity
    light_uni = _lamp_uniformity(placements)

    # 5. TREE_SHADE - Tree shade (using actual canopy area)
    total_canopy = sum(
        compute_canopy_area(p, cfg.default_canopy_size_m)
        for p in placements
        if str(p.get("category", "")).strip().lower() == "tree"
    )
    tree_shade = _clamp(total_canopy / sidewalk_area)

    # 6. BUFFER_RATIO - Buffer ratio
    buffer_ratio = _clamp(
        (left_furnishing_width_m + right_furnishing_width_m) / max(road_width_m, 1e-3)
    )

    # 7. TRANSIT_PROX - Transit proximity
    transit_prox = _transit_proximity(
        bus_stop_points_xz or [],
        road_width_m,
        sidewalk_width_m,
        cfg.transit_decay_m,
    )

    # 8. CROSS_PROV - Crossing provision
    crossing_points = (poi_points_by_type_xz or {}).get("crossing", [])
    cross_prov = _crossing_provision(crossing_points, length_m, cfg.crossing_spacing_m)

    # 9. ENTR_DENS - Entrance density
    entr_dens = _entrance_density(entrance_count, length_m, cfg.entrance_density_ideal)

    # 10. POI_MIX - POI mix
    poi_mix = _poi_mix(land_use_summary or {}, poi_points_by_type_xz or {})

    # 11. MICRO_ENV - Micro environment
    micro_env = _micro_env(tree_shade, mean_noise_shielding, mean_entrance_openness, cfg)

    # 聚合指标
    indicators = {
        "SID_CLR": round(sid_clr, 4),
        "CLEAR_CONT": round(clear_cont, 4),
        "FURN_D": round(furn_d, 4),
        "LIGHT_UNI": round(light_uni, 4),
        "TREE_SHADE": round(tree_shade, 4),
        "BUFFER_RATIO": round(buffer_ratio, 4),
        "TRANSIT_PROX": round(transit_prox, 4),
        "CROSS_PROV": round(cross_prov, 4),
        "ENTR_DENS": round(entr_dens, 4),
        "POI_MIX": round(poi_mix, 4),
        "MICRO_ENV": round(micro_env, 4),
    }

    # 三大支柱
    protection = _mean([indicators["LIGHT_UNI"], indicators["BUFFER_RATIO"], indicators["CROSS_PROV"]])
    comfort = _mean([indicators["SID_CLR"], indicators["CLEAR_CONT"], indicators["TREE_SHADE"], indicators["MICRO_ENV"]])
    delight = _mean([indicators["FURN_D"], indicators["TRANSIT_PROX"], indicators["ENTR_DENS"], indicators["POI_MIX"]])

    walkability_index = round(
        cfg.protection_weight * protection
        + cfg.comfort_weight * comfort
        + cfg.delight_weight * delight,
        4,
    )

    return WalkabilityIndicators(
        sid_clr=indicators["SID_CLR"],
        clear_cont=indicators["CLEAR_CONT"],
        furn_d=indicators["FURN_D"],
        light_uni=indicators["LIGHT_UNI"],
        tree_shade=indicators["TREE_SHADE"],
        buffer_ratio=indicators["BUFFER_RATIO"],
        transit_prox=indicators["TRANSIT_PROX"],
        cross_prov=indicators["CROSS_PROV"],
        entr_dens=indicators["ENTR_DENS"],
        poi_mix=indicators["POI_MIX"],
        micro_env=indicators["MICRO_ENV"],
        protection=round(protection, 4),
        comfort=round(comfort, 4),
        delight=round(delight, 4),
        walkability_index=walkability_index,
        metadata={
            "length_m": round(length_m, 3),
            "sidewalk_width_m": round(sidewalk_width_m, 3),
            "left_clear_path_width_m": round(left_clear, 3),
            "right_clear_path_width_m": round(right_clear, 3),
        },
    )


_AMENITY_CATEGORIES = {"bench", "lamp", "trash", "bus_stop", "mailbox", "hydrant"}
