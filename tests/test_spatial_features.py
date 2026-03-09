"""Tests for spatial_features module (M8)."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.spatial_features import (
    SCENE_STATS_DIM,
    SLOT_DISTANCES_DIM,
    SceneDistanceStats,
    SlotDistances,
    SpatialContext,
    build_spatial_context,
    compute_scene_distance_stats,
    compute_slot_distances,
    vectorize_scene_stats,
    vectorize_slot_distances,
)


# ---------------------------------------------------------------------------
# build_spatial_context
# ---------------------------------------------------------------------------


class TestBuildSpatialContext:
    def test_template_mode_no_graph(self):
        cfg = SimpleNamespace(road_width_m=8.0, length_m=80.0)
        ctx = build_spatial_context(cfg, None, None)
        assert ctx.junction_points_xz == ()
        assert ctx.entrance_points_xz == ()
        assert ctx.road_half_width_m == 4.0
        assert ctx.length_m == 80.0

    def test_with_junctions_from_graph(self):
        cfg = SimpleNamespace(road_width_m=10.0, length_m=100.0)
        node_a = SimpleNamespace(is_junction=True, center_xy=(10.0, 2.0))
        node_b = SimpleNamespace(is_junction=False, center_xy=(20.0, 3.0))
        node_c = SimpleNamespace(is_junction=True, center_xy=(30.0, 4.0))
        graph = SimpleNamespace(nodes=[node_a, node_b, node_c])

        ctx = build_spatial_context(cfg, graph, None)
        assert len(ctx.junction_points_xz) == 2
        assert ctx.junction_points_xz[0] == (10.0, 2.0)
        assert ctx.junction_points_xz[1] == (30.0, 4.0)

    def test_with_entrances_from_poi_context(self):
        cfg = SimpleNamespace(road_width_m=8.0, length_m=80.0)
        poi = SimpleNamespace(entrance_points_xz=((5.0, 6.5), (15.0, -6.5)))

        ctx = build_spatial_context(cfg, None, poi)
        assert len(ctx.entrance_points_xz) == 2
        assert ctx.entrance_points_xz[0] == (5.0, 6.5)


# ---------------------------------------------------------------------------
# compute_slot_distances
# ---------------------------------------------------------------------------


class TestComputeSlotDistances:
    def test_on_sidewalk(self):
        ctx = SpatialContext(
            junction_points_xz=(),
            entrance_points_xz=(),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        sd = compute_slot_distances((10.0, 6.5), ctx)
        assert abs(sd.dist_to_road_edge_m - 2.5) < 1e-6

    def test_on_road_edge_zero(self):
        ctx = SpatialContext(
            junction_points_xz=(),
            entrance_points_xz=(),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        sd = compute_slot_distances((0.0, 3.0), ctx)
        assert sd.dist_to_road_edge_m == 0.0

    def test_junction_distance(self):
        ctx = SpatialContext(
            junction_points_xz=((20.0, 0.0), (50.0, 0.0)),
            entrance_points_xz=(),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        sd = compute_slot_distances((10.0, 0.0), ctx)
        assert abs(sd.dist_to_nearest_junction_m - 10.0) < 1e-6

    def test_entrance_distance(self):
        ctx = SpatialContext(
            junction_points_xz=(),
            entrance_points_xz=((5.0, 6.0),),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        sd = compute_slot_distances((5.0, 6.0), ctx)
        assert sd.dist_to_nearest_entrance_m < 1e-6

    def test_empty_junctions_returns_inf(self):
        ctx = SpatialContext(
            junction_points_xz=(),
            entrance_points_xz=(),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        sd = compute_slot_distances((0.0, 5.0), ctx)
        assert math.isinf(sd.dist_to_nearest_junction_m)
        assert math.isinf(sd.dist_to_nearest_entrance_m)


# ---------------------------------------------------------------------------
# compute_scene_distance_stats
# ---------------------------------------------------------------------------


class TestSceneDistanceStats:
    def test_returns_stats_object(self):
        ctx = SpatialContext(
            junction_points_xz=((0.0, 0.0),),
            entrance_points_xz=((10.0, 6.5),),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        stats = compute_scene_distance_stats(ctx, sample_count=10)
        assert isinstance(stats, SceneDistanceStats)
        assert stats.junction_count == 1
        assert stats.entrance_count == 1
        assert stats.mean_dist_road_edge >= 0.0
        assert stats.std_dist_road_edge >= 0.0

    def test_no_junctions_inf_mean(self):
        ctx = SpatialContext(
            junction_points_xz=(),
            entrance_points_xz=(),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        stats = compute_scene_distance_stats(ctx, sample_count=5)
        assert math.isinf(stats.mean_dist_junction)
        assert math.isinf(stats.mean_dist_entrance)


# ---------------------------------------------------------------------------
# vectorize
# ---------------------------------------------------------------------------


class TestVectorize:
    def test_scene_stats_dim(self):
        stats = SceneDistanceStats(
            mean_dist_road_edge=2.0,
            std_dist_road_edge=0.5,
            mean_dist_junction=25.0,
            std_dist_junction=10.0,
            mean_dist_entrance=8.0,
            std_dist_entrance=3.0,
            junction_count=3,
            entrance_count=2,
        )
        vec = vectorize_scene_stats(stats)
        assert vec.shape == (SCENE_STATS_DIM,)
        assert vec.dtype == np.float32
        assert np.all(vec >= 0.0)
        assert np.all(vec <= 1.0)

    def test_scene_stats_inf_becomes_one(self):
        stats = SceneDistanceStats(
            mean_dist_road_edge=1.25,
            std_dist_road_edge=0.0,
            mean_dist_junction=float("inf"),
            std_dist_junction=0.0,
            mean_dist_entrance=float("inf"),
            std_dist_entrance=0.0,
            junction_count=0,
            entrance_count=0,
        )
        vec = vectorize_scene_stats(stats)
        assert vec[2] == pytest.approx(1.0)  # mean_dist_junction
        assert vec[4] == pytest.approx(1.0)  # mean_dist_entrance

    def test_slot_distances_dim(self):
        sd = SlotDistances(
            dist_to_road_edge_m=2.5,
            dist_to_nearest_junction_m=15.0,
            dist_to_nearest_entrance_m=5.0,
        )
        vec = vectorize_slot_distances(sd)
        assert vec.shape == (SLOT_DISTANCES_DIM,)
        assert vec.dtype == np.float32
        assert np.all(vec >= 0.0)
        assert np.all(vec <= 1.0)

    def test_slot_distances_inf_becomes_one(self):
        sd = SlotDistances(
            dist_to_road_edge_m=1.0,
            dist_to_nearest_junction_m=float("inf"),
            dist_to_nearest_entrance_m=float("inf"),
        )
        vec = vectorize_slot_distances(sd)
        assert vec[1] == pytest.approx(1.0)
        assert vec[2] == pytest.approx(1.0)
