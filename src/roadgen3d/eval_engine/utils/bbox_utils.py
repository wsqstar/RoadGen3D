"""Bounding box utilities for spatial analysis.

All functions operate on bbox_xz format: [x_min, x_max, z_min, z_max]
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple


def extract_bbox_z_range(placement: Mapping[str, Any]) -> Tuple[float, float]:
    """Extract z-axis range from placement bbox_xz or native_size_m.

    Returns:
        (z_min, z_max) in meters
    """
    bbox = placement.get("bbox_xz")
    if bbox and len(bbox) >= 4:
        return float(bbox[2]), float(bbox[3])

    # Fallback: estimate from position and native_size_m
    pos = placement.get("position_xyz", [0.0, 0.0, 0.0])
    z_center = float(pos[2])
    native_size = placement.get("native_size_m") or {}
    depth = float(native_size.get("depth_m", 1.0))
    scale = float(placement.get("scale", 1.0) or 1.0)

    z_half = (depth * scale) / 2.0
    return z_center - z_half, z_center + z_half


def compute_clear_width_from_placements(
    placements: Sequence[Mapping[str, Any]],
    sidewalk_width: float,
    road_width: float,
) -> Tuple[float, float]:
    """Compute actual clear path width using furniture bounding boxes.

    Analyzes furniture bbox_xz to find the minimum unobstructed width
    on left and right sidewalks. Falls back to sidewalk_width if no
    obstructing furniture is found.

    Args:
        placements: List of placed assets
        sidewalk_width: Single sidewalk width in meters
        road_width: Total road width in meters

    Returns:
        (left_clear_m, right_clear_m): Clear widths in meters
    """
    road_half = road_width / 2.0

    # Find furniture on each sidewalk and their distance from road edge
    left_min_dist = sidewalk_width  # Default: no obstruction
    right_min_dist = sidewalk_width  # Default: no obstruction

    for p in placements:
        cat = str(p.get("category", "")).strip().lower()
        if cat in ("building", "house"):
            # Buildings are far from road edge, ignore for clear width
            continue

        z_min, z_max = extract_bbox_z_range(p)

        # Left sidewalk (z > 0): find furniture closest to road edge (z = road_half)
        if z_min > road_half:
            dist_from_road = z_min - road_half
            left_min_dist = min(left_min_dist, dist_from_road)

        # Right sidewalk (z < 0): find furniture closest to road edge (z = -road_half)
        elif z_max < -road_half:
            dist_from_road = abs(z_max - (-road_half))
            right_min_dist = min(right_min_dist, dist_from_road)

    return max(left_min_dist, 0.0), max(right_min_dist, 0.0)


def compute_footprint_area(placement: Mapping[str, Any]) -> float:
    """Calculate actual footprint area from bbox or native_size_m.

    Args:
        placement: Asset placement data

    Returns:
        Footprint area in square meters
    """
    bbox = placement.get("bbox_xz")
    if bbox and len(bbox) >= 4:
        x_span = abs(float(bbox[1]) - float(bbox[0]))
        z_span = abs(float(bbox[3]) - float(bbox[2]))
        return x_span * z_span

    # Fallback: estimate from native_size_m × scale
    native_size = placement.get("native_size_m") or {}
    width = float(native_size.get("width_m", 1.0))
    depth = float(native_size.get("depth_m", 1.0))
    scale = float(placement.get("scale", 1.0) or 1.0)

    return (width * scale) * (depth * scale)


def compute_canopy_area(
    placement: Mapping[str, Any],
    default_canopy_size: Tuple[float, float] = (3.6, 3.6),
) -> float:
    """Calculate tree canopy area from asset metadata.

    Uses native_size_m.canopy_width_m × scale when available,
    falls back to default canopy size otherwise.

    Args:
        placement: Tree placement data
        default_canopy_size: Fallback canopy dimensions (width, depth)

    Returns:
        Canopy area in square meters
    """
    scale = float(placement.get("scale", 1.0) or 1.0)
    native_size = placement.get("native_size_m") or {}

    canopy_width = native_size.get("canopy_width_m")
    if canopy_width is not None:
        # Use actual canopy dimensions scaled by placement scale
        return (canopy_width * scale) ** 2

    # Fallback to constant canopy size
    return default_canopy_size[0] * default_canopy_size[1]
