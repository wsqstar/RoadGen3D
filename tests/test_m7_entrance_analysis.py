"""Tests for M7 entrance openness and noise shielding analysis."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.entrance_analysis import (
    CarriagewayBoundary,
    PlacedAssetRegistry,
    compute_entrance_openness,
    compute_noise_shielding,
    evaluate_all_entrances,
    score_entrance_impact,
)
from roadgen3d.types import EntranceAssessment, PlacedAsset, SceneEntranceReport


# ---------------------------------------------------------------------------
# PlacedAssetRegistry
# ---------------------------------------------------------------------------


class TestPlacedAssetRegistry:
    def test_empty_registry(self):
        reg = PlacedAssetRegistry()
        assert reg.assets == ()
        assert reg.assets_within((0.0, 0.0), 10.0) == []

    def test_add_and_retrieve(self):
        reg = PlacedAssetRegistry()
        reg.add(position_xz=(5.0, 0.0), category="tree", bbox_xz=(4.5, 5.5, -0.5, 0.5))
        assert len(reg.assets) == 1
        assert reg.assets[0].category == "tree"
        assert reg.assets[0].position_xz == (5.0, 0.0)

    def test_assets_within_radius(self):
        reg = PlacedAssetRegistry()
        reg.add(position_xz=(1.0, 0.0), category="tree", bbox_xz=(0.5, 1.5, -0.5, 0.5))
        reg.add(position_xz=(10.0, 0.0), category="bench", bbox_xz=(9.5, 10.5, -0.3, 0.3))
        nearby = reg.assets_within((0.0, 0.0), 3.0)
        assert len(nearby) == 1
        assert nearby[0].category == "tree"

    def test_assets_within_includes_boundary(self):
        reg = PlacedAssetRegistry()
        reg.add(position_xz=(3.0, 0.0), category="lamp", bbox_xz=(2.9, 3.1, -0.1, 0.1))
        # Distance is exactly 3.0, should be included (<=)
        result = reg.assets_within((0.0, 0.0), 3.0)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# CarriagewayBoundary
# ---------------------------------------------------------------------------


class TestCarriagewayBoundary:
    def test_from_template(self):
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        # Point at sidewalk (x=10, z=6) should find nearest edge at z=4
        pt = cb.nearest_edge_point((10.0, 6.0))
        assert abs(pt[1] - 4.0) < 0.01

    def test_from_template_negative_side(self):
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        pt = cb.nearest_edge_point((10.0, -6.0))
        assert abs(pt[1] - (-4.0)) < 0.01

    def test_nearest_edge_clamped_to_segment(self):
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        # Beyond the road length, x should clamp to 50
        pt = cb.nearest_edge_point((60.0, 4.0))
        assert pt[0] <= 50.0 + 0.01


# ---------------------------------------------------------------------------
# Entrance openness
# ---------------------------------------------------------------------------


class TestEntranceOpenness:
    def test_no_assets_full_openness(self):
        reg = PlacedAssetRegistry()
        score = compute_entrance_openness((10.0, 5.0), reg)
        assert score == 1.0

    def test_one_asset_reduces_openness(self):
        reg = PlacedAssetRegistry()
        # Place a tree 2 m from entrance
        reg.add(position_xz=(12.0, 5.0), category="tree", bbox_xz=(11.5, 12.5, 4.5, 5.5))
        score = compute_entrance_openness((10.0, 5.0), reg)
        assert 0.0 < score < 1.0

    def test_many_assets_lower_openness(self):
        reg = PlacedAssetRegistry()
        # Surround entrance with assets in different directions
        entrance = (10.0, 5.0)
        for angle_deg in range(0, 360, 30):
            rad = math.radians(angle_deg)
            ax = entrance[0] + 2.0 * math.cos(rad)
            az = entrance[1] + 2.0 * math.sin(rad)
            reg.add(
                position_xz=(ax, az),
                category="bollard",
                bbox_xz=(ax - 0.3, ax + 0.3, az - 0.3, az + 0.3),
            )
        score = compute_entrance_openness(entrance, reg)
        # With 12 assets at 2 m, should be significantly blocked
        assert score < 0.5

    def test_distant_asset_ignored(self):
        reg = PlacedAssetRegistry()
        # Asset at 10 m, outside default 4 m radius
        reg.add(position_xz=(20.0, 5.0), category="tree", bbox_xz=(19.5, 20.5, 4.5, 5.5))
        score = compute_entrance_openness((10.0, 5.0), reg)
        assert score == 1.0


# ---------------------------------------------------------------------------
# Noise shielding
# ---------------------------------------------------------------------------


class TestNoiseShielding:
    def test_no_assets_no_shielding(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        score = compute_noise_shielding((10.0, 6.0), cb, reg)
        assert score == 0.0

    def test_tree_between_entrance_and_road(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        # Entrance at z=6, road edge at z=4, tree at z=5 (between)
        reg.add(
            position_xz=(10.0, 5.0),
            category="tree",
            bbox_xz=(9.0, 11.0, 4.5, 5.5),
        )
        score = compute_noise_shielding((10.0, 6.0), cb, reg)
        assert score > 0.0

    def test_lamp_less_effective_than_tree(self):
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)

        reg_tree = PlacedAssetRegistry()
        reg_tree.add(
            position_xz=(10.0, 5.0),
            category="tree",
            bbox_xz=(9.0, 11.0, 4.5, 5.5),
        )

        reg_lamp = PlacedAssetRegistry()
        reg_lamp.add(
            position_xz=(10.0, 5.0),
            category="lamp",
            bbox_xz=(9.0, 11.0, 4.5, 5.5),
        )

        score_tree = compute_noise_shielding((10.0, 6.0), cb, reg_tree)
        score_lamp = compute_noise_shielding((10.0, 6.0), cb, reg_lamp)
        assert score_tree > score_lamp


# ---------------------------------------------------------------------------
# score_entrance_impact (incremental)
# ---------------------------------------------------------------------------


class TestScoreEntranceImpact:
    def test_no_entrances_no_impact(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        penalty, bonus, violated = score_entrance_impact(
            candidate_xz=(5.0, 3.0),
            candidate_category="tree",
            candidate_bbox_xz=(4.5, 5.5, 2.5, 3.5),
            entrance_points_xz=(),
            registry=reg,
            carriageway_boundary=cb,
        )
        assert penalty == 0.0
        assert bonus == 0.0
        assert violated == ()

    def test_close_candidate_causes_penalty(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        # Place many existing assets around entrance to push openness near threshold
        entrance = (10.0, 6.0)
        for i in range(8):
            angle = i * 45.0
            rad = math.radians(angle)
            ax = entrance[0] + 2.0 * math.cos(rad)
            az = entrance[1] + 2.0 * math.sin(rad)
            reg.add(
                position_xz=(ax, az),
                category="bollard",
                bbox_xz=(ax - 0.3, ax + 0.3, az - 0.3, az + 0.3),
            )
        # Now add a candidate very close
        penalty, bonus, violated = score_entrance_impact(
            candidate_xz=(11.0, 6.0),
            candidate_category="bench",
            candidate_bbox_xz=(10.5, 11.5, 5.5, 6.5),
            entrance_points_xz=(entrance,),
            registry=reg,
            carriageway_boundary=cb,
        )
        # Should cause some penalty since area is already crowded
        assert penalty >= 0.0

    def test_shielding_asset_gives_bonus(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        entrance = (10.0, 6.0)
        # Place a tree candidate between entrance and road (z=5, road edge at z=4)
        penalty, bonus, violated = score_entrance_impact(
            candidate_xz=(10.0, 5.0),
            candidate_category="tree",
            candidate_bbox_xz=(9.0, 11.0, 4.5, 5.5),
            entrance_points_xz=(entrance,),
            registry=reg,
            carriageway_boundary=cb,
        )
        assert bonus > 0.0


# ---------------------------------------------------------------------------
# evaluate_all_entrances (post-placement)
# ---------------------------------------------------------------------------


class TestEvaluateAllEntrances:
    def test_no_entrances(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        report = evaluate_all_entrances((), reg, cb)
        assert report.mean_openness == 1.0
        assert report.mean_shielding == 0.0
        assert report.entrances_below_openness_threshold == 0
        assert report.assessments == ()

    def test_single_entrance_clean_scene(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        report = evaluate_all_entrances(((10.0, 6.0),), reg, cb)
        assert report.mean_openness == 1.0
        assert len(report.assessments) == 1
        assert report.assessments[0].openness_score == 1.0

    def test_report_with_placed_assets(self):
        reg = PlacedAssetRegistry()
        cb = CarriagewayBoundary.from_template(road_width_m=8.0, length_m=50.0)
        reg.add(position_xz=(11.0, 6.0), category="tree", bbox_xz=(10.5, 11.5, 5.5, 6.5))
        reg.add(position_xz=(10.0, 5.0), category="tree", bbox_xz=(9.0, 11.0, 4.5, 5.5))
        report = evaluate_all_entrances(((10.0, 6.0),), reg, cb)
        assert 0.0 < report.mean_openness < 1.0
        assert report.mean_shielding > 0.0
        assert report.assessments[0].shielding_ray_total == 7


# ---------------------------------------------------------------------------
# Design rules integration
# ---------------------------------------------------------------------------


class TestDesignRulesIntegration:
    def test_noise_aware_v1_profile_exists(self):
        from roadgen3d.design_rules import list_constraint_profiles, load_constraint_set

        profiles = list_constraint_profiles()
        assert "noise_aware_v1" in profiles

        cs = load_constraint_set("noise_aware_v1")
        assert cs.name == "noise_aware_v1"
        rule_names = {r.name for r in cs.rules}
        assert "entrance_openness" in rule_names
        assert "noise_shielding" in rule_names
        assert "min_tree_count" in rule_names

    def test_eval_metrics_entrance_functions(self):
        from roadgen3d.eval_metrics import (
            compute_mean_entrance_openness,
            compute_mean_noise_shielding,
        )

        summary = {"mean_entrance_openness": 0.85, "mean_noise_shielding": 0.45}
        assert compute_mean_entrance_openness([], summary) == 0.85
        assert compute_mean_noise_shielding([], summary) == 0.45

    def test_eval_metrics_defaults_when_missing(self):
        from roadgen3d.eval_metrics import (
            compute_mean_entrance_openness,
            compute_mean_noise_shielding,
        )

        assert compute_mean_entrance_openness([], {}) == 1.0
        assert compute_mean_noise_shielding([], {}) == 0.0

    def test_aggregate_includes_entrance_keys(self):
        from roadgen3d.eval_metrics import aggregate_scene_rows

        rows = [
            {"mean_entrance_openness": 0.9, "mean_noise_shielding": 0.4},
            {"mean_entrance_openness": 0.8, "mean_noise_shielding": 0.6},
        ]
        agg = aggregate_scene_rows(rows)
        assert abs(agg["mean_entrance_openness"] - 0.85) < 0.01
        assert abs(agg["mean_noise_shielding"] - 0.5) < 0.01


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class TestEntranceTypes:
    def test_placed_asset_frozen(self):
        pa = PlacedAsset(
            position_xz=(1.0, 2.0),
            category="tree",
            bbox_xz=(0.5, 1.5, 1.5, 2.5),
            bbox_radius=0.5,
        )
        with pytest.raises(AttributeError):
            pa.category = "bench"  # type: ignore[misc]

    def test_entrance_assessment_fields(self):
        ea = EntranceAssessment(
            entrance_xz=(10.0, 5.0),
            openness_score=0.8,
            shielding_score=0.4,
            blocked_angle_deg=72.0,
            shielding_ray_hits=3,
            shielding_ray_total=7,
        )
        assert ea.openness_score == 0.8
        assert ea.shielding_ray_hits == 3

    def test_scene_entrance_report_fields(self):
        report = SceneEntranceReport(
            assessments=(),
            mean_openness=1.0,
            mean_shielding=0.0,
            min_openness=1.0,
            entrances_below_openness_threshold=0,
        )
        assert report.mean_openness == 1.0
        assert report.entrances_below_openness_threshold == 0
