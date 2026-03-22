"""Tests for POI exclusion-zone computation and visualization helpers."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.poi_rules import (
    PoiContext,
    PoiExclusionInfo,
    PoiRuleSet,
    build_exclusion_zones,
    compute_exclusion_radii,
    load_rule_set,
)
from roadgen3d.spatial_features import SpatialContext, build_spatial_context


# ---------------------------------------------------------------------------
# compute_exclusion_radii
# ---------------------------------------------------------------------------


class TestComputeExclusionRadii:
    def test_default_rule_set_values(self):
        rs = load_rule_set("entrance_fire_bus_stop_v1")
        radii = compute_exclusion_radii(rs)

        # entrance: sigma=1.25, w_max=1.0  =>  1.25 * ln(1.0/0.3) ≈ 1.505
        assert abs(radii["entrance"] - 1.25 * math.log(1.0 / 0.3)) < 1e-6
        # fire: sigma=1.5, w_max=1.0  =>  1.5 * ln(1.0/0.3) ≈ 1.806
        assert abs(radii["fire"] - 1.5 * math.log(1.0 / 0.3)) < 1e-6
        # bus_stop: sigma=2.0, w_max=0.9  =>  2.0 * ln(0.9/0.3) ≈ 2.197
        assert abs(radii["bus_stop"] - 2.0 * math.log(0.9 / 0.3)) < 1e-6

    def test_all_radii_positive(self):
        rs = load_rule_set()
        radii = compute_exclusion_radii(rs)
        for k, v in radii.items():
            assert v > 0.0, f"Expected positive radius for {k}, got {v}"

    def test_custom_threshold(self):
        rs = load_rule_set()
        radii_low = compute_exclusion_radii(rs, threshold=0.1)
        radii_high = compute_exclusion_radii(rs, threshold=0.5)
        # Lower threshold -> larger exclusion zones
        for k in radii_low:
            if radii_high[k] > 0:
                assert radii_low[k] > radii_high[k]


# ---------------------------------------------------------------------------
# build_exclusion_zones
# ---------------------------------------------------------------------------


class TestBuildExclusionZones:
    def _make_poi_ctx(self, n_entrance=2, n_bus=1, n_fire=1):
        return PoiContext(
            entrance_points_xz=tuple((float(i), 5.0) for i in range(n_entrance)),
            bus_stop_points_xz=tuple((10.0 + float(i), 5.0) for i in range(n_bus)),
            fire_points_xz=tuple((20.0 + float(i), 5.0) for i in range(n_fire)),
        )

    def test_zone_count_matches_poi_count(self):
        poi_ctx = self._make_poi_ctx(n_entrance=3, n_bus=2, n_fire=1)
        rs = load_rule_set()
        zones = build_exclusion_zones(poi_ctx, rs)
        # Each rule produces zones for its POI type's points
        # entrance_clearance -> 3, fire_access -> 1, bus_stop_clearance -> 2
        assert len(zones) == 3 + 1 + 2

    def test_empty_poi_yields_no_zones(self):
        poi_ctx = PoiContext((), (), ())
        rs = load_rule_set()
        zones = build_exclusion_zones(poi_ctx, rs)
        assert len(zones) == 0

    def test_zone_fields(self):
        poi_ctx = self._make_poi_ctx(n_entrance=1, n_bus=0, n_fire=0)
        rs = load_rule_set()
        zones = build_exclusion_zones(poi_ctx, rs)
        assert len(zones) == 1
        z = zones[0]
        assert isinstance(z, PoiExclusionInfo)
        assert z.poi_type == "entrance"
        assert z.radius_m > 0.0
        assert z.rule_name == "entrance_clearance"
        assert z.position_xz == (0.0, 5.0)


# ---------------------------------------------------------------------------
# SpatialContext backward compatibility
# ---------------------------------------------------------------------------


class TestSpatialContextCompat:
    def test_default_empty_bus_stop_fire(self):
        ctx = SpatialContext(
            junction_points_xz=(),
            entrance_points_xz=(),
            road_half_width_m=4.0,
            length_m=80.0,
        )
        assert ctx.bus_stop_points_xz == ()
        assert ctx.fire_points_xz == ()

    def test_build_with_poi_context(self):
        poi_ctx = PoiContext(
            entrance_points_xz=((1.0, 2.0),),
            bus_stop_points_xz=((3.0, 4.0), (5.0, 6.0)),
            fire_points_xz=((7.0, 8.0),),
        )
        cfg = SimpleNamespace(road_width_m=8.0, length_m=80.0)
        ctx = build_spatial_context(cfg, poi_context=poi_ctx)
        assert len(ctx.entrance_points_xz) == 1
        assert len(ctx.bus_stop_points_xz) == 2
        assert len(ctx.fire_points_xz) == 1

    def test_build_without_poi_context(self):
        cfg = SimpleNamespace(road_width_m=8.0, length_m=80.0)
        ctx = build_spatial_context(cfg)
        assert ctx.bus_stop_points_xz == ()
        assert ctx.fire_points_xz == ()


# ---------------------------------------------------------------------------
# 2D visualization smoke test
# ---------------------------------------------------------------------------


class TestPlotPoiMarkers:
    @pytest.fixture(autouse=True)
    def _skip_no_matplotlib(self):
        pytest.importorskip("matplotlib")

    def test_plot_scene_with_all_poi_types(self):
        from roadgen3d.spatial_viz import plot_scene_with_markers

        ctx = SpatialContext(
            junction_points_xz=((0.0, 0.0),),
            entrance_points_xz=((5.0, 3.0),),
            road_half_width_m=4.0,
            length_m=40.0,
            bus_stop_points_xz=((10.0, 3.0),),
            fire_points_xz=((15.0, 3.0),),
        )
        cfg = SimpleNamespace(road_width_m=8.0, length_m=40.0, sidewalk_width_m=2.5)

        class _P:
            def __init__(self, pos, cat):
                self.position_xyz = pos
                self.category = cat

        placements = [_P([2.0, 0.0, 3.5], "bench")]
        zones = [
            {"poi_type": "entrance", "position_xz": [5.0, 3.0], "radius_m": 1.5, "rule_name": "entrance_clearance"},
            {"poi_type": "fire", "position_xz": [15.0, 3.0], "radius_m": 1.8, "rule_name": "fire_access"},
        ]
        conflicts = [
            {"position_xz": [2.0, 3.5], "category": "bench", "violated_rules": ["entrance_clearance"], "constraint_penalty": 0.5},
        ]

        fig = plot_scene_with_markers(
            ctx, placements, cfg,
            poi_exclusion_zones=zones,
            poi_conflicts=conflicts,
        )
        import matplotlib.pyplot as mpl_plt
        assert fig is not None
        mpl_plt.close(fig)

    def test_add_poi_markers_and_zones_creates_visible_marker_geometry(self):
        pytest.importorskip("trimesh")
        from trimesh import Scene
        from roadgen3d.street_layout import _add_poi_markers_and_zones

        scene = Scene()
        zones = [
            PoiExclusionInfo(poi_type="entrance", position_xz=(1.0, 2.0), radius_m=1.5, rule_name="entrance_clearance"),
            PoiExclusionInfo(poi_type="fire", position_xz=(4.0, 5.0), radius_m=1.8, rule_name="fire_access"),
            PoiExclusionInfo(poi_type="bus_stop", position_xz=(7.0, 8.0), radius_m=2.2, rule_name="bus_stop_clearance"),
        ]

        _add_poi_markers_and_zones(scene, zones)

        node_names = set(scene.graph.nodes_geometry)
        assert "poi_entrance_0" in node_names
        assert "poi_fire_1" in node_names
        assert "poi_bus_stop_2" in node_names
        assert "poi_base_entrance_0" in node_names
        assert "exclusion_bus_stop_2" in node_names

        marker_geom_name = scene.graph["poi_entrance_0"][1]
        marker_mesh = scene.geometry[marker_geom_name]
        assert marker_mesh.bounds[1][1] > 1.4

    def test_plot_poi_exclusion_overview(self):
        from roadgen3d.spatial_viz import plot_poi_exclusion_overview

        ctx = SpatialContext(
            junction_points_xz=(),
            entrance_points_xz=((5.0, 3.0),),
            road_half_width_m=4.0,
            length_m=40.0,
            bus_stop_points_xz=((10.0, 3.0),),
            fire_points_xz=(),
        )
        cfg = SimpleNamespace(road_width_m=8.0, length_m=40.0, sidewalk_width_m=2.5)
        zones = [
            {"poi_type": "entrance", "position_xz": [5.0, 3.0], "radius_m": 1.5, "rule_name": "entrance_clearance"},
            {"poi_type": "bus_stop", "position_xz": [10.0, 3.0], "radius_m": 2.2, "rule_name": "bus_stop_clearance"},
        ]

        fig = plot_poi_exclusion_overview(ctx, [], cfg, zones, [])
        import matplotlib.pyplot as mpl_plt
        assert fig is not None
        mpl_plt.close(fig)
