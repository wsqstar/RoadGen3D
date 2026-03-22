"""Spatial distance computation for street layout features.

Computes distances from arbitrary points to three reference targets:
  1. Road edge (carriageway boundary)
  2. Nearest intersection / junction
  3. Nearest POI entrance

Used by both M6 program generator (aggregate stats) and M4 layout policy
(per-slot distances).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    extract_poi_points_by_type,
    normalize_poi_points_by_type,
)


@dataclass(frozen=True)
class SpatialContext:
    """Unified container of spatial reference points for a street scene."""

    junction_points_xz: Tuple[Tuple[float, float], ...]
    entrance_points_xz: Tuple[Tuple[float, float], ...]
    road_half_width_m: float
    length_m: float
    # Visualization-only fields (do NOT affect SCENE_STATS_DIM / SLOT_DISTANCES_DIM)
    bus_stop_points_xz: Tuple[Tuple[float, float], ...] = ()
    fire_points_xz: Tuple[Tuple[float, float], ...] = ()
    poi_points_by_type_xz: Dict[str, Tuple[Tuple[float, float], ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mapping = normalize_poi_points_by_type(self.poi_points_by_type_xz or {})
        if not mapping.get("entrance"):
            mapping["entrance"] = list(self.entrance_points_xz)
        if not mapping.get("bus_stop"):
            mapping["bus_stop"] = list(self.bus_stop_points_xz)
        if not mapping.get(CANONICAL_FIRE_POI):
            mapping[CANONICAL_FIRE_POI] = list(self.fire_points_xz)
        object.__setattr__(
            self,
            "poi_points_by_type_xz",
            {
                poi_type: tuple(points)
                for poi_type, points in mapping.items()
            },
        )


@dataclass(frozen=True)
class SlotDistances:
    """Per-slot distances to three reference targets."""

    dist_to_road_edge_m: float
    dist_to_nearest_junction_m: float
    dist_to_nearest_entrance_m: float


@dataclass(frozen=True)
class SceneDistanceStats:
    """Aggregate distance statistics for the whole scene."""

    mean_dist_road_edge: float
    std_dist_road_edge: float
    mean_dist_junction: float
    std_dist_junction: float
    mean_dist_entrance: float
    std_dist_entrance: float
    junction_count: int
    entrance_count: int


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_spatial_context(
    config: object,
    road_segment_graph: object = None,
    poi_context: object = None,
) -> SpatialContext:
    """Build a SpatialContext from available scene data.

    Parameters
    ----------
    config : StreetComposeConfig (or anything with road_width_m, length_m)
    road_segment_graph : Optional RoadSegmentGraph
    poi_context : Optional PoiContext (from poi_rules)
    """
    road_half = float(getattr(config, "road_width_m", 8.0)) / 2.0
    length = float(getattr(config, "length_m", 80.0))

    # Extract junction points from graph nodes where is_junction=True
    junctions: List[Tuple[float, float]] = []
    if road_segment_graph is not None and hasattr(road_segment_graph, "nodes"):
        for node in road_segment_graph.nodes:
            if getattr(node, "is_junction", False):
                cx, cz = getattr(node, "center_xy", (0.0, 0.0))
                junctions.append((float(cx), float(cz)))

    poi_points_by_type = {}
    if poi_context is not None:
        poi_points_by_type = {
            poi_type: tuple((float(pt[0]), float(pt[1])) for pt in points)
            for poi_type, points in extract_poi_points_by_type(poi_context, suffix="xz").items()
        }
    entrances = list(poi_points_by_type.get("entrance", ()))
    bus_stops = list(poi_points_by_type.get("bus_stop", ()))
    fire_pts = list(poi_points_by_type.get(CANONICAL_FIRE_POI, ()))

    return SpatialContext(
        junction_points_xz=tuple(junctions),
        entrance_points_xz=tuple(entrances),
        road_half_width_m=road_half,
        length_m=length,
        bus_stop_points_xz=tuple(bus_stops),
        fire_points_xz=tuple(fire_pts),
        poi_points_by_type_xz=poi_points_by_type,
    )


# ---------------------------------------------------------------------------
# Distance computation
# ---------------------------------------------------------------------------


def _min_distance_to_points(
    point_xz: Tuple[float, float],
    targets: Sequence[Tuple[float, float]],
) -> float:
    """Return minimum Euclidean distance from *point_xz* to any target."""
    if not targets:
        return float("inf")
    px, pz = float(point_xz[0]), float(point_xz[1])
    best = float("inf")
    for tx, tz in targets:
        d = math.hypot(px - float(tx), pz - float(tz))
        if d < best:
            best = d
    return best


def compute_slot_distances(
    point_xz: Tuple[float, float],
    ctx: SpatialContext,
) -> SlotDistances:
    """Compute distances from a single point to the three reference targets."""
    pz = float(point_xz[1])
    dist_edge = max(abs(pz) - ctx.road_half_width_m, 0.0)
    dist_junc = _min_distance_to_points(point_xz, ctx.junction_points_xz)
    dist_ent = _min_distance_to_points(point_xz, ctx.entrance_points_xz)
    return SlotDistances(
        dist_to_road_edge_m=dist_edge,
        dist_to_nearest_junction_m=dist_junc,
        dist_to_nearest_entrance_m=dist_ent,
    )


# ---------------------------------------------------------------------------
# Scene-level aggregate statistics (for M6 program generator)
# ---------------------------------------------------------------------------


def compute_scene_distance_stats(
    ctx: SpatialContext,
    sample_count: int = 40,
) -> SceneDistanceStats:
    """Sample points along the sidewalk zone and compute distance statistics."""
    half_len = ctx.length_m / 2.0
    # Sample on both sidewalks: z = ±(road_half + sidewalk_center_offset)
    # Use a rough offset of road_half + 1.25m (typical sidewalk midpoint)
    sw_offset = ctx.road_half_width_m + 1.25

    edge_dists: List[float] = []
    junc_dists: List[float] = []
    ent_dists: List[float] = []

    n = max(int(sample_count), 4)
    for i in range(n):
        x = -half_len + (i + 0.5) * (ctx.length_m / n)
        for z_sign in (1.0, -1.0):
            z = z_sign * sw_offset
            sd = compute_slot_distances((x, z), ctx)
            edge_dists.append(sd.dist_to_road_edge_m)
            junc_dists.append(sd.dist_to_nearest_junction_m)
            ent_dists.append(sd.dist_to_nearest_entrance_m)

    def _stats(vals: List[float]) -> Tuple[float, float]:
        finite = [v for v in vals if math.isfinite(v)]
        if not finite:
            return (float("inf"), 0.0)
        arr = np.asarray(finite, dtype=np.float64)
        return (float(np.mean(arr)), float(np.std(arr)))

    me, se = _stats(edge_dists)
    mj, sj = _stats(junc_dists)
    mn, sn = _stats(ent_dists)
    return SceneDistanceStats(
        mean_dist_road_edge=me,
        std_dist_road_edge=se,
        mean_dist_junction=mj,
        std_dist_junction=sj,
        mean_dist_entrance=mn,
        std_dist_entrance=sn,
        junction_count=len(ctx.junction_points_xz),
        entrance_count=len(ctx.entrance_points_xz),
    )


# ---------------------------------------------------------------------------
# Vectorization helpers
# ---------------------------------------------------------------------------

_SCENE_SCALES = (15.0, 10.0, 100.0, 50.0, 30.0, 15.0, 50.0, 20.0)
SCENE_STATS_DIM = 8

_SLOT_SCALES = (15.0, 100.0, 30.0)
SLOT_DISTANCES_DIM = 3


def vectorize_scene_stats(stats: SceneDistanceStats) -> np.ndarray:
    """Convert SceneDistanceStats to an 8-dim feature vector in [0, 1]."""
    raw = [
        stats.mean_dist_road_edge,
        stats.std_dist_road_edge,
        stats.mean_dist_junction,
        stats.std_dist_junction,
        stats.mean_dist_entrance,
        stats.std_dist_entrance,
        float(stats.junction_count),
        float(stats.entrance_count),
    ]
    out = np.zeros(SCENE_STATS_DIM, dtype=np.float32)
    for i, (val, scale) in enumerate(zip(raw, _SCENE_SCALES)):
        v = float(val)
        if not math.isfinite(v):
            v = scale  # inf → 1.0 after division
        out[i] = min(v / scale, 1.0)
    return out


def vectorize_slot_distances(sd: SlotDistances) -> np.ndarray:
    """Convert SlotDistances to a 3-dim feature vector in [0, 1]."""
    raw = [
        sd.dist_to_road_edge_m,
        sd.dist_to_nearest_junction_m,
        sd.dist_to_nearest_entrance_m,
    ]
    out = np.zeros(SLOT_DISTANCES_DIM, dtype=np.float32)
    for i, (val, scale) in enumerate(zip(raw, _SLOT_SCALES)):
        v = float(val)
        if not math.isfinite(v):
            v = scale  # inf → 1.0
        out[i] = min(v / scale, 1.0)
    return out
